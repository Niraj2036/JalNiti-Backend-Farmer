# ==========================================================
# AI-ASSISTED CROP PREDICTION PIPELINE
# Location → Real APIs → Gemini fallback → ML model
# ==========================================================

from flask import Blueprint, request, jsonify
import requests
import numpy as np
import joblib
import os
import json
import re

import google.generativeai as genai

# ==========================================================
# CONFIG
# ==========================================================

crop_ai_bp = Blueprint("crop_ai_bp", __name__)

GEMINI_API_KEY = "AIzaSyAMkzfPMwGpXclufNB_5rBwrD7dlk_cQP8"  # export before running
WEATHER_API_KEY ="597b41fddb2a42e5b3065510260702"  # WeatherAPI.com key

MODEL_PATH = "RandomForest.pkl"  # change if needed

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-2.5-flash")

# ==========================================================
# WEATHER (REAL DATA)
# ==========================================================

def get_weather(lat, lon):
    url = "http://api.weatherapi.com/v1/current.json"
    params = {
        "key": WEATHER_API_KEY,
        "q": f"{lat},{lon}",
        "aqi": "no"
    }

    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        raise Exception("Weather API failed")

    data = r.json()

    return {
        "temperature": data["current"]["temp_c"],
        "humidity": data["current"]["humidity"],
        "rainfall": data["current"].get("precip_mm", 0.0)
    }

# ==========================================================
# SOIL (REAL DATA – SoilGrids)
# ==========================================================

def get_soil_ph_soilgrids(lat, lon):
    url = "https://rest.isric.org/soilgrids/v2.0/properties/query"
    params = {
        "lat": lat,
        "lon": lon,
        "property": "phh2o",
        "depth": "0-5cm"
    }

    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        return None

    try:
        data = r.json()
        # SoilGrids pH is scaled ×10
        ph = data["properties"]["layers"][0]["depths"][0]["values"]["mean"] / 10
        return round(ph, 2)
    except Exception:
        return None

# ==========================================================
# GEMINI FALLBACK (ONLY IF DATA MISSING)
# ==========================================================

def gemini_estimate_soil(lat, lon, location_name):
    prompt = f"""
You are an agricultural soil scientist.

Estimate average soil nutrient values for this Indian location.
Respond ONLY in valid JSON. No explanation text.

Location:
- Name: {location_name}
- Latitude: {lat}
- Longitude: {lon}

Return strictly in this format:
{{
  "nitrogen": number,
  "phosphorus": number,
  "potassium": number,
  "ph": number
}}
"""

    response = gemini_model.generate_content(prompt)
    text = response.text.strip()

    json_text = re.search(r"\{.*\}", text, re.S).group()
    return json.loads(json_text)

# ==========================================================
# SOIL DATA ORCHESTRATOR
# ==========================================================

def get_soil_data(lat, lon, location_name):
    soil = {
        "nitrogen": None,
        "phosphorus": None,
        "potassium": None,
        "ph": None
    }

    # Try real soil pH
    soil["ph"] = get_soil_ph_soilgrids(lat, lon)

    # If anything missing → Gemini fills gaps
    if any(v is None for v in soil.values()):
        gemini_data = gemini_estimate_soil(lat, lon, location_name)

        for key in soil:
            if soil[key] is None:
                soil[key] = gemini_data[key]

    return soil

# ==========================================================
# MAIN API ROUTE
# ==========================================================

@crop_ai_bp.route("/predict-crop-ai", methods=["POST"])
def predict_crop_ai():
    data = request.get_json()

    lat = float(data["latitude"])
    lon = float(data["longitude"])
    location = data.get("location", "India")

    # --------------------
    # REAL DATA
    # --------------------
    weather = get_weather(lat, lon)
    soil = get_soil_data(lat, lon, location)

    # --------------------
    # ML MODEL
    # --------------------
    model = joblib.load(MODEL_PATH)

    X = np.array([[
        soil["nitrogen"],
        soil["phosphorus"],
        soil["potassium"],
        weather["temperature"],
        weather["humidity"],
        soil["ph"],
        weather["rainfall"]
    ]])

    prediction = model.predict(X)[0]

    # --------------------
    # RESPONSE
    # --------------------
    return jsonify({
        "location": location,
        "inputs": {
            "soil": soil,
            "weather": weather
        },
        "predicted_crop": prediction
    })
