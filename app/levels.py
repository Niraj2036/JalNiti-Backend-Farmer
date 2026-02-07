import requests
import re
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
    session.get(init_url, headers=COMMON_HEADERS, timeout=30)

# -------------------------------------------------
# Safe JSON parser (handles expired session / HTML)
# -------------------------------------------------
def safe_json(res):
    try:
        return res.json()
    except Exception:
        init_bhunaksha_session()
        return None

# -------------------------------------------------
# Helper: fetch hierarchy levels safely
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

    try:
        res = session.post(url, data=data, headers=COMMON_HEADERS, timeout=30)
        if res.status_code != 200:
            return []

        json_data = safe_json(res)
        if (
            isinstance(json_data, list)
            and len(json_data) > 0
            and isinstance(json_data[0], list)
        ):
            return json_data[0]

        return []
    except Exception:
        return []

# -------------------------------------------------
# Survey sorting (handles 100/A, 100/1, etc.)
# -------------------------------------------------
def survey_sort_key(s):
    if not isinstance(s, str):
        return (0, "")

    parts = s.split("/", 1)
    try:
        main = int(parts[0])
    except ValueError:
        main = 0

    suffix = parts[1] if len(parts) > 1 else ""
    return (main, suffix)

# -------------------------------------------------
# 1️⃣ Districts
# -------------------------------------------------
@levels_bp.route("/districts", methods=["GET"])
def get_districts():
    area = request.args.get("area")
    if area not in ["R", "U"]:
        return jsonify({"error": "area must be R or U"}), 400

    districts = fetch_level(1, f"{area},")
    return jsonify([
        {"code": d["code"], "name": d["value"]}
        for d in districts
    ])

# -------------------------------------------------
# 2️⃣ Talukas
# -------------------------------------------------
@levels_bp.route("/talukas", methods=["GET"])
def get_talukas():
    area = request.args.get("area")
    district = request.args.get("districtCode")

    if not area or not district:
        return jsonify({"error": "area and districtCode required"}), 400

    talukas = fetch_level(2, f"{area},{district.zfill(2)},")
    return jsonify([
        {"code": t["code"], "name": t["value"]}
        for t in talukas
    ])

# -------------------------------------------------
# 3️⃣ Villages
# -------------------------------------------------
@levels_bp.route("/villages", methods=["GET"])
def get_villages():
    area = request.args.get("area")
    district = request.args.get("districtCode")
    taluka = request.args.get("talukaCode")

    if not all([area, district, taluka]):
        return jsonify({"error": "area, districtCode, talukaCode required"}), 400

    villages = fetch_level(
        3,
        f"{area},{district.zfill(2)},{taluka.zfill(2)},"
    )

    return jsonify([
        {"code": v["code"], "name": v["value"]}
        for v in villages
    ])

# -------------------------------------------------
# 4️⃣ Surveys
# -------------------------------------------------
@levels_bp.route("/surveys", methods=["GET"])
def get_surveys():
    area = request.args.get("area")
    district = request.args.get("districtCode")
    taluka = request.args.get("talukaCode")
    village = request.args.get("villageCode")

    if not all([area, district, taluka, village]):
        return jsonify({"error": "all codes required"}), 400

    if not session.cookies:
        init_bhunaksha_session()

    prefix = "RVM" if area == "R" else "UCM"
    loged_levels = f"{prefix}{district.zfill(2)}{taluka.zfill(2)}{village}"

    url = f"{BASE_URL}/kidelistFromGisCodeMH"
    data = {"state": STATE, "logedLevels": loged_levels}

    res = session.post(url, data=data, headers=COMMON_HEADERS, timeout=30)
    if res.status_code != 200:
        return jsonify([])

    raw = safe_json(res)
    if not isinstance(raw, list):
        return jsonify([])

    clean = [s for s in raw if isinstance(s, str) and s.strip()]
    clean.sort(key=survey_sort_key)

    return jsonify(clean)

# -------------------------------------------------
# 5️⃣ Plot Info
# -------------------------------------------------
@levels_bp.route("/plot-info", methods=["GET"])
def get_plot_info():
    area = request.args.get("area")
    district = request.args.get("districtCode")
    taluka = request.args.get("talukaCode")
    village = request.args.get("villageGisCode")
    plot_no = request.args.get("plotNo")

    if not all([area, district, taluka, village, plot_no]):
        return jsonify({"error": "missing parameters"}), 400

    prefix = "RVM" if area == "R" else "UCM"
    giscode = f"{prefix}{district.zfill(2)}{taluka.zfill(2)}{village}"

    plot_url = "https://mahabhunakasha.mahabhumi.gov.in/rest/MapInfo/getPlotInfo"
    payload = {
        "state": STATE,
        "giscode": giscode,
        "plotno": plot_no,
        "srs": "4326"
    }

    res = session.post(plot_url, data=payload, headers=COMMON_HEADERS, timeout=30)
    plot_json = safe_json(res)

    if not plot_json or "plotid" not in plot_json:
        return jsonify({"error": "Plot not found"}), 404

    plotid = plot_json["plotid"]
    lat, lng = fetch_lat_lng_from_extent(giscode, plotid)
    owners = extract_owners(plot_json)

    return jsonify({
        "latitudeApprox": lat,
        "longitudeApprox": lng,
        "owners": owners
    })

# -------------------------------------------------
# Helpers
# -------------------------------------------------
def extract_owners(plot_json):
    owners = []
    text = plot_json.get("info", "")
    blocks = text.split("---------------------------------")

    for block in blocks:
        o = re.search(r"Owner Name\s*:\s*(.+)", block)
        a = re.search(r"Total Area\s*:\s*([\d.]+)", block)
        if o and a:
            owners.append({
                "ownerName": o.group(1).strip(),
                "totalArea": a.group(1).strip()
            })
    return owners

def fetch_lat_lng_from_extent(giscode, plotid):
    url = "https://mahabhunakasha.mahabhumi.gov.in/rest/MapInfo/getExtentGeoref"
    data = {
        "state": STATE,
        "giscode": giscode,
        "plotid": plotid,
        "srs": "4326"
    }

    res = session.post(url, data=data, headers=COMMON_HEADERS, timeout=30)
    extent = safe_json(res)

    if not extent:
        return None, None

    try:
        lng = (extent["xmin"] + extent["xmax"]) / 2
        lat = (extent["ymin"] + extent["ymax"]) / 2
        return lat, lng
    except Exception:
        return None, None
