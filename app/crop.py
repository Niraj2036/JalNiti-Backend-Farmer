from pathlib import Path
import pandas as pd
import math
from difflib import SequenceMatcher
from flask import Blueprint, request, jsonify
from datetime import date
import requests

crop_bp = Blueprint("crop_bp", __name__)

# =========================
# PATH RESOLUTION
# =========================

BASE_DIR = Path(__file__).resolve().parent.parent

CROP_FILE = BASE_DIR / "Crop Coefficients.xlsx"
STATION_FILE = BASE_DIR / "station_coordinates.xlsx"

# =========================
# LOAD FILES
# =========================

df_crop = pd.read_excel(CROP_FILE)
df_station = pd.read_excel(STATION_FILE)

# ---- Column mapping ----
CROP_COL = df_crop.columns[0]
STATION_COL = df_crop.columns[1]
WATER_MM_COL = df_crop.columns[2]     # seasonal ET (mm)
PROFIT_COL = df_crop.columns[6]       # profit per litre OR efficiency score

STATION_NAME_COL = df_station.columns[0]
LAT_COL = df_station.columns[1]
LON_COL = df_station.columns[2]

# ---- Normalize text ----
df_crop[CROP_COL] = df_crop[CROP_COL].str.lower()
df_crop[STATION_COL] = df_crop[STATION_COL].str.lower()
df_station[STATION_NAME_COL] = df_station[STATION_NAME_COL].str.lower()

# =========================
# GEO HELPERS
# =========================

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # km
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )

    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_closest_station(lat, lon):
    df_station["distance"] = df_station.apply(
        lambda r: haversine(lat, lon, r[LAT_COL], r[LON_COL]),
        axis=1
    )
    return df_station.sort_values("distance").iloc[0][STATION_NAME_COL]

# =========================
# CROP MATCHING
# =========================

def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def best_crop_match(station, crop):
    crop = crop.lower()
    subset = df_crop[df_crop[STATION_COL] == station].copy()

    subset["sim"] = subset[CROP_COL].apply(
        lambda c: similarity(c, crop)
    )

    return subset.sort_values("sim", ascending=False).iloc[0]

# =========================
# SEASON LOGIC
# =========================

def get_current_season(today=None):
    if today is None:
        today = date.today()

    month = today.month

    if month in [6, 7, 8, 9, 10]:
        return "kharif"
    elif month in [11, 12, 1, 2, 3]:
        return "rabi"
    else:
        return "zaid"


def get_season_dates(season, today=None):
    if today is None:
        today = date.today()

    year = today.year
    today_str = today.strftime("%Y%m%d")

    if season == "kharif":
        start = f"{year}0601"
        end = f"{year}1015"

    elif season == "rabi":
        if today.month >= 10:
            start = f"{year}1015"
            end = f"{year + 1}0331"
        else:
            start = f"{year - 1}1015"
            end = f"{year}0331"

    elif season == "zaid":
        start = f"{year}0315"
        end = f"{year}0615"

    # 🚨 CRITICAL FIX: prevent future dates
    if end > today_str:
        end = today_str

    return start, end


# =========================
# RAINFALL (NASA POWER)
# =========================

def get_rainfall(lat, lon):
    season = get_current_season()
    start, end = get_season_dates(season)

    url = "https://power.larc.nasa.gov/api/temporal/daily/point"
    params = {
        "parameters": "PRECTOTCORR",   # ✅ EXACT MATCH
        "community": "AG",
        "longitude": lon,
        "latitude": lat,
        "start": start,
        "end": end,
        "format": "JSON"
    }

    r = requests.get(url, params=params, timeout=20)

    if r.status_code != 200:
        print("[RAIN][ERROR]", r.status_code, r.text)
        return 0

    data = r.json()

    rainfall_data = (
        data.get("properties", {})
            .get("parameter", {})
            .get("PRECTOTCORR", {})
    )

    valid_values = [
        v for v in rainfall_data.values()
        if isinstance(v, (int, float)) and v >= 0
    ]

    total = round(sum(valid_values), 2)

    print(f"[RAIN] {season.upper()} rainfall = {total} mm")

    return total

# =========================
# ROUTES
# =========================

@crop_bp.route("/water-requirement", methods=["POST"])
def water_requirement():
    data = request.get_json()

    latitude = float(data["latitude"])
    longitude = float(data["longitude"])
    crop = data["crop"]
    farm_area = float(data["farm_area"])  # hectares

    station = get_closest_station(latitude, longitude)
    row = best_crop_match(station, crop)

    crop_et_mm = row[WATER_MM_COL]

    seasonal_rain_mm = get_rainfall(latitude, longitude)
    effective_rain_mm = 0.7 * seasonal_rain_mm

    net_irrigation_mm = max(crop_et_mm - effective_rain_mm, 0)

    # 1 mm over 1 hectare = 10,000 litres
    water_litres = net_irrigation_mm * farm_area * 10000

    profit_per_litre = row[PROFIT_COL]
    total_profit = water_litres * profit_per_litre

    return jsonify({
        "station": station,
        "season": get_current_season(),
        "crop_used": row[CROP_COL],
        "crop_et_mm": round(crop_et_mm, 2),
        "seasonal_rain_mm": round(seasonal_rain_mm, 2),
        "effective_rain_mm": round(effective_rain_mm, 2),
        "net_irrigation_mm": round(net_irrigation_mm, 2),
        "water_required_litres": round(water_litres, 2),
        "total_revenue": round(total_profit, 2)
    })


@crop_bp.route("/top-crops", methods=["GET"])
def top_crops():
    latitude = float(request.args.get("latitude"))
    longitude = float(request.args.get("longitude"))

    station = get_closest_station(latitude, longitude)
    subset = df_crop[df_crop[STATION_COL] == station]

    top3 = subset.sort_values(PROFIT_COL, ascending=False).head(3)

    return jsonify({
        "station": station,
        "season": get_current_season(),
        "top_3_crops": [
            {
                "crop": r[CROP_COL],
                "profit_metric": r[PROFIT_COL]
            }
            for _, r in top3.iterrows()
        ]
    })
