import requests
from flask import Blueprint, request, jsonify

levels_bp = Blueprint("levels", __name__)

BASE_URL = "https://mahabhunakasha.mahabhumi.gov.in/rest/VillageMapService"
STATE = "27"

# -------------------------------------------------
# Persistent session (MANDATORY for Bhunaksha)
# -------------------------------------------------
session = requests.Session()

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://mahabhunakasha.mahabhumi.gov.in",
    "Referer": "https://mahabhunakasha.mahabhumi.gov.in/27/index.html"
}


# -------------------------------------------------
# Initialize session (creates JSESSIONID)
# -------------------------------------------------
def init_bhunaksha_session():
    init_url = "https://mahabhunakasha.mahabhumi.gov.in/27/index.html"

    print("\n🔹 Initializing Bhunaksha session")
    res = session.get(init_url, headers=COMMON_HEADERS, timeout=30)

    print("Init status:", res.status_code)
    print("Cookies:", session.cookies.get_dict())


# -------------------------------------------------
# Helper: fetch hierarchy levels
# -------------------------------------------------
def fetch_level(level, codes=""):
    if not session.cookies:
        init_bhunaksha_session()

    url = f"{BASE_URL}/ListsAfterLevelGeoref"

    data = {
        "state": STATE,
        "level": str(level),
        "codes": codes,
        "hasmap": "true"
    }

    print("\n==============================")
    print("POST ListsAfterLevelGeoref")
    print("Payload:", data)
    print("Cookies:", session.cookies.get_dict())

    try:
        res = session.post(url, data=data, headers=COMMON_HEADERS, timeout=30)

        print("Status:", res.status_code)
        print("Content-Type:", res.headers.get("Content-Type"))
        print("Raw response (300 chars):")
        print(res.text[:300])

        if res.status_code != 200:
            print("❌ Bhunaksha rejected request")
            return []

        json_data = res.json()

        if isinstance(json_data, list) and len(json_data) > 0:
            print("✅ Returned rows:", len(json_data[0]))
            return json_data[0]

        print("⚠️ Empty JSON structure")
        return []

    except Exception as e:
        print("🔥 Exception:", e)
        return []


# -------------------------------------------------
# 1️⃣ Rural / Urban → Districts
# GET /api/levels/districts?area=R
# -------------------------------------------------
@levels_bp.route("/districts", methods=["GET"])
def get_districts():
    area = request.args.get("area")  # R or U

    if area not in ["R", "U"]:
        return jsonify({"error": "area must be R or U"}), 400

    districts = fetch_level(level=1, codes=f"{area},")

    return jsonify([
        {"code": d["code"], "name": d["value"]}
        for d in districts
    ])


# -------------------------------------------------
# 2️⃣ District → Talukas
# GET /api/levels/talukas?area=R&districtCode=19
# -------------------------------------------------
@levels_bp.route("/talukas", methods=["GET"])
def get_talukas():
    area = request.args.get("area")
    district_code = request.args.get("districtCode")

    if not area or not district_code:
        return jsonify({"error": "area and districtCode required"}), 400

    talukas = fetch_level(
        level=2,
        codes=f"{area},{district_code},"
    )

    return jsonify([
        {"code": t["code"], "name": t["value"]}
        for t in talukas
    ])


# -------------------------------------------------
# 3️⃣ Taluka → Villages
# GET /api/levels/villages?area=R&districtCode=19&talukaCode=02
# -------------------------------------------------
@levels_bp.route("/villages", methods=["GET"])
def get_villages():
    area = request.args.get("area")
    district_code = request.args.get("districtCode")
    taluka_code = request.args.get("talukaCode")

    if not all([area, district_code, taluka_code]):
        return jsonify({"error": "area, districtCode, talukaCode required"}), 400

    villages = fetch_level(
        level=3,
        codes=f"{area},{district_code},{taluka_code},"
    )

    return jsonify([
        {"code": v["code"], "name": v["value"]}
        for v in villages
    ])


