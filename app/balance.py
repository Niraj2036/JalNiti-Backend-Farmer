import json
import math
from datetime import datetime
from unicodedata import category
from shapely.geometry import Point
import requests
import geopandas as gpd
import rasterio
import json
from rapidfuzz import process, fuzz
from flask import Blueprint, request, jsonify
from pyproj import Transformer
import os
balance_bp = Blueprint("balance", __name__)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# -------------------------------------------------
# RULE CONSTANTS
# -------------------------------------------------
LITHOLOGY_TIF_PATH = os.path.join(
    BASE_DIR, "Spatial_data", "india_geology_2m.tif"
)

TALUKA_GEOJSON_PATH = os.path.join(
    BASE_DIR, "Spatial_data", "India_geojson", "india_taluk.geojson"
)

DISTRICT_GEOJSON_PATH = os.path.join(
    BASE_DIR, "Spatial_data", "India_geojson", "india_district.geojson"
)
INGRES_JSON_PATH = os.path.join(
    BASE_DIR, "India_Ingris_Data_Complete.json"
)
TALUKA_AREA_JSON = os.path.join(
    BASE_DIR, "taluka_areas.json"
)

taluka_gdf = gpd.read_file(TALUKA_GEOJSON_PATH).to_crs("EPSG:4326")
district_gdf = gpd.read_file(DISTRICT_GEOJSON_PATH).to_crs("EPSG:4326")

CATEGORY_FACTOR = {
    "Safe": 0.40,
    "Semi-Critical": 0.25,
    "Critical": 0.10,
    "Over-exploited": 0.05
}

LITHOLOGY_FACTOR = {
    1: 1.00,   # Alluvium
    2: 1.00,   # Sandstone / Sedimentary
    3: 1.00,   # Limestone
    4: 1.00,   # Granite / Gneiss
    5: 1.00,   # Basalt
    0: 1.00    # Unknown
}

LIFECYCLE_FACTOR = 0.70  # one crop lifecycle


# -------------------------------------------------
# HELPERS
# -------------------------------------------------

def slope_factor(slope_deg):
    # if slope_deg <= 2:
    #     return 1.0
    # elif slope_deg <= 5:
    #     return 0.85
    # elif slope_deg <= 10:
    #     return 0.65
    # else:
    #     return 0.40
    return 1.0


def get_admin_from_latlon(lat, lon, taluka_gdf, district_gdf):
    point = Point(lon, lat)

    taluka_row = taluka_gdf[taluka_gdf.contains(point)]
    district_row = district_gdf[district_gdf.contains(point)]

    return {
        "taluka": taluka_row.iloc[0]["NAME_3"] if not taluka_row.empty else None,
        "district": district_row.iloc[0]["NAME_2"] if not district_row.empty else None
    }
def search_by_location_name(locations, query, score_cutoff=70):
        # Try exact match first
        for loc in locations:
            if loc["locationName"].lower() == query.lower():
                return loc   # return full JSON of exact match

        # If no exact match, fallback to fuzzy search
        names = [loc["locationName"] for loc in locations]
        match = process.extractOne(
            query,
            names,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff
        )

        if match:  # if fuzzy match found
            name, score, idx = match
            return locations[idx]

        return None   # if no match at all

# -------------------------------------------------
# INGRES DATA FETCH (YOU PROVIDED THIS LOGIC)
# -------------------------------------------------

def fetch_ingres_with_fallback(taluka, district):
    with open(INGRES_JSON_PATH, "r", encoding="utf-8") as f: 
        ingres_data = json.load(f)

    # Try taluka first
    if taluka:
        match =search_by_location_name(
            ingres_data, taluka, score_cutoff=70
        )
        if match:
            return match, "taluka"

    # Fallback to district
    if district:
        match = search_by_location_name(
            ingres_data, district, score_cutoff=70
        )
        if match:
            return match, "district"

    return None, None


