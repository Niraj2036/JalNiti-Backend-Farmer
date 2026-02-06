from flask import Blueprint, request, jsonify
import requests
import pandas as pd
import re

# =========================
# Blueprint
# =========================
sowing_bp = Blueprint("sowing_bp", __name__)

# =========================
# RULES & CATEGORIES
# =========================

CATEGORY_RULES = {
    "small_vegetables": {"soil_temp": 10, "moisture": 0.22, "rain_prob": 45, "rain_mm": 6},
    "solanaceae": {"soil_temp": 15, "moisture": 0.20, "rain_prob": 30, "rain_mm": 5},
    "cucurbitaceae": {"soil_temp": 14, "moisture": 0.23, "rain_prob": 40, "rain_mm": 6},
    "roots_tubers": {"soil_temp": 12, "moisture": 0.21, "rain_prob": 45, "rain_mm": 6},
    "legumes": {"soil_temp": 12, "moisture": 0.18, "rain_prob": 50, "rain_mm": 7},
    "perennial_veg": {"soil_temp": 10, "moisture": 0.22, "rain_prob": 45, "rain_mm": 6},
    "fiber_crops": {"soil_temp": 14, "moisture": 0.20, "rain_prob": 40, "rain_mm": 6},
    "oil_crops": {"soil_temp": 12, "moisture": 0.18, "rain_prob": 45, "rain_mm": 6},
    "cereals": {"soil_temp": 10, "moisture": 0.17, "rain_prob": 55, "rain_mm": 8},
    "forages": {"soil_temp": 10, "moisture": 0.20, "rain_prob": 55, "rain_mm": 8},
    "sugar_crop": {"soil_temp": 18, "moisture": 0.25, "rain_prob": 50, "rain_mm": 10},
    "tropical_trees": {"soil_temp": 18, "moisture": 0.25, "rain_prob": 45, "rain_mm": 8},
    "fruit_trees": {"soil_temp": 15, "moisture": 0.22, "rain_prob": 40, "rain_mm": 6},
    "wetlands": {"soil_temp": 15, "moisture": 0.30, "rain_prob": 100, "rain_mm": 25},
}

CROP_CATEGORY = {
    "tomato": "solanaceae",
    "egg plant": "solanaceae",
    "sweet peppers bell": "solanaceae",
    "rice": "wetlands",
    "potato": "roots_tubers",
    "onion dry": "small_vegetables",
    "garlic": "small_vegetables",
    "lentil": "legumes",
    "groundnut peanut": "legumes",
    "cotton": "fiber_crops",
    "sugar cane": "sugar_crop",
    "banana year 1": "tropical_trees",
    "banana year 2": "tropical_trees",
    "kiwi": "fruit_trees",
    "grapes wine": "fruit_trees",
    "hops": "fruit_trees",
    # (you can keep extending this safely)
}

# =========================
# HELPERS
# =========================

def normalize_crop_name(crop: str) -> str:
    crop = crop.lower().strip()
    crop = re.sub(r"[^\w\s]", "", crop)
    crop = re.sub(r"\s+", " ", crop)
    return crop


def get_forecast(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": [
            "soil_temperature_0_to_10cm",
            "soil_moisture_0_to_10cm",
            "precipitation"
        ],
        "daily": ["precipitation_probability_max"],
        "forecast_days": 16,
        "timezone": "auto",
        "models": "gfs_seamless"
    }

    r = requests.get(url, params=params)
    r.raise_for_status()
    data = r.json()

    hourly_df = pd.DataFrame({
        "time": pd.to_datetime(data["hourly"]["time"]),
        "soil_temp": data["hourly"]["soil_temperature_0_to_10cm"],
        "soil_moisture": data["hourly"]["soil_moisture_0_to_10cm"],
        "rain_mm": data["hourly"]["precipitation"]
    })

    hourly_df["date"] = hourly_df["time"].dt.date

    daily_from_hourly = hourly_df.groupby("date").agg({
        "soil_temp": "mean",
        "soil_moisture": "mean",
        "rain_mm": "sum"
    }).reset_index()

    daily_prob = pd.DataFrame({
        "date": pd.to_datetime(data["daily"]["time"]).date,
        "rain_prob": data["daily"]["precipitation_probability_max"]
    })

    return pd.merge(daily_from_hourly, daily_prob, on="date", how="left")


def score_days(df: pd.DataFrame, rules: dict):
    results = []

    for _, d in df.iterrows():
        score = 0
        reasons = []

        if d.soil_temp >= rules["soil_temp"]:
            score += 3
            reasons.append("Soil temperature suitable")
        else:
            reasons.append("Soil temperature not suitable")

        if d.soil_moisture >= rules["moisture"]:
            score += 3
            reasons.append("Adequate soil moisture")
        else:
            reasons.append("Low soil moisture")

        if d.rain_prob <= rules["rain_prob"]:
            score += 2
            reasons.append("Rain probability acceptable")
        else:
            reasons.append("High rain probability")

        if d.rain_mm <= rules["rain_mm"]:
            score += 2
            reasons.append("No heavy rainfall expected")
        else:
            reasons.append("Heavy rainfall risk")

        results.append({
            "date": str(d.date),
            "score": score,
            "soil_temp": round(d.soil_temp, 2),
            "soil_moisture": round(d.soil_moisture, 3),
            "rain_prob": d.rain_prob,
            "rain_mm": round(d.rain_mm, 2),
            "reasons": reasons
        })

    return sorted(results, key=lambda x: x["score"], reverse=True)

# =========================
# ROUTE
# =========================

@sowing_bp.route("/best-sowing-day", methods=["GET"])
def best_sowing_day():
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    crop_raw = request.args.get("crop")

    if not crop_raw:
        return jsonify({"error": "crop parameter missing"}), 400

    crop = normalize_crop_name(crop_raw)
    category = CROP_CATEGORY.get(crop)

    if not category:
        return jsonify({"error": "Crop not found", "crop_received": crop}), 400

    rules = CATEGORY_RULES[category]
    forecast_df = get_forecast(lat, lon)
    ranked_days = score_days(forecast_df, rules)

    return jsonify({
        "crop": crop,
        "category": category,
        "rules_used": rules,
        "best_day": ranked_days[0],
        "top_3_days": ranked_days[:3]
    })