# -------------------------------------------------
# 4️⃣ Village → Survey Numbers
# GET /api/levels/surveys?villageCode=2719...
# -------------------------------------------------
@levels_bp.route("/surveys", methods=["GET"])
def get_surveys():
    area = request.args.get("area")
    district_code = request.args.get("districtCode")
    taluka_code = request.args.get("talukaCode")
    village_code = request.args.get("villageCode")

    if not all([area, district_code, taluka_code, village_code]):
        return jsonify({
            "error": "area, districtCode, talukaCode, villageCode required"
        }), 400

    if not session.cookies:
        init_bhunaksha_session()

    if area == "R":
        loged_levels = f"RVM{district_code}{taluka_code}{village_code}"
    else:
        loged_levels = f"UCM{district_code}{taluka_code}{village_code}"

    url = f"{BASE_URL}/kidelistFromGisCodeMH"
    data = {
        "state": STATE,
        "logedLevels": loged_levels
    }

    res = session.post(url, data=data, headers=COMMON_HEADERS, timeout=30)

    if res.status_code != 200:
        return jsonify([])

    raw_surveys = res.json()

    # 🔥 NUMERIC SORT
    sorted_surveys = sorted(raw_surveys, key=lambda x: int(x))

    return jsonify(sorted_surveys)

@levels_bp.route("/plot-info", methods=["GET"])
def get_plot_info():
    area = request.args.get("area")                    # R / U
    district_code = request.args.get("districtCode")
    taluka_code = request.args.get("talukaCode")
    village_gis_code = request.args.get("villageGisCode")
    plot_no = request.args.get("plotNo")

    if not all([area, district_code, taluka_code, village_gis_code, plot_no]):
        return jsonify({
            "error": "area, districtCode, talukaCode, villageGisCode, plotNo required"
        }), 400

    if not session.cookies:
        init_bhunaksha_session()

    # -----------------------------
    # Construct giscode
    # -----------------------------
    if area == "R":
        giscode = f"RVM{district_code}{taluka_code}{village_gis_code}"
    elif area == "U":
        giscode = f"UCM{district_code}{taluka_code}{village_gis_code}"
    else:
        return jsonify({"error": "area must be R or U"}), 400

    # -----------------------------
    # 1️⃣ Call getPlotInfo
    # -----------------------------
    plot_info_url = "https://mahabhunakasha.mahabhumi.gov.in/rest/MapInfo/getPlotInfo"

    plot_payload = {
        "state": "27",
        "giscode": giscode,
        "plotno": plot_no,
        "srs": "4326"
    }

    res = session.post(plot_info_url, data=plot_payload, headers=COMMON_HEADERS, timeout=30)

    if res.status_code != 200:
        return jsonify({})

    plot_json = res.json()

    plotid = plot_json.get("plotid")
    if not plotid:
        return jsonify({})

    # -----------------------------
    # 2️⃣ Call getExtentGeoref
    # -----------------------------
    latitude, longitude = fetch_lat_lng_from_extent(giscode, plotid)

    # -----------------------------
    # 3️⃣ Extract owners
    # -----------------------------
    owners = extract_owners(plot_json)

    # -----------------------------
    # Final response
    # -----------------------------
    return jsonify({
        "latitude": latitude,
        "longitude": longitude,
        "owners": owners
    })

import re

def extract_owners(plot_json):
    owners = []
    info_text = plot_json.get("info", "")

    blocks = info_text.split("---------------------------------")

    for block in blocks:
        owner_match = re.search(r"Owner Name\s*:\s*(.+)", block)
        area_match = re.search(r"Total Area\s*:\s*([\d.]+)", block)

        if owner_match and area_match:
            owners.append({
                "ownerName": owner_match.group(1).strip(),
                "totalArea": area_match.group(1).strip()
            })

    return owners
def fetch_lat_lng_from_extent(giscode, plotid):
    url = "https://mahabhunakasha.mahabhumi.gov.in/rest/MapInfo/getExtentGeoref"

    data = {
        "state": "27",
        "giscode": giscode,
        "plotid": plotid,
        "srs": "4326"
    }

    res = session.post(url, data=data, headers=COMMON_HEADERS, timeout=30)

    if res.status_code != 200:
        return None, None

    extent = res.json()

    xmin = extent.get("xmin")
    xmax = extent.get("xmax")
    ymin = extent.get("ymin")
    ymax = extent.get("ymax")

    if None in [xmin, xmax, ymin, ymax]:
        return None, None

    longitude = (xmin + xmax) / 2
    latitude = (ymin + ymax) / 2

    return latitude, longitude