def fetch_ingres_business_data(ingres_match):
    url = "https://ingres.iith.ac.in/api/gec/getBusinessDataForUserOpen"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "parentLocName": "INDIA",
        "locname": ingres_match["locationName"],
        "loctype": ingres_match["locationType"],
        "view": "admin",
        "locuuid": ingres_match["locationUUID"],
        "year": "2024-2025",
        "computationType": "normal",
        "component": "recharge",
        "period": "annual",
        "category": ingres_match["categoryTotal"],
        "mapOnClickParams": "true",
        "stateuuid": ingres_match.get("stateUUID"),
        "verificationStatus": 1,
        "approvalLevel": 1,
        "parentuuid": ingres_match.get("stateUUID")
    }

    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    r.raise_for_status()

    return r.json()

def extract_mcm(value):
    """
    Extract numeric groundwater value (MCM) from INGRES fields.
    Specifically handles availabilityForFutureUse structure.
    """
    if value is None:
        return 0.0

    # Case 1: already numeric
    if isinstance(value, (int, float)):
        return float(value)

    # Case 2: availabilityForFutureUse dict
    if isinstance(value, dict):
        # Prefer 'total'
        if "total" in value:
            return float(value["total"])
        # Fallbacks (just in case)
        if "non_command" in value:
            return float(value["non_command"])
        return 0.0

    # Case 3: unexpected format
    return 0.0

def shorten_ingres_response(api_response):
    for entry in api_response:
        return {
            "locationName": entry.get("locationName"),
            "totalGWAvailability": entry.get("totalGWAvailability"),
            "availabilityForFutureUse": entry.get("availabilityForFutureUse"),
            "stageOfExtraction": entry.get("stageOfExtraction"),
            "category": entry.get("category")
        }
    return None

def get_slope(lat, lon):
    """
    Terrain slope (degrees)
    """
    print("[SLOPE] Slope API called")
    url = "https://api.opentopodata.org/v1/srtm90m"
    r = requests.get(url, params={"locations": f"{lat},{lon}"}, timeout=10)
    print("data:", r.json())
    return r.json()["results"][0]["elevation"]

# -------------------------------------------------
# MAIN GW BALANCE FUNCTION
# -------------------------------------------------

def groundwater_balance_from_api_input(lat, lon, farm_area_ares):
    """
    Entry point for Flask API.
    Accepts ONLY:
      - latitude
      - longitude
      - farm_area_ares
    """

    # -----------------------------
    # Step 1: Admin resolution
    # -----------------------------
    admin = get_admin_from_latlon(lat, lon, taluka_gdf, district_gdf)
    taluka = admin["taluka"]
    district = admin["district"]

    if not taluka:
        return {"error": "Taluka could not be resolved from coordinates"}

    # -----------------------------
    # Step 2: Taluka area lookup
    # -----------------------------
    taluka_area_sq_km = get_taluka_area_sq_km(taluka)

    if not taluka_area_sq_km:
        return {"error": "Taluka area not found"}

    # -----------------------------
    # Step 3: Lithology from raster
    # -----------------------------
    lithology_code = sample_raster(
        LITHOLOGY_TIF_PATH,
        lat,
        lon
    )

    # -----------------------------
    # Step 4: Slope
    # -----------------------------
    slope_deg = get_slope(lat, lon)

    # -----------------------------
    # Step 5: Groundwater balance
    # -----------------------------
    return calculate_groundwater_balance(
        lat=lat,
        lon=lon,
        farm_area_ares=farm_area_ares,
        taluka_area_sq_km=taluka_area_sq_km,
        lithology_code=lithology_code,
        slope_deg=slope_deg,
        taluka_gdf=taluka_gdf,
        district_gdf=district_gdf
    )
def get_taluka_area_sq_km(taluka_name):
    with open(TALUKA_AREA_JSON, "r", encoding="utf-8") as f:
        taluka_areas = json.load(f)

    for entry in taluka_areas:
        if entry["sdtname"].lower() == taluka_name.lower():
            return entry["area_km2"]

    # fallback: fuzzy match
    names = [e["sdtname"] for e in taluka_areas]
    match = process.extractOne(
        taluka_name,
        names,
        scorer=fuzz.WRatio,
        score_cutoff=70
    )

    if match:
        name, score, idx = match
        return taluka_areas[idx]["area_km2"]

    return None

@balance_bp.route("/gw-balance", methods=["POST"])
def gw_balance():
    data = request.json

    lat = data["latitude"]
    lon = data["longitude"]
    farm_area = data["farm_area_ares"]

    result = groundwater_balance_from_api_input(lat, lon, farm_area)
    return jsonify(result)

def sample_raster(raster_path, lat, lon):
    """
    Sample a single pixel value from a raster (local or COG).
    """
    with rasterio.open(raster_path) as ds:
        transformer = Transformer.from_crs(
            "EPSG:4326", ds.crs, always_xy=True
        )
        x, y = transformer.transform(lon, lat)
        row, col = ds.index(x, y)
        return int(ds.read(1)[row, col])
    
def extract_category(cat):
    """
    Normalize INGRES category to a rule-compatible string.
    """
    if not cat:
        return "Critical"

    # Case 1: already string
    if isinstance(cat, str):
        return cat.title()  # safe -> Safe

    # Case 2: dict (most common INGRES format)
    if isinstance(cat, dict):
        if "total" in cat and cat["total"]:
            return str(cat["total"]).title()
        if "non_command" in cat and cat["non_command"]:
            return str(cat["non_command"]).title()

    # Fallback (conservative)
    return "Critical"

    
def calculate_groundwater_balance(
    lat,
    lon,
    farm_area_ares,
    taluka_area_sq_km,
    lithology_code,
    slope_deg,
    taluka_gdf,
    district_gdf
):
    """
    Rule-based groundwater availability
    for ONE crop lifecycle
    """

    # -----------------------------
    # Step 1: Admin resolution
    # -----------------------------
    admin = get_admin_from_latlon(lat, lon, taluka_gdf, district_gdf)
    taluka = admin["taluka"]
    district = admin["district"]

    # -----------------------------
    # Step 2: INGRES data
    # -----------------------------
    ingres_match, level_used = fetch_ingres_with_fallback(taluka, district)

    if not ingres_match:
        return {"error": "No INGRES groundwater data available"}

    ingres_api_data = fetch_ingres_business_data(ingres_match)
    ingres_data = shorten_ingres_response(ingres_api_data)

    # -----------------------------
    # Step 3: Administrative entitlement
    # -----------------------------
    gw_mcm = extract_mcm(
    ingres_data.get("availabilityForFutureUse")
)

    taluka_gw_litres = gw_mcm * 1_000_000 * 1000

    taluka_area_ares = taluka_area_sq_km * 10_000
    area_fraction = farm_area_ares / taluka_area_ares

    farm_entitlement = taluka_gw_litres * area_fraction

    # -----------------------------
    # Step 4: Apply rule factors
    # -----------------------------
    category = extract_category(ingres_data.get("category"))
    cat_factor = CATEGORY_FACTOR.get(category, 0.10)


    litho_factor = LITHOLOGY_FACTOR.get(lithology_code, 0.50)
    slp_factor = slope_factor(slope_deg)

    final_litres = (
        farm_entitlement
        * cat_factor
        * litho_factor
        * slp_factor
        * LIFECYCLE_FACTOR
    )

    # -----------------------------
    # Final structured output
    # -----------------------------
    return {
        "groundwater_available_litres": round(final_litres, 2)*100,
        "basis": {
            "level_used": level_used,
            "category": category,
            "farm_area_ares": farm_area_ares,
            "taluka_area_sq_km": taluka_area_sq_km,
            "lithology_code": lithology_code,
            "lifecycle": "single crop"
        }
    }
