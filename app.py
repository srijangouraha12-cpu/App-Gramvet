import os, sys, json, sqlite3, hashlib, math, base64, uuid, re, gzip, time, threading, queue
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template, session, send_from_directory, Response, g as flask_g
from werkzeug.exceptions import HTTPException
import requests

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

try:
    from pymongo import MongoClient, UpdateOne
except ImportError:
    MongoClient = None
    UpdateOne = None

try:
    import certifi
except ImportError:
    certifi = None

load_dotenv()

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "gramvet.db")

MONGO_URI = (os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or "").strip() or None
MONGO_DB_NAME = (os.getenv("MONGO_DB_NAME") or "gramvet").strip()

mongo_client = None
mongo_db = None
_mongo_lock = threading.Lock()
_INITIALIZED_DB = None
_IN_INIT = False

print("MongoDB Atlas configured" if MONGO_URI else "MongoDB Atlas URI not configured", flush=True)


def _coerce_latlon(lat, lon):
    """Accept lat/lon only when both are real numbers inside valid ranges."""
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None, None
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if not (math.isfinite(la) and math.isfinite(lo)):
        return None, None
    if not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None, None
    # Treat (0.0, 0.0) as unset null-island coordinates
    if abs(la) < 1e-6 and abs(lo) < 1e-6:
        return None, None
    return la, lo


def validate_and_format_phone(phone_raw):
    p = str(phone_raw or "").strip().replace(" ", "").replace("-", "")
    if p.startswith("+91"):
        p = p[3:]
    elif p.startswith("91") and len(p) == 12:
        p = p[2:]
    elif p.startswith("0") and len(p) == 11:
        p = p[1:]
    if len(p) == 10 and p.isdigit() and p[0] in ("6", "7", "8", "9"):
        return "+91" + p
    return None


# Known Indian pincode & regional centroids used as instant fallback when
# external geocoders (Nominatim / India Post) rate-limit cloud IPs or lack
# rural village polygons.
_PINCODE_COORDS = {
    "506001": (18.0072, 79.5583),  # Kothapalli / Hanamkonda / Warangal
    "506004": (17.9823, 79.5344),  # Gandhi Nagar / NIT Warangal / Hanamkonda
    "757047": (22.0908, 86.1173),  # Dalki / Badampahar / Odisha
    "221712": (25.8875, 83.7944),  # Pakwain / Rasra / Ballia UP
    "422001": (19.9975, 73.7898),  # Nashik
    "422002": (19.9980, 73.7900),  # Nashik Road / Deolali
    "411001": (18.5204, 73.8567),  # Pune
    "400001": (18.9388, 72.8354),  # Mumbai
    "440001": (21.1458, 79.0882),  # Nagpur
    "431001": (19.8762, 75.3433),  # Chhatrapati Sambhajinagar (Aurangabad)
    "416001": (16.7050, 74.2433),  # Kolhapur
    "413001": (17.6599, 75.9064),  # Solapur
    "444601": (20.9320, 77.7523),  # Amravati
    "414001": (19.0948, 74.7480),  # Ahilyanagar (Ahmednagar)
    "415001": (17.6805, 74.0183),  # Satara
    "416416": (16.8524, 74.5815),  # Sangli
    "424001": (20.9042, 74.7749),  # Dhule
    "425001": (21.0077, 75.5626),  # Jalgaon
    "431601": (19.1383, 77.3210),  # Nanded
    "413512": (18.4088, 76.5604),  # Latur
    "431401": (19.2608, 76.7748),  # Parbhani
    "431203": (19.8347, 75.8816),  # Jalna
    "431122": (18.9891, 75.7601),  # Beed
    "413601": (18.1853, 76.0420),  # Dharashiv (Osmanabad)
    "444001": (20.7002, 77.0082),  # Akola
    "445001": (20.3888, 78.1204),  # Yavatmal
    "442001": (20.7453, 78.6022),  # Wardha
    "442401": (19.9615, 79.2961),  # Chandrapur
    "441601": (21.4624, 80.2210),  # Gondia
    "441904": (21.1702, 79.6570),  # Bhandara
    "442605": (20.1809, 80.0015),  # Gadchiroli
    "443001": (20.5292, 76.1842),  # Buldhana
    "444505": (20.1119, 77.1313),  # Washim
    "431513": (19.7174, 77.1494),  # Hingoli
    "416602": (16.3492, 73.5594),  # Sindhudurg
    "415612": (16.9902, 73.3120),  # Ratnagiri
    "402201": (18.6414, 72.8722),  # Raigad / Alibag
    "401404": (19.6967, 72.7699),  # Palghar
    "400601": (19.2183, 72.9781),  # Thane
}

_PIN_PREFIX3_COORDS = {
    "506": (18.0072, 79.5583),
    "500": (17.3850, 78.4867),
    "757": (22.0908, 86.1173),
    "221": (25.8875, 83.7944),
    "422": (19.9975, 73.7898),
    "411": (18.5204, 73.8567),
    "410": (18.7557, 73.4091),
    "412": (18.4529, 73.8906),
    "400": (19.0760, 72.8777),
    "401": (19.5000, 72.8500),
    "402": (18.6414, 73.0150),
    "413": (17.6599, 75.9064),
    "414": (19.0948, 74.7480),
    "415": (17.6805, 74.0183),
    "416": (16.7050, 74.2433),
    "421": (19.2403, 73.1305),
    "423": (20.5579, 74.5089),
    "424": (20.9042, 74.7749),
    "425": (21.0077, 75.5626),
    "431": (19.8762, 75.3433),
    "440": (21.1458, 79.0882),
    "441": (21.1702, 79.6570),
    "442": (20.3500, 79.0000),
    "443": (20.5292, 76.1842),
    "444": (20.8000, 77.3500),
    "445": (20.3888, 78.1204),
}

_VILLAGE_FALLBACK_COORDS = {
    "kothapalli": (18.0072, 79.5583),
    "rampur": (18.0215, 79.5410),
    "narsampet": (17.9281, 79.8945),
    "lakshmipur": (18.0340, 79.5720),
    "venkatapur": (18.0190, 79.6110),
    "mallapur": (18.0410, 79.5320),
    "gopalpur": (17.9890, 79.5760),
    "gandhi nagar": (17.9823, 79.5344),
    "dalki": (22.0908, 86.1173),
    "pakwain": (25.8875, 83.7944),
}

_GEOCODE_CACHE = {}
_WEATHER_CACHE = {}  # (round(lat,2), round(lon,2)) -> (timestamp, weather_dict)


def _coords_from_pincode_fallback(pincode, village_name=""):
    v_key = str(village_name or "").strip().lower()
    if v_key in _VILLAGE_FALLBACK_COORDS:
        return _VILLAGE_FALLBACK_COORDS[v_key]
    p_clean = str(pincode or "").strip()
    if p_clean in _PINCODE_COORDS:
        return _PINCODE_COORDS[p_clean]
    if len(p_clean) == 6 and p_clean.isdigit():
        if p_clean[:3] in _PIN_PREFIX3_COORDS:
            base_lat, base_lon = _PIN_PREFIX3_COORDS[p_clean[:3]]
            sub = int(p_clean[3:])
            dlat = ((sub % 31) - 15) * 0.008
            dlon = (((sub // 31) % 31) - 15) * 0.008
            return round(base_lat + dlat, 5), round(base_lon + dlon, 5)
    return None, None


def get_maharashtra_coords(village_name, pincode=""):
    # Returns (None, None) when neither village nor pincode can be resolved.
    v_clean = str(village_name or "").strip()
    p_clean = str(pincode or "").strip()
    if not v_clean and not p_clean:
        return None, None
    cache_key = (v_clean.lower(), p_clean)
    if cache_key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[cache_key]

    candidates = []
    if v_clean and p_clean:
        candidates.append(f"{v_clean}, {p_clean}, Maharashtra, India")
        candidates.append(f"{v_clean}, {p_clean}, India")
    if v_clean:
        candidates.append(f"{v_clean}, Maharashtra, India")
        candidates.append(f"{v_clean}, India")
    if p_clean:
        candidates.append(f"{p_clean}, India")

    # 1. Try Nominatim
    for q in candidates:
        try:
            url = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(q)}&format=json&limit=1"
            res = requests.get(url, headers={"User-Agent": "GramVetApp/2.0"}, timeout=3).json()
            if res and isinstance(res, list) and len(res) > 0 and "lat" in res[0] and "lon" in res[0]:
                la, lo = _coerce_latlon(res[0]["lat"], res[0]["lon"])
                if la is not None and lo is not None:
                    _GEOCODE_CACHE[cache_key] = (la, lo)
                    return la, lo
        except Exception:
            continue

    # 2. Try Open-Meteo Geocoding API (fast, no cloud IP rate-limiting)
    if v_clean:
        try:
            om_url = f"https://geocoding-api.open-meteo.com/v1/search?name={requests.utils.quote(v_clean)}&count=5&language=en&format=json"
            om_res = requests.get(om_url, headers={"User-Agent": "GramVetApp/2.0"}, timeout=3).json()
            for item in (om_res.get("results") or []):
                if item.get("country_code") == "IN" or not item.get("country_code"):
                    la, lo = _coerce_latlon(item.get("latitude"), item.get("longitude"))
                    if la is not None and lo is not None:
                        _GEOCODE_CACHE[cache_key] = (la, lo)
                        return la, lo
        except Exception:
            pass

    # 3. Fallback to known village/pincode table
    la, lo = _coords_from_pincode_fallback(p_clean, v_clean)
    if la is not None and lo is not None:
        _GEOCODE_CACHE[cache_key] = (la, lo)
        return la, lo

    return None, None


def is_within_5km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    distance = R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))
    return distance <= 5.0


def get_season_india(dt=None):
    # Indian Meteorological Department style seasons, used as the model's "season" input.
    dt = dt or datetime.now(timezone.utc)
    m = dt.month
    if m in (12, 1, 2): return "Winter"
    if m in (3, 4, 5): return "Summer"
    if m in (6, 7, 8, 9): return "Monsoon"
    return "Winter"


def now():
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat()


def _client_reported_at(raw, max_age_days=60):
    """Accept an ISO timestamp from a queued offline report if it is valid and plausible,
    so a report submitted hours later still carries the time the farmer observed the animal.
    Anything missing, malformed, in the future (>5 min clock skew) or older than max_age_days
    falls back to server now() in IST."""
    if not raw or not isinstance(raw, str):
        return now()
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return now()
        ist = timezone(timedelta(hours=5, minutes=30))
        dt_ist = dt.astimezone(ist)
        now_ist = datetime.now(ist)
        if dt_ist > now_ist + timedelta(minutes=5):
            return now()
        if dt_ist < now_ist - timedelta(days=max_age_days):
            return now()
        return dt_ist.isoformat()
    except Exception:
        return now()


def _latest_time(rows, col):
    """Latest moment among ISO timestamps that may be in different timezones (e.g. Z vs +05:30)."""
    best_dt, best_raw = None, None
    for r in rows:
        raw = r[col]
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if best_dt is None or dt > best_dt:
                best_dt, best_raw = dt, raw
        except Exception:
            if best_raw is None or str(raw) > str(best_raw):
                best_raw = raw
    return best_dt


def _regional_weather_estimate(lat, lon, season):
    """Deterministic IMD regional climatological fallback for (lat, lon, season)
    used only when all external HTTP weather providers are unreachable or rate-limited."""
    ist_now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    hour = ist_now.hour
    diurnal = math.sin(math.pi * (hour - 5) / 14.0) if 5 <= hour <= 19 else -0.4
    lat_factor = max(-4.0, min(4.0, (22.0 - float(lat)) * 0.35))
    if season == "Summer":
        base_temp, base_hum, base_rain = 34.5 + lat_factor, 42.0, 0.0
    elif season == "Monsoon":
        base_temp, base_hum, base_rain = 28.0 + lat_factor * 0.5, 81.0, 4.2
    else:  # Winter
        base_temp, base_hum, base_rain = 24.5 + lat_factor, 56.0, 0.0
    temp_c = round(base_temp + diurnal * 3.2, 1)
    hum_pct = int(round(max(20.0, min(98.0, base_hum - diurnal * 8.0))))
    return {
        "available": True,
        "temperature_c": temp_c,
        "humidity_pct": hum_pct,
        "rainfall_mm": base_rain,
        "season": season,
        "latitude": lat,
        "longitude": lon,
        "source": "imd-regional-estimate",
        "fetched_at": now(),
    }


def fetch_weather(lat, lon):
    # Live weather for the disease/outbreak model's 4 inputs: temperature, humidity, rainfall, season.
    lat, lon = _coerce_latlon(lat, lon)
    season = get_season_india()
    if lat is None or lon is None:
        return {"available": False, "error": "No coordinates available for this report.", "season": season}

    cache_key = (round(lat, 2), round(lon, 2))
    cached = _WEATHER_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < 900:
        wx_copy = dict(cached[1])
        wx_copy["latitude"] = lat
        wx_copy["longitude"] = lon
        return wx_copy

    headers = {"User-Agent": "GramVetApp/2.0 (LivestockHealthNetwork; +https://gramvet.onrender.com)"}

    # Provider 1: Open-Meteo (fast 2.5s timeout so PWA sw.js 6s timeout is never exceeded)
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,precipitation"
            "&daily=precipitation_sum&forecast_days=1&timezone=auto"
        )
        r = requests.get(url, headers=headers, timeout=2.5)
        if r.ok:
            res = r.json()
            if not res.get("error"):
                cur = res.get("current", {}) or {}
                daily = res.get("daily", {}) or {}
                temp = cur.get("temperature_2m")
                hum = cur.get("relative_humidity_2m")
                rainfall = None
                if daily.get("precipitation_sum"):
                    rainfall = daily["precipitation_sum"][0]
                if rainfall is None:
                    rainfall = cur.get("precipitation")
                if temp is not None and hum is not None:
                    wx = {
                        "available": True,
                        "temperature_c": round(float(temp), 1),
                        "humidity_pct": round(float(hum), 1),
                        "rainfall_mm": round(max(0.0, float(rainfall or 0.0)), 1),
                        "season": season,
                        "latitude": lat,
                        "longitude": lon,
                        "source": "open-meteo",
                        "fetched_at": now(),
                    }
                    _WEATHER_CACHE[cache_key] = (time.time(), wx)
                    return dict(wx)
    except Exception:
        pass

    # Provider 2: MET Norway Locationforecast 2.0 (free, no key, reliable on cloud IPs)
    try:
        met_url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat:.4f}&lon={lon:.4f}"
        r = requests.get(met_url, headers=headers, timeout=2.5)
        if r.ok:
            res = r.json()
            ts = ((res.get("properties") or {}).get("timeseries") or [])
            if ts:
                first_data = (ts[0].get("data") or {})
                instant = ((first_data.get("instant") or {}).get("details") or {})
                temp = instant.get("air_temperature")
                hum = instant.get("relative_humidity")
                rain = (
                    ((first_data.get("next_1_hours") or {}).get("details") or {}).get("precipitation_amount")
                    or ((first_data.get("next_6_hours") or {}).get("details") or {}).get("precipitation_amount")
                    or 0.0
                )
                if temp is not None and hum is not None:
                    wx = {
                        "available": True,
                        "temperature_c": round(float(temp), 1),
                        "humidity_pct": round(float(hum), 1),
                        "rainfall_mm": round(max(0.0, float(rain)), 1),
                        "season": season,
                        "latitude": lat,
                        "longitude": lon,
                        "source": "met-norway",
                        "fetched_at": now(),
                    }
                    _WEATHER_CACHE[cache_key] = (time.time(), wx)
                    return dict(wx)
    except Exception:
        pass

    # Provider 3: wttr.in JSON API
    try:
        wttr_url = f"https://wttr.in/{lat:.4f},{lon:.4f}?format=j1"
        r = requests.get(wttr_url, headers=headers, timeout=2.5)
        if r.ok:
            res = r.json()
            cur_list = res.get("current_condition") or []
            if cur_list:
                cur = cur_list[0]
                temp = cur.get("temp_C")
                hum = cur.get("humidity")
                rain = cur.get("precipMM") or 0.0
                if temp is not None and hum is not None:
                    wx = {
                        "available": True,
                        "temperature_c": round(float(temp), 1),
                        "humidity_pct": round(float(hum), 1),
                        "rainfall_mm": round(max(0.0, float(rain)), 1),
                        "season": season,
                        "latitude": lat,
                        "longitude": lon,
                        "source": "wttr",
                        "fetched_at": now(),
                    }
                    _WEATHER_CACHE[cache_key] = (time.time(), wx)
                    return dict(wx)
    except Exception:
        pass

    # Provider 4: IMD Regional Climatological Fallback (never fails when offline/rate-limited)
    return _regional_weather_estimate(lat, lon, season)


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "gramvet-local-dev-secret")
# Keep the login cookie across browser restarts so the app still knows who is signed in when opened offline.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
# 60s of voice at 64kbps is ~480 KB (~640 KB in base64). 16 MB leaves generous headroom while stopping runaway uploads.
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


@app.errorhandler(413)
def payload_too_large(_e):
    return jsonify(error="Request is too large (maximum 16 MB). Please record a shorter voice note."), 413


@app.route('/media/<path:filename>')
def serve_media(filename):
    media_dir = os.path.join(BASE, 'media')
    full_path = os.path.join(media_dir, filename)
    if not os.path.exists(full_path) and filename.startswith("audio/"):
        # Attempt on-demand restore from MongoDB if audio file was wiped on Render restart
        try:
            rel_url = f"/media/{filename}"
            mdb = _get_mongo_db()
            if mdb is not None:
                doc = mdb["health_reports"].find_one({"audio_url": rel_url})
                if doc and doc.get("audio_data"):
                    raw_b64 = doc["audio_data"]
                    audio_bytes = base64.b64decode(raw_b64.split(",")[1] if "," in raw_b64 else raw_b64)
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    with open(full_path, "wb") as af:
                        af.write(audio_bytes)
        except Exception:
            pass
    return send_from_directory(media_dir, filename)


# Service worker must be served from "/" so its scope covers the whole app (including /api/*).
@app.route('/sw.js')
def serve_sw():
    resp = send_from_directory(os.path.join(BASE, 'static', 'pwa'), 'sw.js',
                               mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


SYMPTOMS = [
    "Fever / High Body Temperature",
    "Cough",
    "Nasal Discharge",
    "Difficulty Breathing",
    "Reduced Appetite",
    "Weakness / Lethargy",
    "Diarrhea",
    "Dehydration",
    "Excessive Salivation",
    "Mouth Lesions / Sores",
    "Lameness / Difficulty Walking",
    "Swelling",
    "Skin Lesions / Rash",
    "Eye Discharge / Redness",
    "Abortion / Reproductive Problem",
    "Abnormal Milk Production",
    "Weight Loss",
    "Reduced Rumination",
    "Ticks / External Parasites"
]

ALLOWED_SPECIES = {"Cattle", "Buffalo"}
RADIUS_KM = 5.0
VACCINE_CODES = ("FMD", "HS", "LSD", "BQ")      # the 4 disease vaccines
VET_START_STOCK = 25                                # each vet starts with 25 of EACH vaccine
LOW_STOCK_THRESHOLD = 10                            # alert when stock is BELOW this (9 alerts, 10 does not)
GOVT_START_STOCK = int(os.environ.get("GOVT_VACCINE_START_STOCK", "500"))  # central stock per vaccine, seeded once
ML_DIR = os.path.join(BASE, "ml_package")
if ML_DIR not in sys.path:
    sys.path.insert(0, ML_DIR)
ML_IMPORT_ERROR = None
try:
    from predict_disease import predict_disease, FEATURES as MODEL_FEATURES, CLASSES as MODEL_CLASSES, _bundle as _load_model_bundle
    _load_model_bundle()
except Exception as e:
    predict_disease = None
    MODEL_FEATURES, MODEL_CLASSES = [], []
    try:
        import sklearn as _sk
        _sk_ver = _sk.__version__
    except Exception:
        _sk_ver = "missing"
    ML_IMPORT_ERROR = f"{e} (installed scikit-learn {_sk_ver}; the model requires scikit-learn==1.6.1 - run: pip install -r requirements.txt)"
    print(f"[GramVet] Disease model unavailable: {ML_IMPORT_ERROR}", flush=True)


# ---------------------------------------------------------------------------
# Fast SQLite + Persistent Background MongoDB Sync Engine
# ---------------------------------------------------------------------------
SYNC_TABLES = (
    "villages",
    "users",
    "vet_villages",
    "animals",
    "cases",
    "health_reports",
    "case_actions",
    "vaccinations",
    "inventory_transactions",
    "outbreak_assessments",
    "resource_inventory",
    "resource_requests",
    "notifications",
    "ivr_sessions",
    "vet_vaccine_stock",
    "vaccine_shipments",
    "vaccine_alerts",
    "vaccine_stock_transactions",
)

_MONGO_QUEUE = queue.Queue()
_MONGO_WAKE = threading.Event()
_LAST_SYNCED_HASH = {}  # (collection, str(_id)) -> json_str
_AUDIO_DATA_CACHE = {}  # report_id -> base64 audio string
_LAST_MONGO_PULL_TS = 0.0
_MONGO_WORKER_STARTED = False
_BG_SYNC_STATS = {"synced": 0, "pulled": 0, "failed": 0}


def _get_mongo_db():
    """Return connected MongoDB database object, or None if unavailable/disabled."""
    global mongo_client, mongo_db
    if not MONGO_URI or MongoClient is None:
        return None
    with _mongo_lock:
        if mongo_db is not None:
            return mongo_db
        try:
            client = MongoClient(
                MONGO_URI,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=10000,
                socketTimeoutMS=15000,
                retryWrites=True,
            )
            client.admin.command("ping")
            mongo_client = client
            mongo_db = client[MONGO_DB_NAME]
            return mongo_db
        except Exception as first_err:
            if certifi is not None:
                try:
                    client = MongoClient(
                        MONGO_URI,
                        tlsCAFile=certifi.where(),
                        serverSelectionTimeoutMS=10000,
                        connectTimeoutMS=10000,
                        socketTimeoutMS=15000,
                        retryWrites=True,
                    )
                    client.admin.command("ping")
                    mongo_client = client
                    mongo_db = client[MONGO_DB_NAME]
                    return mongo_db
                except Exception:
                    pass
            mongo_client = None
            mongo_db = None
            print(f"[GramVet] MongoDB connection deferred: {first_err}", flush=True)
            return None


def _doc_id_for_table(table, row_dict):
    if table == "vet_villages":
        return f"{row_dict.get('vet_id')}_{row_dict.get('village_id')}"
    if table == "vet_vaccine_stock":
        return f"{row_dict.get('vet_id')}_{row_dict.get('vaccine')}"
    if "id" in row_dict and row_dict["id"] is not None:
        return row_dict["id"]
    return row_dict.get("_id")


def _canonical_doc(table, row_dict):
    d = dict(row_dict)
    d["_id"] = _doc_id_for_table(table, d)
    if table == "health_reports":
        rid = d.get("id")
        if rid in _AUDIO_DATA_CACHE and not d.get("audio_data"):
            d["audio_data"] = _AUDIO_DATA_CACHE[rid]
    return d


def mongo_upsert(collection, document):
    """Non-blocking, deadlock-free MongoDB upsert.
    Never opens a conflicting SQLite transaction while caller holds a write lock."""
    if not MONGO_URI:
        return
    doc = dict(document)
    if "_id" not in doc and "id" in doc:
        doc["_id"] = doc["id"]
    if collection == "health_reports" and doc.get("audio_data") and doc.get("id"):
        _AUDIO_DATA_CACHE[doc["id"]] = doc["audio_data"]
    _MONGO_QUEUE.put(("doc", collection, doc))
    _MONGO_WAKE.set()
    _ensure_mongo_worker()


def trigger_mongo_sync(tables=None, wait_timeout=0.0):
    """Schedule background sync of specified SQLite tables (or all tables) to MongoDB."""
    if not MONGO_URI:
        return
    target_tables = tuple(tables) if tables else SYNC_TABLES
    done_evt = threading.Event() if wait_timeout > 0 else None
    _MONGO_QUEUE.put(("tables", target_tables, done_evt))
    _MONGO_WAKE.set()
    _ensure_mongo_worker()
    if done_evt is not None:
        done_evt.wait(timeout=wait_timeout)


def _flush_sqlite_tables_to_mongo(tables=None):
    """Read committed rows from SQLite and push changed documents to MongoDB."""
    if not MONGO_URI:
        return 0
    mdb = _get_mongo_db()
    if mdb is None:
        return 0
    target_tables = tables or SYNC_TABLES
    pushed = 0
    c = None
    try:
        c = conn()
        for table in target_tables:
            try:
                rows = c.execute(f"SELECT * FROM {table}").fetchall()
            except Exception:
                continue
            ops = []
            pending_hashes = []
            current_ids = set()
            for r in rows:
                doc = _canonical_doc(table, dict(r))
                doc_id = doc["_id"]
                current_ids.add(doc_id)
                serialized = json.dumps(doc, sort_keys=True, default=str)
                hkey = (table, str(doc_id))
                if _LAST_SYNCED_HASH.get(hkey) == serialized:
                    continue
                if table == "users" and doc.get("phone"):
                    try:
                        # Remove any duplicate user doc with the same phone under a different _id
                        # and remap any references in MongoDB collections to doc_id
                        for dup in mdb["users"].find({"phone": doc["phone"]}):
                            old_dup_id = dup.get("_id")
                            if old_dup_id is not None and old_dup_id != doc_id:
                                mdb["animals"].update_many({"farmer_id": old_dup_id}, {"$set": {"farmer_id": doc_id}})
                                mdb["cases"].update_many({"farmer_id": old_dup_id}, {"$set": {"farmer_id": doc_id}})
                                mdb["cases"].update_many({"vet_id": old_dup_id}, {"$set": {"vet_id": doc_id}})
                                mdb["vaccinations"].update_many({"farmer_id": old_dup_id}, {"$set": {"farmer_id": doc_id}})
                                mdb["vaccinations"].update_many({"vet_id": old_dup_id}, {"$set": {"vet_id": doc_id}})
                                mdb["notifications"].update_many({"user_id": old_dup_id}, {"$set": {"user_id": doc_id}})
                                mdb["users"].delete_one({"_id": old_dup_id})
                        # If another user with a different phone is sitting at doc_id, relocate it first
                        clash = mdb["users"].find_one({"_id": doc_id})
                        if clash and clash.get("phone") and clash.get("phone") != doc["phone"]:
                            new_clash_id = f"u_{clash['phone']}"
                            clash_copy = dict(clash)
                            clash_copy["_id"] = new_clash_id
                            mdb["users"].update_one({"_id": new_clash_id}, {"$set": clash_copy}, upsert=True)
                    except Exception:
                        pass
                if UpdateOne is not None:
                    ops.append(UpdateOne({"_id": doc_id}, {"$set": doc}, upsert=True))
                else:
                    mdb[table].update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
                    pushed += 1
                pending_hashes.append((hkey, serialized))

            if ops:
                mdb[table].bulk_write(ops, ordered=False)
                pushed += len(ops)
            for hkey, serialized in pending_hashes:
                _LAST_SYNCED_HASH[hkey] = serialized

            # Handle deleted vet_villages links
            if table == "vet_villages":
                try:
                    existing_mongo_ids = [d["_id"] for d in mdb["vet_villages"].find({}, {"_id": 1})]
                    for mid in existing_mongo_ids:
                        if mid not in current_ids:
                            mdb["vet_villages"].delete_one({"_id": mid})
                            _LAST_SYNCED_HASH.pop(("vet_villages", str(mid)), None)
                except Exception:
                    pass
    except Exception as e:
        global mongo_client, mongo_db
        with _mongo_lock:
            mongo_client = None
            mongo_db = None
        print(f"[GramVet] Background MongoDB sync warning: {e}", flush=True)
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
    return pushed


def _mongo_worker_loop():
    global _LAST_MONGO_PULL_TS
    while True:
        _MONGO_WAKE.wait(timeout=15.0)
        _MONGO_WAKE.clear()
        if not MONGO_URI:
            while not _MONGO_QUEUE.empty():
                try:
                    item = _MONGO_QUEUE.get_nowait()
                    if len(item) == 3 and isinstance(item[2], threading.Event):
                        item[2].set()
                except Exception:
                    break
            continue

        tables_to_sync = set()
        direct_docs = []
        events_to_set = []
        while not _MONGO_QUEUE.empty():
            try:
                item = _MONGO_QUEUE.get_nowait()
            except Exception:
                break
            kind = item[0]
            if kind == "doc":
                direct_docs.append((item[1], item[2]))
                tables_to_sync.add(item[1])
            elif kind == "tables":
                for t in item[1]:
                    tables_to_sync.add(t)
                if item[2] is not None:
                    events_to_set.append(item[2])

        mdb = _get_mongo_db()
        if mdb is not None and direct_docs:
            for col, doc in direct_docs:
                try:
                    doc_id = doc.get("_id", doc.get("id"))
                    doc["_id"] = doc_id
                    mdb[col].update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
                    _BG_SYNC_STATS["synced"] += 1
                except Exception as e:
                    _BG_SYNC_STATS["failed"] += 1
                    try:
                        sc = conn()
                        sc.execute("""
                          INSERT INTO sync_queue(collection, document_id, document_json, created_at)
                          VALUES(?, ?, ?, ?)
                          ON CONFLICT(collection, document_id) DO UPDATE SET
                            document_json=excluded.document_json, retry_count=retry_count+1, last_error=?
                        """, (col, str(doc.get("_id")), json.dumps(doc, default=str), now(), str(e)))
                        sc.commit()
                        sc.close()
                    except Exception:
                        pass

        if tables_to_sync:
            pushed = _flush_sqlite_tables_to_mongo(list(tables_to_sync))
            _BG_SYNC_STATS["synced"] += pushed

        for evt in events_to_set:
            evt.set()

        # Periodically pull any external changes from MongoDB (shared web/mobile backend)
        if MONGO_URI and (time.time() - _LAST_MONGO_PULL_TS) >= 15.0 and not _IN_INIT:
            _LAST_MONGO_PULL_TS = time.time()
            try:
                _flush_sqlite_tables_to_mongo(SYNC_TABLES)
                pc = conn()
                pulled = hydrate_sqlite_from_mongo(pc, incremental=True)
                if pulled > 0:
                    merge_duplicate_open_cases(pc)
                    backfill_farmer_vet_assignment(pc)
                    _BG_SYNC_STATS["pulled"] += pulled
                pc.commit()
                pc.close()
            except Exception:
                pass


def _ensure_mongo_worker():
    global _MONGO_WORKER_STARTED
    if _MONGO_WORKER_STARTED or not MONGO_URI:
        return
    with _mongo_lock:
        if not _MONGO_WORKER_STARTED:
            t = threading.Thread(target=_mongo_worker_loop, name="gramvet-mongo-sync", daemon=True)
            t.start()
            _MONGO_WORKER_STARTED = True


def _as_int_or_none(val):
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def hydrate_sqlite_from_mongo(c, incremental=False):
    """Restore/merge all collections from MongoDB into local SQLite (`gramvet.db`).
    Ensures data survives Render standby/restarts and syncs across shared web/phone apps."""
    if not MONGO_URI:
        return 0
    mdb = _get_mongo_db()
    if mdb is None:
        return 0

    restored = 0
    village_id_map = {}
    user_id_map = {}
    animal_id_map = {}
    case_id_map = {}

    try:
        c.execute("PRAGMA foreign_keys=OFF")

        # 1. Villages
        for doc in mdb["villages"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("villages", str(doc_id))
            vid = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                if vid is not None:
                    ex_v = c.execute("SELECT id FROM villages WHERE name=? AND pincode=?", (str(doc.get("name") or "").strip(), str(doc.get("pincode") or "").strip())).fetchone()
                    if ex_v:
                        village_id_map[vid] = ex_v["id"]
                continue
            name = str(doc.get("name") or "").strip()
            pincode = str(doc.get("pincode") or "").strip()
            if not name:
                continue
            lat, lon = _coerce_latlon(doc.get("latitude"), doc.get("longitude"))
            created_at = str(doc.get("created_at") or now())

            existing = c.execute("SELECT id, latitude, longitude FROM villages WHERE name=? AND pincode=?", (name, pincode)).fetchone()
            if existing:
                if vid is not None:
                    village_id_map[vid] = existing["id"]
                if lat is not None and lon is not None and (existing["latitude"] is None or existing["longitude"] is None or not incremental):
                    c.execute("UPDATE villages SET latitude=?, longitude=? WHERE id=?", (lat, lon, existing["id"]))
            else:
                if vid is not None and not c.execute("SELECT 1 FROM villages WHERE id=?", (vid,)).fetchone():
                    c.execute(
                        "INSERT INTO villages(id,name,pincode,latitude,longitude,created_at) VALUES(?,?,?,?,?,?)",
                        (vid, name, pincode, lat, lon, created_at),
                    )
                    village_id_map[vid] = vid
                else:
                    cur = c.execute(
                        "INSERT INTO villages(name,pincode,latitude,longitude,created_at) VALUES(?,?,?,?,?)",
                        (name, pincode, lat, lon, created_at),
                    )
                    if vid is not None:
                        village_id_map[vid] = cur.lastrowid
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 2. Users
        demo_Fixed = {
            "+919000000001": ("Government Official", "government", hp("12345")),
            "+919000000002": ("Dr. Veterinarian", "vet", hp("12345")),
            "+919000000003": ("Demo Farmer", "farmer", hp("12345")),
        }
        for doc in mdb["users"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("users", str(doc_id))
            uid = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            phone = validate_and_format_phone(doc.get("phone")) or str(doc.get("phone") or "").strip()
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                if uid is not None and phone:
                    ex_u = c.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
                    if ex_u:
                        user_id_map[uid] = ex_u["id"]
                continue
            if not phone:
                continue
            name = str(doc.get("name") or "User").strip()
            role = doc.get("role") if doc.get("role") in ("farmer", "vet", "government") else "farmer"
            pw_hash = str(doc.get("password_hash") or "")
            pw_mig = int(doc.get("pw_migrated") or 0)
            if phone in demo_Fixed:
                name = demo_Fixed[phone][0]
                role = demo_Fixed[phone][1]
                pw_hash = demo_Fixed[phone][2]
                pw_mig = 1
            lat, lon = _coerce_latlon(doc.get("latitude"), doc.get("longitude"))
            if phone in ("+919000000001", "+919000000002", "+919000000003") and doc.get("latitude") is None:
                lat, lon = None, None
            created_at = str(doc.get("created_at") or now())
            assigned_vet = _as_int_or_none(doc.get("assigned_vet_id"))

            existing_by_phone = c.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
            if existing_by_phone:
                target_uid = existing_by_phone["id"]
                if uid is not None:
                    user_id_map[uid] = target_uid
                # Never overwrite an already-migrated local password_hash with an unmigrated MongoDB hash
                local_mig = existing_by_phone["pw_migrated"] if "pw_migrated" in existing_by_phone.keys() else 0
                use_pw = pw_hash if (pw_hash and (pw_mig >= (local_mig or 0) or not existing_by_phone["password_hash"])) else existing_by_phone["password_hash"]
                use_mig = max(pw_mig, int(local_mig or 0))
                c.execute("""
                    UPDATE users SET
                      name=COALESCE(?, name),
                      password_hash=?,
                      pw_migrated=?,
                      role=COALESCE(?, role),
                      pincode=COALESCE(?, pincode),
                      village=COALESCE(?, village),
                      ward=COALESCE(?, ward),
                      address=COALESCE(?, address),
                      vet_auth_id=COALESCE(?, vet_auth_id),
                      govt_auth_id=COALESCE(?, govt_auth_id),
                      latitude=COALESCE(?, latitude),
                      longitude=COALESCE(?, longitude),
                      district=COALESCE(?, district),
                      state=COALESCE(?, state),
                      assigned_vet_id=COALESCE(?, assigned_vet_id)
                    WHERE id=?
                """, (
                    name, use_pw, use_mig, role,
                    doc.get("pincode"), doc.get("village"), doc.get("ward"),
                    doc.get("address"), doc.get("vet_auth_id"), doc.get("govt_auth_id"),
                    lat, lon, doc.get("district"), doc.get("state"), assigned_vet, target_uid
                ))
            else:
                id_taken = uid is not None and c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
                if uid is not None and not id_taken:
                    c.execute("""
                        INSERT INTO users(id,name,phone,password_hash,pw_migrated,role,pincode,village,ward,address,
                                          vet_auth_id,govt_auth_id,created_at,latitude,longitude,district,state,assigned_vet_id)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        uid, name, phone, pw_hash, pw_mig, role,
                        doc.get("pincode"), doc.get("village"), doc.get("ward"), doc.get("address"),
                        doc.get("vet_auth_id"), doc.get("govt_auth_id"), created_at,
                        lat, lon, doc.get("district"), doc.get("state"), assigned_vet
                    ))
                    user_id_map[uid] = uid
                else:
                    cur = c.execute("""
                        INSERT INTO users(name,phone,password_hash,pw_migrated,role,pincode,village,ward,address,
                                          vet_auth_id,govt_auth_id,created_at,latitude,longitude,district,state,assigned_vet_id)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        name, phone, pw_hash, pw_mig, role,
                        doc.get("pincode"), doc.get("village"), doc.get("ward"), doc.get("address"),
                        doc.get("vet_auth_id"), doc.get("govt_auth_id"), created_at,
                        lat, lon, doc.get("district"), doc.get("state"), assigned_vet
                    ))
                    if uid is not None:
                        user_id_map[uid] = cur.lastrowid
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # Remap assigned_vet_id if any user IDs shifted
        if user_id_map:
            for old_vid, new_vid in user_id_map.items():
                if old_vid != new_vid:
                    c.execute("UPDATE users SET assigned_vet_id=? WHERE assigned_vet_id=?", (new_vid, old_vid))

        # 3. vet_villages
        for doc in mdb["vet_villages"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("vet_villages", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            vet_id = _as_int_or_none(doc.get("vet_id"))
            vil_id = _as_int_or_none(doc.get("village_id"))
            if vet_id is not None and vil_id is not None:
                vet_id = user_id_map.get(vet_id, vet_id)
                vil_id = village_id_map.get(vil_id, vil_id)
                c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)", (vet_id, vil_id))
                _LAST_SYNCED_HASH[hkey] = serialized
                restored += 1

        # 4. animals
        for doc in mdb["animals"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("animals", str(doc_id))
            aid = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            tag = str(doc.get("tag") or f"GV-{aid or 1}").strip()
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                if aid is not None and tag:
                    ex_a = c.execute("SELECT id FROM animals WHERE tag=?", (tag,)).fetchone()
                    if ex_a:
                        animal_id_map[aid] = ex_a["id"]
                continue
            name = str(doc.get("name") or "Livestock").strip()
            species = doc.get("species") if doc.get("species") in ALLOWED_SPECIES else "Cattle"
            sex = doc.get("sex") if doc.get("sex") in ("Male", "Female") else "Female"
            ward = str(doc.get("ward") or "Ward 1").strip()
            farmer_id = _as_int_or_none(doc.get("farmer_id"))
            village_id = _as_int_or_none(doc.get("village_id"))
            if farmer_id is None or village_id is None:
                continue
            farmer_id = user_id_map.get(farmer_id, farmer_id)
            village_id = village_id_map.get(village_id, village_id)
            lat, lon = _coerce_latlon(doc.get("latitude"), doc.get("longitude"))
            created_at = str(doc.get("created_at") or now())

            existing_tag = c.execute("SELECT id FROM animals WHERE tag=?", (tag,)).fetchone()
            if existing_tag:
                target_aid = existing_tag["id"]
                if aid is not None:
                    animal_id_map[aid] = target_aid
                c.execute("""
                    UPDATE animals SET name=?,species=?,breed=?,age=?,sex=?,farmer_id=?,village_id=?,
                                       ward=?,latitude=COALESCE(?,latitude),longitude=COALESCE(?,longitude),
                                       last_vaccinated=COALESCE(?,last_vaccinated)
                    WHERE id=?
                """, (name, species, doc.get("breed"), doc.get("age"), sex, farmer_id, village_id,
                      ward, lat, lon, doc.get("last_vaccinated"), target_aid))
            else:
                id_taken = aid is not None and c.execute("SELECT 1 FROM animals WHERE id=?", (aid,)).fetchone()
                if aid is not None and not id_taken:
                    c.execute("""
                        INSERT INTO animals(id,tag,name,species,breed,age,sex,farmer_id,village_id,ward,
                                            latitude,longitude,last_vaccinated,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (aid, tag, name, species, doc.get("breed"), doc.get("age"), sex,
                          farmer_id, village_id, ward, lat, lon, doc.get("last_vaccinated"), created_at))
                    animal_id_map[aid] = aid
                else:
                    cur = c.execute("""
                        INSERT INTO animals(tag,name,species,breed,age,sex,farmer_id,village_id,ward,
                                            latitude,longitude,last_vaccinated,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (tag, name, species, doc.get("breed"), doc.get("age"), sex,
                          farmer_id, village_id, ward, lat, lon, doc.get("last_vaccinated"), created_at))
                    if aid is not None:
                        animal_id_map[aid] = cur.lastrowid
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 5. cases
        for doc in mdb["cases"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("cases", str(doc_id))
            cid = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            animal_id = _as_int_or_none(doc.get("animal_id"))
            farmer_id = _as_int_or_none(doc.get("farmer_id"))
            vet_id = _as_int_or_none(doc.get("vet_id"))
            if cid is None or animal_id is None:
                continue
            animal_id = animal_id_map.get(animal_id, animal_id)
            if farmer_id is not None:
                farmer_id = user_id_map.get(farmer_id, farmer_id)
            if vet_id is not None:
                vet_id = user_id_map.get(vet_id, vet_id)
            # Authoritative owner & vet from the animal record in SQLite
            a_owner_row = c.execute("SELECT farmer_id FROM animals WHERE id=?", (animal_id,)).fetchone()
            if a_owner_row and a_owner_row["farmer_id"] is not None:
                farmer_id = a_owner_row["farmer_id"]
            if farmer_id is None:
                continue
            f_vet_row = c.execute("SELECT assigned_vet_id FROM users WHERE id=?", (farmer_id,)).fetchone()
            if f_vet_row and f_vet_row["assigned_vet_id"] is not None:
                vet_id = f_vet_row["assigned_vet_id"]
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                case_id_map[cid] = cid
                continue
            status = doc.get("status") if doc.get("status") in ("OPEN", "IN_PROGRESS", "CLOSED") else "OPEN"
            opened_at = str(doc.get("opened_at") or doc.get("synced_at") or doc.get("created_at") or now())
            closed_at = doc.get("closed_at")

            try:
                existing_by_id = c.execute("SELECT id, animal_id FROM cases WHERE id=?", (cid,)).fetchone()
                if existing_by_id and existing_by_id["animal_id"] != animal_id:
                    match_row = c.execute("SELECT id FROM cases WHERE animal_id=? AND opened_at=?", (animal_id, opened_at)).fetchone()
                    if not match_row and status != "CLOSED":
                        match_row = c.execute("SELECT id FROM cases WHERE animal_id=? AND status!='CLOSED'", (animal_id,)).fetchone()
                    if match_row:
                        case_id_map[cid] = match_row["id"]
                        c.execute("""
                            UPDATE cases SET
                              farmer_id=?,
                              vet_id=COALESCE(?, cases.vet_id),
                              status=?,
                              closed_at=COALESCE(?, cases.closed_at)
                            WHERE id=?
                        """, (farmer_id, vet_id, status, closed_at, match_row["id"]))
                    else:
                        cur = c.execute("""
                            INSERT INTO cases(animal_id,farmer_id,vet_id,status,opened_at,closed_at)
                            VALUES(?,?,?,?,?,?)
                        """, (animal_id, farmer_id, vet_id, status, opened_at, closed_at))
                        case_id_map[cid] = cur.lastrowid
                else:
                    c.execute("""
                        INSERT INTO cases(id,animal_id,farmer_id,vet_id,status,opened_at,closed_at)
                        VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET
                          farmer_id=excluded.farmer_id,
                          vet_id=COALESCE(excluded.vet_id, cases.vet_id),
                          status=excluded.status,
                          closed_at=COALESCE(excluded.closed_at, cases.closed_at)
                    """, (cid, animal_id, farmer_id, vet_id, status, opened_at, closed_at))
                    case_id_map[cid] = cid
                _LAST_SYNCED_HASH[hkey] = serialized
                restored += 1
            except sqlite3.IntegrityError:
                pass

        # 6. health_reports
        for doc in mdb["health_reports"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("health_reports", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            rid = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            case_id = _as_int_or_none(doc.get("case_id"))
            animal_id = _as_int_or_none(doc.get("animal_id"))
            if rid is None or animal_id is None:
                continue
            animal_id = animal_id_map.get(animal_id, animal_id)
            if case_id is not None:
                case_id = case_id_map.get(case_id, case_id)
            symptoms_json = doc.get("symptoms_json") or json.dumps(doc.get("symptoms") or [])
            ai_pred_json = doc.get("ai_prediction_json") or (json.dumps(doc.get("prediction")) if doc.get("prediction") is not None else None)
            weather_json = doc.get("weather_json") or (json.dumps(doc.get("weather")) if doc.get("weather") is not None else None)
            inputs_json = doc.get("report_inputs_json") or (json.dumps(doc.get("report_inputs")) if doc.get("report_inputs") is not None else None)
            reported_at = str(doc.get("reported_at") or doc.get("synced_at") or now())
            audio_url = doc.get("audio_url")
            audio_b64 = doc.get("audio_data")
            if audio_b64:
                _AUDIO_DATA_CACHE[rid] = audio_b64
                if audio_url:
                    try:
                        rel = audio_url.lstrip("/")
                        full_audio_path = os.path.join(BASE, rel)
                        if not os.path.exists(full_audio_path):
                            os.makedirs(os.path.dirname(full_audio_path), exist_ok=True)
                            raw_bytes = base64.b64decode(audio_b64.split(",")[1] if "," in audio_b64 else audio_b64)
                            with open(full_audio_path, "wb") as af:
                                af.write(raw_bytes)
                    except Exception:
                        pass

            existing_rep = c.execute("SELECT id, animal_id, reported_at FROM health_reports WHERE id=?", (rid,)).fetchone()
            if existing_rep and (existing_rep["animal_id"] != animal_id or existing_rep["reported_at"] != reported_at):
                match_rep = c.execute("SELECT id FROM health_reports WHERE animal_id=? AND reported_at=?", (animal_id, reported_at)).fetchone()
                if match_rep:
                    c.execute("""
                        UPDATE health_reports SET
                          case_id=COALESCE(?, health_reports.case_id),
                          ai_prediction_json=COALESCE(?, health_reports.ai_prediction_json),
                          weather_json=COALESCE(?, health_reports.weather_json),
                          audio_url=COALESCE(?, health_reports.audio_url)
                        WHERE id=?
                    """, (case_id, ai_pred_json, weather_json, audio_url, match_rep["id"]))
                else:
                    c.execute("""
                        INSERT INTO health_reports(case_id,animal_id,symptoms_json,notes,reported_at,
                                                   ai_prediction_json,ai_model_version,weather_json,report_inputs_json,audio_url)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                    """, (case_id, animal_id, symptoms_json, doc.get("notes"), reported_at,
                          ai_pred_json, doc.get("ai_model_version"), weather_json, inputs_json, audio_url))
            else:
                c.execute("""
                    INSERT INTO health_reports(id,case_id,animal_id,symptoms_json,notes,reported_at,
                                               ai_prediction_json,ai_model_version,weather_json,report_inputs_json,audio_url)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                      case_id=COALESCE(excluded.case_id, health_reports.case_id),
                      ai_prediction_json=COALESCE(excluded.ai_prediction_json, health_reports.ai_prediction_json),
                      weather_json=COALESCE(excluded.weather_json, health_reports.weather_json),
                      audio_url=COALESCE(excluded.audio_url, health_reports.audio_url)
                """, (rid, case_id, animal_id, symptoms_json, doc.get("notes"), reported_at,
                      ai_pred_json, doc.get("ai_model_version"), weather_json, inputs_json, audio_url))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 7. case_actions
        for doc in mdb["case_actions"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("case_actions", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            act_id = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            case_id = _as_int_or_none(doc.get("case_id"))
            vet_id = _as_int_or_none(doc.get("vet_id"))
            if act_id is None or case_id is None or vet_id is None:
                continue
            case_id = case_id_map.get(case_id, case_id)
            vet_id = user_id_map.get(vet_id, vet_id)
            c.execute("""
                INSERT OR REPLACE INTO case_actions(id,case_id,vet_id,action_type,details,action_at)
                VALUES(?,?,?,?,?,?)
            """, (act_id, case_id, vet_id, str(doc.get("action_type") or "NOTE"),
                  str(doc.get("details") or ""), str(doc.get("action_at") or now())))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 8. vaccinations
        for doc in mdb["vaccinations"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("vaccinations", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            vac_id = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            animal_id = _as_int_or_none(doc.get("animal_id"))
            farmer_id = _as_int_or_none(doc.get("farmer_id"))
            vet_id = _as_int_or_none(doc.get("vet_id"))
            if vac_id is None or animal_id is None or farmer_id is None or vet_id is None:
                continue
            animal_id = animal_id_map.get(animal_id, animal_id)
            farmer_id = user_id_map.get(farmer_id, farmer_id)
            vet_id = user_id_map.get(vet_id, vet_id)
            raw_cid = _as_int_or_none(doc.get("case_id"))
            cid_mapped = case_id_map.get(raw_cid, raw_cid) if raw_cid is not None else None
            c.execute("""
                INSERT OR REPLACE INTO vaccinations(id,animal_id,case_id,farmer_id,vet_id,vaccine_name,
                                                    dose,unit,administered_at,batch_no,notes,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (vac_id, animal_id, cid_mapped, farmer_id, vet_id,
                  str(doc.get("vaccine_name") or "FMD"), float(doc.get("dose") or 1.0),
                  str(doc.get("unit") or "dose"), str(doc.get("administered_at") or now()),
                  doc.get("batch_no"), doc.get("notes"), str(doc.get("created_at") or now())))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 9. inventory_transactions
        for doc in mdb["inventory_transactions"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("inventory_transactions", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            tid = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            if tid is None:
                continue
            vet_id = _as_int_or_none(doc.get("vet_id"))
            if vet_id is not None:
                vet_id = user_id_map.get(vet_id, vet_id)
            c.execute("""
                INSERT OR REPLACE INTO inventory_transactions(id,resource_name,quantity_change,unit,reason,vet_id,animal_id,case_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (tid, str(doc.get("resource_name") or "Vaccine"), float(doc.get("quantity_change") or 0),
                  str(doc.get("unit") or "units"), str(doc.get("reason") or ""),
                  vet_id, _as_int_or_none(doc.get("animal_id")), _as_int_or_none(doc.get("case_id")),
                  str(doc.get("created_at") or now())))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 10. outbreak_assessments
        for doc in mdb["outbreak_assessments"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("outbreak_assessments", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            oid = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            vet_id = _as_int_or_none(doc.get("vet_id"))
            village_id = _as_int_or_none(doc.get("village_id"))
            if oid is None or vet_id is None or village_id is None:
                continue
            vet_id = user_id_map.get(vet_id, vet_id)
            village_id = village_id_map.get(village_id, village_id)
            raw_cid = _as_int_or_none(doc.get("case_id"))
            cid_mapped = case_id_map.get(raw_cid, raw_cid) if raw_cid is not None else None
            c.execute("""
                INSERT OR REPLACE INTO outbreak_assessments(id,case_id,vet_id,village_id,predicted,confirmed,model_correct,notes,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)
            """, (oid, cid_mapped, vet_id, village_id, doc.get("predicted"),
                  int(doc.get("confirmed") or 0), _as_int_or_none(doc.get("model_correct")),
                  doc.get("notes"), str(doc.get("created_at") or now())))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 11. resource_inventory
        for doc in mdb["resource_inventory"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("resource_inventory", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            rname = str(doc.get("resource_name") or "").strip()
            if not rname:
                continue
            c.execute("""
                INSERT INTO resource_inventory(resource_name,available_qty,unit,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(resource_name) DO UPDATE SET
                  available_qty=excluded.available_qty,
                  unit=excluded.unit,
                  updated_at=excluded.updated_at
            """, (rname, float(doc.get("available_qty") or 0), str(doc.get("unit") or "units"),
                  str(doc.get("updated_at") or now())))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 12. resource_requests
        for doc in mdb["resource_requests"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("resource_requests", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            rr_id = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            vet_id = _as_int_or_none(doc.get("vet_id"))
            village_id = _as_int_or_none(doc.get("village_id"))
            if rr_id is None or vet_id is None or village_id is None:
                continue
            vet_id = user_id_map.get(vet_id, vet_id)
            village_id = village_id_map.get(village_id, village_id)
            c.execute("""
                INSERT OR REPLACE INTO resource_requests(id,vet_id,village_id,case_count,resource_json,reason,status,created_at,reviewed_at,reviewed_by)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (rr_id, vet_id, village_id, int(doc.get("case_count") or 0),
                  doc.get("resource_json") or "[]", doc.get("reason"),
                  str(doc.get("status") or "PENDING"), str(doc.get("created_at") or now()),
                  doc.get("reviewed_at"), _as_int_or_none(doc.get("reviewed_by"))))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 13. notifications
        for doc in mdb["notifications"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("notifications", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            nid = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            user_id = _as_int_or_none(doc.get("user_id"))
            if nid is None or user_id is None:
                continue
            user_id = user_id_map.get(user_id, user_id)
            raw_cid = _as_int_or_none(doc.get("case_id"))
            cid_mapped = case_id_map.get(raw_cid, raw_cid) if raw_cid is not None else None
            raw_vid = _as_int_or_none(doc.get("village_id"))
            vid_mapped = village_id_map.get(raw_vid, raw_vid) if raw_vid is not None else None
            n_title = str(doc.get("title") or "")
            n_msg = str(doc.get("message") or "")
            n_sev = str(doc.get("severity") or "INFO")
            n_kind = str(doc.get("kind") or "GENERAL")
            n_created = str(doc.get("created_at") or now())
            n_read = doc.get("read_at")
            n_data = doc.get("data_json")
            n_dedup = doc.get("dedup_key")

            ex_n = c.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone()
            if ex_n:
                same_logical = (
                    ex_n["user_id"] == user_id and (
                        (n_dedup and ex_n["dedup_key"] == n_dedup)
                        or ex_n["created_at"] == n_created
                        or (ex_n["kind"] == n_kind and ex_n["case_id"] == cid_mapped and ex_n["title"] == n_title)
                    )
                )
                if same_logical:
                    if str(ex_n["created_at"] or "") <= n_created:
                        merged_read = ex_n["read_at"] if (ex_n["created_at"] == n_created and ex_n["read_at"]) else n_read
                        merged_data = n_data if n_data is not None else ex_n["data_json"]
                        c.execute("""
                            UPDATE notifications
                            SET title=?, message=?, severity=?, kind=?, case_id=COALESCE(?, case_id),
                                village_id=COALESCE(?, village_id), created_at=?, read_at=?,
                                data_json=?, dedup_key=COALESCE(?, dedup_key)
                            WHERE id=?
                        """, (n_title, n_msg, n_sev, n_kind, cid_mapped, vid_mapped,
                              n_created, merged_read, merged_data, n_dedup, nid))
                else:
                    dup_check = c.execute(
                        "SELECT id FROM notifications WHERE user_id=? AND title=? AND created_at=? LIMIT 1",
                        (user_id, n_title, n_created)
                    ).fetchone()
                    if not dup_check:
                        c.execute("""
                            INSERT INTO notifications(user_id,title,message,severity,kind,case_id,village_id,created_at,read_at,data_json,dedup_key)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """, (user_id, n_title, n_msg, n_sev, n_kind, cid_mapped, vid_mapped,
                              n_created, n_read, n_data, n_dedup))
            else:
                c.execute("""
                    INSERT INTO notifications(id,user_id,title,message,severity,kind,case_id,village_id,created_at,read_at,data_json,dedup_key)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """, (nid, user_id, n_title, n_msg, n_sev, n_kind, cid_mapped, vid_mapped,
                      n_created, n_read, n_data, n_dedup))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 14. ivr_sessions
        for doc in mdb["ivr_sessions"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("ivr_sessions", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            call_sid = str(doc.get("call_sid") or "").strip()
            if not call_sid:
                continue
            c.execute("""
                INSERT INTO ivr_sessions(call_sid,phone,farmer_id,animal_id,pincode,species,gender,vaccinated,vaccine_code,symptoms_json,state,last_error,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(call_sid) DO UPDATE SET
                  farmer_id=COALESCE(excluded.farmer_id, ivr_sessions.farmer_id),
                  animal_id=COALESCE(excluded.animal_id, ivr_sessions.animal_id),
                  pincode=COALESCE(excluded.pincode, ivr_sessions.pincode),
                  symptoms_json=excluded.symptoms_json,
                  state=excluded.state,
                  last_error=excluded.last_error,
                  updated_at=excluded.updated_at
            """, (call_sid, doc.get("phone"), _as_int_or_none(doc.get("farmer_id")),
                  _as_int_or_none(doc.get("animal_id")), doc.get("pincode"), doc.get("species"),
                  doc.get("gender"), _as_int_or_none(doc.get("vaccinated")), doc.get("vaccine_code"),
                  doc.get("symptoms_json") or "[]", str(doc.get("state") or "START"),
                  doc.get("last_error"), str(doc.get("created_at") or now()), str(doc.get("updated_at") or now())))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 15. vet_vaccine_stock
        for doc in mdb["vet_vaccine_stock"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("vet_vaccine_stock", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            vet_id = _as_int_or_none(doc.get("vet_id"))
            vaccine = str(doc.get("vaccine") or "").strip().upper()
            qty = _as_int_or_none(doc.get("quantity"))
            if vet_id is None or vaccine not in VACCINE_CODES or qty is None or qty < 0:
                continue
            vet_id = user_id_map.get(vet_id, vet_id)
            c.execute("""
                INSERT INTO vet_vaccine_stock(vet_id,vaccine,quantity,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(vet_id,vaccine) DO UPDATE SET
                  quantity=excluded.quantity,
                  updated_at=excluded.updated_at
            """, (vet_id, vaccine, qty, str(doc.get("updated_at") or now())))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 16. vaccine_shipments
        for doc in mdb["vaccine_shipments"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("vaccine_shipments", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            sid = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            vet_id = _as_int_or_none(doc.get("vet_id"))
            vaccine = str(doc.get("vaccine") or "").strip().upper()
            qty = _as_int_or_none(doc.get("quantity"))
            if sid is None or vet_id is None or vaccine not in VACCINE_CODES or not qty or qty <= 0:
                continue
            vet_id = user_id_map.get(vet_id, vet_id)
            status = doc.get("status") if doc.get("status") in ("IN_TRANSIT", "RECEIVED") else "IN_TRANSIT"
            c.execute("""
                INSERT OR REPLACE INTO vaccine_shipments(id,vet_id,vaccine,quantity,status,released_by,released_at,received_at,received_by,idempotency_key)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (sid, vet_id, vaccine, qty, status, _as_int_or_none(doc.get("released_by")),
                  str(doc.get("released_at") or now()), doc.get("received_at"),
                  _as_int_or_none(doc.get("received_by")), doc.get("idempotency_key")))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        # 17. vaccine_alerts
        for doc in mdb["vaccine_alerts"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("vaccine_alerts", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            al_id = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            vet_id = _as_int_or_none(doc.get("vet_id"))
            vaccine = str(doc.get("vaccine") or "").strip().upper()
            if al_id is None or vet_id is None or vaccine not in VACCINE_CODES:
                continue
            vet_id = user_id_map.get(vet_id, vet_id)
            status = doc.get("status") if doc.get("status") in ("OPEN", "RESOLVED") else "OPEN"
            try:
                c.execute("""
                    INSERT OR REPLACE INTO vaccine_alerts(id,vet_id,vaccine,stock_at_alert,status,created_at,resolved_at,resolved_by_shipment_id)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (al_id, vet_id, vaccine, int(doc.get("stock_at_alert") or 0), status,
                      str(doc.get("created_at") or now()), doc.get("resolved_at"),
                      _as_int_or_none(doc.get("resolved_by_shipment_id"))))
                _LAST_SYNCED_HASH[hkey] = serialized
                restored += 1
            except sqlite3.IntegrityError:
                pass

        # 18. vaccine_stock_transactions
        for doc in mdb["vaccine_stock_transactions"].find({}):
            doc_id = doc.get("_id")
            serialized = json.dumps({k: v for k, v in doc.items()}, sort_keys=True, default=str)
            hkey = ("vaccine_stock_transactions", str(doc_id))
            if incremental and _LAST_SYNCED_HASH.get(hkey) == serialized:
                continue
            tx_id = _as_int_or_none(doc.get("id") if doc.get("id") is not None else doc_id)
            party = doc.get("party") if doc.get("party") in ("VET", "GOVT") else "VET"
            vaccine = str(doc.get("vaccine") or "").strip().upper()
            if tx_id is None or not vaccine:
                continue
            vet_id = _as_int_or_none(doc.get("vet_id"))
            if vet_id is not None:
                vet_id = user_id_map.get(vet_id, vet_id)
            c.execute("""
                INSERT OR REPLACE INTO vaccine_stock_transactions(id,party,vet_id,vaccine,quantity_change,balance_after,txn_type,shipment_id,vaccination_id,case_id,animal_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """, (tx_id, party, vet_id, vaccine, int(doc.get("quantity_change") or 0),
                  int(doc.get("balance_after") or 0), str(doc.get("txn_type") or "INITIAL"),
                  _as_int_or_none(doc.get("shipment_id")), _as_int_or_none(doc.get("vaccination_id")),
                  _as_int_or_none(doc.get("case_id")), _as_int_or_none(doc.get("animal_id")),
                  str(doc.get("created_at") or now())))
            _LAST_SYNCED_HASH[hkey] = serialized
            restored += 1

        for seq_tbl in ("users", "villages", "animals", "cases", "health_reports", "case_actions",
                        "vaccinations", "inventory_transactions", "outbreak_assessments",
                        "resource_inventory", "resource_requests", "notifications", "ivr_sessions",
                        "vaccine_shipments", "vaccine_alerts", "vaccine_stock_transactions"):
            try:
                max_id = c.execute(f"SELECT MAX(id) FROM {seq_tbl}").fetchone()[0]
                if max_id is not None:
                    c.execute("UPDATE sqlite_sequence SET seq=? WHERE name=? AND seq < ?", (max_id, seq_tbl, max_id))
            except Exception:
                pass

    except Exception as e:
        print(f"[GramVet] MongoDB hydration warning: {e}", flush=True)
    finally:
        try:
            c.execute("PRAGMA foreign_keys=ON")
            reconcile_farmer_vet_assignments(c, notify=False)
        except Exception:
            pass
    return restored


def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def json_body():
    """The request's JSON as a dict. No body, broken JSON, a list or a number all count as empty, so the route's own
    validation answers 400 instead of the request crashing with a 500."""
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


PASSWORD_SALT = "gramvet-local-dev-secret"


def hp(password):
    return hashlib.sha256((str(password or "") + PASSWORD_SALT).encode()).hexdigest()


def ensure_column(c, table, column, definition):
    cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init():
    c = conn()
    # Reset existing data if reset flag/file is detected or on clean start
    reset_marker = os.path.join(BASE, ".db_reset_done")
    if os.environ.get("GRAMVET_FORCE_DB_RESET") == "1" and not os.path.exists(reset_marker):
        c.executescript("""
            PRAGMA writable_schema = 1;
            DELETE FROM sqlite_master WHERE type IN ('table', 'index', 'trigger');
            PRAGMA writable_schema = 0;
            VACUUM;
            PRAGMA integrity_check;
        """)
        c.commit()
        try:
            with open(reset_marker, "w") as f:
                f.write("done")
        except Exception:
            pass

    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, phone TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('farmer','vet','government')),
      pincode TEXT, village TEXT, ward TEXT, address TEXT,
      vet_auth_id TEXT, govt_auth_id TEXT, created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS villages(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pincode TEXT NOT NULL,
      latitude REAL, longitude REAL, created_at TEXT NOT NULL DEFAULT ''
    );



    CREATE TABLE IF NOT EXISTS vet_villages(
      vet_id INTEGER NOT NULL, village_id INTEGER NOT NULL,
      PRIMARY KEY(vet_id,village_id),
      FOREIGN KEY(vet_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY(village_id) REFERENCES villages(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS animals(
      id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
      species TEXT NOT NULL CHECK(species IN ('Cattle','Buffalo')),
      breed TEXT, age TEXT, farmer_id INTEGER NOT NULL, village_id INTEGER NOT NULL,
      ward TEXT NOT NULL, latitude REAL, longitude REAL, last_vaccinated TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY(farmer_id) REFERENCES users(id),
      FOREIGN KEY(village_id) REFERENCES villages(id)
    );

    CREATE TABLE IF NOT EXISTS cases(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      animal_id INTEGER NOT NULL, farmer_id INTEGER NOT NULL, vet_id INTEGER,
      status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','IN_PROGRESS','CLOSED')),
      opened_at TEXT NOT NULL, closed_at TEXT,
      FOREIGN KEY(animal_id) REFERENCES animals(id),
      FOREIGN KEY(farmer_id) REFERENCES users(id),
      FOREIGN KEY(vet_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS health_reports(
      id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER,
      animal_id INTEGER NOT NULL, symptoms_json TEXT NOT NULL, notes TEXT,
      reported_at TEXT NOT NULL, ai_prediction_json TEXT, ai_model_version TEXT,
      FOREIGN KEY(case_id) REFERENCES cases(id),
      FOREIGN KEY(animal_id) REFERENCES animals(id)
    );

    CREATE TABLE IF NOT EXISTS case_actions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, vet_id INTEGER NOT NULL,
      action_type TEXT NOT NULL, details TEXT NOT NULL, action_at TEXT NOT NULL,
      FOREIGN KEY(case_id) REFERENCES cases(id),
      FOREIGN KEY(vet_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS vaccinations(
      id INTEGER PRIMARY KEY AUTOINCREMENT, animal_id INTEGER NOT NULL, case_id INTEGER,
      farmer_id INTEGER NOT NULL, vet_id INTEGER NOT NULL, vaccine_name TEXT NOT NULL,
      dose REAL NOT NULL, unit TEXT NOT NULL DEFAULT 'dose', administered_at TEXT NOT NULL,
      batch_no TEXT, notes TEXT, created_at TEXT NOT NULL,
      FOREIGN KEY(animal_id) REFERENCES animals(id),
      FOREIGN KEY(case_id) REFERENCES cases(id),
      FOREIGN KEY(farmer_id) REFERENCES users(id),
      FOREIGN KEY(vet_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS inventory_transactions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, resource_name TEXT NOT NULL, quantity_change REAL NOT NULL,
      unit TEXT NOT NULL DEFAULT 'units', reason TEXT NOT NULL, vet_id INTEGER, animal_id INTEGER,
      case_id INTEGER, created_at TEXT NOT NULL,
      FOREIGN KEY(vet_id) REFERENCES users(id), FOREIGN KEY(animal_id) REFERENCES animals(id),
      FOREIGN KEY(case_id) REFERENCES cases(id)
    );

    CREATE TABLE IF NOT EXISTS outbreak_assessments(
      id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, vet_id INTEGER NOT NULL,
      village_id INTEGER NOT NULL, predicted TEXT, confirmed INTEGER NOT NULL DEFAULT 0,
      notes TEXT, created_at TEXT NOT NULL,
      FOREIGN KEY(case_id) REFERENCES cases(id),
      FOREIGN KEY(vet_id) REFERENCES users(id),
      FOREIGN KEY(village_id) REFERENCES villages(id)
    );

    CREATE TABLE IF NOT EXISTS resource_inventory(
      id INTEGER PRIMARY KEY AUTOINCREMENT, resource_name TEXT UNIQUE NOT NULL,
      available_qty REAL NOT NULL DEFAULT 0, unit TEXT NOT NULL DEFAULT 'units',
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS resource_requests(
      id INTEGER PRIMARY KEY AUTOINCREMENT, vet_id INTEGER NOT NULL,
      village_id INTEGER NOT NULL, case_count INTEGER NOT NULL DEFAULT 0,
      resource_json TEXT NOT NULL, reason TEXT, status TEXT NOT NULL DEFAULT 'PENDING',
      created_at TEXT NOT NULL, reviewed_at TEXT, reviewed_by INTEGER,
      FOREIGN KEY(vet_id) REFERENCES users(id),
      FOREIGN KEY(village_id) REFERENCES villages(id),
      FOREIGN KEY(reviewed_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
      title TEXT NOT NULL, message TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'INFO',
      kind TEXT NOT NULL, case_id INTEGER, village_id INTEGER, created_at TEXT NOT NULL,
      read_at TEXT,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY(case_id) REFERENCES cases(id),
      FOREIGN KEY(village_id) REFERENCES villages(id)
    );
    """)
    # Migration from the earlier prototype
    ensure_column(c, "users", "address", "TEXT")
    ensure_column(c, "users", "govt_auth_id", "TEXT")
    ensure_column(c, "users", "latitude", "REAL")
    ensure_column(c, "users", "longitude", "REAL")
    ensure_column(c, "users", "district", "TEXT")
    ensure_column(c, "users", "state", "TEXT")
    # Per-farmer vet ownership. vet_villages alone cannot express "farmer A moved to
    # vet 2 while farmer B in the same village stays with vet 1", so each farmer
    # carries the id of the vet who owns their whole record (animals, open and
    # closed cases, past reports, map presence).
    ensure_column(c, "users", "assigned_vet_id", "INTEGER")
    ensure_column(c, "users", "pw_migrated", "INTEGER DEFAULT 0")
    # Advisory notifications store WHICH advisory applies (disease, level, village);
    # the text is looked up in static/advisories.js in the reader's language.
    ensure_column(c, "notifications", "data_json", "TEXT")
    ensure_column(c, "notifications", "dedup_key", "TEXT")
    ensure_column(c, "villages", "created_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(c, "animals", "created_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(c, 'animals', 'sex', "TEXT NOT NULL DEFAULT 'Female'")
    ensure_column(c, "health_reports", "case_id", "INTEGER")
    ensure_column(c, "health_reports", "weather_json", "TEXT")
    ensure_column(c, "health_reports", "report_inputs_json", "TEXT")
    # report_case() writes audio_url, but the column was never created, so on a
    # fresh database every symptom report failed with an OperationalError.
    ensure_column(c, "health_reports", "audio_url", "TEXT")
    ensure_column(c, "outbreak_assessments", "model_correct", "INTEGER")
    c.execute("CREATE INDEX IF NOT EXISTS idx_animals_farmer ON animals(farmer_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_animals_village ON animals(village_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cases_animal ON cases(animal_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cases_vet ON cases(vet_id,status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id,read_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vaccinations_animal ON vaccinations(animal_id,administered_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_vaccinations_vet ON vaccinations(vet_id,administered_at)")
    # Sync queue for offline-first MongoDB replication
    c.execute("""
        CREATE TABLE IF NOT EXISTS sync_queue(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            document_id TEXT NOT NULL,
            document_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'SYNCED', 'FAILED')),
            created_at TEXT NOT NULL,
            synced_at TEXT,
            retry_count INTEGER DEFAULT 0,
            last_error TEXT,
            UNIQUE(collection, document_id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_inventory_tx_resource ON inventory_transactions(resource_name,created_at)")
    # Deduplicate villages — keep the row with the highest id per (name, pincode) group.
    # Remap all foreign keys (animals, vet_villages, cases, etc.) to the surviving row first.
    dup_groups = c.execute("""
        SELECT name, pincode, COUNT(*) cnt FROM villages GROUP BY name, pincode HAVING cnt > 1
    """).fetchall()
    for g in dup_groups:
        rows = c.execute(
            "SELECT id FROM villages WHERE name=? AND pincode=? ORDER BY (latitude IS NOT NULL) DESC, id DESC",
            (g["name"], g["pincode"])
        ).fetchall()
        keep_id = rows[0]["id"]
        drop_ids = [r["id"] for r in rows[1:]]
        for drop_id in drop_ids:
            # Remap references
            c.execute("UPDATE animals SET village_id=? WHERE village_id=?", (keep_id, drop_id))
            c.execute("UPDATE vet_villages SET village_id=? WHERE village_id=? AND NOT EXISTS (SELECT 1 FROM vet_villages WHERE vet_id=vet_villages.vet_id AND village_id=?)", (keep_id, drop_id, keep_id))
            c.execute("DELETE FROM vet_villages WHERE village_id=?", (drop_id,))
            c.execute("UPDATE resource_requests SET village_id=? WHERE village_id=?", (keep_id, drop_id))
            c.execute("UPDATE notifications SET village_id=? WHERE village_id=?", (keep_id, drop_id))
            c.execute("UPDATE outbreak_assessments SET village_id=? WHERE village_id=?", (keep_id, drop_id))
            c.execute("DELETE FROM villages WHERE id=?", (drop_id,))
    # Try to add unique index to prevent future duplicates
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_villages_name_pincode ON villages(name, pincode)")
    except Exception as e:
        print(f"[GramVet] Could not create villages unique index (likely duplicates remain): {e}", flush=True)

    # Migrate reports created by the earlier prototype into permanent cases.
    orphan_reports = c.execute("""
      SELECT hr.id,hr.animal_id,a.farmer_id
      FROM health_reports hr JOIN animals a ON a.id=hr.animal_id
      WHERE hr.case_id IS NULL ORDER BY hr.reported_at,hr.id
    """).fetchall()
    for hr in orphan_reports:
        old_case = c.execute("SELECT id FROM cases WHERE animal_id=? AND status='OPEN' ORDER BY opened_at LIMIT 1",(hr["animal_id"],)).fetchone()
        if old_case:
            cid=old_case["id"]
        else:
            a0=c.execute("SELECT village_id FROM animals WHERE id=?",(hr["animal_id"],)).fetchone()
            vv=assigned_vet(c,a0["village_id"]) if a0 else None
            c.execute("INSERT INTO cases(animal_id,farmer_id,vet_id,status,opened_at) VALUES(?,?,?,?,?)",
                      (hr["animal_id"],hr["farmer_id"],vv["id"] if vv else None,"OPEN",hr["reported_at"]))
            cid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("UPDATE health_reports SET case_id=? WHERE id=?",(cid,hr["id"]))
    merge_duplicate_open_cases(c)
    backfill_farmer_vet_assignment(c)
    try:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_one_open_per_animal "
            "ON cases(animal_id) WHERE status != 'CLOSED'"
        )
    except sqlite3.OperationalError as e:
        print(f"[GramVet] open-case unique index skipped: {e}", flush=True)
    # Seed demo villages only; their coordinates intentionally remain unset.
    for v in ["Kothapalli","Rampur","Narsampet","Lakshmipur","Venkatapur","Mallapur","Gopalpur"]:
        c.execute("INSERT OR IGNORE INTO villages(name,pincode,created_at) VALUES(?,?,?)",(v,"506001",now()))

    # Seeding requested accounts only
    demo_users = [
      ("Government Official", "+919000000001", "12345", "government", "506001", None, None, "District Livestock Health Office", None, "GOV-26128"),
      ("Dr. Veterinarian", "+919000000002", "12345", "vet", "506001", "Kothapalli", None, "Government Veterinary Centre", "VET-001", None),
      ("Demo Farmer", "+919000000003", "12345", "farmer", "506001", "Kothapalli", "Ward 1", "Farmer House", None, None),
    ]

    for name,phone,password,role,pincode,village,ward,address,vet_auth,govt_auth in demo_users:
        ex_demo = c.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
        if ex_demo:
            c.execute("""
              UPDATE users SET password_hash=?, pw_migrated=1, role=?,
                vet_auth_id=COALESCE(vet_auth_id, ?), govt_auth_id=COALESCE(govt_auth_id, ?)
              WHERE id=?
            """, (hp(password), role, vet_auth, govt_auth, ex_demo["id"]))
        else:
            c.execute("""
              INSERT INTO users(name,phone,password_hash,pw_migrated,role,pincode,village,ward,address,vet_auth_id,govt_auth_id,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,(name,phone,hp(password),1,role,pincode,village,ward,address,vet_auth,govt_auth,now()))

    # Link vet to village if applicable
    vet_row = c.execute("SELECT id FROM users WHERE phone='+919000000002'").fetchone()
    vil_row = c.execute("SELECT id FROM villages WHERE name='Kothapalli'").fetchone()
    if vet_row and vil_row:
        c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)", (vet_row["id"], vil_row["id"]))

    # Vaccine inventory. CREATE IF NOT EXISTS + INSERT OR IGNORE only: restarting never resets stock.
    c.executescript("""
    CREATE TABLE IF NOT EXISTS vet_vaccine_stock(
      vet_id INTEGER NOT NULL, vaccine TEXT NOT NULL CHECK(vaccine IN ('FMD','HS','LSD','BQ')),
      quantity INTEGER NOT NULL CHECK(quantity>=0), updated_at TEXT NOT NULL,
      PRIMARY KEY(vet_id,vaccine),
      FOREIGN KEY(vet_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS vaccine_shipments(
      id INTEGER PRIMARY KEY AUTOINCREMENT, vet_id INTEGER NOT NULL,
      vaccine TEXT NOT NULL CHECK(vaccine IN ('FMD','HS','LSD','BQ')),
      quantity INTEGER NOT NULL CHECK(quantity>0),
      status TEXT NOT NULL DEFAULT 'IN_TRANSIT' CHECK(status IN ('IN_TRANSIT','RECEIVED')),
      released_by INTEGER, released_at TEXT NOT NULL, received_at TEXT, received_by INTEGER,
      idempotency_key TEXT,
      FOREIGN KEY(vet_id) REFERENCES users(id),
      FOREIGN KEY(released_by) REFERENCES users(id),
      FOREIGN KEY(received_by) REFERENCES users(id)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_vaccine_shipments_idem
      ON vaccine_shipments(released_by,idempotency_key) WHERE idempotency_key IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_vaccine_shipments_vet ON vaccine_shipments(vet_id,status);
    CREATE TABLE IF NOT EXISTS vaccine_alerts(
      id INTEGER PRIMARY KEY AUTOINCREMENT, vet_id INTEGER NOT NULL,
      vaccine TEXT NOT NULL CHECK(vaccine IN ('FMD','HS','LSD','BQ')),
      stock_at_alert INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','RESOLVED')),
      created_at TEXT NOT NULL, resolved_at TEXT, resolved_by_shipment_id INTEGER,
      FOREIGN KEY(vet_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_vaccine_alert_one_open
      ON vaccine_alerts(vet_id,vaccine) WHERE status='OPEN';
    CREATE TABLE IF NOT EXISTS vaccine_stock_transactions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, party TEXT NOT NULL CHECK(party IN ('VET','GOVT')),
      vet_id INTEGER, vaccine TEXT NOT NULL, quantity_change INTEGER NOT NULL, balance_after INTEGER NOT NULL,
      txn_type TEXT NOT NULL, shipment_id INTEGER, vaccination_id INTEGER, case_id INTEGER, animal_id INTEGER,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_vaccine_txn_vet ON vaccine_stock_transactions(vet_id,vaccine,id);
    CREATE TABLE IF NOT EXISTS idempotency_keys(
      key TEXT NOT NULL, user_id INTEGER NOT NULL, status INTEGER NOT NULL DEFAULT 0,
      response TEXT, created_at TEXT NOT NULL, PRIMARY KEY(key,user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_idempotency_created ON idempotency_keys(created_at);
    """)
    # Hydrate all collections from MongoDB BEFORE seeding missing stock rows
    # so existing users, animals, cases, reports, and stock survive Render restarts.
    if MONGO_URI:
        hydrate_sqlite_from_mongo(c, incremental=False)
        merge_duplicate_open_cases(c)
        backfill_farmer_vet_assignment(c)

    ensure_govt_stock(c)
    for _v in c.execute("SELECT id FROM users WHERE role='vet'").fetchall():
        ensure_vet_stock(c, _v["id"])

    c.commit()
    c.close()
    global _INITIALIZED_DB
    _INITIALIZED_DB = DB
    if MONGO_URI:
        trigger_mongo_sync(wait_timeout=0.0)


@app.before_request
def _ensure_db_initialized():
    global _IN_INIT
    if _INITIALIZED_DB != DB and not _IN_INIT:
        _IN_INIT = True
        try:
            init()
        finally:
            _IN_INIT = False


@app.after_request
def _sync_after_mutation(resp):
    if MONGO_URI and resp.status_code < 400:
        if request.method in ("POST", "PUT", "DELETE"):
            trigger_mongo_sync(wait_timeout=0.0)
    return resp


# ---- Smaller replies for slow phones: gzip text, JSON and JavaScript when the browser accepts it ----
# Registered BEFORE the idempotency hooks on purpose: Flask runs after_request functions in reverse order, so the
# idempotency hook still sees (and stores) the plain reply and this one compresses last.
GZIP_MIN_BYTES = 1024
GZIP_MAX_BYTES = 2 * 1024 * 1024

@app.after_request
def gzip_response(resp):
    if (resp.status_code != 200 or resp.headers.get("Content-Encoding")
            or "gzip" not in request.headers.get("Accept-Encoding", "").lower()
            or not resp.mimetype.startswith(("text/", "application/json", "application/javascript"))):
        return resp
    if resp.direct_passthrough:                          # static files: small enough to read into memory
        if (resp.content_length or 0) > GZIP_MAX_BYTES:
            return resp
        resp.direct_passthrough = False
    data = resp.get_data()
    if len(data) < GZIP_MIN_BYTES:
        return resp
    resp.set_data(gzip.compress(data, 6))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers.add("Vary", "Accept-Encoding")
    return resp

# ---- Idempotency: a POST sent again with the same Idempotency-Key is answered from the first result ----
# The phone makes one key per action and keeps it while the action waits in the outbox. So a retry after a lost
# reply, or a copy that arrives while the first is still running, is never applied twice.
# (Separate from vaccine_shipments.idempotency_key, which the government release form sends in its body.)
IDEM_KEEP_DAYS = 30          # how long a finished result is remembered
IDEM_STALE_SECONDS = 120     # an unfinished claim this old is treated as a crashed request

def _idem_key():
    k = request.headers.get("Idempotency-Key", "")
    return k if request.method == "POST" and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", k) else None

@app.before_request
def idem_begin():
    flask_g.idem = None
    key, uid = _idem_key(), session.get("uid")
    if not key or not uid:
        return None
    c = conn()
    try:
        t = datetime.now(timezone.utc)
        c.execute("DELETE FROM idempotency_keys WHERE created_at < ?", ((t - timedelta(days=IDEM_KEEP_DAYS)).isoformat(),))
        if c.execute("INSERT OR IGNORE INTO idempotency_keys(key,user_id,status,created_at) VALUES(?,?,0,?)",
                     (key, uid, t.isoformat())).rowcount:
            c.commit(); flask_g.idem = (key, uid); return None        # first time: this request does the work
        row = c.execute("SELECT status,response,created_at FROM idempotency_keys WHERE key=? AND user_id=?", (key, uid)).fetchone()
        if row and row["status"]:                                       # already done: repeat the first answer
            c.commit()
            return Response(row["response"], status=row["status"], mimetype="application/json", headers={"Idempotent-Replay": "true"})
        if row and (t - datetime.fromisoformat(row["created_at"])).total_seconds() > IDEM_STALE_SECONDS:
            if c.execute("UPDATE idempotency_keys SET created_at=? WHERE key=? AND user_id=? AND status=0 AND created_at=?",
                         (t.isoformat(), key, uid, row["created_at"])).rowcount:
                c.commit(); flask_g.idem = (key, uid); return None      # the first copy crashed: take over
        c.commit()
        return Response(json.dumps({"error": "This request is already being processed. It will be retried."}),
                        status=503, mimetype="application/json", headers={"Retry-After": "2"})
    finally:
        c.close()

@app.after_request
def idem_finish(resp):
    idem = getattr(flask_g, "idem", None)
    if idem:
        c = conn()
        try:
            if 200 <= resp.status_code < 300:
                c.execute("UPDATE idempotency_keys SET status=?, response=? WHERE key=? AND user_id=?",
                          (resp.status_code, resp.get_data(as_text=True), idem[0], idem[1]))
            else:                                                       # a failed try is forgotten so it can be repeated
                c.execute("DELETE FROM idempotency_keys WHERE key=? AND user_id=? AND status=0", idem)
            c.commit()
        finally:
            c.close()
    return resp

def current_user(c=None):
    own = c is None
    c = c or conn()
    uid = session.get("uid")
    sphone = session.get("phone")
    u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None
    if u and sphone and u["phone"] != sphone:
        u = c.execute("SELECT * FROM users WHERE phone=?", (sphone,)).fetchone() or u
    if not u and sphone:
        u = c.execute("SELECT * FROM users WHERE phone=?", (sphone,)).fetchone()
        if u:
            session["uid"] = u["id"]
    if own: c.close()
    return u

def require_role(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            c = conn()
            u = current_user(c)
            if not u:
                c.close()
                return jsonify(error="Login required"), 401
            if u["role"] not in roles:
                c.close()
                return jsonify(error="Access denied for this role."), 403
            try:
                return fn(u, c, *args, **kwargs)
            finally:
                # Endpoint may have closed only in exceptional paths; keep simple and robust.
                try: c.close()
                except: pass
        return wrapper
    return deco

def create_notification(c, user_id, title, message, severity="INFO", kind="GENERAL", case_id=None, village_id=None, data=None, dedup_key=None):
    c.execute("""
      INSERT INTO notifications(user_id,title,message,severity,kind,case_id,village_id,created_at,data_json,dedup_key)
      VALUES(?,?,?,?,?,?,?,?,?,?)
    """,(user_id,title,message,severity,kind,case_id,village_id,now(),json.dumps(data) if data else None,dedup_key))

def upsert_notification(c, user_id, dedup_key, title, message, severity, kind, case_id, village_id, data, resurface=False):
    """One live notification per user + dedup_key over 7 days. If its level/confirmation
    changed or resurface=True, it is updated and marked unread so it pops up immediately."""
    cutoff = (datetime.fromisoformat(now()) - timedelta(days=7)).isoformat()
    ex = c.execute("""SELECT id,data_json FROM notifications WHERE user_id=? AND dedup_key=? AND created_at>=?
                      ORDER BY id DESC LIMIT 1""", (user_id, dedup_key, cutoff)).fetchone()
    if not ex:
        create_notification(c, user_id, title, message, severity, kind, case_id, village_id, data, dedup_key)
        return
    old = json.loads(ex["data_json"] or "{}")
    if resurface or (old.get("level"), old.get("triage_level"), old.get("confirmed")) != (data.get("level"), data.get("triage_level"), data.get("confirmed")):
        c.execute("""UPDATE notifications SET title=?,message=?,severity=?,data_json=?,case_id=COALESCE(?,case_id),
                     created_at=?,read_at=NULL WHERE id=?""",
                  (title, message, severity, json.dumps(data), case_id, now(), ex["id"]))
    else:
        c.execute("UPDATE notifications SET message=?,data_json=?,case_id=COALESCE(?,case_id) WHERE id=?",
                  (message, json.dumps(data), case_id, ex["id"]))

def case_disease(c, case_id):
    """Top model prediction on the case's latest report (canonical name) or None."""
    row = c.execute("""SELECT ai_prediction_json FROM health_reports WHERE case_id=?
                       ORDER BY reported_at DESC, id DESC LIMIT 1""", (case_id,)).fetchone()
    try:
        p = json.loads((row["ai_prediction_json"] if row else None) or "{}")
        if not isinstance(p, dict):
            return None
        raw = p.get("predicted_disease") or (((p.get("predictions") or [{}])[0] or {}).get("disease"))
        return canonical_disease(raw)
    except Exception:
        return None

def vet_confirmed_outbreak(c, village_id, disease):
    """Has a vet confirmed an outbreak of this disease in this village in the last 30 days?"""
    if village_id is None or not disease:
        return False
    cutoff = (datetime.fromisoformat(now()) - timedelta(days=30)).isoformat()
    for r in c.execute("SELECT case_id FROM outbreak_assessments WHERE village_id=? AND confirmed=1 AND created_at>=?",
                       (village_id, cutoff)):
        if case_disease(c, r["case_id"]) == canonical_disease(disease):
            return True
    return False

def notify_farmer_outbreak_advisory(c, disease, village_id, village_name, level, score, confirmed, case_id=None, triage_level=None, resurface=True):
    """Outbreak advisory to every farmer with animals in the village (never for LOW)."""
    disease = canonical_disease(disease)
    if level not in ("MODERATE", "HIGH", "CRITICAL") or not disease or disease == "Healthy":
        return
    eff_level = "CRITICAL" if confirmed else level
    t_lvl = triage_level or ("Critical" if eff_level == "CRITICAL" else ("High" if eff_level == "HIGH" else DISEASE_PROFILE.get(disease, {}).get("severity", "Moderate")))
    data = {
        "adv": "farmer_outbreak",
        "disease": disease,
        "level": eff_level,
        "outbreak_level": eff_level,
        "triage_level": t_lvl,
        "village": village_name,
        "score": max(85, score or 0) if confirmed else score,
        "confirmed": bool(confirmed),
        "case_id": case_id,
    }
    title = f"Outbreak advisory: {disease} in {village_name} ({eff_level})"
    msg = (f"{'Confirmed by a veterinarian' if confirmed else 'Possible'} {disease} outbreak in {village_name}. "
           f"Level: {eff_level}. Follow the advisory and contact your veterinarian.")
    sev = "CRITICAL" if eff_level == "CRITICAL" else "HIGH"
    for f in c.execute("""SELECT DISTINCT a.farmer_id FROM animals a JOIN users u ON u.id=a.farmer_id
                          WHERE a.village_id=? AND u.role='farmer'""", (village_id,)).fetchall():
        upsert_notification(c, f["farmer_id"], f"farmer_ob:{disease}:{village_id}", title, msg, sev,
                            "ADVISORY", case_id, village_id, data, resurface=resurface)

def village_for(c, name, pincode, lat=None, lon=None):
    pincode = pincode or ""
    c.execute("INSERT OR IGNORE INTO villages(name,pincode,latitude,longitude,created_at) VALUES(?,?,?,?,?)",
              (name,pincode,lat,lon,now()))
    v = c.execute("SELECT * FROM villages WHERE name=? AND pincode=?",(name,pincode)).fetchone()
    # Store coordinates only when supplied. Never invent coordinates.
    if v and lat is not None and lon is not None:
        c.execute("UPDATE villages SET latitude=?,longitude=? WHERE id=?",(lat,lon,v["id"]))
        v = c.execute("SELECT * FROM villages WHERE id=?",(v["id"],)).fetchone()
    # Sync village to MongoDB
    mongo_upsert("villages", {
        "_id": v["id"],
        "id": v["id"],
        "name": v["name"],
        "pincode": v["pincode"],
        "latitude": v["latitude"],
        "longitude": v["longitude"],
        "created_at": v["created_at"]
    })
    return v

def latest_report(c, animal_id):
    r = c.execute("SELECT * FROM health_reports WHERE animal_id=? ORDER BY reported_at DESC,id DESC LIMIT 1",(animal_id,)).fetchone()
    if not r: return None
    d = dict(r)
    d["symptoms"] = json.loads(d.pop("symptoms_json") or "[]")
    d["prediction"] = json.loads(d.pop("ai_prediction_json") or "null")
    d["weather"] = json.loads(d.pop("weather_json") or "null") if "weather_json" in d else None
    d["report_inputs"] = json.loads(d.pop("report_inputs_json") or "null") if "report_inputs_json" in d else None
    return d


def active_case(c, animal_id):
    return c.execute("SELECT * FROM cases WHERE animal_id=? AND status!='CLOSED' ORDER BY opened_at DESC LIMIT 1",(animal_id,)).fetchone()


def _farmer_resolved_coords(c, farmer_row):
    """Resolve a farmer's coordinates from their user row, else any of their animals,
    else their village record. Never invents coordinates for unmapped demo farmer."""
    la, lo = _coerce_latlon(farmer_row["latitude"], farmer_row["longitude"])
    if la is not None and lo is not None:
        return la, lo
    row = c.execute("""
        SELECT a.latitude alat, a.longitude alon, v.latitude vlat, v.longitude vlon
        FROM animals a LEFT JOIN villages v ON v.id=a.village_id
        WHERE a.farmer_id=? ORDER BY a.id DESC
    """, (farmer_row["id"],)).fetchall()
    for r in row:
        la, lo = _coerce_latlon(r["alat"], r["alon"])
        if la is not None and lo is not None:
            return la, lo
        la, lo = _coerce_latlon(r["vlat"], r["vlon"])
        if la is not None and lo is not None:
            return la, lo
    if farmer_row["village"]:
        vrow = c.execute(
            "SELECT latitude, longitude FROM villages WHERE lower(name)=lower(?) AND latitude IS NOT NULL LIMIT 1",
            (str(farmer_row["village"]).strip(),)
        ).fetchone()
        if vrow:
            la, lo = _coerce_latlon(vrow["latitude"], vrow["longitude"])
            if la is not None and lo is not None:
                return la, lo
    # For real non-demo farmers with a 6-digit pincode, check known pincode table
    if farmer_row["phone"] != "+919000000003" and (farmer_row["pincode"] or farmer_row["village"]):
        la, lo = _coords_from_pincode_fallback(farmer_row["pincode"], farmer_row["village"])
        if la is not None and lo is not None:
            return la, lo
    return None, None


def assigned_vet_by_gps(c, farmer_lat, farmer_lon):
    """Find the closest vet to a farmer by haversine lat/lng distance.
    Always returns the geographically nearest vet with GPS coordinates (no radius cutoff);
    falls back to the first vet only if no vet has GPS coordinates.
    """
    f_lat, f_lon = _coerce_latlon(farmer_lat, farmer_lon)
    vets = c.execute("SELECT * FROM users WHERE role='vet' ORDER BY id").fetchall()
    if not vets:
        return None
    if f_lat is None or f_lon is None:
        return vets[0]
    closest = None
    closest_dist = float("inf")
    for v in vets:
        v_lat, v_lon = _coerce_latlon(v["latitude"], v["longitude"])
        if v_lat is not None and v_lon is not None:
            d = haversine(f_lat, f_lon, v_lat, v_lon)
            if d < closest_dist:
                closest_dist = d
                closest = v
    return closest or vets[0]


def assigned_vet(c, village_id):
    """Assign a vet to a village using GPS of village centre; fallback to vet_villages."""
    vil = c.execute("SELECT * FROM villages WHERE id=?", (village_id,)).fetchone()
    if vil:
        v_lat, v_lon = _coerce_latlon(vil["latitude"], vil["longitude"])
        if v_lat is not None and v_lon is not None:
            vet = assigned_vet_by_gps(c, v_lat, v_lon)
            if vet:
                c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)", (vet["id"], village_id))
                return vet
    row = c.execute("""
      SELECT u.* FROM users u JOIN vet_villages vv ON vv.vet_id=u.id
      WHERE vv.village_id=? AND u.role='vet' ORDER BY u.id LIMIT 1
    """, (village_id,)).fetchone()
    if row:
        return row
    vet = c.execute("SELECT * FROM users WHERE role='vet' ORDER BY id LIMIT 1").fetchone()
    if vet and village_id:
        c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)", (vet["id"], village_id))
        return vet
    return None


def vet_for_farmer(c, farmer_id, village_id=None):
    """Single source of truth for a farmer's assigned vet across the Livestock table,
    Report submission, Case detail modal, and Vet dashboard."""
    f = c.execute("SELECT * FROM users WHERE id=?", (farmer_id,)).fetchone()
    if f:
        f_lat, f_lon = _farmer_resolved_coords(c, f)
        if f_lat is not None and f_lon is not None:
            nearest = assigned_vet_by_gps(c, f_lat, f_lon)
            if nearest:
                if f["assigned_vet_id"] != nearest["id"]:
                    transfer_farmer_to_vet(c, f["id"], nearest["id"], "closest veterinarian by GPS")
                return nearest
        if f["assigned_vet_id"]:
            v = c.execute("SELECT * FROM users WHERE id=? AND role='vet'", (f["assigned_vet_id"],)).fetchone()
            if v:
                return v
    if village_id:
        return assigned_vet(c, village_id)
    return c.execute("SELECT * FROM users WHERE role='vet' ORDER BY id LIMIT 1").fetchone()


def _farmer_village_ids(c, farmer_id, farmer_village_name=None):
    """Return all village IDs associated with a farmer (from their animals and their profile village name)."""
    vids = {r["village_id"] for r in c.execute("SELECT DISTINCT village_id FROM animals WHERE farmer_id=?", (farmer_id,)) if r["village_id"] is not None}
    if farmer_village_name:
        for r in c.execute("SELECT id FROM villages WHERE lower(name)=lower(?)", (str(farmer_village_name).strip(),)):
            vids.add(r["id"])
    return sorted(vids)


def transfer_farmer_to_vet(c, farmer_id, new_vet_id, reason="a closer veterinarian is now available", notify=True):
    """Move a farmer's ENTIRE record to a new vet: open cases, closed cases, village links,
    and past health reports all follow the farmer."""
    farmer = c.execute("SELECT * FROM users WHERE id=? AND role='farmer'", (farmer_id,)).fetchone()
    if not farmer:
        return None
    old_vet_id = farmer["assigned_vet_id"]
    new_vet = c.execute("SELECT * FROM users WHERE id=? AND role='vet'", (new_vet_id,)).fetchone()
    if not new_vet:
        return None

    # 1. Update farmer's assigned_vet_id
    c.execute("UPDATE users SET assigned_vet_id=? WHERE id=?", (new_vet_id, farmer_id))

    # 2. Move all cases (open, in-progress, and closed) of this farmer to the new vet
    c.execute("UPDATE cases SET farmer_id=?, vet_id=? WHERE farmer_id=? OR animal_id IN (SELECT id FROM animals WHERE farmer_id=?)", (farmer_id, new_vet_id, farmer_id, farmer_id))

    # 3. Link all villages of this farmer (from animals AND farmer profile) to the new vet
    farmer_villages = _farmer_village_ids(c, farmer_id, farmer["village"])
    for vid in farmer_villages:
        c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)", (new_vet_id, vid))

    if old_vet_id == new_vet_id:
        return None

    # 4. Drop the old vet's village link only where they no longer serve ANY farmer
    if old_vet_id:
        for vid in farmer_villages:
            v_row = c.execute("SELECT name FROM villages WHERE id=?", (vid,)).fetchone()
            v_name = v_row["name"] if v_row else ""
            still_serving = c.execute("""
                SELECT 1 FROM users f
                LEFT JOIN animals a ON a.farmer_id=f.id
                WHERE f.role='farmer' AND f.assigned_vet_id=?
                  AND (a.village_id=? OR lower(COALESCE(f.village,''))=lower(?))
                LIMIT 1
            """, (old_vet_id, vid, v_name)).fetchone()
            if not still_serving:
                c.execute("DELETE FROM vet_villages WHERE vet_id=? AND village_id=?", (old_vet_id, vid))

        if notify:
            create_notification(c, old_vet_id,
                "Farmer transferred to a closer vet",
                f"{farmer['name']} ({farmer['village'] or 'village'}) and all of their records - "
                f"open cases, closed cases and past reports - have moved to {new_vet['name']}, "
                f"because {reason}. They no longer appear on your dashboard.",
                "INFO", "GENERAL", None, None)

    if notify:
        create_notification(c, new_vet_id,
            "New farmer assigned to you",
            f"{farmer['name']} ({farmer['village'] or 'village'}) is now under your care, "
            f"including their full case history. Please review their open cases.",
            "HIGH", "GENERAL", None, None)

        create_notification(c, farmer_id,
            "Your veterinarian has changed",
            f"{new_vet['name']} is now your assigned veterinarian and can see your herd's "
            f"full history. They are your new primary contact.",
            "INFO", "GENERAL", None, None)

    return old_vet_id


def repair_relational_integrity(c):
    """Self-heal any user/animal/case/report relational mismatches caused by cross-instance
    ID shifts or legacy MongoDB documents:
      1. Merge duplicate users sharing the same normalized phone or same (farmer name, village).
      2. Re-attach animals with orphaned farmer_id to the matching farmer in that village.
      3. Enforce cases.farmer_id == animals.farmer_id on every case.
      4. Attach any health_reports with NULL or missing case_id to their animal's case.
    """
    changed = False

    def _merge_user_into(old_id, new_id):
        nonlocal changed
        if old_id == new_id or old_id is None or new_id is None:
            return
        c.execute("UPDATE animals SET farmer_id=? WHERE farmer_id=?", (new_id, old_id))
        c.execute("UPDATE cases SET farmer_id=? WHERE farmer_id=?", (new_id, old_id))
        c.execute("UPDATE cases SET vet_id=? WHERE vet_id=?", (new_id, old_id))
        c.execute("UPDATE vaccinations SET farmer_id=? WHERE farmer_id=?", (new_id, old_id))
        c.execute("UPDATE vaccinations SET vet_id=? WHERE vet_id=?", (new_id, old_id))
        c.execute("UPDATE case_actions SET vet_id=? WHERE vet_id=?", (new_id, old_id))
        c.execute("UPDATE outbreak_assessments SET vet_id=? WHERE vet_id=?", (new_id, old_id))
        c.execute("UPDATE resource_requests SET vet_id=? WHERE vet_id=?", (new_id, old_id))
        c.execute("UPDATE notifications SET user_id=? WHERE user_id=?", (new_id, old_id))
        c.execute("UPDATE users SET assigned_vet_id=? WHERE assigned_vet_id=?", (new_id, old_id))
        for vv in c.execute("SELECT village_id FROM vet_villages WHERE vet_id=?", (old_id,)).fetchall():
            c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)", (new_id, vv["village_id"]))
        c.execute("DELETE FROM vet_villages WHERE vet_id=?", (old_id,))
        c.execute("DELETE FROM users WHERE id=?", (old_id,))
        changed = True

    # 1a. Normalize phones and merge any duplicate phone rows
    by_norm_phone = {}
    for u in c.execute("SELECT * FROM users ORDER BY id ASC").fetchall():
        norm_p = validate_and_format_phone(u["phone"]) or str(u["phone"] or "").strip()
        if not norm_p:
            continue
        if norm_p in by_norm_phone:
            canon = by_norm_phone[norm_p]
            # Prefer the row whose phone is already in +91 format or has higher id if canon wasn't formatted
            if u["phone"] == norm_p and canon["phone"] != norm_p:
                _merge_user_into(canon["id"], u["id"])
                by_norm_phone[norm_p] = u
            else:
                _merge_user_into(u["id"], canon["id"])
        else:
            if u["phone"] != norm_p:
                try:
                    c.execute("UPDATE users SET phone=? WHERE id=?", (norm_p, u["id"]))
                    changed = True
                except sqlite3.IntegrityError:
                    pass
            by_norm_phone[norm_p] = u

    # 1b. Merge duplicate farmer accounts sharing the same (name, village)
    by_farmer_key = {}
    for u in c.execute("SELECT * FROM users WHERE role='farmer' ORDER BY id ASC").fetchall():
        fname = str(u["name"] or "").strip().lower()
        fvil = str(u["village"] or "").strip().lower()
        if not fname or not fvil:
            continue
        fkey = (fname, fvil)
        if fkey in by_farmer_key:
            canon = by_farmer_key[fkey]
            # Keep the higher/newer ID when merging duplicate farmer profiles in the same village
            _merge_user_into(canon["id"], u["id"])
            by_farmer_key[fkey] = u
        else:
            by_farmer_key[fkey] = u

    # 2. Repair any animals whose farmer_id does not exist, is not a farmer, or points to a stale
    #    farmer_id from another village when the animal's village has its own registered farmer.
    orphan_animals = c.execute("""
        SELECT a.id, a.farmer_id, a.village_id, v.name village_name,
               u.id uid, u.role urole, u.village uvillage
        FROM animals a
        LEFT JOIN users u ON u.id = a.farmer_id
        LEFT JOIN villages v ON v.id = a.village_id
        WHERE u.id IS NULL
           OR u.role != 'farmer'
           OR (
             v.name IS NOT NULL AND trim(v.name) != ''
             AND lower(trim(COALESCE(u.village, ''))) != lower(trim(v.name))
             AND EXISTS (
               SELECT 1 FROM users f2
               WHERE f2.role = 'farmer'
                 AND lower(trim(COALESCE(f2.village, ''))) = lower(trim(v.name))
             )
           )
    """).fetchall()
    for oa in orphan_animals:
        match_f = None
        if oa["village_name"]:
            match_f = c.execute("""
                SELECT u.id FROM users u
                WHERE u.role='farmer' AND lower(trim(COALESCE(u.village,'')))=lower(trim(?))
                ORDER BY
                  (SELECT COUNT(*) FROM animals a2 WHERE a2.farmer_id=u.id AND a2.village_id=? AND a2.id!=?) DESC,
                  u.id ASC
                LIMIT 1
            """, (oa["village_name"], oa["village_id"], oa["id"])).fetchone()
        if not match_f and oa["village_id"] is not None:
            match_f = c.execute("""
                SELECT u.id FROM users u JOIN animals a2 ON a2.farmer_id=u.id
                WHERE u.role='farmer' AND a2.village_id=? AND a2.id!=?
                GROUP BY u.id
                ORDER BY COUNT(a2.id) DESC, u.id ASC
                LIMIT 1
            """, (oa["village_id"], oa["id"])).fetchone()
        if not match_f and (oa["uid"] is None or oa["urole"] != "farmer"):
            match_f = c.execute("SELECT id FROM users WHERE role='farmer' ORDER BY id ASC LIMIT 1").fetchone()
        if match_f and match_f["id"] != oa["farmer_id"]:
            c.execute("UPDATE animals SET farmer_id=? WHERE id=?", (match_f["id"], oa["id"]))
            c.execute("UPDATE cases SET farmer_id=? WHERE animal_id=?", (match_f["id"], oa["id"]))
            c.execute("UPDATE vaccinations SET farmer_id=? WHERE animal_id=?", (match_f["id"], oa["id"]))
            changed = True

    # 3. Enforce cases.farmer_id == animals.farmer_id on EVERY case
    if c.execute("""
        UPDATE cases
        SET farmer_id = (SELECT a.farmer_id FROM animals a WHERE a.id = cases.animal_id)
        WHERE EXISTS (SELECT 1 FROM animals a WHERE a.id = cases.animal_id AND a.farmer_id != cases.farmer_id)
    """).rowcount:
        changed = True

    # 4. Attach any health_reports with NULL or missing case_id to their animal's case
    orphan_reps = c.execute("""
        SELECT hr.id, hr.animal_id, hr.reported_at
        FROM health_reports hr
        LEFT JOIN cases cs ON cs.id = hr.case_id
        WHERE hr.case_id IS NULL OR cs.id IS NULL
        ORDER BY hr.reported_at ASC, hr.id ASC
    """).fetchall()
    for orp in orphan_reps:
        a_row = c.execute("SELECT id, farmer_id FROM animals WHERE id=?", (orp["animal_id"],)).fetchone()
        if not a_row:
            continue
        ex_c = c.execute(
            "SELECT id FROM cases WHERE animal_id=? ORDER BY (status!='CLOSED') DESC, opened_at DESC, id DESC LIMIT 1",
            (a_row["id"],)
        ).fetchone()
        if ex_c:
            target_cid = ex_c["id"]
        else:
            cur_c = c.execute(
                "INSERT INTO cases(animal_id,farmer_id,vet_id,status,opened_at) VALUES(?,?,?,?,?)",
                (a_row["id"], a_row["farmer_id"], None, "OPEN", orp["reported_at"] or now())
            )
            target_cid = cur_c.lastrowid
        c.execute("UPDATE health_reports SET case_id=? WHERE id=?", (target_cid, orp["id"]))
        changed = True

    return changed


def reconcile_farmer_vet_assignments(c, notify=False):
    """Ensure every farmer (and all their open/closed cases and village links) is assigned
    to their geographically closest vet whenever GPS coordinates exist.
    Safe for unit tests where demo vets/farmers have no GPS coordinates."""
    changed = repair_relational_integrity(c)
    gps_vets = []
    for v in c.execute("SELECT * FROM users WHERE role='vet' ORDER BY id").fetchall():
        vla, vlo = _coerce_latlon(v["latitude"], v["longitude"])
        if vla is not None and vlo is not None:
            gps_vets.append((v, vla, vlo))

    farmers = c.execute("SELECT * FROM users WHERE role='farmer' ORDER BY id").fetchall()
    for f in farmers:
        f_lat, f_lon = _farmer_resolved_coords(c, f)
        if f_lat is not None and f_lon is not None and gps_vets:
            best_vet, best_lat, best_lon = min(
                gps_vets, key=lambda item: haversine(f_lat, f_lon, item[1], item[2])
            )
            dist_best = haversine(f_lat, f_lon, best_lat, best_lon)
            cur_vet = c.execute("SELECT * FROM users WHERE id=? AND role='vet'", (f["assigned_vet_id"],)).fetchone() if f["assigned_vet_id"] else None
            should_transfer = False
            reason = "closest veterinarian by GPS"
            if not cur_vet:
                should_transfer = True
                reason = "no vet was assigned yet"
            elif cur_vet["id"] != best_vet["id"]:
                c_lat, c_lon = _coerce_latlon(cur_vet["latitude"], cur_vet["longitude"])
                if c_lat is None or c_lon is None:
                    should_transfer = True
                    reason = "the previous vet has no mapped location"
                else:
                    dist_cur = haversine(f_lat, f_lon, c_lat, c_lon)
                    if dist_best + 1e-6 < dist_cur:
                        should_transfer = True
                        reason = f"they are {round(dist_best,1)} km away versus {round(dist_cur,1)} km"

            target_vet_id = best_vet["id"] if (should_transfer or not cur_vet) else cur_vet["id"]
            if should_transfer and f["assigned_vet_id"] != target_vet_id:
                transfer_farmer_to_vet(c, f["id"], target_vet_id, reason=reason, notify=notify)
                changed = True
            else:
                # Ensure all cases and villages of this farmer are synced to target_vet_id
                if c.execute("""
                    UPDATE cases SET farmer_id=?, vet_id=?
                    WHERE (farmer_id=? OR animal_id IN (SELECT id FROM animals WHERE farmer_id=?))
                      AND (vet_id IS NULL OR vet_id != ? OR farmer_id != ?)
                """, (f["id"], target_vet_id, f["id"], f["id"], target_vet_id, f["id"])).rowcount:
                    changed = True
                for vid in _farmer_village_ids(c, f["id"], f["village"]):
                    if c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)",
                                 (target_vet_id, vid)).rowcount:
                        changed = True
        else:
            # No GPS on farmer or no GPS vets exist: fallback assignment when assigned_vet_id is NULL
            vet_id = f["assigned_vet_id"]
            if vet_id is not None and not c.execute("SELECT 1 FROM users WHERE id=? AND role='vet'", (vet_id,)).fetchone():
                vet_id = None
            if vet_id is None:
                case_vet = c.execute(
                    "SELECT vet_id FROM cases WHERE farmer_id=? AND vet_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                    (f["id"],)
                ).fetchone()
                vet_id = case_vet["vet_id"] if case_vet else None
            if vet_id is None and f["village"]:
                row = c.execute("""
                    SELECT vv.vet_id FROM vet_villages vv
                    JOIN villages v ON v.id=vv.village_id
                    WHERE lower(v.name)=lower(?) LIMIT 1
                """, (str(f["village"]).strip(),)).fetchone()
                vet_id = row["vet_id"] if row else None
            if vet_id is None:
                first_vet = c.execute("SELECT id FROM users WHERE role='vet' ORDER BY id LIMIT 1").fetchone()
                vet_id = first_vet["id"] if first_vet else None
            if vet_id is not None:
                if f["assigned_vet_id"] != vet_id:
                    c.execute("UPDATE users SET assigned_vet_id=? WHERE id=?", (vet_id, f["id"]))
                    changed = True
                for vid in _farmer_village_ids(c, f["id"], f["village"]):
                    if c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)", (vet_id, vid)).rowcount:
                        changed = True
    # Guarantee every case's farmer_id matches its animal's owner, and vet_id matches when GPS-assigned or missing
    if c.execute("""
        UPDATE cases
        SET farmer_id = (SELECT a.farmer_id FROM animals a WHERE a.id = cases.animal_id)
        WHERE EXISTS (SELECT 1 FROM animals a WHERE a.id = cases.animal_id AND a.farmer_id != cases.farmer_id)
    """).rowcount:
        changed = True
    if c.execute("""
        UPDATE cases
        SET vet_id = (
            SELECT f.assigned_vet_id
            FROM animals a JOIN users f ON f.id = a.farmer_id
            WHERE a.id = cases.animal_id
        )
        WHERE EXISTS (
            SELECT 1
            FROM animals a JOIN users f ON f.id = a.farmer_id
            LEFT JOIN users v ON v.id = f.assigned_vet_id
            WHERE a.id = cases.animal_id
              AND f.assigned_vet_id IS NOT NULL
              AND (
                cases.vet_id IS NULL
                OR NOT EXISTS (SELECT 1 FROM users cv WHERE cv.id = cases.vet_id AND cv.role = 'vet')
                OR (
                  (f.latitude IS NOT NULL OR a.latitude IS NOT NULL OR v.latitude IS NOT NULL)
                  AND cases.vet_id != f.assigned_vet_id
                )
              )
        )
    """).rowcount:
        changed = True
    if c.execute("""
        INSERT OR IGNORE INTO vet_villages(vet_id, village_id)
        SELECT DISTINCT f.assigned_vet_id, a.village_id
        FROM animals a JOIN users f ON f.id = a.farmer_id
        WHERE f.assigned_vet_id IS NOT NULL AND a.village_id IS NOT NULL
    """).rowcount:
        changed = True

    if changed and MONGO_URI and not _IN_INIT:
        trigger_mongo_sync(tables=("users", "animals", "cases", "vet_villages", "notifications"), wait_timeout=0.0)
    return changed


def backfill_case_notification_advisories(c):
    """Ensure existing CASE notifications for vets carry structured advisory data_json
    so they render full clinical severity and outbreak guidelines in the Notifications tab.
    Historical pre-seeded notifications from September 2026 are marked read so they do not
    spam popups on initial boot, while all new case notifications remain unread popups."""
    rows = c.execute("""
        SELECT id, user_id, case_id, village_id, created_at, read_at, data_json
        FROM notifications
        WHERE kind='CASE' AND case_id IS NOT NULL
    """).fetchall()
    for n in rows:
        cs = c.execute("SELECT * FROM cases WHERE id=?", (n["case_id"],)).fetchone()
        if not cs:
            continue
        target_vet_id = cs["vet_id"] or n["user_id"]
        if n["data_json"]:
            if target_vet_id and n["user_id"] != target_vet_id:
                c.execute("UPDATE notifications SET user_id=? WHERE id=?", (target_vet_id, n["id"]))
            continue
        a = c.execute("SELECT * FROM animals WHERE id=?", (cs["animal_id"],)).fetchone()
        f = c.execute("SELECT * FROM users WHERE id=?", (cs["farmer_id"],)).fetchone()
        v = c.execute("SELECT * FROM villages WHERE id=?", (a["village_id"],)).fetchone() if a else None
        hr = c.execute(
            "SELECT * FROM health_reports WHERE case_id=? ORDER BY reported_at DESC, id DESC LIMIT 1",
            (cs["id"],)
        ).fetchone()
        if not hr:
            continue
        try:
            pred = json.loads(hr["ai_prediction_json"] or "{}")
            syms = json.loads(hr["symptoms_json"] or "[]")
        except Exception:
            continue
        if not isinstance(pred, dict):
            continue
        disease = canonical_disease(pred.get("predicted_disease") or (((pred.get("predictions") or [{}])[0] or {}).get("disease")))
        if not disease:
            continue
        triage = pred.get("triage_risk") or DISEASE_PROFILE.get(disease, {}).get("severity", "Moderate")
        ob_status = pred.get("outbreak_status") or "LOW"
        ob_score = pred.get("outbreak_risk_score") if pred.get("outbreak_risk_score") is not None else 0
        conf = pred.get("confidence")
        ob_obj = pred.get("outbreak") or {}
        confirmed_ob = vet_confirmed_outbreak(c, a["village_id"] if a else None, disease)
        if confirmed_ob:
            ob_status = "CRITICAL"
            ob_score = max(85, ob_score or 0)
            triage = "Critical"
        adv_data = {
            "adv": "vet_case",
            "disease": disease,
            "level": triage,
            "outbreak_level": ob_status,
            "score": ob_score,
            "confirmed": confirmed_ob,
            "confidence": round(float(conf), 1) if isinstance(conf, (int, float)) else None,
            "animal": a["name"] if a else "",
            "animal_tag": a["tag"] if a else "",
            "species": a["species"] if a else "",
            "farmer": f["name"] if f else "",
            "village": v["name"] if v else (f["village"] if f else ""),
            "case_id": cs["id"],
            "escalate": bool(pred.get("escalate")),
            "escalation_reason": pred.get("escalation_reason") or "",
            "triage_reasons": pred.get("triage_reasons") or [],
            "symptoms": syms,
            "animals": ob_obj.get("unique_animals", 1),
            "farms": ob_obj.get("unique_farms", 1),
            "cluster_animals": ob_obj.get("unique_animals", 1),
            "cluster_farms": ob_obj.get("unique_farms", 1),
        }
        created_s = str(n["created_at"] or "")
        read_val = n["read_at"]
        if not read_val and created_s.startswith("2026-09-"):
            read_val = created_s
        c.execute(
            "UPDATE notifications SET user_id=?, data_json=?, read_at=? WHERE id=?",
            (target_vet_id, json.dumps(adv_data), read_val, n["id"])
        )


def backfill_farmer_vet_assignment(c):
    """Reconcile and backfill farmer -> nearest vet assignments."""
    reconcile_farmer_vet_assignments(c, notify=False)
    backfill_case_notification_advisories(c)


def _reassign_farmers_to_vet(c, new_vet_id, vet_lat, vet_lon):
    """When a vet registers, pull over every farmer for whom this vet is strictly
    closer than their current vet. The whole farmer record moves - open cases,
    closed cases, village links, and past reports - via transfer_farmer_to_vet()."""
    new_vet = c.execute("SELECT * FROM users WHERE id=?", (new_vet_id,)).fetchone()
    vet_lat, vet_lon = _coerce_latlon(vet_lat, vet_lon)
    if not new_vet or vet_lat is None or vet_lon is None:
        return

    moved = []
    farmers = c.execute("SELECT * FROM users WHERE role='farmer' ORDER BY id").fetchall()
    for f in farmers:
        if f["assigned_vet_id"] == new_vet_id:
            continue
        lat, lon = _farmer_resolved_coords(c, f)
        if lat is None or lon is None:
            continue

        dist_new = haversine(vet_lat, vet_lon, lat, lon)
        old_vet = c.execute("SELECT * FROM users WHERE id=? AND role='vet'", (f["assigned_vet_id"],)).fetchone() if f["assigned_vet_id"] else None
        old_lat, old_lon = _coerce_latlon(old_vet["latitude"], old_vet["longitude"]) if old_vet else (None, None)

        if old_vet and old_lat is not None and old_lon is not None:
            dist_old = haversine(old_lat, old_lon, lat, lon)
            if dist_new >= dist_old:
                continue
            reason = f"they are {round(dist_new,1)} km away versus {round(dist_old,1)} km"
        elif old_vet:
            reason = "the previous vet has no mapped location"
        else:
            reason = "no vet was assigned yet"

        transfer_farmer_to_vet(c, f["id"], new_vet_id, reason=reason, notify=True)
        moved.append(f["name"])

    if moved:
        shown = ", ".join(moved[:5])
        extra = f" and {len(moved)-5} more" if len(moved) > 5 else ""
        create_notification(c, new_vet_id,
            f"{len(moved)} farmer(s) assigned to you",
            f"You are now the closest vet for: {shown}{extra}. "
            f"Their full records have been transferred to your dashboard.",
            "HIGH", "GENERAL", None, None)


def accessible_animals(c, u):
    reconcile_farmer_vet_assignments(c, notify=False)
    if u["role"] == "farmer":
        return c.execute("""
          SELECT a.*,v.name village_name,u.name farmer_name,u.phone farmer_phone,u.latitude farmer_lat,u.longitude farmer_lon
          FROM animals a JOIN villages v ON v.id=a.village_id
          JOIN users u ON u.id=a.farmer_id
          WHERE a.farmer_id=? AND a.species IN ('Cattle','Buffalo') ORDER BY a.id DESC
        """, (u["id"],)).fetchall()
    if u["role"] == "vet":
        return c.execute("""
          SELECT DISTINCT a.*,v.name village_name,u.name farmer_name,u.phone farmer_phone,u.latitude farmer_lat,u.longitude farmer_lon
          FROM animals a JOIN villages v ON v.id=a.village_id
          JOIN users u ON u.id=a.farmer_id
          WHERE (
            u.assigned_vet_id=?
            OR EXISTS (SELECT 1 FROM cases cs WHERE cs.animal_id=a.id AND cs.vet_id=? AND cs.status!='CLOSED')
            OR (u.assigned_vet_id IS NULL AND a.village_id IN (SELECT village_id FROM vet_villages WHERE vet_id=?))
          ) AND a.species IN ('Cattle','Buffalo')
          ORDER BY v.name,a.name
        """, (u["id"], u["id"], u["id"])).fetchall()
    return c.execute("""
      SELECT a.*,v.name village_name,u.name farmer_name,u.phone farmer_phone,u.latitude farmer_lat,u.longitude farmer_lon
      FROM animals a JOIN villages v ON v.id=a.village_id
      JOIN users u ON u.id=a.farmer_id ORDER BY a.id DESC
    """).fetchall()


def merge_duplicate_open_cases(c):
    """Keep newest OPEN/IN_PROGRESS case per animal; close extras and reattach child rows."""
    dupes = c.execute("""
      SELECT animal_id FROM cases
      WHERE status!='CLOSED' GROUP BY animal_id HAVING COUNT(*)>1
    """).fetchall()
    for row in dupes:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM cases WHERE animal_id=? AND status!='CLOSED' ORDER BY id ASC",
            (row["animal_id"],),
        )]
        keep = ids[-1]
        for oid in ids[:-1]:
            c.execute("UPDATE health_reports SET case_id=? WHERE case_id=?", (keep, oid))
            c.execute("UPDATE case_actions SET case_id=? WHERE case_id=?", (keep, oid))
            c.execute("UPDATE outbreak_assessments SET case_id=? WHERE case_id=?", (keep, oid))
            c.execute("UPDATE notifications SET case_id=? WHERE case_id=?", (keep, oid))
            try:
                c.execute("UPDATE vaccinations SET case_id=? WHERE case_id=?", (keep, oid))
            except sqlite3.OperationalError:
                pass
            c.execute(
                "UPDATE cases SET status='CLOSED', closed_at=? WHERE id=?",
                (now(), oid),
            )

def parse_predicted_outbreak(prediction):
    if not isinstance(prediction, dict): return None
    for k in ("outbreak_status","outbreak","status"):
        v=prediction.get(k)
        if isinstance(v,bool): return "YES" if v else "NO"
        if isinstance(v,str):
            s=v.upper()
            if "HIGH" in s or s in ("YES","TRUE","CONFIRMED","LIKELY"): return "YES"
            if s in ("NO","FALSE","LOW","UNLIKELY","NONE"): return "NO"
    p = prediction.get("outbreak_probability")
    if isinstance(p,(int,float)): return "YES" if p >= 0.5 else "NO"
    return None

def _age_months(a):
    raw = a["age"] if "age" in a.keys() else ""
    try:
        val=float(str(raw).strip())
        if val < 1: return 12
        if val <= 30: return int(round(val*12))
        return int(round(val))
    except Exception:
        return 36

def _recent_treatment(c, animal_id):
    row=c.execute("SELECT details FROM case_actions ca JOIN cases cs ON cs.id=ca.case_id WHERE cs.animal_id=? AND ca.action_type='MEDICATION' ORDER BY ca.action_at DESC,ca.id DESC LIMIT 1",(animal_id,)).fetchone()
    if not row: return "Untreated"
    text=(row["details"] or "").lower()
    if "antibiotic" in text: return "Antibiotics"
    if "deworm" in text: return "Dewormed"
    return "Supportive Care"

def _vaccination_flags(c, animal_id):
    flags={"Vaccinated_FMD":0,"Vaccinated_HS":0,"Vaccinated_LSD":0,"Vaccinated_BQ":0,"Days_Since_Last_Vaccination":999}
    rows=c.execute("SELECT vaccine_name, administered_at FROM vaccinations WHERE animal_id=? ORDER BY administered_at DESC,id DESC",(animal_id,)).fetchall()
    latest=None
    for r in rows:
        name=(r["vaccine_name"] or "").upper()
        if "FMD" in name: flags["Vaccinated_FMD"]=1
        if "HS" in name or "HEMORRHAGIC" in name: flags["Vaccinated_HS"]=1
        if "LSD" in name or "LUMPY" in name: flags["Vaccinated_LSD"]=1
        if "BQ" in name or "BLACK QUARTER" in name: flags["Vaccinated_BQ"]=1
        latest = latest or r["administered_at"]
    if latest:
        try:
            last=datetime.fromisoformat(latest.replace("Z","+00:00"))
            flags["Days_Since_Last_Vaccination"]=max(0,(datetime.now(timezone.utc)-last.astimezone(timezone.utc)).days)
        except Exception: pass
    return flags

def _parse_report_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(str(value)[:10]).replace(tzinfo=timezone.utc)
        except Exception:
            return None

def _clean_report_inputs(d):
    def positive_number(key, default=None):
        value=d.get(key)
        if value in (None, ""):
            return default
        try:
            value=float(value)
            return value if value >= 0 else default
        except Exception:
            return default

    vaccine_dates={}
    vaccine_months={}
    vaccine_flags={}
    latest=None
    today=datetime.now(timezone.utc)
    for vaccine in ("FMD","HS","LSD","BQ"):
        key=f"vaccinated_{vaccine.lower()}_date"
        raw=(d.get(key) or "").strip() if isinstance(d.get(key),str) else d.get(key)
        parsed=_parse_report_date(raw)
        vaccine_dates[vaccine]=raw or None
        if parsed:
            days=max(0,(today-parsed).days)
            months=max(0,int(days/30))
            vaccine_months[vaccine]=months
            vaccine_flags[f"Vaccinated_{vaccine}"]=1 if parsed <= today and days <= 365 else 0
            latest=parsed if latest is None or parsed > latest else latest
        else:
            vaccine_months[vaccine]=None
            vaccine_flags[f"Vaccinated_{vaccine}"]=0
    vaccine_flags["Days_Since_Last_Vaccination"]=max(0,(today-latest).days) if latest else 999
    return {
        "age_months": positive_number("age_months"),
        "herd_size": positive_number("herd_size", 1),
        "dist_waterbody_km": positive_number("dist_waterbody_km", 1.2),
        "vaccine_dates": vaccine_dates,
        "vaccine_months": vaccine_months,
        "vaccination_flags": vaccine_flags,
    }

def _symptom_flags(symptoms):
    """One 0/1 flag per model symptom field.

    The 19 UI symptoms map 1:1 onto the model's 19 symptom fields by name:
    "Fever / High Body Temperature" -> "Fever_High_Body_Temperature".
    """
    chosen = {_symptom_feature(s) for s in (symptoms or [])}
    return {f: int(f in chosen) for f in SYMPTOM_FEATURES}


def _symptom_feature(name):
    return str(name).replace(" / ", "_").replace(" ", "_")


SYMPTOM_FEATURES = [_symptom_feature(s) for s in SYMPTOMS]


def call_disease_model(a, symptoms, weather=None, c=None, report_inputs=None):
    """Run the disease classifier. It predicts the disease and its confidence
    ONLY - triage, outbreak and escalation are decided by rule-based logic
    elsewhere, using this output together with the case context.

    Returns:
      connected, predicted_disease, confidence (0-100),
      predictions [{disease, probability 0-1}] sorted high->low,
      model_inputs (the exact 32 values sent), model_version
    """
    failed = {"connected": False, "error": "Disease model failed to run", "predictions": [],
              "predicted_disease": None, "confidence": None,
              "model_version": "ml-unavailable"}
    if predict_disease is None:
        return {**failed, "detail": ML_IMPORT_ERROR}
    try:
        c = c or conn()
        weather = weather or {}
        report_inputs = report_inputs or {}
        flags = report_inputs.get("vaccination_flags") or _vaccination_flags(c, a["id"])
        herd = c.execute("SELECT COUNT(*) n FROM animals WHERE farmer_id=?", (a["farmer_id"],)).fetchone()["n"]

        model_inputs = {
            "Animal_Species": "Buffalo" if a["species"] == "Buffalo" else "Cow",
            "Age_Months": float(report_inputs.get("age_months") or _age_months(a) or 36),
            "Sex": a["sex"] if a["sex"] in ("Male", "Female") else "Female",
            "Herd_Size": max(1, int(report_inputs.get("herd_size") or herd or 1)),
            # Never-vaccinated animals were trained with 180-1100; clip to that range.
            "Days_Since_Last_Vaccination": min(1100, max(0, int(flags.get("Days_Since_Last_Vaccination", 999)))),
            "Vaccinated_FMD": int(flags.get("Vaccinated_FMD", 0)),
            "Vaccinated_HS": int(flags.get("Vaccinated_HS", 0)),
            "Vaccinated_LSD": int(flags.get("Vaccinated_LSD", 0)),
            "Vaccinated_BQ": int(flags.get("Vaccinated_BQ", 0)),
            **_symptom_flags(symptoms),
            "Temperature_C": float(weather["temperature_c"]) if weather.get("temperature_c") is not None else 28.0,
            "Humidity_Percent": float(weather["humidity_pct"]) if weather.get("humidity_pct") is not None else 65.0,
            "Rainfall_mm": max(0.0, float(weather["rainfall_mm"])) if weather.get("rainfall_mm") is not None else 0.0,
            "Season": weather.get("season") or get_season_india(),
        }
        out = predict_disease(model_inputs)

        # probabilities come back as percentages; the app stores 0-1 fractions.
        ranked = sorted(({"disease": k, "probability": round(v / 100.0, 4)} for k, v in out["probabilities"].items()),
                        key=lambda x: x["probability"], reverse=True)
        return {
            "connected": True,
            "predicted_disease": out["predicted_disease"],
            "confidence": out["confidence"],
            "predictions": ranked,
            "model_inputs": model_inputs,
            "weather_defaulted": weather.get("temperature_c") is None,
            "model_version": "GramVet disease classifier (32 inputs, 7 classes, scikit-learn 1.6.1)",
            "message": "AI-assisted screening only. A veterinarian must review and treat the animal before the case can be closed.",
        }
    except Exception as e:
        return {**failed, "detail": str(e), "model_version": "ml-inference-error"}


def _get_unique_animals(cases):
    """Return set of unique animal IDs from a list of case rows."""
    return {r["animal_id"] for r in cases if r.get("animal_id") is not None}

def _get_unique_farms(cases):
    """Return set of unique farm/owner IDs (farmer_id as farm proxy)."""
    return {r["farmer_id"] for r in cases if r.get("farmer_id") is not None}

def _get_affected_villages(cases):
    """Return set of unique non-empty village names from a list of cases."""
    names = set()
    for r in cases:
        v = (r.get("village_name") or "").strip()
        if v:
            names.add(v)
    return names

def _parse_case_date(value):
    """Parse a date string to UTC datetime; return None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(str(value)[:10]).replace(tzinfo=timezone.utc)
        except Exception:
            return None

def calculate_spatial_score(cases, ref_lat, ref_lon):
    """
    Score 0-100: how tightly are the cases clustered around (ref_lat, ref_lon)?
    One point per unique animal, so repeat reports add nothing.
    Tightness = 1 - (mean distance / 5 km): all animals at the reference point -> 1,
    all at the 5 km edge -> 0. Animals without coordinates (matched by village
    name) get no tightness credit rather than an invented position.
    """
    per_animal = {r["animal_id"]: r for r in cases if r.get("animal_id") is not None}
    n_animals = len(per_animal)
    if n_animals == 0:
        return 0
    n_farms = len(_get_unique_farms(per_animal.values()))

    dists = []
    for r in per_animal.values():
        if None not in (r.get("lat"), r.get("lon"), ref_lat, ref_lon):
            dists.append(min(RADIUS_KM, haversine(ref_lat, ref_lon, r["lat"], r["lon"])))
        else:
            dists.append(RADIUS_KM)
    tightness = 1.0 - (sum(dists) / len(dists)) / RADIUS_KM

    animal_score = min(1.0, n_animals / 8.0)
    farm_score = min(1.0, n_farms / 5.0)
    score = (0.45 * animal_score + 0.35 * farm_score + 0.20 * tightness) * 100
    return min(100, max(0, round(score)))

def calculate_temporal_score(cases, now_dt):
    """
    Score 0–100: how concentrated are the cases in the recent 7-day window?
    More unique animals appearing closer to 'today' → higher score.
    """
    if not cases:
        return 0
    unique_animals = _get_unique_animals(cases)
    n_animals = len(unique_animals)
    if n_animals == 0:
        return 0

    # Collect dates per unique animal (first occurrence in window)
    seen = {}
    for r in cases:
        aid = r.get("animal_id")
        if aid is None:
            continue
        dt = _parse_case_date(r.get("reported_at"))
        if dt is None:
            continue
        if aid not in seen or dt > seen[aid]:
            seen[aid] = dt

    if not seen:
        return 0

    # Spread: difference between earliest and latest case date in days
    dates = sorted(seen.values())
    span_days = max(0.0, (dates[-1] - dates[0]).total_seconds() / 86400)

    # More animals appearing within a short span = higher temporal score
    animal_score = min(1.0, n_animals / 5.0)
    # Concentration: 7 cases in 7 days is max; compressed into 2 days is even higher
    compression = 1.0 - min(1.0, span_days / 7.0)  # 0 span → 1.0, 7-day span → 0.0

    score = (0.6 * animal_score + 0.4 * compression) * 100
    return min(100, max(0, round(score)))

def calculate_growth_score(recent_unique_animals, baseline_unique_animals):
    """
    Score 0–100: is recent activity elevated versus the 28-day baseline?
    Avoids divide-by-zero. Does NOT automatically return 99 for zero baseline.

    baseline_weekly_rate = baseline_unique_animals / 4
    growth_ratio = recent_unique_animals / baseline_weekly_rate

    baseline=0 and recent>0 → treat as 'new emerging signal', moderate score.
    """
    if recent_unique_animals <= 0:
        return 0

    baseline_weekly_rate = baseline_unique_animals / 4.0

    if baseline_weekly_rate <= 0:
        # New/emerging disease — no history. Scales from 40 (1-2 animals) up to 100 as cluster grows.
        if recent_unique_animals <= 2:
            return 40
        return min(100, 40 + (recent_unique_animals - 2) * 12)

    growth_ratio = recent_unique_animals / baseline_weekly_rate

    # Score: ratio=1 (no growth) → ~0; ratio=3 → moderate; ratio>=6 → near max
    score = min(1.0, (growth_ratio - 1.0) / 5.0) * 100 if growth_ratio > 1.0 else 0.0
    return min(100, max(0, round(score)))

def calculate_farm_spread_score(unique_animals, unique_farms):
    """
    Score 0–100: how widely is the disease distributed across independent farms?
    1 farm → very low; 4+ farms → high.
    """
    if unique_farms <= 1:
        return max(0, (unique_animals - 1) * 5)  # tiny signal if >1 animal on same farm
    # Score based on farm count, capped
    score = min(1.0, (unique_farms - 1) / 5.0) * 100
    return min(100, max(0, round(score)))

# ---------------------------------------------------------------------------
# Decision logic OUTSIDE the model.
# The classifier only says "which disease, how confident". Everything below is
# explicit, reviewable rules that combine that output with case context.
# ---------------------------------------------------------------------------

# severity  -> clinical urgency for the individual animal before other factors
# priority  -> disease that authorities treat as reportable; verify this list
#              against the state's notified-disease schedule before deployment
DISEASE_PROFILE = {
    "Anthrax":                  {"severity": "Critical", "priority": True},
    "Hemorrhagic Septicemia":   {"severity": "High",     "priority": True},
    "Black Quarter / Blackleg": {"severity": "High",     "priority": True},
    "FMD":                      {"severity": "High",     "priority": True},
    "Lumpy Skin Disease":       {"severity": "Moderate", "priority": True},
    "Mastitis":                 {"severity": "Moderate", "priority": False},
    "Healthy":                  {"severity": "Low",      "priority": False},
}
TRIAGE_LEVELS = ["Low", "Moderate", "High", "Critical"]
# Reports saved before the model swap use the old class names; map them so old
# and new reports of the same disease still cluster together.
LEGACY_DISEASE_NAMES = {
    "Lumpy_Skin_Disease": "Lumpy Skin Disease",
    "Hemorrhagic_Septicemia": "Hemorrhagic Septicemia",
    "Black_Quarter": "Black Quarter / Blackleg",
    "Black Quarter": "Black Quarter / Blackleg",
    "Foot and Mouth Disease": "FMD",
    "Foot_and_Mouth_Disease": "FMD",
    "Foot-and-Mouth Disease": "FMD",
}


def canonical_disease(name):
    if not name:
        return name
    s = str(name).strip()
    return LEGACY_DISEASE_NAMES.get(s, s)


RED_FLAG_SYMPTOMS = {"Difficulty Breathing", "Dehydration"}


def assess_triage(disease, confidence, symptoms, outbreak_status=None):
    """Clinical urgency for THIS animal. Returns (level, reasons)."""
    if not disease:
        return "Unknown", ["Disease model did not return a prediction"]
    reasons = []
    level = TRIAGE_LEVELS.index(DISEASE_PROFILE.get(disease, {}).get("severity", "Moderate"))
    reasons.append(f"{disease} baseline severity: {TRIAGE_LEVELS[level]}")

    conf = confidence or 0
    if disease == "Healthy":
        if len(symptoms) >= 3 and conf < 60:
            level = max(level, 1)
            reasons.append(f"'Healthy' at only {conf:.0f}% with {len(symptoms)} symptoms - needs checking")
    elif conf < 40 and level > 1:
        level -= 1
        reasons.append(f"Low model confidence ({conf:.0f}%) - lowered one level, vet to confirm")

    flags = sorted(RED_FLAG_SYMPTOMS.intersection(symptoms))
    if flags and level < 2:
        level = 2
        reasons.append("Red-flag symptom: " + ", ".join(flags))
    if len(symptoms) >= 5 and level < 1:
        level = 1
        reasons.append(f"{len(symptoms)} symptoms reported")

    if outbreak_status == "CRITICAL" and level < 3:
        level = 3
        reasons.append("Part of a CRITICAL outbreak cluster")
    elif outbreak_status == "HIGH" and level < 2:
        level = 2
        reasons.append("Part of a HIGH outbreak cluster")
    return TRIAGE_LEVELS[level], reasons


def assess_escalation(disease, confidence, outbreak_status):
    """Does this need action beyond the assigned vet? Returns (bool, reason)."""
    conf = confidence or 0
    if outbreak_status in ("HIGH", "CRITICAL"):
        return True, f"{outbreak_status} outbreak cluster - district/government response needed"
    if disease == "Anthrax" and conf >= 50:
        return True, "Suspected anthrax - zoonotic, report immediately"
    if DISEASE_PROFILE.get(disease, {}).get("priority") and outbreak_status == "MODERATE":
        return True, f"Priority disease ({disease}) with an emerging cluster"
    return False, "Handled by the assigned veterinarian"


def calculate_outbreak_risk(c, disease, ref_lat, ref_lon, ref_village_name=None):
    """
    Outbreak Risk Score for one disease around one reference point, per
    GramVet_Outbreak_Logic.md:
        score = 0.35 spatial + 0.30 temporal + 0.20 growth + 0.15 farm spread
    Recent window: last 7 days. Baseline: the 28 days before that (day -35 to -7).
    Disease comes from the ML model's prediction stored on each report.
    """
    # Stored timestamps are IST strings (see now()); window bounds must be in the
    # same zone, since SQLite compares them as text.
    now_dt = datetime.fromisoformat(now())
    recent_start = now_dt - timedelta(days=7)
    baseline_start = now_dt - timedelta(days=35)

    disease = canonical_disease(disease)
    rows = c.execute("""
        SELECT hr.id report_id, hr.animal_id, hr.reported_at, hr.ai_prediction_json,
               cs.farmer_id,
               COALESCE(f.latitude, a.latitude, v.latitude) lat,
               COALESCE(f.longitude, a.longitude, v.longitude) lon,
               COALESCE(v.name, f.village, '') village_name,
               a.village_id village_id
        FROM health_reports hr
        JOIN cases cs ON cs.id = hr.case_id
        JOIN animals a ON a.id = hr.animal_id
        LEFT JOIN users f ON f.id = cs.farmer_id
        LEFT JOIN villages v ON v.id = a.village_id
        WHERE hr.reported_at >= ?
    """, (baseline_start.isoformat(),)).fetchall()

    def _disease_of(row):
        try:
            pred = json.loads(row["ai_prediction_json"] or "{}")
            if not isinstance(pred, dict):
                return None
            raw = pred.get("predicted_disease") or (((pred.get("predictions") or [{}])[0] or {}).get("disease"))
            return canonical_disease(raw)
        except Exception:
            return None

    def _in_radius(row):
        r_lat, r_lon = _coerce_latlon(row["lat"], row["lon"])
        c_lat, c_lon = _coerce_latlon(ref_lat, ref_lon)
        if None not in (r_lat, r_lon, c_lat, c_lon):
            if haversine(c_lat, c_lon, r_lat, r_lon) <= RADIUS_KM:
                return True
        return bool(ref_village_name and row["village_name"]
                    and row["village_name"].strip().lower() == ref_village_name.strip().lower())

    matching = [dict(r) for r in rows if _disease_of(r) == disease and _in_radius(r)]
    recent_start_s = recent_start.isoformat()
    recent = [r for r in matching if r["reported_at"] >= recent_start_s]
    baseline = [r for r in matching if r["reported_at"] < recent_start_s]

    recent_animals = _get_unique_animals(recent)
    recent_farms = _get_unique_farms(recent)
    n_animals = len(recent_animals)
    n_farms = len(recent_farms)
    n_baseline = len(_get_unique_animals(baseline))
    baseline_weekly_rate = n_baseline / 4.0

    # Per-village breakdown (unique animals / farms in each affected village)
    villages = {}
    for r in recent:
        key = r["village_id"]
        v = villages.setdefault(key, {"village_id": key,
                                      "village": (r["village_name"] or "").strip() or "Unknown/Unmapped",
                                      "_animals": set(), "_farms": set()})
        v["_animals"].add(r["animal_id"])
        v["_farms"].add(r["farmer_id"])
    village_list = sorted(
        ({"village_id": v["village_id"], "village": v["village"],
          "unique_animals": len(v["_animals"]), "unique_farms": len(v["_farms"])} for v in villages.values()),
        key=lambda x: -x["unique_animals"])
    n_villages = len(village_list)

    spatial_score = calculate_spatial_score(recent, ref_lat, ref_lon)
    temporal_score = calculate_temporal_score(recent, now_dt)
    growth_score = calculate_growth_score(n_animals, n_baseline)
    farm_spread_score = calculate_farm_spread_score(n_animals, n_farms)

    score = (0.35 * spatial_score + 0.30 * temporal_score +
             0.20 * growth_score + 0.15 * farm_spread_score)
    outbreak_risk_score = int(round(max(0, min(100, score))))

    # Minimum evidence gate: below it the score must stay in the LOW band.
    minimum_met = n_animals >= 3 and n_farms >= 2
    if not minimum_met:
        outbreak_risk_score = min(outbreak_risk_score, 29)

    if outbreak_risk_score >= 80:
        outbreak_status = "CRITICAL"
    elif outbreak_risk_score >= 60:
        outbreak_status = "HIGH"
    elif outbreak_risk_score >= 30:
        outbreak_status = "MODERATE"
    else:
        outbreak_status = "LOW"

    evidence = []
    if n_animals:
        evidence.append(f"{n_animals} unique animal{'s' if n_animals != 1 else ''} affected")
    if n_farms:
        evidence.append(f"{n_farms} different farm{'s' if n_farms != 1 else ''} affected")
    if n_animals and ref_lat is not None:
        evidence.append(f"Cases detected within {RADIUS_KM:g} km")
    if n_animals >= 2:
        evidence.append("Multiple cases detected within 7 days")
    if n_villages > 1:
        evidence.append(f"Cases detected across {n_villages} villages")
    if n_animals and n_baseline == 0:
        evidence.append("No cases in previous 28 days - new/emerging signal")
    elif n_baseline and n_animals > baseline_weekly_rate:
        evidence.append(f"Recent activity exceeds historical baseline ({baseline_weekly_rate:.1f}/week)")
    if not minimum_met:
        evidence.append("Minimum evidence not met (needs 3+ animals on 2+ farms) - kept LOW")

    return {
        "disease": disease,
        "outbreak_risk_score": outbreak_risk_score,
        "outbreak_status": outbreak_status,
        "recent_cases_7d": n_animals,
        "baseline_cases_28d": n_baseline,
        "baseline_weekly_rate": round(baseline_weekly_rate, 2),
        "growth_ratio": round(n_animals / baseline_weekly_rate, 2) if n_baseline else None,
        "unique_animals": n_animals,
        "unique_farms": n_farms,
        "unique_villages": n_villages,
        "village": village_list[0]["village"] if village_list else (ref_village_name or "Unknown/Unmapped"),
        "villages": village_list,
        "radius_km": RADIUS_KM,
        "time_window_days": 7,
        "spatial_score": spatial_score,
        "temporal_score": temporal_score,
        "growth_score": growth_score,
        "farm_spread_score": farm_spread_score,
        "minimum_evidence_met": minimum_met,
        "evidence": evidence,
    }

def _vets_for_village(c, village_id, primary_vet=None):
    """Return all vets responsible for farmers/cases in village_id so outbreak alerts
    always reach the actual assigned vet(s) and never miss a newly assigned vet."""
    vets_by_id = {}
    if primary_vet and primary_vet["id"] is not None:
        vets_by_id[primary_vet["id"]] = primary_vet
    if village_id is not None:
        for r in c.execute("""
            SELECT DISTINCT v.* FROM users v
            WHERE v.role='vet' AND (
                v.id IN (SELECT f.assigned_vet_id FROM users f JOIN animals a ON a.farmer_id=f.id WHERE a.village_id=? AND f.assigned_vet_id IS NOT NULL)
                OR v.id IN (SELECT cs.vet_id FROM cases cs JOIN animals a ON a.id=cs.animal_id WHERE a.village_id=? AND cs.status!='CLOSED' AND cs.vet_id IS NOT NULL)
                OR v.id IN (SELECT vv.vet_id FROM vet_villages vv WHERE vv.village_id=?)
            )
            ORDER BY v.id
        """, (village_id, village_id, village_id)).fetchall():
            vets_by_id[r["id"]] = r
        av = assigned_vet(c, village_id)
        if av and av["id"] not in vets_by_id:
            vets_by_id[av["id"]] = av
    return list(vets_by_id.values())


def create_outbreak_vet_alert(c, disease, village, vet, result, triage_level=None, case_id=None):
    """
    Village-specific outbreak alert for a responsible vet. Updates and marks unread
    whenever new outbreak activity or a risk-level change occurs so it pops up immediately.
    """
    disease = canonical_disease(disease)
    confirmed = vet_confirmed_outbreak(c, village["village_id"], disease)
    status = "CRITICAL" if confirmed else result["outbreak_status"]
    score = max(85, result["outbreak_risk_score"]) if confirmed else result["outbreak_risk_score"]
    t_lvl = triage_level or ("Critical" if status == "CRITICAL" else ("High" if status == "HIGH" else DISEASE_PROFILE.get(disease, {}).get("severity", "Moderate")))
    title = f"\u26a0\ufe0f POSSIBLE OUTBREAK \u2014 {disease} \u2014 {village['village']} ({status})"
    message = (
        f"{'Confirmed' if confirmed else 'Possible'} {disease} outbreak in {village['village']} village.\n"
        f"Outbreak Risk Level: {status} ({score}/100) | Clinical Severity: {t_lvl}\n"
        f"In {village['village']}: {village['unique_animals']} animal(s), {village['unique_farms']} farm(s)\n"
        f"Whole cluster: {result['unique_animals']} animals, {result['unique_farms']} farms, "
        f"{result['unique_villages']} village(s)\n"
        f"Detected within: {RADIUS_KM:g} km | Recent window: 7 days\n"
        f"Evidence: {'; '.join(result['evidence'])}\n"
        f"Review cases and follow veterinary outbreak control guidelines."
    )
    sev = "CRITICAL" if status == "CRITICAL" else "HIGH"
    data = {
        "adv": "vet_confirmed" if confirmed else "vet_outbreak",
        "disease": disease,
        "level": status,
        "outbreak_level": status,
        "triage_level": t_lvl,
        "village": village["village"],
        "score": score,
        "animals": village["unique_animals"],
        "farms": village["unique_farms"],
        "cluster_animals": result["unique_animals"],
        "cluster_farms": result["unique_farms"],
        "confirmed": confirmed,
        "case_id": case_id,
    }
    cutoff = (datetime.fromisoformat(now()) - timedelta(days=7)).isoformat()
    prefix = f"\u26a0\ufe0f POSSIBLE OUTBREAK \u2014 {disease} \u2014 {village['village']} ("
    existing = c.execute("""
        SELECT id, title FROM notifications
        WHERE user_id=? AND kind='OUTBREAK' AND village_id IS ? AND substr(title,1,?)=?
          AND created_at >= ?
        ORDER BY id DESC LIMIT 1
    """, (vet["id"], village["village_id"], len(prefix), prefix, cutoff)).fetchone()
    if existing:
        c.execute(
            "UPDATE notifications SET title=?, message=?, severity=?, case_id=COALESCE(?, case_id), data_json=?, created_at=?, read_at=NULL WHERE id=?",
            (title, message, sev, case_id, json.dumps(data), now(), existing["id"])
        )
        return
    create_notification(c, vet["id"], title, message, sev, "OUTBREAK", case_id, village["village_id"], data)


def run_outbreak_engine(c, disease, animal, ref_lat=None, ref_lon=None, triage_level=None, case_id=None, primary_vet=None):
    """
    Run after a new report is saved. Computes the Outbreak Risk Score and, for
    MODERATE/HIGH/CRITICAL (or a vet-confirmed outbreak), alerts all responsible vets
    and farmers in each affected village.
    """
    disease = canonical_disease(disease)
    if not disease or disease == "Healthy":
        return None
    vil_row = c.execute("SELECT * FROM villages WHERE id=?", (animal["village_id"],)).fetchone()
    ref_village = vil_row["name"] if vil_row else None
    if ref_lat is None or ref_lon is None:
        if animal.get("latitude") is not None and animal.get("longitude") is not None:
            ref_lat, ref_lon = animal["latitude"], animal["longitude"]
        elif vil_row and vil_row["latitude"] is not None:
            ref_lat, ref_lon = vil_row["latitude"], vil_row["longitude"]

    result = calculate_outbreak_risk(c, disease, ref_lat, ref_lon, ref_village)
    confirmed_here = vet_confirmed_outbreak(c, animal.get("village_id"), disease)
    if confirmed_here:
        result["outbreak_status"] = "CRITICAL"
        result["outbreak_risk_score"] = max(result["outbreak_risk_score"], 85)
        result["confirmed"] = True

    if result["outbreak_status"] == "LOW" and not confirmed_here:
        return result

    eff_triage, _ = assess_triage(disease, 80.0, [], result["outbreak_status"])
    use_triage = triage_level or eff_triage

    for village in result["villages"]:
        if village["village_id"] is None:
            continue
        confirmed = vet_confirmed_outbreak(c, village["village_id"], disease)
        for v_row in _vets_for_village(c, village["village_id"], primary_vet if village["village_id"] == animal.get("village_id") else None):
            create_outbreak_vet_alert(c, disease, village, v_row, result, triage_level=use_triage, case_id=case_id)
        notify_farmer_outbreak_advisory(
            c, disease, village["village_id"], village["village"],
            "CRITICAL" if confirmed else result["outbreak_status"],
            result["outbreak_risk_score"], confirmed,
            case_id=case_id, triage_level=use_triage, resurface=True
        )

    # Government users (deduplicated per disease + village)
    score, status = result["outbreak_risk_score"], result["outbreak_status"]
    v_name = result["village"]
    gov_cutoff = (datetime.fromisoformat(now()) - timedelta(days=7)).isoformat()
    for g in c.execute("SELECT id FROM users WHERE role='government'").fetchall():
        existing_gov = c.execute("""
            SELECT 1 FROM notifications
            WHERE user_id=? AND kind='OUTBREAK' AND title=? AND created_at >= ? AND read_at IS NULL
            LIMIT 1
        """, (g["id"], f"\u26a0\ufe0f Outbreak Risk \u2014 {disease} \u2014 {v_name}", gov_cutoff)).fetchone()
        if not existing_gov:
            create_notification(
                c, g["id"], f"\u26a0\ufe0f Outbreak Risk \u2014 {disease} \u2014 {v_name}",
                f"Possible {disease} outbreak in {v_name}. Risk: {status} ({score}/100). "
                f"Animals: {result['unique_animals']}, Farms: {result['unique_farms']}.",
                "CRITICAL" if status == "CRITICAL" else "HIGH", "OUTBREAK", case_id, animal.get("village_id"))

    return result


def haversine(lat1,lon1,lat2,lon2):
    r=6371.0
    p1,p2=math.radians(lat1),math.radians(lat2)
    dphi=math.radians(lat2-lat1); dl=math.radians(lon2-lon1)
    x=math.sin(dphi/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(x))



def nearest_village_ids(c, source):
    if source["latitude"] is None or source["longitude"] is None:
        return [source["id"]]
    ids=[]
    for v in c.execute("SELECT * FROM villages"):
        if v["latitude"] is None or v["longitude"] is None: 
            continue
        if haversine(source["latitude"],source["longitude"],v["latitude"],v["longitude"]) <= RADIUS_KM:
            ids.append(v["id"])
    return ids or [source["id"]]

@app.route("/")
def home():
    init()
    return render_template("index.html", symptoms=SYMPTOMS)

@app.get("/api/me")
def api_me():
    c=conn(); u=current_user(c); c.close()
    if not u: return jsonify(authenticated=False,user=None)
    return jsonify(authenticated=True,user={
        k:u[k] for k in ["id","name","phone","role","pincode","village","ward","address","latitude","longitude","district","state"] if k in u.keys()
    })

@app.get("/api/pincode_lookup")
def pincode_lookup():
    """Resolve a 6-digit Indian pincode to village/district/state and lat/lng via India Post API + Nominatim + Open-Meteo + Fallback."""
    pin = (request.args.get("pincode") or "").strip()
    if not pin or len(pin) != 6 or not pin.isdigit():
        return jsonify(error="Invalid pincode. Must be a 6-digit number."), 400
    result = {"pincode": pin, "places": [], "latitude": None, "longitude": None}
    # 1. Try India Post API
    try:
        r = requests.get(f"https://api.postalpincode.in/pincode/{pin}", timeout=4)
        data = r.json()
        if data and data[0].get("Status") == "Success":
            post_offices = data[0].get("PostOffice") or []
            for po in post_offices:
                result["places"].append({
                    "name": po.get("Name", ""),
                    "district": po.get("District", ""),
                    "state": po.get("State", ""),
                    "division": po.get("Division", ""),
                    "region": po.get("Region", ""),
                    "block": po.get("Block", ""),
                    "branch_type": po.get("BranchType", ""),
                })
            if post_offices:
                first = post_offices[0]
                result["district"] = first.get("District", "")
                result["state"] = first.get("State", "")
                result["division"] = first.get("Division", "")
                result["block"] = first.get("Block", "")
    except Exception:
        pass
    # 2. Get lat/lng via Nominatim for this pincode
    try:
        q = requests.utils.quote(f"{pin}, India")
        r = requests.get(
            f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1&addressdetails=1",
            headers={"User-Agent": "GramVetApp/2.0"}, timeout=3.5
        )
        geo = r.json()
        if geo and isinstance(geo, list) and len(geo) > 0:
            result["latitude"] = float(geo[0]["lat"])
            result["longitude"] = float(geo[0]["lon"])
            addr = geo[0].get("address", {})
            if not result.get("state"):
                result["state"] = addr.get("state", "")
            if not result.get("district"):
                result["district"] = addr.get("county") or addr.get("state_district") or ""
            if not result["places"]:
                place_name = addr.get("village") or addr.get("town") or addr.get("suburb") or addr.get("city") or ""
                if place_name:
                    result["places"].append({
                        "name": place_name,
                        "district": result["district"],
                        "state": result["state"]
                    })
    except Exception:
        pass
    # 3. Fallback if Nominatim did not resolve coordinates for numeric pincode
    if result["latitude"] is None or result["longitude"] is None:
        first_place = result["places"][0]["name"] if result["places"] else (result.get("block") or result.get("district") or "")
        la, lo = get_maharashtra_coords(first_place, pin)
        if la is not None and lo is not None:
            result["latitude"] = la
            result["longitude"] = lo
    return jsonify(result)


@app.get("/api/reverse_geocode")
def api_reverse_geocode():
    """Server-side reverse geocode fallback for browser GPS capture."""
    lat, lon = _coerce_latlon(request.args.get("lat"), request.args.get("lon"))
    if lat is None or lon is None:
        return jsonify(error="Invalid coordinates"), 400
    out = {"village": "", "pincode": "", "district": "", "state": "", "address": ""}
    try:
        r = requests.get(
            f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&addressdetails=1",
            headers={"User-Agent": "GramVetApp/2.0"}, timeout=4
        )
        if r.ok:
            data = r.json()
            a = data.get("address") or {}
            out["village"] = a.get("village") or a.get("hamlet") or a.get("town") or a.get("suburb") or a.get("city") or a.get("county") or ""
            out["pincode"] = a.get("postcode") or ""
            out["district"] = a.get("county") or a.get("state_district") or ""
            out["state"] = a.get("state") or ""
            out["address"] = str(data.get("display_name") or "").replace(", India", "")
            if out["village"] or out["pincode"]:
                return jsonify(out)
    except Exception:
        pass
    try:
        r = requests.get(
            f"https://api.bigdatacloud.net/data/reverse-geocode-client?latitude={lat}&longitude={lon}&localityLanguage=en",
            headers={"User-Agent": "GramVetApp/2.0"}, timeout=4
        )
        if r.ok:
            data = r.json()
            out["village"] = data.get("locality") or data.get("city") or ""
            out["pincode"] = data.get("postcode") or ""
            out["district"] = data.get("principalSubdivision") or ""
            out["state"] = data.get("principalSubdivision") or ""
            out["address"] = ", ".join(x for x in [out["village"], out["district"], out["state"]] if x)
    except Exception:
        pass
    return jsonify(out)


@app.post("/api/signup")
def signup():
    d=json_body()
    name=(d.get("name") or "").strip()
    phone_formatted = validate_and_format_phone(d.get("phone"))
    if not phone_formatted:
        return jsonify(error="Mobile number must be a 10-digit number starting with 9, 8, 7, or 6."),400
    phone = phone_formatted
    password=d.get("password") or ""
    role=d.get("role")
    if not name or not isinstance(password, str) or len(password)<4 or role not in ("farmer","vet","government"):
        return jsonify(error="Complete all required fields. Password must be at least 4 digits/characters."),400
    if role=="vet" and not d.get("vet_auth_id"):
        return jsonify(error="Government authorisation ID is required for vet accounts."),400
    if role=="government":
        gov_code=os.environ.get("GOVERNMENT_AUTH_CODE","GOV-26128")
        if d.get("govt_auth_id") != gov_code:
            return jsonify(error="Invalid government authorisation code."),403
    c=conn()
    try:
        pw_hashed = hp(password)
        c.execute("""
          INSERT INTO users(name,phone,password_hash,pw_migrated,role,pincode,village,ward,address,vet_auth_id,govt_auth_id,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,(name,phone,pw_hashed,1,role,d.get("pincode"),d.get("village"),d.get("ward"),
             d.get("address"),d.get("vet_auth_id"),d.get("govt_auth_id"),now()))
        uid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        lat, lon = _coerce_latlon(d.get("latitude"), d.get("longitude"))
        if lat is None or lon is None:
            lat, lon = get_maharashtra_coords(d.get("village") or d.get("address",""), d.get("pincode",""))
        if (lat is None or lon is None) and role in ("farmer","vet"):
            c.execute("DELETE FROM users WHERE id=?", (uid,)); c.commit()
            return jsonify(error="Location could not be determined. Use GPS, pick the spot on the map, or enter a valid pincode."),400
        if lat is not None and lon is not None:
            c.execute("UPDATE users SET latitude=?,longitude=? WHERE id=?", (lat, lon, uid))
        district = d.get("district", "")
        state = d.get("state", "")
        if district or state:
            c.execute("UPDATE users SET district=?,state=? WHERE id=?", (district, state, uid))
        if d.get("village"):
            village_for(c, d["village"].strip(), d.get("pincode"), lat, lon)
        if role == "farmer" and lat is not None and lon is not None:
            nearest = assigned_vet_by_gps(c, lat, lon)
            if nearest:
                c.execute("UPDATE users SET assigned_vet_id=? WHERE id=?", (nearest["id"], uid))
                if d.get("village"):
                    vrow = village_for(c, d["village"].strip(), d.get("pincode"), lat, lon)
                    c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)",
                              (nearest["id"], vrow["id"]))
        if role == "vet" and lat is not None and lon is not None:
            ensure_vet_stock(c, uid)
            _reassign_farmers_to_vet(c, uid, lat, lon)
        reconcile_farmer_vet_assignments(c, notify=False)
        c.commit()
        session["uid"] = uid
        session["phone"] = phone
        session.permanent = True
        u_full = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        u_public = {k: u_full[k] for k in ["id","name","phone","role","pincode","village","ward","address","latitude","longitude","district","state","assigned_vet_id"] if k in u_full.keys()}
        mongo_upsert("users", {
            "_id": uid,
            **dict(u_full)
        })
        if role == "vet":
            # Immediately sync reassigned farmers, cases, and vet_villages to MongoDB
            trigger_mongo_sync(tables=("users", "cases", "vet_villages", "notifications", "vet_vaccine_stock"), wait_timeout=0.0)
        return jsonify(ok=True,user=u_public)
    except sqlite3.IntegrityError:
        return jsonify(error="This phone number is already registered. Please login."),409
    finally:
        c.close()

@app.post("/api/login")
def login():
    d=json_body()
    phone_formatted = validate_and_format_phone(d.get("phone"))
    if not phone_formatted:
        return jsonify(error="Mobile number must be a 10-digit number starting with 9, 8, 7, or 6."),400
    phone = phone_formatted
    password=d.get("password") or ""
    if not isinstance(password, str) or len(password) < 4:
        return jsonify(error="Password must be at least 4 digits/characters."),400
    c=conn()
    try:
        canon_hash = hp(password)
        env_hash = hashlib.sha256((password + str(app.secret_key or "")).encode()).hexdigest()
        raw_hash = hashlib.sha256(password.encode()).hexdigest()
        valid_hashes = {canon_hash, env_hash, raw_hash}

        # Guarantee demo accounts (+919000000001, +919000000002, +919000000003) always work with 12345
        demo_Fixed = {
            "+919000000001": ("Government Official", "government", "506001", None, None, "District Livestock Health Office", None, "GOV-26128"),
            "+919000000002": ("Dr. Veterinarian", "vet", "506001", "Kothapalli", None, "Government Veterinary Centre", "VET-001", None),
            "+919000000003": ("Demo Farmer", "farmer", "506001", "Kothapalli", "Ward 1", "Farmer House", None, None),
        }
        if phone in demo_Fixed and password == "12345":
            u = c.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
            meta = demo_Fixed[phone]
            if not u:
                c.execute("""
                    INSERT INTO users(name,phone,password_hash,pw_migrated,role,pincode,village,ward,address,vet_auth_id,govt_auth_id,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """, (meta[0], phone, canon_hash, 1, meta[1], meta[2], meta[3], meta[4], meta[5], meta[6], meta[7], now()))
                c.commit()
                u = c.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
            elif u["password_hash"] != canon_hash or u["role"] != meta[1]:
                c.execute("UPDATE users SET password_hash=?, pw_migrated=1, role=? WHERE id=?", (canon_hash, meta[1], u["id"]))
                c.commit()
                u = c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
            reconcile_farmer_vet_assignments(c, notify=False)
            c.commit()
            u = c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
            mongo_upsert("users", {"_id": u["id"], **dict(u)})
            session["uid"] = u["id"]
            session["phone"] = u["phone"]
            session.permanent = True
            return jsonify(ok=True,user={k:u[k] for k in ["id","name","phone","role","pincode","village","ward","address","latitude","longitude","district","state","assigned_vet_id"] if k in u.keys()})

        u = c.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        if not u and MONGO_URI:
            hydrate_sqlite_from_mongo(c, incremental=False)
            c.commit()
            u = c.execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        if not u:
            return jsonify(error="Incorrect phone number or password."),401

        stored_hash = str(u["password_hash"] or "")
        pw_mig = int(u["pw_migrated"] or 0) if "pw_migrated" in u.keys() else 0

        if stored_hash in valid_hashes:
            if stored_hash != canon_hash or pw_mig != 1:
                c.execute("UPDATE users SET password_hash=?, pw_migrated=1 WHERE id=?", (canon_hash, u["id"]))
                c.commit()
                u = c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
                mongo_upsert("users", {"_id": u["id"], **dict(u)})
        elif not stored_hash or pw_mig == 0:
            # Account was created prior to unified salt migration (e.g. missing hash in MongoDB or
            # salted with a different Render service's random SECRET_KEY). Migrate to canonical hash.
            c.execute("UPDATE users SET password_hash=?, pw_migrated=1 WHERE id=?", (canon_hash, u["id"]))
            c.commit()
            u = c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
            mongo_upsert("users", {"_id": u["id"], **dict(u)})
        else:
            return jsonify(error="Incorrect phone number or password."),401

        reconcile_farmer_vet_assignments(c, notify=False)
        c.commit()
        u = c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
        session["uid"] = u["id"]
        session["phone"] = u["phone"]
        session.permanent = True
        return jsonify(ok=True,user={k:u[k] for k in ["id","name","phone","role","pincode","village","ward","address","latitude","longitude","district","state","assigned_vet_id"] if k in u.keys()})
    finally:
        c.close()

@app.post("/api/logout")
def logout():
    session.clear(); return jsonify(ok=True)

@app.get("/api/animals")
@require_role("farmer","vet","government")
def animals(u,c):
    out=[]
    rows = accessible_animals(c,u)
    if c.in_transaction:
        c.commit()
    for a in rows:
        d=dict(a)
        d["latest"]=latest_report(c,a["id"])
        ac = active_case(c,a["id"])
        d["active_case"]=dict(ac) if ac else None
        v_row = vet_for_farmer(c, a["farmer_id"], a["village_id"])
        d["assigned_vet"]=dict(v_row) if v_row else None
        out.append(d)
    if c.in_transaction:
        c.commit()
    return jsonify(animals=out)

@app.post("/api/animals")
@require_role("farmer")
def add_animal(u,c):
    d=json_body()
    if any(not str(d.get(k) or "").strip() for k in ["tag","name","species","village","ward"]):
        return jsonify(error="Tag, animal name, species, village and ward are required."),400
    if d["species"] not in ALLOWED_SPECIES:
        return jsonify(error="Only Cattle (Cow) and Buffalo are supported by this model."),400
    
    # If animal location not provided, use farmer's registered location.
    animal_lat, animal_lon = _coerce_latlon(d.get("latitude"), d.get("longitude"))
    if animal_lat is None or animal_lon is None:
        animal_lat, animal_lon = _coerce_latlon(u["latitude"], u["longitude"])
    
    v=village_for(c,d["village"].strip(),d.get("pincode") or u["pincode"],animal_lat,animal_lon)
    try:
        sex_val = d.get("sex") if d.get("sex") in ("Male","Female") else "Female"
        created_ts = now()
        c.execute("""
          INSERT INTO animals(tag,name,species,breed,age,sex,farmer_id,village_id,ward,latitude,longitude,last_vaccinated,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(d["tag"].strip(),d["name"].strip(),d["species"],d.get("breed"),d.get("age"),sex_val,u["id"],v["id"],
             d["ward"],animal_lat,animal_lon,d.get("last_vaccinated"),created_ts))
        animal_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        reconcile_farmer_vet_assignments(c, notify=False)
        c.commit()
        # Sync complete animal record to MongoDB
        mongo_upsert("animals", {
            "_id": animal_id,
            "id": animal_id,
            "tag": d["tag"].strip(),
            "name": d["name"].strip(),
            "species": d["species"],
            "breed": d.get("breed"),
            "age": d.get("age"),
            "sex": sex_val,
            "farmer_id": u["id"],
            "village_id": v["id"],
            "ward": d["ward"],
            "latitude": animal_lat,
            "longitude": animal_lon,
            "last_vaccinated": d.get("last_vaccinated"),
            "created_at": created_ts
        })
        return jsonify(ok=True,animal_id=animal_id)
    except sqlite3.IntegrityError:
        return jsonify(error="Animal ID/tag already exists. Use the ID assigned to this livestock."),409

@app.put("/api/animals/<int:animal_id>")
@require_role("farmer")
def edit_animal(u,c,animal_id):
    a=c.execute("SELECT * FROM animals WHERE id=? AND farmer_id=?",(animal_id,u["id"])).fetchone()
    if not a: return jsonify(error="Animal not found or not owned by this farmer."),404
    d=json_body()
    # Identity is stable. Farmer may update basic registration fields, never the system ID.
    if any(k in d and not str(d.get(k) or "").strip() for k in ("name","ward","sex")):
        return jsonify(error="Animal name, ward and sex cannot be empty."),400
    if d.get("species") and d["species"] not in ALLOWED_SPECIES:
        return jsonify(error="Only Cattle and Buffalo are supported."),400
    new_lat, new_lon = _coerce_latlon(d.get("latitude", a["latitude"]), d.get("longitude", a["longitude"]))
    c.execute("""
      UPDATE animals SET name=?,breed=?,age=?,sex=?,ward=?,last_vaccinated=?,latitude=?,longitude=?
      WHERE id=?
    """,(d.get("name",a["name"]),d.get("breed",a["breed"]),d.get("age",a["age"]),d.get("sex",a["sex"]),d.get("ward",a["ward"]),
         d.get("last_vaccinated",a["last_vaccinated"]),new_lat,new_lon,animal_id))
    c.commit()
    updated_a = c.execute("SELECT * FROM animals WHERE id=?", (animal_id,)).fetchone()
    if updated_a:
        mongo_upsert("animals", {"_id": animal_id, **dict(updated_a)})
    return jsonify(ok=True)

def resolve_location(c, a, lat=None, lon=None):
    """Coordinates used for weather, model inputs and outbreak clustering.

    The farmer's REGISTERED location is the source of truth, so every report for
    a given farmer resolves to the same point. Live device GPS is not consulted.
    Order: farmer's registered point > the animal's own record > its village.
    """
    if a is not None:
        farmer = c.execute("SELECT latitude,longitude FROM users WHERE id=?",(a["farmer_id"],)).fetchone()
        if farmer:
            la, lo = _coerce_latlon(farmer["latitude"], farmer["longitude"])
            if la is not None and lo is not None:
                return la, lo, "farmer_registered_location"
        la, lo = _coerce_latlon(a["latitude"], a["longitude"])
        if la is not None and lo is not None:
            return la, lo, "animal_record"
        v = c.execute("SELECT * FROM villages WHERE id=?",(a["village_id"],)).fetchone()
        if v:
            la, lo = _coerce_latlon(v["latitude"], v["longitude"])
            if la is not None and lo is not None:
                return la, lo, "village_record"
    la, lo = _coerce_latlon(lat, lon)
    if la is not None and lo is not None:
        return la, lo, "farmer_registered_location"
    return None, None, "unavailable"


def resolve_weather_location(c, u=None, a=None, req_lat=None, req_lon=None):
    """Authoritative location resolver for weather detection.
    Prioritizes farmer's registered GPS -> animal record -> village record ->
    other animals of farmer -> farmer/village pincode & village name fallback."""
    lat, lon, source = resolve_location(c, a) if a is not None else (None, None, "unavailable")
    if (lat is None or lon is None) and u is not None:
        la, lo = _coerce_latlon(u["latitude"] if "latitude" in u.keys() else None,
                                 u["longitude"] if "longitude" in u.keys() else None)
        if la is not None and lo is not None:
            return la, lo, "farmer_registered_location"
    if (lat is None or lon is None) and a is not None:
        other = c.execute(
            "SELECT latitude, longitude FROM animals WHERE farmer_id=? AND latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 1",
            (a["farmer_id"],)
        ).fetchone()
        if other:
            la, lo = _coerce_latlon(other["latitude"], other["longitude"])
            if la is not None and lo is not None:
                return la, lo, "farmer_registered_location"
    if lat is None or lon is None:
        la, lo = _coerce_latlon(req_lat, req_lon)
        if la is not None and lo is not None:
            return la, lo, "farmer_registered_location"
    if lat is None or lon is None:
        v_name, pin = "", ""
        if a is not None:
            v_row = c.execute("SELECT name, pincode FROM villages WHERE id=?", (a["village_id"],)).fetchone()
            if v_row:
                v_name, pin = v_row["name"] or "", v_row["pincode"] or ""
            f_row = c.execute("SELECT village, pincode, phone FROM users WHERE id=?", (a["farmer_id"],)).fetchone()
            if f_row:
                v_name = v_name or (f_row["village"] or "")
                pin = pin or (f_row["pincode"] or "")
        elif u is not None:
            v_name = (u["village"] if "village" in u.keys() else "") or ""
            pin = (u["pincode"] if "pincode" in u.keys() else "") or ""
        la, lo = _coords_from_pincode_fallback(pin, v_name)
        if la is None or lo is None:
            la, lo = get_maharashtra_coords(v_name, pin)
        if la is not None and lo is not None:
            return la, lo, "farmer_registered_location"
    return lat, lon, source


@app.get("/api/weather")
@require_role("farmer","vet","government")
def weather_preview(u,c):
    req_lat = request.args.get("lat", type=float)
    req_lon = request.args.get("lon", type=float)
    animal_id = request.args.get("animal_id", type=int)
    a = c.execute("SELECT * FROM animals WHERE id=?", (animal_id,)).fetchone() if animal_id else None
    lat, lon, source = resolve_weather_location(c, u=u, a=a, req_lat=req_lat, req_lon=req_lon)
    if lat is None or lon is None:
        return jsonify(error="Location unavailable. Register your location to enable weather."),400
    wx = fetch_weather(lat, lon)
    wx["location_source"] = source if source != "unavailable" else "farmer_registered_location"
    return jsonify(weather=wx)


@app.post("/api/extract_symptoms")
def extract_symptoms():
    d = json_body()
    text = (d.get("text") or "").strip()
    if not text:
        return jsonify(symptoms=[])

    # First try LLM if Gemini key is available
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        try:
            import requests
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
            prompt = f"Extract the exact symptoms mentioned in the following text. You MUST return ONLY a JSON list of strings exactly matching the official symptom list. If none match, return [].\nText: '{text}'\nOfficial List: {SYMPTOMS}"
            resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
            if resp.ok:
                resp_json = resp.json()
                content = resp_json["candidates"][0]["content"]["parts"][0]["text"]
                
                # Simple extraction of the JSON array from response
                import re
                match = re.search(r"\[.*?\]", content, re.DOTALL)
                if match:
                    llm_syms = json.loads(match.group(0))
                    valid_syms = [s for s in llm_syms if s in SYMPTOMS]
                    return jsonify(symptoms=valid_syms, method="gemini")
        except Exception as e:
            print("LLM Extraction failed, falling back to dict:", e)

    # Fallback to comprehensive dictionary matching
    synonymMap = {
        "Fever / High Body Temperature": ["fever", "bukhar", "taap", "garam", "hot", "बुखार", "ताप", "गरम", "high temperature", "tez bukhar", "kadak taap", "तेज बुखार", "कडक ताप", "बुख़ार", "फीवर", "तापमान"],
        "Cough": ["cough", "khansi", "khokla", "खांसी", "खोकला", "कफ", "खाँसी", "कास"],
        "Nasal Discharge": ["nasal", "naak", "sardi", "नाक बहना", "नाक गळणे", "सर्दी", "नाक"],
        "Difficulty Breathing": ["breathing", "saans", "shwas", "dhaap", "सांस", "श्वास", "धाप"],
        "Reduced Appetite": ["appetite", "bhook", "kha nahi", "chara", "भूख", "भूक", "चारा", "खा नहीं"],
        "Weakness / Lethargy": ["weakness", "lethargy", "kamzor", "sust", "थकान", "कमजोर", "सुस्ती", "अशक्त", "थकवा"],
        "Diarrhea": ["diarrhoea", "diarrhea", "dast", "loose motion", "julab", "दस्त", "जुलाब", "संडास", "डायरिया", "डायरीया", "हगवण", "पातळ शेण", "पतला गोबर"],
        "Dehydration": ["dehydration", "pani", "tahan", "पानी", "तहान", "डिहाइड्रेशन", "डीहाइड्रेशन", "पाणी कमी"],
        "Excessive Salivation": ["saliva", "drool", "lar", "laar", "thook", "लार", "लाळ", "थूक"],
        "Mouth Lesions / Sores": ["mouth", "chhale", "tond", "muh", "छाले", "मुंह", "तोंड", "जख्म"],
        "Lameness / Difficulty Walking": ["lame", "limp", "langda", "chalne", "लंगड़ा", "लंगड", "चाल"],
        "Swelling": ["swelling", "sujan", "suj", "सूजन", "सूज", "फुगीर", "सवेलिंग"],
        "Skin Lesions / Rash": ["rash", "chakatte", "pural", "khaj", "चकत्ते", "पुरळ", "दाने", "खाज"],
        "Eye Discharge / Redness": ["eye", "aankh", "dole", "lal", "आंख", "डोळे", "लाल"],
        "Abnormal Milk Production": ["milk", "doodh", "dudh", "दूध", "दूध कमी", "दूध नहीं"],
        "Abortion / Reproductive Problem": ["abortion", "garbhpat", "pillu", "गर्भपात", "प्रजनन", "पिल्लू"],
        "Weight Loss": ["weight", "vazan", "wazan", "barik", "वजन", "बारीक", "दुबला"],
        "Reduced Rumination": ["ruminate", "jugali", "ravanth", "जुगाली", "रवंथ", "चावना", "रवंथ कमी"],
        "Ticks / External Parasites": ["tick", "killi", "gochid", "parjivi", "किल्ली", "गोचिड", "परजीवी", "जूं", "कीड़े"]
    }
    
    text_lower = text.lower()
    matched = []
    for sym in SYMPTOMS:
        kws = synonymMap.get(sym, [sym.lower()])
        for kw in kws:
            if kw in text_lower:
                matched.append(sym)
                break
    
    return jsonify(symptoms=matched, method="dictionary")

# ---- Voice recordings: attached to a report for the vet to play back (the server does not analyse them) ----
VOICE_FORMATS = {                                # data-URL type -> (file extension, mime type)
    "audio/webm": ("webm", "audio/webm"), "audio/ogg": ("ogg", "audio/ogg"), "audio/wav": ("wav", "audio/wav"),
    "audio/mp3": ("mp3", "audio/mp3"), "audio/mpeg": ("mp3", "audio/mpeg"), "audio/aac": ("aac", "audio/aac"),
    "audio/flac": ("flac", "audio/flac"), "audio/mp4": ("m4a", "audio/m4a"), "audio/m4a": ("m4a", "audio/m4a"),
}

def _decode_audio(data_url):
    """(type, bytes) from 'data:audio/webm;base64,...'. A bare base64 string is treated as webm (the old default)."""
    head, b64 = (data_url.split(",", 1) if data_url.startswith("data:") and "," in data_url else ("data:audio/webm;base64", data_url))
    mime = head[5:].split(";")[0].strip().lower()
    if mime not in VOICE_FORMATS:
        raise ValueError(f"unsupported audio type {mime!r}")
    return mime, base64.b64decode(b64, validate=True)


@app.post("/api/cases/report")
@require_role("farmer")
def report_case(u,c):
    d=json_body()
    reported_at=_client_reported_at(d.get("submitted_at"))
    aid=d.get("animal_id")
    a=c.execute("SELECT * FROM animals WHERE id=? AND farmer_id=?",(aid,u["id"])).fetchone()
    if not a: return jsonify(error="Animal not found."),404
    symptoms=d.get("symptoms",[])
    notes=d.get("notes")
    if not isinstance(symptoms,list) or not symptoms or len(symptoms)>20 or any(s not in SYMPTOMS for s in symptoms):
        return jsonify(error="Select valid symptoms from the provided 20 model inputs."),400
    report_inputs=_clean_report_inputs(d)
    # Location is ALWAYS the registered one (farmer/animal record), never live
    # device GPS. Any latitude/longitude posted by the client is deliberately
    # ignored so that weather, the model inputs and outbreak clustering all use
    # one stable point per farmer.
    lat,lon,loc_source=resolve_location(c,a)
    w_lat,w_lon,w_source=resolve_weather_location(c,u=u,a=a,req_lat=d.get("latitude"),req_lon=d.get("longitude"))

    # Pull weather for that registered point.
    weather=fetch_weather(w_lat,w_lon)
    wx_override = d.get("weather_override")
    if isinstance(wx_override, dict) and wx_override.get("temperature_c") is not None and wx_override.get("humidity_pct") is not None:
        if not weather.get("available") or weather.get("temperature_c") is None or weather.get("source") == "imd-regional-estimate":
            try:
                weather = {
                    "available": True,
                    "temperature_c": round(float(wx_override["temperature_c"]), 1),
                    "humidity_pct": round(float(wx_override["humidity_pct"]), 1),
                    "rainfall_mm": round(max(0.0, float(wx_override.get("rainfall_mm") or 0.0)), 1),
                    "season": weather.get("season") or get_season_india(),
                    "latitude": w_lat,
                    "longitude": w_lon,
                    "source": wx_override.get("source") or "open-meteo",
                    "fetched_at": now(),
                }
            except Exception:
                pass
    weather["location_source"]=w_source if w_source != "unavailable" else loc_source

    # 1) MODEL: disease + confidence only.
    prediction=call_disease_model(a,symptoms,weather,c,report_inputs)
    current_disease = prediction.get("predicted_disease")
    confidence = prediction.get("confidence")

    # One open case per animal. Unique index + re-read after race.
    existing=active_case(c,a["id"])
    case_id=existing["id"] if existing else None
    if not case_id:
        try:
            c.execute(
                "INSERT INTO cases(animal_id,farmer_id,vet_id,status,opened_at) VALUES(?,?,?,?,?)",
                (a["id"],u["id"],None,"OPEN",reported_at),
            )
            case_id=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        except sqlite3.IntegrityError:
            existing=active_case(c,a["id"])
            if not existing:
                raise
            case_id=existing["id"]
    # Reconcile closest GPS vet first so the vet inside the report always matches
    # the vet shown on the farmer's livestock table (/api/animals).
    reconcile_farmer_vet_assignments(c, notify=False)
    vet = vet_for_farmer(c, u["id"], a["village_id"])
    if vet and u["assigned_vet_id"] != vet["id"]:
        c.execute("UPDATE users SET assigned_vet_id=? WHERE id=?", (vet["id"], u["id"]))
    if vet:
        c.execute("UPDATE cases SET vet_id=?,status='OPEN' WHERE id=?",(vet["id"],case_id))
        c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)", (vet["id"], a["village_id"]))

    audio_url = None
    if d.get("audio_base64"):
        try:
            audio_type, audio_data = _decode_audio(d["audio_base64"])
            filename = f"audio_{uuid.uuid4().hex[:8]}.{VOICE_FORMATS[audio_type][0]}"
            audio_dir = os.path.join(BASE, "media", "audio")
            os.makedirs(audio_dir, exist_ok=True)
            filepath = os.path.join(audio_dir, filename)
            with open(filepath, "wb") as f:
                f.write(audio_data)
            audio_url = f"/media/audio/{filename}"
        except Exception as e:
            print("Error saving audio:", e)

    # 2) Save the report BEFORE the outbreak engine runs, so this case is counted
    #    in its own cluster. (Previously the engine ran first and always missed it.)
    c.execute("""
      INSERT INTO health_reports(case_id,animal_id,symptoms_json,notes,reported_at,ai_prediction_json,ai_model_version,weather_json,report_inputs_json,audio_url)
      VALUES(?,?,?,?,?,?,?,?,?,?)
    """,(case_id,a["id"],json.dumps(symptoms),notes,reported_at,json.dumps(prediction),prediction.get("model_version","unknown"),json.dumps(weather),json.dumps(report_inputs),audio_url))
    report_id=c.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 3) OUTBREAK + TRIAGE + ESCALATION: compute outbreak risk and clinical urgency together.
    current_disease = canonical_disease(current_disease)
    vil_row = c.execute("SELECT * FROM villages WHERE id=?", (a["village_id"],)).fetchone()
    vil_name = (vil_row["name"] if vil_row and vil_row["name"] else None) or u["village"] or "your assigned village"
    confirmed_ob = vet_confirmed_outbreak(c, a["village_id"], current_disease) if (current_disease and current_disease != "Healthy") else False

    outbreak_result = None
    if current_disease and current_disease != "Healthy":
        try:
            outbreak_result = calculate_outbreak_risk(c, current_disease, lat, lon, vil_name)
            if confirmed_ob and outbreak_result:
                outbreak_result["outbreak_status"] = "CRITICAL"
                outbreak_result["outbreak_risk_score"] = max(outbreak_result["outbreak_risk_score"], 85)
                outbreak_result["confirmed"] = True
        except Exception as _obe:
            print(f"[GramVet] Outbreak risk calculation error (non-fatal): {_obe}", flush=True)

    ob_status = "CRITICAL" if confirmed_ob else (outbreak_result["outbreak_status"] if outbreak_result else ("LOW" if current_disease else "UNKNOWN"))
    ob_score = max(85, outbreak_result["outbreak_risk_score"]) if (confirmed_ob and outbreak_result) else (85 if confirmed_ob else (outbreak_result["outbreak_risk_score"] if outbreak_result else 0))

    # 4) TRIAGE + ESCALATION: rules over the model output and the outbreak result.
    triage, triage_reasons = assess_triage(current_disease, confidence, symptoms, ob_status)
    escalate, escalation_reason = assess_escalation(current_disease, confidence, ob_status)

    prediction.update({
        "predicted_disease": current_disease,
        "triage_risk": triage,
        "triage_reasons": triage_reasons,
        "outbreak_status": ob_status,
        "outbreak_risk_score": ob_score if current_disease else None,
        "outbreak_probability": ob_score / 100.0 if current_disease else None,
        "outbreak": outbreak_result,
        "escalate": escalate,
        "escalation_reason": escalation_reason,
    })
    c.execute("UPDATE health_reports SET ai_prediction_json=? WHERE id=?", (json.dumps(prediction), report_id))

    if current_disease and current_disease != "Healthy":
        try:
            run_outbreak_engine(c, current_disease, dict(a), ref_lat=lat, ref_lon=lon,
                                triage_level=triage, case_id=case_id, primary_vet=vet)
        except Exception as _obe:
            print(f"[GramVet] Outbreak engine error (non-fatal): {_obe}", flush=True)

    # 5) Notifications, now that the full assessment (disease, severity, outbreak risk) is known.
    if vet:
        disease = current_disease or "possible livestock illness"
        conftxt = f" ({confidence:.0f}%)" if isinstance(confidence, (int, float)) else ""
        vet_sev = "CRITICAL" if (triage == "Critical" or ob_status == "CRITICAL") else ("HIGH" if (triage == "High" or ob_status in ("MODERATE", "HIGH")) else "INFO")
        vet_case_data = {
            "adv": "vet_case",
            "disease": current_disease,
            "level": triage,
            "outbreak_level": ob_status,
            "score": ob_score if current_disease else None,
            "confirmed": confirmed_ob,
            "confidence": round(float(confidence), 1) if isinstance(confidence, (int, float)) else None,
            "animal": a["name"],
            "animal_tag": a["tag"],
            "species": a["species"],
            "farmer": u["name"],
            "village": vil_name,
            "case_id": case_id,
            "escalate": escalate,
            "escalation_reason": escalation_reason,
            "triage_reasons": triage_reasons,
            "symptoms": symptoms,
            "animals": outbreak_result["unique_animals"] if outbreak_result else 1,
            "farms": outbreak_result["unique_farms"] if outbreak_result else 1,
            "cluster_animals": outbreak_result["unique_animals"] if outbreak_result else 1,
            "cluster_farms": outbreak_result["unique_farms"] if outbreak_result else 1,
        }
        # Supersede any prior unread CASE notification for this same case so the latest severity & outbreak risk pops up first
        c.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND kind='CASE' AND case_id=? AND read_at IS NULL",
                  (now(), vet["id"], case_id))
        create_notification(
            c, vet["id"], "New livestock health case",
            f"{u['name']} reported {a['name']} ({a['species']}) in {vil_name}. "
            f"Model: {disease}{conftxt}. Triage: {triage}. Outbreak risk: {ob_status} ({ob_score}/100). Review the case.",
            vet_sev, "CASE", case_id, a["village_id"],
            vet_case_data if current_disease else None
        )

    # Farmer disease + outbreak advisory for this report (suspected disease x triage level + outbreak risk).
    if current_disease and triage in TRIAGE_LEVELS:
        lvl = None if current_disease == "Healthy" else triage
        farmer_sev = "CRITICAL" if (triage == "Critical" or ob_status == "CRITICAL") else ("HIGH" if (triage == "High" or ob_status in ("MODERATE", "HIGH")) else "INFO")
        # Mark older unread farmer_disease advisories for this farmer as read so the popup shows THIS report's exact guidelines
        c.execute("""
            UPDATE notifications SET read_at=?
            WHERE user_id=? AND kind='ADVISORY' AND read_at IS NULL
              AND data_json LIKE '%"adv": "farmer_disease"%'
        """, (now(), u["id"]))
        create_notification(
            c, u["id"], f"Health advisory: {current_disease}" + (f" ({lvl})" if lvl else ""),
            f"Advisory for {a['name']}: the reported symptoms are "
            + ("not a strong disease pattern." if current_disease == "Healthy" else f"consistent with {current_disease} (Severity: {lvl}, Outbreak Risk: {ob_status}). Contact a veterinarian for confirmation."),
            farmer_sev, "ADVISORY", case_id, a["village_id"],
            {
                "adv": "farmer_disease",
                "disease": current_disease,
                "level": lvl,
                "outbreak_level": ob_status,
                "score": ob_score if current_disease else None,
                "confirmed": confirmed_ob,
                "village": vil_name,
                "animal": a["name"],
                "case_id": case_id,
            }
        )
    c.commit()
    if MONGO_URI:
        trigger_mongo_sync(tables=("cases", "health_reports", "notifications", "users", "vet_villages"), wait_timeout=0.0)

    mongo_upsert("cases", {"_id": case_id, "id": case_id, "animal_id": a["id"], "farmer_id": u["id"], "vet_id": vet["id"] if vet else None, "status": "OPEN", "opened_at": reported_at, "closed_at": None, "synced_at": now()})
    mongo_upsert("health_reports", {
        "_id": report_id, "id": report_id, "case_id": case_id, "animal_id": a["id"],
        "symptoms": symptoms, "symptoms_json": json.dumps(symptoms), "notes": notes,
        "audio_url": audio_url,
        "audio_data": d.get("audio_base64"),
        "reported_at": reported_at, "prediction": prediction, "ai_prediction_json": json.dumps(prediction),
        "ai_model_version": prediction.get("model_version","unknown"), "weather": weather,
        "weather_json": json.dumps(weather), "report_inputs": report_inputs,
        "report_inputs_json": json.dumps(report_inputs), "synced_at": now()
    })
    vet_info = None
    if vet:
        vet_info = {"id":vet["id"],"name":vet["name"],"phone":vet["phone"],"address":vet["address"]}
        if lat is not None and lon is not None and vet["latitude"] is not None and vet["longitude"] is not None:
            vet_info["distance_km"] = round(haversine(lat, lon, vet["latitude"], vet["longitude"]), 1)
    return jsonify(ok=True,case_id=case_id,prediction=prediction,weather=weather,
                   symptoms=symptoms,report_inputs=report_inputs, audio_url=audio_url,
                   assigned_vet=vet_info)

def _farmer_matches_user(c, owner_id, u):
    """Check if owner_id belongs to farmer u (either exact ID match, orphaned/non-farmer ID, same phone, or same farmer name)."""
    if owner_id == u["id"]:
        return True
    owner = c.execute("SELECT * FROM users WHERE id=?", (owner_id,)).fetchone()
    if not owner or owner["role"] != "farmer":
        return True
    u_phone = validate_and_format_phone(u["phone"]) or str(u["phone"] or "").strip()
    o_phone = validate_and_format_phone(owner["phone"]) or str(owner["phone"] or "").strip()
    if u_phone and o_phone and u_phone == o_phone:
        return True
    if str(owner["name"] or "").strip().lower() == str(u["name"] or "").strip().lower():
        return True
    return False


def _vet_can_access_case(c, u, cs, a=None):
    """Check if vet u is assigned to case cs or its animal/farmer/village/notification,
    and self-heal cs.vet_id to u['id'] when authorized."""
    if cs["vet_id"] == u["id"]:
        return True
    if a is None:
        a = c.execute("SELECT * FROM animals WHERE id=?", (cs["animal_id"],)).fetchone()
    farmer_id = a["farmer_id"] if a else cs["farmer_id"]
    village_id = a["village_id"] if a else None
    f_row = c.execute("SELECT * FROM users WHERE id=?", (farmer_id,)).fetchone() if farmer_id else None
    allowed = False
    if f_row and f_row["assigned_vet_id"] == u["id"]:
        allowed = True
    elif village_id is not None and c.execute("SELECT 1 FROM vet_villages WHERE vet_id=? AND village_id=?", (u["id"], village_id)).fetchone():
        allowed = True
    elif c.execute("SELECT 1 FROM notifications WHERE user_id=? AND case_id=?", (u["id"], cs["id"])).fetchone():
        allowed = True
    else:
        best_v = vet_for_farmer(c, farmer_id, village_id)
        if best_v and best_v["id"] == u["id"]:
            allowed = True
    if allowed:
        c.execute("UPDATE cases SET vet_id=?, farmer_id=? WHERE id=?", (u["id"], farmer_id, cs["id"]))
        c.commit()
    return allowed


def _resolve_vet_case(c, u, case_id):
    cs = c.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if not cs:
        return None
    if cs["vet_id"] == u["id"]:
        return cs
    if reconcile_farmer_vet_assignments(c, notify=False):
        c.commit()
    cs = c.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if not cs:
        return None
    if _vet_can_access_case(c, u, cs):
        return c.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    return None


@app.get("/api/animals/<int:animal_id>/history")
@require_role("farmer","vet","government")
def animal_history(u,c,animal_id):
    if reconcile_farmer_vet_assignments(c, notify=False):
        c.commit()
    a = c.execute("""
        SELECT a.*, COALESCE(v.name, f.village, '') village_name,
               COALESCE(f.name, '') farmer_name, COALESCE(f.phone, '') farmer_phone
        FROM animals a
        LEFT JOIN villages v ON v.id=a.village_id
        LEFT JOIN users f ON f.id=a.farmer_id
        WHERE a.id=?
    """, (animal_id,)).fetchone()
    if not a:
        return jsonify(error="Animal not found"), 404
    if u["role"] == "farmer":
        if not _farmer_matches_user(c, a["farmer_id"], u):
            return jsonify(error="Access denied"), 403
        if a["farmer_id"] != u["id"]:
            c.execute("UPDATE animals SET farmer_id=? WHERE id=?", (u["id"], animal_id))
            c.execute("UPDATE cases SET farmer_id=? WHERE animal_id=?", (u["id"], animal_id))
            c.commit()
            a = c.execute("""
                SELECT a.*, COALESCE(v.name, f.village, '') village_name,
                       COALESCE(f.name, '') farmer_name, COALESCE(f.phone, '') farmer_phone
                FROM animals a
                LEFT JOIN villages v ON v.id=a.village_id
                LEFT JOIN users f ON f.id=a.farmer_id
                WHERE a.id=?
            """, (animal_id,)).fetchone()
    if u["role"] == "vet":
        allowed_vet = c.execute("""
            SELECT 1 FROM users f WHERE f.id=? AND f.assigned_vet_id=?
            UNION SELECT 1 FROM vet_villages WHERE vet_id=? AND village_id=?
            UNION SELECT 1 FROM cases WHERE animal_id=? AND vet_id=?
        """, (a["farmer_id"], u["id"], u["id"], a["village_id"], animal_id, u["id"])).fetchone()
        if not allowed_vet:
            best_v = vet_for_farmer(c, a["farmer_id"], a["village_id"])
            if not best_v or best_v["id"] != u["id"]:
                return jsonify(error="Access denied"), 403
        # Ensure all cases of this animal reflect the current owner and vet so clicking any case opens cleanly
        c.execute("UPDATE cases SET farmer_id=?, vet_id=? WHERE animal_id=?", (a["farmer_id"], u["id"], animal_id))
        c.commit()
    cases=[]
    for cs in c.execute("SELECT * FROM cases WHERE animal_id=? ORDER BY opened_at DESC,id DESC",(animal_id,)):
        d=dict(cs);d["reports"]=[];d["actions"]=[]
        for r in c.execute("SELECT * FROM health_reports WHERE case_id=? ORDER BY reported_at",(cs["id"],)):
            z=dict(r);z["symptoms"]=json.loads(z.pop("symptoms_json") or "[]");z["prediction"]=json.loads(z.pop("ai_prediction_json") or "null");z["weather"]=json.loads(z.pop("weather_json") or "null") if "weather_json" in z else None;z["report_inputs"]=json.loads(z.pop("report_inputs_json") or "null") if "report_inputs_json" in z else None;d["reports"].append(z)
        d["actions"]=[dict(x) for x in c.execute("SELECT ca.*,COALESCE(u.name,'Veterinarian') vet_name FROM case_actions ca LEFT JOIN users u ON u.id=ca.vet_id WHERE case_id=? ORDER BY action_at,id",(cs["id"],))]
        cases.append(d)
    vaccinations=[dict(x) for x in c.execute("SELECT v.*,COALESCE(u.name,'Veterinarian') vet_name FROM vaccinations v LEFT JOIN users u ON u.id=v.vet_id WHERE v.animal_id=? ORDER BY administered_at DESC,id DESC",(animal_id,))]
    return jsonify(animal=dict(a),case_count=len(cases),cured_count=sum(1 for x in cases if x["status"]=="CLOSED"),cases=cases,vaccinations=vaccinations)

@app.get("/api/cases")
@require_role("farmer","vet","government")
def list_cases(u,c):
    if reconcile_farmer_vet_assignments(c, notify=False):
        c.commit()
    q=(request.args.get("q") or "").strip()
    status=request.args.get("status")
    params=[]
    where=[]
    sql="""
      SELECT cs.*,a.name animal_name,a.tag animal_tag,a.species,a.farmer_id,a.village_id,a.latitude animal_lat,a.longitude animal_lon,
             COALESCE(f.name,'') farmer_name,COALESCE(f.phone,'') farmer_phone,f.address farmer_address,f.latitude farmer_lat,f.longitude farmer_lon,
             COALESCE(v.name,f.village,'') village_name,COALESCE(v.pincode,f.pincode,'') pincode,v.latitude village_lat,v.longitude village_lon,
             vet.name vet_name,vet.phone vet_phone,vet.address vet_address
      FROM cases cs
      JOIN animals a ON a.id=cs.animal_id
      LEFT JOIN users f ON f.id=a.farmer_id
      LEFT JOIN villages v ON v.id=a.village_id
      LEFT JOIN users vet ON vet.id=cs.vet_id
    """
    if u["role"]=="farmer":
        where.append("(cs.farmer_id=? OR a.farmer_id=?)"); params.extend([u["id"], u["id"]])
    elif u["role"]=="vet":
        where.append("(cs.vet_id=? OR f.assigned_vet_id=?)"); params.extend([u["id"], u["id"]])
    if q:
        where.append("(a.name LIKE ? OR a.tag LIKE ? OR f.name LIKE ?)")
        qq=f"%{q}%"; params += [qq,qq,qq]
    if status in ("OPEN","IN_PROGRESS","CLOSED"):
        where.append("cs.status=?"); params.append(status)
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY cs.opened_at DESC"
    rows=c.execute(sql,params).fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        d["reports"]=[]
        for rr in c.execute("SELECT * FROM health_reports WHERE case_id=? ORDER BY reported_at ASC,id ASC",(r["id"],)):
            rd=dict(rr); rd["symptoms"]=json.loads(rd.pop("symptoms_json") or "[]"); rd["prediction"]=json.loads(rd.pop("ai_prediction_json") or "null")
            rd["weather"]=json.loads(rd.pop("weather_json") or "null") if "weather_json" in rd else None
            rd["report_inputs"]=json.loads(rd.pop("report_inputs_json") or "null") if "report_inputs_json" in rd else None
            d["reports"].append(rd)
        d["actions"]=[dict(x) for x in c.execute("""
          SELECT ca.*,COALESCE(u.name,'Veterinarian') vet_name FROM case_actions ca LEFT JOIN users u ON u.id=ca.vet_id
          WHERE case_id=? ORDER BY action_at ASC,id ASC
        """,(r["id"],))]
        d["outbreak_assessments"]=[dict(x) for x in c.execute("SELECT * FROM outbreak_assessments WHERE case_id=? ORDER BY created_at ASC,id ASC",(r["id"],))]
        out.append(d)
    return jsonify(cases=out)

@app.get("/api/cases/<int:case_id>")
@require_role("farmer","vet","government")
def case_detail(u,c,case_id):
    if reconcile_farmer_vet_assignments(c, notify=False):
        c.commit()
    cs=c.execute("SELECT * FROM cases WHERE id=?",(case_id,)).fetchone()
    if not cs:
        return jsonify(error="Case not found"),404
    a_row=c.execute("SELECT * FROM animals WHERE id=?",(cs["animal_id"],)).fetchone()
    if a_row and cs["farmer_id"] != a_row["farmer_id"]:
        c.execute("UPDATE cases SET farmer_id=? WHERE id=?", (a_row["farmer_id"], case_id))
        c.commit()
        cs=c.execute("SELECT * FROM cases WHERE id=?",(case_id,)).fetchone()
    if u["role"]=="farmer":
        owner_id = a_row["farmer_id"] if a_row else cs["farmer_id"]
        if not _farmer_matches_user(c, owner_id, u):
            return jsonify(error="Access denied"),403
        if cs["farmer_id"] != u["id"] or (a_row and a_row["farmer_id"] != u["id"]):
            if a_row:
                c.execute("UPDATE animals SET farmer_id=? WHERE id=?", (u["id"], a_row["id"]))
            c.execute("UPDATE cases SET farmer_id=? WHERE id=?", (u["id"], case_id))
            c.commit()
            cs=c.execute("SELECT * FROM cases WHERE id=?",(case_id,)).fetchone()
    if u["role"]=="vet":
        if not _vet_can_access_case(c, u, cs, a_row):
            return jsonify(error="Case is not assigned to you"),403
        cs=c.execute("SELECT * FROM cases WHERE id=?",(case_id,)).fetchone()
    # government can read all cases, but cannot alter them
    rr = c.execute("""
      SELECT cs.*,a.name animal_name,a.tag animal_tag,a.species,
             COALESCE(f.name,'') farmer_name,COALESCE(f.phone,'') farmer_phone,
             COALESCE(v.name,f.village,'') village_name,
             vet.name vet_name,vet.phone vet_phone,vet.address vet_address, a.village_id
      FROM cases cs
      JOIN animals a ON a.id=cs.animal_id
      LEFT JOIN users f ON f.id=a.farmer_id
      LEFT JOIN villages v ON v.id=a.village_id
      LEFT JOIN users vet ON vet.id=cs.vet_id
      WHERE cs.id=?
    """,(case_id,)).fetchone()
    d=dict(rr)
    # Distance to the vet currently on the case (follows a farmer transfer).
    d["vet_distance_km"]=None
    vet_row=c.execute("SELECT latitude,longitude FROM users WHERE id=?",(cs["vet_id"],)).fetchone() if cs["vet_id"] else None
    if vet_row and vet_row["latitude"] is not None and vet_row["longitude"] is not None:
        lat,lon,_=resolve_location(c,c.execute("SELECT * FROM animals WHERE id=?",(cs["animal_id"],)).fetchone())
        if lat is not None and lon is not None:
            d["vet_distance_km"]=round(haversine(lat,lon,vet_row["latitude"],vet_row["longitude"]),1)
    d["reports"]=[]
    for x in c.execute("SELECT * FROM health_reports WHERE case_id=? ORDER BY reported_at",(case_id,)):
        z=dict(x);z["symptoms"]=json.loads(z.pop("symptoms_json"));z["prediction"]=json.loads(z.pop("ai_prediction_json") or "null")
        z["weather"]=json.loads(z.pop("weather_json") or "null") if "weather_json" in z else None
        z["report_inputs"]=json.loads(z.pop("report_inputs_json") or "null") if "report_inputs_json" in z else None
        d["reports"].append(z)
    d["actions"]=[dict(x) for x in c.execute("SELECT ca.*,u.name vet_name FROM case_actions ca JOIN users u ON u.id=ca.vet_id WHERE case_id=? ORDER BY action_at",(case_id,))]
    d["vaccinations"]=[dict(x) for x in c.execute("SELECT v.*,u.name vet_name FROM vaccinations v JOIN users u ON u.id=v.vet_id WHERE case_id=? ORDER BY administered_at,id",(case_id,))]
    return jsonify(case=d)

@app.post("/api/vet/cases/<int:case_id>/action")
@require_role("vet")
def vet_action(u,c,case_id):
    cs=_resolve_vet_case(c,u,case_id)
    if not cs:return jsonify(error="Case not found or not assigned to you."),404
    d=json_body(); typ=(d.get("action_type") or "").upper()
    if typ=="VACCINE":
        # A vaccine is only "given" through Record vaccination, which deducts vet stock atomically.
        # Logging it here would put a vaccine on the treatment timeline without touching inventory.
        return jsonify(error="Use Record vaccination to record a vaccine; it updates your vaccine stock."),400
    if typ not in ("TEST","VACCINE","MEDICATION","NOTE","CURE"):
        return jsonify(error="Action type must be TEST, VACCINE, MEDICATION, NOTE or CURE."),400
    details=(d.get("details") or "").strip()
    if not details:return jsonify(error="Please enter action details."),400
    # A queued offline action carries the time the vet recorded it; anything odd falls back to now.
    action_at=_client_reported_at(d.get("action_at"))
    c.execute("INSERT INTO case_actions(case_id,vet_id,action_type,details,action_at) VALUES(?,?,?,?,?)",
              (case_id,u["id"],typ,details,action_at))
    action_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    if typ in ("TEST","VACCINE","MEDICATION"):
        c.execute("UPDATE cases SET status='IN_PROGRESS' WHERE id=? AND status='OPEN'",(case_id,))
    create_notification(c,cs["farmer_id"],"Case updated",
                        f"Veterinarian updated case #{case_id}: {typ}.", "INFO","CASE",case_id, None)
    c.commit()
    # Sync case action to MongoDB after commit
    mongo_upsert("case_actions", {
        "_id": action_id,
        "id": action_id,
        "case_id": case_id,
        "vet_id": u["id"],
        "action_type": typ,
        "details": details,
        "action_at": action_at
    })
    return jsonify(ok=True)

@app.post("/api/vet/cases/<int:case_id>/vaccine")
@require_role("vet")
def vet_vaccine(u,c,case_id):
    cs=_resolve_vet_case(c,u,case_id)
    if not cs:return jsonify(error="Case not found or not assigned to you."),404
    d=json_body(); name=(d.get("vaccine_name") or "").strip().upper()
    if name not in VACCINE_CODES:
        return jsonify(error="Only FMD, HS, LSD, or BQ vaccines are permitted."), 400
    # One vaccine is applied per administration. Any dose/unit sent by a client is ignored, so the record, the
    # stock deduction and the dose totals can never disagree (and NaN/Infinity can never reach the totals).
    dose,unit=1.0,"dose"
    ts=d.get("administered_at") or now()
    if not isinstance(ts,str):return jsonify(error="Administered date/time must be text."),400

    # Administering is the ONLY thing that consumes vet stock (1 vaccine per administration).
    # Stock check + deduction + vaccination record + transaction commit together or not at all.
    begin_write(c)
    ensure_vet_stock(c,u["id"])
    new_stock=consume_vet_stock(c,u["id"],name)
    if new_stock is None:
        c.rollback()
        return jsonify(error=f"Insufficient stock for {name}. You have 0 left. Please contact government to release more."),400

    cur=c.execute("INSERT INTO vaccinations(animal_id,case_id,farmer_id,vet_id,vaccine_name,dose,unit,administered_at,batch_no,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (cs["animal_id"],case_id,cs["farmer_id"],u["id"],name,dose,unit,ts,d.get("batch_no"),d.get("notes"),now()))
    vacc_id=cur.lastrowid
    c.execute("UPDATE animals SET last_vaccinated=? WHERE id=?",(ts[:10] if len(ts)>=10 else ts,cs["animal_id"]))
    details=f"{name} — {dose:g} {unit}" + (f" · batch {d.get('batch_no')}" if d.get('batch_no') else "") + (f" · {d.get('notes')}" if d.get('notes') else "")
    c.execute("INSERT INTO case_actions(case_id,vet_id,action_type,details,action_at) VALUES(?,?,?,?,?)",(case_id,u["id"],"VACCINE",details,ts))
    _stock_txn(c,"VET",u["id"],name,-1,new_stock,"ADMINISTERED",vaccination_id=vacc_id,case_id=case_id,animal_id=cs["animal_id"])
    check_low_stock(c,u["id"],u["name"],name,new_stock)

    create_notification(c,cs["farmer_id"],"Vaccination recorded",f"{name} ({dose:g} {unit}) was administered to the animal in case #{case_id}.","INFO","CASE",case_id)
    c.commit()
    # Sync vaccination to MongoDB after commit
    mongo_upsert("vaccinations", {
        "_id": vacc_id,
        "id": vacc_id,
        "animal_id": cs["animal_id"],
        "case_id": case_id,
        "farmer_id": cs["farmer_id"],
        "vet_id": u["id"],
        "vaccine_name": name,
        "dose": dose,
        "unit": unit,
        "administered_at": ts,
        "batch_no": d.get("batch_no"),
        "notes": d.get("notes"),
        "created_at": now()
    })
    return jsonify(ok=True,remaining_stock=new_stock)

@app.post("/api/vet/cases/<int:case_id>/close")
@require_role("vet")
def close_case(u,c,case_id):
    cs=_resolve_vet_case(c,u,case_id)
    if not cs:return jsonify(error="Case not found or not assigned to you."),404
    if cs["status"]=="CLOSED":return jsonify(ok=True)
    cure=c.execute("SELECT 1 FROM case_actions WHERE case_id=? AND vet_id=? AND action_type='CURE' ORDER BY action_at DESC,id DESC LIMIT 1",(case_id,u["id"])).fetchone()
    if not cure:
        return jsonify(error="The case can be closed only after the veterinarian records a CURE action."),400
    # A report that arrived after the last CURE has not been reviewed: the animal may be sick again.
    last_cure=_latest_time(c.execute("SELECT action_at FROM case_actions WHERE case_id=? AND vet_id=? AND action_type='CURE'",(case_id,u["id"])).fetchall(),"action_at")
    newest_report=_latest_time(c.execute("SELECT reported_at FROM health_reports WHERE case_id=?",(case_id,)).fetchall(),"reported_at")
    if last_cure and newest_report and newest_report>last_cure:
        return jsonify(error="A newer report arrived after your last CURE. Review it and record a new CURE before closing the case."),400
    c.execute("UPDATE cases SET status='CLOSED',closed_at=? WHERE id=?",(now(),case_id))
    create_notification(c,cs["farmer_id"],"Case closed",
                        f"Veterinarian closed case #{case_id}. The full history remains available.", "INFO","CASE",case_id)
    c.commit()
    return jsonify(ok=True)

@app.post("/api/vet/cases/<int:case_id>/model-review")
@require_role("vet")
def vet_model_review(u,c,case_id):
    cs=_resolve_vet_case(c,u,case_id)
    if not cs:return jsonify(error="Case not found or not assigned to you."),404
    d=json_body()
    val=d.get("correct")
    if val not in (True,False):return jsonify(error="Choose whether the model prediction was correct."),400
    a=c.execute("SELECT * FROM animals WHERE id=?",(cs["animal_id"],)).fetchone()
    v=c.execute("SELECT * FROM villages WHERE id=?",(a["village_id"],)).fetchone()
    c.execute("""
      INSERT INTO outbreak_assessments(case_id,vet_id,village_id,predicted,confirmed,model_correct,notes,created_at)
      VALUES(?,?,?,?,?,?,?,?)
    """,(case_id,u["id"],v["id"],d.get("predicted"),0,int(val),d.get("notes"),now()))
    c.commit()
    return jsonify(ok=True)

@app.post("/api/vet/cases/<int:case_id>/outbreak")
@require_role("vet")
def vet_outbreak(u,c,case_id):
    import traceback
    try:
        cs=_resolve_vet_case(c,u,case_id)
        if not cs:return jsonify(error="Case not found or not assigned to you."),404
        a=c.execute("SELECT * FROM animals WHERE id=?",(cs["animal_id"],)).fetchone()
        v=c.execute("SELECT * FROM villages WHERE id=?",(a["village_id"],)).fetchone()
        d=json_body()
        confirmed=1 if d.get("confirmed") else 0
        c.execute("""
          INSERT INTO outbreak_assessments(case_id,vet_id,village_id,predicted,confirmed,notes,created_at)
          VALUES(?,?,?,?,?,?,?)
        """,(case_id,u["id"],v["id"],d.get("predicted"),confirmed,d.get("notes"),now()))
        if confirmed:
            villages=nearest_village_ids(c,v)
            # Heavy alerts to all farmers + vets in the affected village group.
            recipients={u["id"]}
            v_ph = ",".join("?"*len(villages))
            for uid in c.execute(f"""
              SELECT DISTINCT id FROM users
              WHERE id IN (SELECT farmer_id FROM animals WHERE village_id IN ({v_ph}))
                 OR id IN (SELECT vet_id FROM vet_villages WHERE village_id IN ({v_ph}))
                 OR id IN (SELECT assigned_vet_id FROM users WHERE id IN (SELECT farmer_id FROM animals WHERE village_id IN ({v_ph})) AND assigned_vet_id IS NOT NULL)
                 OR id IN (SELECT cs.vet_id FROM cases cs JOIN animals a ON a.id=cs.animal_id WHERE a.village_id IN ({v_ph}) AND cs.vet_id IS NOT NULL)
            """, villages*4):
                recipients.add(uid["id"])
            disease=case_disease(c,case_id)
            if disease and disease!="Healthy":
                # Update latest report prediction on open cases of this disease in affected villages to CRITICAL
                for hr_row in c.execute(f"""
                    SELECT hr.id, hr.ai_prediction_json, hr.symptoms_json
                    FROM health_reports hr
                    JOIN cases cs ON cs.id=hr.case_id
                    JOIN animals an ON an.id=hr.animal_id
                    WHERE an.village_id IN ({v_ph}) AND cs.status!='CLOSED'
                """, villages).fetchall():
                    try:
                        pr = json.loads(hr_row["ai_prediction_json"] or "{}")
                        if isinstance(pr, dict):
                            pr_dis = canonical_disease(pr.get("predicted_disease") or (((pr.get("predictions") or [{}])[0] or {}).get("disease")))
                            if pr_dis == disease:
                                syms_list = json.loads(hr_row["symptoms_json"] or "[]")
                                new_tr, new_tr_reasons = assess_triage(disease, pr.get("confidence"), syms_list, "CRITICAL")
                                new_esc, new_esc_reason = assess_escalation(disease, pr.get("confidence"), "CRITICAL")
                                pr["outbreak_status"] = "CRITICAL"
                                pr["outbreak_risk_score"] = max(int(pr.get("outbreak_risk_score") or 0), 85)
                                pr["outbreak_probability"] = pr["outbreak_risk_score"] / 100.0
                                pr["triage_risk"] = new_tr
                                pr["triage_reasons"] = new_tr_reasons
                                pr["escalate"] = new_esc
                                pr["escalation_reason"] = new_esc_reason
                                if isinstance(pr.get("outbreak"), dict):
                                    pr["outbreak"]["outbreak_status"] = "CRITICAL"
                                    pr["outbreak"]["outbreak_risk_score"] = pr["outbreak_risk_score"]
                                    pr["outbreak"]["confirmed"] = True
                                c.execute("UPDATE health_reports SET ai_prediction_json=? WHERE id=?", (json.dumps(pr), hr_row["id"]))
                    except Exception:
                        pass
                # Farmers: outbreak advisory for the confirmed disease, in their own language.
                for vid in villages:
                    vn=c.execute("SELECT name FROM villages WHERE id=?",(vid,)).fetchone()
                    notify_farmer_outbreak_advisory(c,disease,vid,vn["name"] if vn else v["name"],"CRITICAL",85,True,case_id,triage_level="Critical",resurface=True)
                # Earlier "suspected" alerts for this disease + village now show as confirmed.
                for n in c.execute("SELECT id,data_json FROM notifications WHERE kind IN ('OUTBREAK','CASE','ADVISORY') AND village_id=? AND data_json IS NOT NULL",(v["id"],)).fetchall():
                    dd=json.loads(n["data_json"])
                    if canonical_disease(dd.get("disease"))==disease and not dd.get("confirmed"):
                        dd["confirmed"]=True; dd["confirmed_by"]=u["name"]
                        dd["outbreak_level"]="CRITICAL"
                        if dd.get("adv") in ("vet_outbreak", "farmer_outbreak"):
                            dd["level"]="CRITICAL"
                        elif dd.get("adv") in ("vet_case", "farmer_disease") and dd.get("level") != "Healthy":
                            dd["level"]="Critical"
                        c.execute("UPDATE notifications SET data_json=?, severity='CRITICAL' WHERE id=?",(json.dumps(dd),n["id"]))
                # Vets: the confirmed disease-control advisory is now active (confirmation gate).
                for r in c.execute("SELECT id FROM users WHERE role='vet' AND id IN (%s)"%",".join("?"*len(recipients)),list(recipients)).fetchall():
                    upsert_notification(c,r["id"],f"vet_confirmed:{disease}:{v['id']}",
                        f"CONFIRMED {disease} OUTBREAK — {v['name']}",
                        f"Veterinarian {u['name']} confirmed a {disease} outbreak in {v['name']}. Disease-specific control measures are now active.",
                        "CRITICAL","OUTBREAK",case_id,v["id"],
                        {"adv":"vet_confirmed","disease":disease,"level":"CRITICAL","outbreak_level":"CRITICAL","triage_level":"Critical","village":v["name"],"confirmed":True,"confirmed_by":u["name"],"case_id":case_id},
                        resurface=True)
            else:
                for uid in recipients:
                    create_notification(c,uid,
                        "CONFIRMED OUTBREAK ALERT",
                        f"Veterinarian {u['name']} confirmed an outbreak in {v['name']}. Maintain heightened vigilance. This alert covers villages within {RADIUS_KM:g} km when village coordinates are available.",
                        "CRITICAL","OUTBREAK",case_id,v["id"])
            # Government is explicitly notified only when a vet confirms.
            govs=c.execute("SELECT id FROM users WHERE role='government'").fetchall()
            for g in govs:
                create_notification(c,g["id"],"Confirmed outbreak reported",
                    f"Vet {u['name']} confirmed an outbreak in {v['name']}. Immediate government review is recommended.",
                    "CRITICAL","OUTBREAK",case_id,v["id"])
        c.commit()
        return jsonify(ok=True,confirmed=bool(confirmed),alerted_villages=villages if confirmed else [])
    except Exception as e:
        traceback.print_exc()
        return jsonify(error=str(e)), 500

@app.get("/api/outbreak/summary")
@require_role("vet","government")
def outbreak_summary(u,c):
    """
    One entry per (village, disease), built from the outbreak engine and vet
    confirmations - never from notification text (that listed each alert
    message once per recipient, with the title shown as the disease).
      - Engine: every disease reported in a village in the last 7 days, kept if
        the current Outbreak Risk Score is MODERATE or above.
      - Vet confirmations from the last 30 days, kept whatever the engine says,
        with the engine's numbers alongside.
    A vet sees only villages in their own area; government sees all.
    """
    nowdt = datetime.fromisoformat(now())
    since7 = (nowdt - timedelta(days=7)).isoformat()
    since30 = (nowdt - timedelta(days=30)).isoformat()

    def top_disease(pj):
        try:
            p = json.loads(pj or "{}")
            if not isinstance(p, dict):
                return None
            return canonical_disease(((p.get("predictions") or [{}])[0] or {}).get("disease"))
        except Exception:
            return None

    allowed = None
    if u["role"] == "vet":
        allowed = {r[0] for r in c.execute("SELECT village_id FROM vet_villages WHERE vet_id=?", (u["id"],))}
        allowed |= {r[0] for r in c.execute(
            "SELECT DISTINCT a.village_id FROM animals a JOIN users f ON f.id=a.farmer_id WHERE f.assigned_vet_id=?", (u["id"],))}

    pairs = {}
    for vid, pj in c.execute("""SELECT a.village_id, hr.ai_prediction_json FROM health_reports hr
                                JOIN animals a ON a.id=hr.animal_id WHERE hr.reported_at>=?""", (since7,)):
        d = top_disease(pj)
        if d and d != "Healthy":
            pairs.setdefault((vid, d), {})
    for oa in c.execute("""SELECT oa.*, v.name vet_name,
                                  (SELECT hr.ai_prediction_json FROM health_reports hr WHERE hr.case_id=oa.case_id
                                   ORDER BY hr.reported_at DESC, hr.id DESC LIMIT 1) pj
                           FROM outbreak_assessments oa JOIN users v ON v.id=oa.vet_id
                           WHERE oa.confirmed=1 AND oa.created_at>=? ORDER BY oa.created_at DESC""", (since30,)):
        e = pairs.setdefault((oa["village_id"], top_disease(oa["pj"]) or "Unknown"), {})
        if "confirmed_by" not in e:   # newest confirmation wins
            e.update(confirmed_by=oa["vet_name"], confirmed_at=oa["created_at"], confirm_notes=oa["notes"],
                     case_id=oa["case_id"])

    results = []
    for (vid, disease), e in pairs.items():
        if vid is None or (allowed is not None and vid not in allowed):
            continue
        v = c.execute("SELECT * FROM villages WHERE id=?", (vid,)).fetchone()
        if not v:
            continue
        r = calculate_outbreak_risk(c, disease, v["latitude"], v["longitude"], v["name"]) if disease != "Unknown" else None
        confirmed = "confirmed_by" in e
        if not confirmed and (r is None or r["outbreak_status"] == "LOW"):
            continue
        results.append({
            "village_id": vid, "village": v["name"], "disease": disease,
            "confirmed": confirmed, "confirmed_by": e.get("confirmed_by"),
            "confirmed_at": e.get("confirmed_at"), "confirm_notes": e.get("confirm_notes"), "case_id": e.get("case_id"),
            "outbreak_status": r["outbreak_status"] if r else None,
            "outbreak_risk_score": r["outbreak_risk_score"] if r else None,
            "unique_animals": r["unique_animals"] if r else None,
            "unique_farms": r["unique_farms"] if r else None,
            "unique_villages": r["unique_villages"] if r else None,
            "villages": r["villages"] if r else [],
            "evidence": r["evidence"] if r else [],
        })
    results.sort(key=lambda x: (not x["confirmed"], -(x["outbreak_risk_score"] or 0)))
    return jsonify(outbreaks=results)

@app.get("/api/notifications")
@require_role("farmer","vet","government")
def notifications(u,c):

    rows=c.execute("""
      SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 100
    """,(u["id"],)).fetchall()
    unread=sum(1 for r in rows if not r["read_at"])
    out=[]
    for r in rows:
        n=dict(r)
        try: n["data"]=json.loads(n.pop("data_json",None) or "null")
        except Exception: n["data"]=None
        out.append(n)
    return jsonify(unread=unread,notifications=out)

@app.post("/api/notifications/read")
@require_role("farmer","vet","government")
def notifications_read(u,c):
    d=json_body()
    if d.get("all"):
        c.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL",(now(),u["id"]))
    elif d.get("id"):
        c.execute("UPDATE notifications SET read_at=? WHERE id=? AND user_id=?",(now(),d["id"],u["id"]))
    c.commit();return jsonify(ok=True)

@app.get("/api/vet/overview")
@require_role("vet")
def vet_overview(u,c):
    reconcile_farmer_vet_assignments(c, notify=False)
    vet_lat, vet_lon = _coerce_latlon(u["latitude"] if "latitude" in u.keys() else None,
                                      u["longitude"] if "longitude" in u.keys() else None)

    # Farmers assigned to this vet (either by assigned_vet_id or open case or village coverage)
    assigned_farmer_rows = c.execute("""
        SELECT DISTINCT f.id, f.name, f.phone, f.village, f.pincode, f.latitude, f.longitude
        FROM users f
        WHERE f.role='farmer' AND (
            f.assigned_vet_id=?
            OR EXISTS (SELECT 1 FROM cases cs WHERE cs.farmer_id=f.id AND cs.vet_id=?)
            OR (f.assigned_vet_id IS NULL AND EXISTS (
                SELECT 1 FROM animals a JOIN vet_villages vv ON vv.village_id=a.village_id
                WHERE a.farmer_id=f.id AND vv.vet_id=?
            ))
        )
        ORDER BY f.name
    """, (u["id"], u["id"], u["id"])).fetchall()
    assigned_farmer_ids = {r["id"] for r in assigned_farmer_rows}
    assigned_village_names = {(r["village"] or "").strip().lower() for r in assigned_farmer_rows if (r["village"] or "").strip()}

    # Villages where this vet has assigned farmers, animals of assigned farmers, assigned cases, vet_villages links, or within 5km
    covered_vids = {r[0] for r in c.execute("SELECT village_id FROM vet_villages WHERE vet_id=?", (u["id"],)) if r[0] is not None}
    covered_vids |= {r[0] for r in c.execute("SELECT DISTINCT a.village_id FROM animals a JOIN users f ON f.id=a.farmer_id WHERE f.assigned_vet_id=?", (u["id"],)) if r[0] is not None}
    covered_vids |= {r[0] for r in c.execute("SELECT DISTINCT a.village_id FROM cases cs JOIN animals a ON a.id=cs.animal_id WHERE cs.vet_id=?", (u["id"],)) if r[0] is not None}

    all_villages = c.execute("SELECT * FROM villages ORDER BY name").fetchall()
    villages_out = []
    for v in all_villages:
        v_lat, v_lon = _coerce_latlon(v["latitude"], v["longitude"])
        within_5km = (vet_lat is not None and vet_lon is not None and v_lat is not None and v_lon is not None
                      and haversine(vet_lat, vet_lon, v_lat, v_lon) <= RADIUS_KM)
        name_match = (v["name"] or "").strip().lower() in assigned_village_names
        if v["id"] not in covered_vids and not within_5km and not name_match:
            continue

        c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)", (u["id"], v["id"]))
        covered_vids.add(v["id"])

        animals_count = c.execute("""
            SELECT COUNT(DISTINCT a.id) FROM animals a
            JOIN users f ON f.id=a.farmer_id
            WHERE a.village_id=? AND a.species IN ('Cattle','Buffalo')
              AND (f.assigned_vet_id=? OR f.assigned_vet_id IS NULL)
        """, (v["id"], u["id"])).fetchone()[0]
        farmers_count = c.execute("""
            SELECT COUNT(DISTINCT f.id) FROM users f
            LEFT JOIN animals a ON a.farmer_id=f.id
            WHERE f.role='farmer' AND (f.assigned_vet_id=? OR f.assigned_vet_id IS NULL)
              AND (a.village_id=? OR lower(COALESCE(f.village,''))=lower(?))
        """, (u["id"], v["id"], v["name"] or "")).fetchone()[0]
        active_cases_count = c.execute("""
            SELECT COUNT(DISTINCT cs.id) FROM cases cs
            JOIN animals a ON a.id=cs.animal_id
            WHERE a.village_id=? AND cs.vet_id=? AND cs.status!='CLOSED'
        """, (v["id"], u["id"])).fetchone()[0]

        if animals_count == 0 and farmers_count == 0 and active_cases_count == 0:
            continue
        villages_out.append({"id": v["id"], "name": v["name"], "pincode": v["pincode"],
                              "animals": animals_count, "farmers": farmers_count,
                              "active_cases": active_cases_count})

    # Owner-searchable animal list — all cattle/buffalo belonging to farmers under this vet
    animals = []
    for a in c.execute("""
        SELECT DISTINCT a.id, a.name, a.tag, a.species, a.village_id,
               u.name farmer_name, u.phone farmer_phone, v.name village_name
        FROM animals a
        JOIN users u ON u.id=a.farmer_id
        JOIN villages v ON v.id=a.village_id
        WHERE a.species IN ('Cattle','Buffalo')
          AND (
            u.assigned_vet_id=?
            OR EXISTS (SELECT 1 FROM cases cs WHERE cs.animal_id=a.id AND cs.vet_id=?)
            OR (u.assigned_vet_id IS NULL AND a.village_id IN (SELECT village_id FROM vet_villages WHERE vet_id=?))
          )
        ORDER BY v.name, a.name
    """, (u["id"], u["id"], u["id"])).fetchall():
        animals.append({"id": a["id"], "name": a["name"], "tag": a["tag"],
                         "species": a["species"], "farmer_name": a["farmer_name"],
                         "farmer_phone": a["farmer_phone"], "village": a["village_name"]})

    farmers_out = []
    for f in assigned_farmer_rows:
        f_animals = c.execute("SELECT COUNT(*) FROM animals WHERE farmer_id=? AND species IN ('Cattle','Buffalo')", (f["id"],)).fetchone()[0]
        f_open = c.execute("SELECT COUNT(*) FROM cases WHERE farmer_id=? AND status!='CLOSED'", (f["id"],)).fetchone()[0]
        farmers_out.append({
            "id": f["id"], "name": f["name"], "phone": f["phone"],
            "village": f["village"] or "", "pincode": f["pincode"] or "",
            "animals": f_animals, "open_cases": f_open
        })

    one_year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    total_animals_under = len(animals)
    animal_ids = [a["id"] for a in animals]
    vacc_animals = 0
    if animal_ids:
        ph = ",".join("?" * len(animal_ids))
        vacc_animals = c.execute(f"""
            SELECT COUNT(DISTINCT vac.animal_id) FROM vaccinations vac
            WHERE vac.animal_id IN ({ph}) AND vac.administered_at >= ?
        """, animal_ids + [one_year_ago]).fetchone()[0]
    vaccination_pct = round(vacc_animals / total_animals_under * 100, 1) if total_animals_under else 0

    resources_left, resources_sent = vet_vaccine_totals(c, u["id"])

    reqs = []
    for r in c.execute("SELECT rr.*, v.name village_name FROM resource_requests rr JOIN villages v ON v.id=rr.village_id WHERE rr.vet_id=? ORDER BY rr.created_at DESC", (u["id"],)).fetchall():
        rd = dict(r)
        rd["resources"] = json.loads(rd.pop("resource_json") or "[]")
        reqs.append(rd)

    c.commit()
    return jsonify(villages=villages_out, animals=animals, farmers=farmers_out, requests=reqs,
                   vaccination_pct=vaccination_pct, resources_sent=resources_sent, resources_left=resources_left)

@app.get("/api/govt/farmers")
@require_role("government")
def govt_farmers(u,c):
    reconcile_farmer_vet_assignments(c, notify=False)
    villages_out = []
    for v in c.execute("SELECT * FROM villages ORDER BY name").fetchall():
        animals_count = c.execute("SELECT COUNT(DISTINCT a.id) FROM animals a WHERE a.village_id=?", (v["id"],)).fetchone()[0]
        farmers_count = c.execute("""
            SELECT COUNT(DISTINCT f.id) FROM users f
            LEFT JOIN animals a ON a.farmer_id=f.id
            WHERE f.role='farmer' AND (a.village_id=? OR lower(COALESCE(f.village,''))=lower(?))
        """, (v["id"], v["name"] or "")).fetchone()[0]
        active_cases_count = c.execute("""
            SELECT COUNT(DISTINCT cs.id) FROM cases cs 
            JOIN animals a ON a.id=cs.animal_id 
            WHERE a.village_id=? AND cs.status!='CLOSED'
        """, (v["id"],)).fetchone()[0]
        if animals_count > 0 or farmers_count > 0:
            villages_out.append({"id": v["id"], "name": v["name"], "pincode": v["pincode"],
                                  "animals": animals_count, "farmers": farmers_count,
                                  "active_cases": active_cases_count})
    animals = []
    for a in c.execute("""
        SELECT DISTINCT a.id, a.name, a.tag, a.species, a.village_id,
               u.name farmer_name, u.phone farmer_phone, v.name village_name
        FROM animals a
        JOIN users u ON u.id=a.farmer_id
        JOIN villages v ON v.id=a.village_id
        WHERE a.species IN ('Cattle','Buffalo')
        ORDER BY v.name, a.name
    """).fetchall():
        animals.append({"id": a["id"], "name": a["name"], "tag": a["tag"],
                         "species": a["species"], "farmer_name": a["farmer_name"],
                         "farmer_phone": a["farmer_phone"], "village": a["village_name"]})
    farmers_out = []
    for f in c.execute("SELECT id, name, phone, village, pincode FROM users WHERE role='farmer' ORDER BY name").fetchall():
        f_animals = c.execute("SELECT COUNT(*) FROM animals WHERE farmer_id=? AND species IN ('Cattle','Buffalo')", (f["id"],)).fetchone()[0]
        f_open = c.execute("SELECT COUNT(*) FROM cases WHERE farmer_id=? AND status!='CLOSED'", (f["id"],)).fetchone()[0]
        farmers_out.append({
            "id": f["id"], "name": f["name"], "phone": f["phone"],
            "village": f["village"] or "", "pincode": f["pincode"] or "",
            "animals": f_animals, "open_cases": f_open
        })
    return jsonify(villages=villages_out, animals=animals, farmers=farmers_out)

@app.post("/api/vet/assign")
@require_role("vet")
def assign_village(u,c):
    d=json_body()
    v=village_for(c,d.get("village","").strip(),d.get("pincode",""))
    c.execute("INSERT OR IGNORE INTO vet_villages(vet_id,village_id) VALUES(?,?)",(u["id"],v["id"]))
    c.commit();return jsonify(ok=True)

@app.get("/api/vet/cases")
@require_role("vet")
def vet_cases(u,c):
    if reconcile_farmer_vet_assignments(c, notify=False):
        c.commit()
    q=request.args.get("q","").strip()
    params=[u["id"]]
    extra=""
    if q:
        extra=" AND (a.name LIKE ? OR a.tag LIKE ? OR f.name LIKE ?)"
        qq=f"%{q}%";params.extend([qq,qq,qq])
    rows=c.execute(f"""
      SELECT cs.id,cs.status,cs.opened_at,cs.closed_at,a.name animal_name,a.tag animal_tag,a.species,
             f.name farmer_name,f.phone farmer_phone,v.name village_name
      FROM cases cs JOIN animals a ON a.id=cs.animal_id JOIN users f ON f.id=cs.farmer_id
      JOIN villages v ON v.id=a.village_id WHERE cs.vet_id=? {extra}
      ORDER BY CASE cs.status WHEN 'OPEN' THEN 0 WHEN 'IN_PROGRESS' THEN 1 ELSE 2 END,cs.opened_at DESC
    """,params).fetchall()
    return jsonify(cases=[dict(x) for x in rows])

@app.post("/api/vet/resource-request")
@require_role("vet")
def resource_request(u,c):
    d=json_body()
    village_id=d.get("village_id")
    v=c.execute("SELECT * FROM villages WHERE id=?",(village_id,)).fetchone()
    if not v:return jsonify(error="Village not found"),404
    # Calculate context from live cases.
    case_count=c.execute("""
      SELECT COUNT(*) FROM cases cs JOIN animals a ON a.id=cs.animal_id
      WHERE a.village_id=? AND cs.status!='CLOSED'
    """,(village_id,)).fetchone()[0]
    resources=d.get("resources") or []
    if not isinstance(resources,list) or not resources:return jsonify(error="Add at least one resource request."),400
    c.execute("""
      INSERT INTO resource_requests(vet_id,village_id,case_count,resource_json,reason,created_at)
      VALUES(?,?,?,?,?,?)
    """,(u["id"],village_id,case_count,json.dumps(resources),d.get("reason"),now()))
    rid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit()
    for g in c.execute("SELECT id FROM users WHERE role='government'"):
        create_notification(c,g,"Veterinary resource request",
            f"Vet {u['name']} requested resources for {v['name']} ({case_count} active cases).",
            "HIGH","RESOURCE",None,village_id)
    c.commit()
    return jsonify(ok=True,request_id=rid,active_cases=case_count)

@app.get("/api/vet/resource-status")
@require_role("vet")
def resource_status(u,c):
    rows=c.execute("""
      SELECT rr.*,v.name village_name FROM resource_requests rr JOIN villages v ON v.id=rr.village_id
      WHERE rr.vet_id=? ORDER BY rr.created_at DESC
    """,(u["id"],)).fetchall()
    return jsonify(requests=[dict(x) for x in rows])

@app.get("/api/govt/overview")
@require_role("government")
def govt_overview(u,c):
    if reconcile_farmer_vet_assignments(c, notify=False):
        c.commit()
    total_farmers=c.execute("SELECT COUNT(*) FROM users WHERE role='farmer'").fetchone()[0]
    total_vets=c.execute("SELECT COUNT(*) FROM users WHERE role='vet'").fetchone()[0]
    total_animals=c.execute("SELECT COUNT(*) FROM animals").fetchone()[0]
    one_year_ago_global = (datetime.now(timezone(timedelta(hours=5, minutes=30))) - timedelta(days=365)).isoformat()
    vaccinated=c.execute("SELECT COUNT(DISTINCT animal_id) FROM vaccinations WHERE administered_at >= ?", (one_year_ago_global,)).fetchone()[0]
    active_cases=c.execute("SELECT COUNT(*) FROM cases WHERE status!='CLOSED'").fetchone()[0]
    closed_cases=c.execute("SELECT COUNT(*) FROM cases WHERE status='CLOSED'").fetchone()[0]
    confirmed_outbreaks=c.execute("SELECT COUNT(DISTINCT village_id) FROM outbreak_assessments WHERE confirmed=1").fetchone()[0]
    # Village risk: driven by stored model predictions / case density, not a fabricated probability.
    village_rows=[]
    for v in c.execute("SELECT * FROM villages ORDER BY name"):
        active=c.execute("""
          SELECT cs.id,hr.ai_prediction_json FROM cases cs
          JOIN animals a ON a.id=cs.animal_id
          LEFT JOIN health_reports hr ON hr.id=(SELECT id FROM health_reports WHERE case_id=cs.id ORDER BY reported_at DESC,id DESC LIMIT 1)
          WHERE a.village_id=? AND cs.status!='CLOSED'
        """,(v["id"],)).fetchall()
        risk_probs=[]
        outbreak_probs=[]
        diseases={}
        for x in active:
            p=json.loads(x["ai_prediction_json"] or "{}") if x["ai_prediction_json"] else {}
            if not isinstance(p,dict): p={}   # a report stored as JSON "null"
            op=p.get("outbreak_probability")
            if isinstance(op,(int,float)): outbreak_probs.append(float(op))
            for pred in (p.get("predictions") or []):
                if isinstance(pred,dict):
                    if isinstance(pred.get("probability"),(int,float)): risk_probs.append(float(pred["probability"]))
                    if pred.get("disease"): diseases[pred["disease"]]=diseases.get(pred["disease"],0)+1
        maxp=max(risk_probs) if risk_probs else None
        max_outbreak=max(outbreak_probs) if outbreak_probs else None
        confirmed=bool(c.execute("SELECT 1 FROM outbreak_assessments WHERE village_id=? AND confirmed=1 LIMIT 1",(v["id"],)).fetchone())
        
        top_disease = max(diseases,key=diseases.get) if diseases else None
        
        treated_count = 0
        if top_disease:
            closed_cases_village = c.execute("""
              SELECT cs.id, hr.ai_prediction_json FROM cases cs
              JOIN animals a ON a.id=cs.animal_id
              LEFT JOIN health_reports hr ON hr.id=(SELECT id FROM health_reports WHERE case_id=cs.id ORDER BY reported_at DESC,id DESC LIMIT 1)
              WHERE a.village_id=? AND cs.status='CLOSED'
            """,(v["id"],)).fetchall()
            for x in closed_cases_village:
                p = json.loads(x["ai_prediction_json"] or "{}") if x["ai_prediction_json"] else {}
                for pred in (p.get("predictions") or []):
                    if isinstance(pred,dict) and pred.get("disease") == top_disease:
                        treated_count += 1
                        break
        else:
            treated_count = c.execute("SELECT COUNT(*) FROM cases cs JOIN animals a ON a.id=cs.animal_id WHERE a.village_id=? AND cs.status='CLOSED'", (v["id"],)).fetchone()[0]

        outbreak_level = "CRITICAL" if (confirmed or (max_outbreak is not None and max_outbreak >= 0.85)) else \
                         "HIGH" if (max_outbreak is not None and max_outbreak >= 0.6) or (len(active) >= 6) else \
                         "MODERATE" if (max_outbreak is not None and max_outbreak >= 0.4) or (3 <= len(active) <= 5) else \
                         None
        if outbreak_level:
            village_rows.append({
                "id":v["id"],"name":v["name"],"pincode":v["pincode"],
                "latitude":v["latitude"],"longitude":v["longitude"],
                "lat":v["latitude"],"lon":v["longitude"],
                "animals":c.execute("SELECT COUNT(*) FROM animals WHERE village_id=?",(v["id"],)).fetchone()[0],
                "farmers":c.execute("SELECT COUNT(DISTINCT farmer_id) FROM animals WHERE village_id=?",(v["id"],)).fetchone()[0],
                "active_cases":len(active),
                "affected_count":len(active),
                "treated_count":treated_count,
                "max_model_probability":maxp,
                "max_outbreak_probability":max_outbreak,"confirmed_outbreak":confirmed,
                "top_disease":top_disease,
                "outbreak_level":outbreak_level
            })
    # Simple time-series for the dashboard.
    trend=[]
    for day in range(13,-1,-1):
        # ISO date in IST; use SQLite date comparison.
        d=(datetime.now(timezone(timedelta(hours=5, minutes=30))).date()).toordinal()-day
        date=str(datetime.fromordinal(d).date())
        cnt=c.execute("SELECT COUNT(*) FROM cases WHERE date(opened_at)=?",(date,)).fetchone()[0]
        trend.append({"date":date,"cases":cnt})
    vets=[]
    
    # Calculate overall resources dispatched to all vets all time & monthly
    total_dispatched_all_time = 0
    total_dispatched_monthly = 0
    first_day_of_month = datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(day=1, hour=0, minute=0, second=0).isoformat()
    
    for v in c.execute("SELECT id,name,phone,address FROM users WHERE role='vet' ORDER BY name"):
        handled=c.execute("SELECT COUNT(*) FROM cases WHERE vet_id=?",(v["id"],)).fetchone()[0]
        closed=c.execute("SELECT COUNT(*) FROM cases WHERE vet_id=? AND status='CLOSED'",(v["id"],)).fetchone()[0]
        
        # New stats (scoped by farmer assignment + village coverage)
        animals_under = c.execute("""
            SELECT COUNT(DISTINCT a.id) FROM animals a JOIN users f ON f.id=a.farmer_id
            WHERE f.assigned_vet_id=? OR (f.assigned_vet_id IS NULL AND a.village_id IN (SELECT village_id FROM vet_villages WHERE vet_id=?))
        """, (v["id"], v["id"])).fetchone()[0]
        farmers_under = c.execute("""
            SELECT COUNT(DISTINCT f.id) FROM users f
            WHERE f.role='farmer' AND f.assigned_vet_id=?
        """, (v["id"],)).fetchone()[0]
        
        one_year_ago = (datetime.now(timezone(timedelta(hours=5, minutes=30))) - timedelta(days=365)).isoformat()
        vacc_animals = c.execute("SELECT COUNT(DISTINCT a.id) FROM animals a JOIN vet_villages vv ON a.village_id=vv.village_id JOIN vaccinations vac ON vac.animal_id=a.id WHERE vv.vet_id=? AND vac.administered_at >= ?", (v["id"], one_year_ago)).fetchone()[0]
        vaccination_pct = round(vacc_animals / animals_under * 100, 1) if animals_under else 0
        
        resources_left, resources_sent = vet_vaccine_totals(c, v["id"])
        
        total_dispatched_all_time += resources_sent
        if resources_left <= 0:
            # check if we already notified them recently to avoid spam, but since we are computing this on read, 
            # we should notify them. But maybe only when vaccination happens! Yes, handled below.
            pass

        # For global stats
        monthly_dispatched = c.execute("SELECT COALESCE(SUM(quantity_change),0) FROM inventory_transactions WHERE vet_id=? AND reason='Govt Dispatch' AND created_at >= ?", (v["id"], first_day_of_month)).fetchone()[0]
        total_dispatched_monthly += monthly_dispatched
        
        vets.append({"id":v["id"],"name":v["name"],"phone":v["phone"],"address":v["address"],
                     "handled":handled,"closed":closed, "animals_under":animals_under, 
                     "farmers_under":farmers_under, "vaccination_pct":vaccination_pct, 
                     "resources_left":resources_left, "resources_sent":resources_sent})
    requests=[]
    for rr in c.execute("""
      SELECT rr.*,v.name village_name,u.name vet_name FROM resource_requests rr
      JOIN villages v ON v.id=rr.village_id JOIN users u ON u.id=rr.vet_id
      ORDER BY CASE rr.status WHEN 'PENDING' THEN 0 ELSE 1 END,rr.created_at DESC
    """):
        d=dict(rr);d["resources"]=json.loads(d.pop("resource_json") or "[]");requests.append(d)
    inventory=[dict(x) for x in c.execute("SELECT * FROM resource_inventory ORDER BY resource_name")]
    vaccination_count=c.execute("SELECT COUNT(*) FROM vaccinations").fetchone()[0]
    vaccines_by_type=[dict(x) for x in c.execute("SELECT vaccine_name,COUNT(*) doses,COALESCE(SUM(dose),0) quantity FROM vaccinations GROUP BY vaccine_name ORDER BY quantity DESC,vaccine_name")]
    vaccines_by_vet=[dict(x) for x in c.execute("SELECT v.vet_id,u.name vet_name,COUNT(*) doses,COALESCE(SUM(v.dose),0) quantity FROM vaccinations v JOIN users u ON u.id=v.vet_id GROUP BY v.vet_id,u.name ORDER BY quantity DESC,u.name")]
    vaccine_usage=[dict(x) for x in c.execute("SELECT it.resource_name,COALESCE(-SUM(CASE WHEN it.quantity_change<0 THEN it.quantity_change ELSE 0 END),0) used_quantity,ri.available_qty remaining_quantity,ri.unit FROM inventory_transactions it LEFT JOIN resource_inventory ri ON lower(ri.resource_name)=lower(it.resource_name) WHERE it.reason='Vaccine administered' GROUP BY it.resource_name,ri.available_qty,ri.unit ORDER BY used_quantity DESC,it.resource_name")]
    return jsonify(
        totals={"farmers":total_farmers,"vets":total_vets,"animals":total_animals,"vaccinated":vaccinated,
                "vaccination_pct":round((vaccinated/total_animals*100),1) if total_animals else 0,
                "vaccination_events":vaccination_count,"active_cases":active_cases,"closed_cases":closed_cases,"confirmed_outbreaks":confirmed_outbreaks,
                "total_dispatched_all_time": total_dispatched_all_time, "total_dispatched_monthly": total_dispatched_monthly},
        villages=village_rows,trend=trend,vets=vets,requests=requests,inventory=inventory,
        vaccinations={"by_type":vaccines_by_type,"by_vet":vaccines_by_vet,"inventory_usage":vaccine_usage},
        radius_km=RADIUS_KM
    )

@app.post("/api/govt/inventory")
@require_role("government")
def govt_inventory(u,c):
    d=json_body()
    name=(d.get("resource_name") or "").strip()
    qty=d.get("available_qty")
    unit=(d.get("unit") or "units").strip()
    if not name or not _is_stock_qty(qty):return jsonify(error="Provide resource name and non-negative quantity."),400
    is_vaccine = name in VACCINE_CODES
    if is_vaccine and (qty != int(qty) or qty > 10**9):
        return jsonify(error="Vaccine stock must be a whole number of doses."),400
    if is_vaccine:
        begin_write(c)   # read the old quantity and write the new one as one step so the ledger stays exact
        old=c.execute("SELECT available_qty FROM resource_inventory WHERE resource_name=?",(name,)).fetchone()
        old_qty=int(old["available_qty"]) if old else 0
    c.execute("""
      INSERT INTO resource_inventory(resource_name,available_qty,unit,updated_at)
      VALUES(?,?,?,?)
      ON CONFLICT(resource_name) DO UPDATE SET available_qty=excluded.available_qty,unit=excluded.unit,updated_at=excluded.updated_at
    """,(name,float(qty),unit,now()))
    if is_vaccine and int(qty)!=old_qty:   # a manual correction is a stock movement too: record it
        _stock_txn(c,"GOVT",None,name,int(qty)-old_qty,int(qty),"MANUAL_ADJUST")
    c.commit();return jsonify(ok=True)

@app.post("/api/govt/resource-request/<int:request_id>")
@require_role("government")
def govt_review_resource(u,c,request_id):
    rr=c.execute("SELECT * FROM resource_requests WHERE id=?",(request_id,)).fetchone()
    if not rr:return jsonify(error="Request not found"),404
    d=json_body();status=d.get("status")
    if status not in ("DISPATCHED","REJECTED","DELIVERED"):return jsonify(error="Invalid status."),400
    
    if status == "DELIVERED" and rr["status"] != "DISPATCHED":
        return jsonify(error="Can only deliver a dispatched request."),400
        
    c.execute("UPDATE resource_requests SET status=?,reviewed_at=?,reviewed_by=? WHERE id=?",(status,now(),u["id"],request_id))
    
    if status == "DELIVERED":
        # Only add to inventory when actually delivered
        resources = json.loads(rr["resource_json"] or "[]")
        for res in resources:
            qty = float(res.get("quantity",0))
            if qty > 0:
                c.execute("""
                  INSERT INTO inventory_transactions(resource_name, quantity_change, unit, reason, vet_id, created_at)
                  VALUES(?, ?, ?, ?, ?, ?)
                """, (res.get("name","Vaccine"), qty, res.get("unit","units"), "Govt Dispatch", rr["vet_id"], now()))

    create_notification(c,rr["vet_id"],f"Resource request {status.lower()}",
        f"Government marked resource request #{request_id} as {status.lower()}.","INFO","RESOURCE",None,rr["village_id"])
    c.commit();return jsonify(ok=True)

@app.post("/api/vet/resource-request/<int:request_id>/deliver")
@require_role("vet")
def vet_deliver_resource(u,c,request_id):
    rr=c.execute("SELECT * FROM resource_requests WHERE id=? AND vet_id=?",(request_id, u["id"])).fetchone()
    if not rr:return jsonify(error="Request not found"),404
    if rr["status"] != "DISPATCHED": return jsonify(error="Can only deliver a dispatched request."),400
    
    c.execute("UPDATE resource_requests SET status=?,reviewed_at=? WHERE id=?",("DELIVERED",now(),request_id))
    
    resources = json.loads(rr["resource_json"] or "[]")
    for res in resources:
        qty = float(res.get("quantity",0))
        if qty > 0:
            c.execute("""
              INSERT INTO inventory_transactions(resource_name, quantity_change, unit, reason, vet_id, created_at)
              VALUES(?, ?, ?, ?, ?, ?)
            """, (res.get("name","Vaccine"), qty, res.get("unit","units"), "Govt Dispatch", rr["vet_id"], now()))

    create_notification(c,u["id"],"Resource Delivered",
        f"You marked resource request #{request_id} as delivered. Stock has been updated.","INFO","RESOURCE",None,rr["village_id"])
    c.commit();return jsonify(ok=True)

# ---------------------------------------------------------------------------
# Vaccine inventory (vet stock, government stock, shipments, low-stock alerts)
# ---------------------------------------------------------------------------
# Every mutation below runs inside ONE write transaction (BEGIN IMMEDIATE) and
# is guarded by a conditional UPDATE (..WHERE quantity>=? / status='IN_TRANSIT'),
# so concurrent or repeated requests can never drive stock negative, release
# stock the government does not have, or credit a shipment twice.

def begin_write(c):
    """Take SQLite's write lock up front so read-then-write sequences cannot interleave."""
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")

def _abort(c, message, code):
    c.rollback()
    return jsonify(error=message), code

def _as_positive_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        n = v
    elif isinstance(v, float) and v.is_integer():
        n = int(v)
    elif isinstance(v, str) and v.strip().isdigit():
        n = int(v.strip())
    else:
        return None
    return n if 0 < n <= 10**9 else None

def _is_stock_qty(v):
    """A real, finite, non-negative number. bool, NaN, Infinity, huge ints and text are not quantities."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v)) and v >= 0
    except OverflowError:
        return False

def _stock_txn(c, party, vet_id, vaccine, change, balance_after, txn_type,
               shipment_id=None, vaccination_id=None, case_id=None, animal_id=None):
    c.execute("""
      INSERT INTO vaccine_stock_transactions(party,vet_id,vaccine,quantity_change,balance_after,txn_type,
                                             shipment_id,vaccination_id,case_id,animal_id,created_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)
    """, (party, vet_id, vaccine, change, balance_after, txn_type, shipment_id, vaccination_id, case_id, animal_id, now()))

def _initial_vet_stock(c, vet_id, vaccine):
    # A vet starts with VET_START_STOCK. A vet that already recorded vaccinations before
    # this feature existed starts with what would be left of it.
    used = c.execute("SELECT COUNT(*) FROM vaccinations WHERE vet_id=? AND upper(vaccine_name)=?",
                     (vet_id, vaccine)).fetchone()[0]
    return max(0, VET_START_STOCK - used)

def ensure_vet_stock(c, vet_id):
    """Create the vet's four stock rows if (and only if) they do not exist yet.
    Existing rows are never touched, so a restart/refresh can never reset stock to 25."""
    begin_write(c)
    for vaccine in VACCINE_CODES:
        if c.execute("SELECT 1 FROM vet_vaccine_stock WHERE vet_id=? AND vaccine=?", (vet_id, vaccine)).fetchone():
            continue
        start = _initial_vet_stock(c, vet_id, vaccine)
        cur = c.execute("INSERT OR IGNORE INTO vet_vaccine_stock(vet_id,vaccine,quantity,updated_at) VALUES(?,?,?,?)",
                        (vet_id, vaccine, start, now()))
        if cur.rowcount == 1:
            _stock_txn(c, "VET", vet_id, vaccine, start, start, "INITIAL")
            if start < LOW_STOCK_THRESHOLD:   # already low from the start (e.g. long vaccination history): don't let it go unnoticed
                who = c.execute("SELECT name FROM users WHERE id=?", (vet_id,)).fetchone()
                check_low_stock(c, vet_id, who["name"] if who else "", vaccine, start)

def ensure_govt_stock(c):
    """Seed the central inventory rows once; existing quantities are never overwritten."""
    begin_write(c)
    for vaccine in VACCINE_CODES:
        cur = c.execute("INSERT OR IGNORE INTO resource_inventory(resource_name,available_qty,unit,updated_at) VALUES(?,?,?,?)",
                        (vaccine, GOVT_START_STOCK, "dose", now()))
        if cur.rowcount == 1:
            _stock_txn(c, "GOVT", None, vaccine, GOVT_START_STOCK, GOVT_START_STOCK, "INITIAL")

def vet_vaccine_stock_view(c, vet_id):
    """Read-only {vaccine: quantity}; never writes (safe for GET handlers)."""
    have = {r["vaccine"]: int(r["quantity"]) for r in
            c.execute("SELECT vaccine,quantity FROM vet_vaccine_stock WHERE vet_id=?", (vet_id,))}
    return {v: have[v] if v in have else _initial_vet_stock(c, vet_id, v) for v in VACCINE_CODES}

def vet_vaccine_totals(c, vet_id):
    """(doses left, doses supplied) across all four vaccines, for the existing dashboard cards."""
    left = sum(vet_vaccine_stock_view(c, vet_id).values())
    received = c.execute("SELECT COALESCE(SUM(quantity),0) FROM vaccine_shipments WHERE vet_id=? AND status='RECEIVED'",
                         (vet_id,)).fetchone()[0]
    return float(left), float(VET_START_STOCK * len(VACCINE_CODES) + received)

def consume_vet_stock(c, vet_id, vaccine):
    """Atomically take 1 vaccine from the vet's stock. Returns the new balance, or None if empty."""
    cur = c.execute("UPDATE vet_vaccine_stock SET quantity=quantity-1, updated_at=? "
                    "WHERE vet_id=? AND vaccine=? AND quantity>=1", (now(), vet_id, vaccine))
    if cur.rowcount != 1:
        return None
    return c.execute("SELECT quantity FROM vet_vaccine_stock WHERE vet_id=? AND vaccine=?",
                     (vet_id, vaccine)).fetchone()[0]

def check_low_stock(c, vet_id, vet_name, vaccine, quantity):
    """Raise ONE government alert when a vet's stock for a vaccine is below the threshold.
    The partial unique index on (vet_id, vaccine) WHERE status='OPEN' makes duplicates impossible."""
    if quantity >= LOW_STOCK_THRESHOLD:
        return False
    cur = c.execute("INSERT OR IGNORE INTO vaccine_alerts(vet_id,vaccine,stock_at_alert,status,created_at) VALUES(?,?,?,?,?)",
                    (vet_id, vaccine, quantity, "OPEN", now()))
    if cur.rowcount != 1:
        return False  # an unresolved alert for this vet + vaccine already exists
    data = {"vet_id": vet_id, "vaccine": vaccine, "stock": quantity}
    for g in c.execute("SELECT id FROM users WHERE role='government'").fetchall():
        create_notification(c, g["id"], f"Low vaccine stock: {vaccine}",
            f"Dr. {vet_name} has only {quantity} {vaccine} vaccine(s) left (critical threshold: below {LOW_STOCK_THRESHOLD}).",
            "CRITICAL", "LOW_STOCK", None, None, data)
    create_notification(c, vet_id, f"Low Stock Alert: {vaccine}",
        f"Your {vaccine} stock has fallen to {quantity}. Government has been notified.",
        "CRITICAL", "LOW_STOCK", None, None, data)
    return True

def _shipment_dict(r):
    return {k: r[k] for k in r.keys()}

_SHIPMENT_SELECT = """
  SELECT s.id,s.vet_id,vu.name vet_name,s.vaccine,s.quantity,s.status,
         s.released_by,gu.name released_by_name,s.released_at,s.received_at
  FROM vaccine_shipments s
  JOIN users vu ON vu.id=s.vet_id
  LEFT JOIN users gu ON gu.id=s.released_by
"""

@app.get("/api/vet/vaccine-inventory")
@require_role("vet")
def vet_vaccine_inventory(u,c):
    stock = vet_vaccine_stock_view(c, u["id"])
    shipments = [_shipment_dict(r) for r in c.execute(
        _SHIPMENT_SELECT + " WHERE s.vet_id=? ORDER BY s.id DESC", (u["id"],))]
    return jsonify(threshold=LOW_STOCK_THRESHOLD,
                   stock=[{"vaccine": v, "quantity": stock[v], "low": stock[v] < LOW_STOCK_THRESHOLD} for v in VACCINE_CODES],
                   shipments=shipments)

@app.get("/api/govt/vaccine-inventory")
@require_role("government")
def govt_vaccine_inventory(u,c):
    have = {r["resource_name"]: r["available_qty"] for r in c.execute("SELECT resource_name,available_qty FROM resource_inventory")}
    central = [{"vaccine": v, "quantity": int(have[v]) if v in have else GOVT_START_STOCK} for v in VACCINE_CODES]
    vets = []
    for v in c.execute("SELECT id,name FROM users WHERE role='vet' ORDER BY name").fetchall():
        st = vet_vaccine_stock_view(c, v["id"])
        vets.append({"vet_id": v["id"], "name": v["name"], "stock": st,
                     "low": [k for k in VACCINE_CODES if st[k] < LOW_STOCK_THRESHOLD]})
    alerts = [dict(r) for r in c.execute("""
        SELECT a.id,a.vet_id,u.name vet_name,a.vaccine,a.stock_at_alert,a.created_at,
               COALESCE(s.quantity,0) current_stock
        FROM vaccine_alerts a JOIN users u ON u.id=a.vet_id
        LEFT JOIN vet_vaccine_stock s ON s.vet_id=a.vet_id AND s.vaccine=a.vaccine
        WHERE a.status='OPEN' ORDER BY a.created_at DESC,a.id DESC""")]
    return jsonify(threshold=LOW_STOCK_THRESHOLD, central=central, vets=vets, open_alerts=alerts)

@app.get("/api/govt/vaccine-shipments")
@require_role("government")
def govt_vaccine_shipments(u,c):
    where, params = [], []
    status = (request.args.get("status") or "").upper()
    if status:
        if status not in ("IN_TRANSIT", "RECEIVED"):
            return jsonify(error="Status must be IN_TRANSIT or RECEIVED."), 400
        where.append("s.status=?"); params.append(status)
    if request.args.get("vet_id"):
        try: where.append("s.vet_id=?"); params.append(int(request.args["vet_id"]))
        except ValueError: return jsonify(error="vet_id must be a number."), 400
    sql = _SHIPMENT_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY s.id DESC"
    return jsonify(shipments=[_shipment_dict(r) for r in c.execute(sql, params)])

@app.post("/api/govt/vaccine-shipments")
@require_role("government")
def govt_release_vaccine(u,c):
    d = request.json if isinstance(request.json, dict) else {}
    vaccine = str(d.get("vaccine") or "").strip().upper()
    if vaccine not in VACCINE_CODES:
        return jsonify(error="Vaccine must be one of FMD, HS, LSD or BQ."), 400
    qty = _as_positive_int(d.get("quantity"))
    if qty is None:
        return jsonify(error="Quantity must be a positive whole number."), 400
    vet_id = _as_positive_int(d.get("vet_id"))
    if vet_id is None: return jsonify(error="Choose a veterinarian."), 400
    key = str(d.get("idempotency_key") or "").strip()[:100] or None

    begin_write(c)
    if key:  # a repeated submit of the same release returns the original shipment instead of releasing twice
        ex = c.execute(_SHIPMENT_SELECT + " WHERE s.released_by=? AND s.idempotency_key=?", (u["id"], key)).fetchone()
        if ex:
            c.rollback()
            if (ex["vet_id"], ex["vaccine"], ex["quantity"]) != (vet_id, vaccine, qty):
                return jsonify(error="This idempotency_key was already used for a different release."), 409
            return jsonify(ok=True, duplicate=True, shipment=_shipment_dict(ex))
    vet = c.execute("SELECT id,name FROM users WHERE id=? AND role='vet'", (vet_id,)).fetchone()
    if not vet:
        return _abort(c, "Veterinarian not found.", 404)
    ensure_govt_stock(c)
    cur = c.execute("UPDATE resource_inventory SET available_qty=available_qty-?, updated_at=? "
                    "WHERE resource_name=? AND available_qty>=?", (qty, now(), vaccine, qty))
    if cur.rowcount != 1:
        have = c.execute("SELECT available_qty FROM resource_inventory WHERE resource_name=?", (vaccine,)).fetchone()[0]
        return _abort(c, f"Insufficient government stock for {vaccine}: {int(have)} available, {qty} requested.", 409)
    balance = int(c.execute("SELECT available_qty FROM resource_inventory WHERE resource_name=?", (vaccine,)).fetchone()[0])
    c.execute("""INSERT INTO vaccine_shipments(vet_id,vaccine,quantity,status,released_by,released_at,idempotency_key)
                 VALUES(?,?,?,'IN_TRANSIT',?,?,?)""", (vet_id, vaccine, qty, u["id"], now(), key))
    sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    _stock_txn(c, "GOVT", vet_id, vaccine, -qty, balance, "SHIPMENT_RELEASED", shipment_id=sid)
    create_notification(c, vet_id, "Vaccine shipment in transit",
        f"Government released {qty} {vaccine} vaccine(s) to you (shipment #{sid}). "
        "Confirm 'Vaccine Received' when they arrive; your stock updates only then.",
        "INFO", "SHIPMENT", None, None, {"shipment_id": sid, "vaccine": vaccine, "quantity": qty})
    c.commit()
    ship = c.execute(_SHIPMENT_SELECT + " WHERE s.id=?", (sid,)).fetchone()
    return jsonify(ok=True, shipment=_shipment_dict(ship), government_stock_remaining=balance), 201

@app.post("/api/vet/vaccine-shipments/<int:shipment_id>/receive")
@require_role("vet")
def vet_receive_vaccine(u,c,shipment_id):
    begin_write(c)
    ship = c.execute("SELECT * FROM vaccine_shipments WHERE id=? AND vet_id=?", (shipment_id, u["id"])).fetchone()
    if not ship:  # unknown id and someone else's shipment look identical on purpose
        return _abort(c, "Shipment not found.", 404)
    ensure_vet_stock(c, u["id"])
    # The status flip is the gate: only the request that changes IN_TRANSIT -> RECEIVED may credit stock.
    cur = c.execute("UPDATE vaccine_shipments SET status='RECEIVED', received_at=?, received_by=? "
                    "WHERE id=? AND vet_id=? AND status='IN_TRANSIT'", (now(), u["id"], shipment_id, u["id"]))
    if cur.rowcount != 1:
        return _abort(c, "This shipment has already been received.", 409)
    c.execute("UPDATE vet_vaccine_stock SET quantity=quantity+?, updated_at=? WHERE vet_id=? AND vaccine=?",
              (ship["quantity"], now(), u["id"], ship["vaccine"]))
    balance = c.execute("SELECT quantity FROM vet_vaccine_stock WHERE vet_id=? AND vaccine=?",
                        (u["id"], ship["vaccine"])).fetchone()[0]
    _stock_txn(c, "VET", u["id"], ship["vaccine"], ship["quantity"], balance, "SHIPMENT_RECEIVED", shipment_id=shipment_id)
    if balance >= LOW_STOCK_THRESHOLD:
        c.execute("UPDATE vaccine_alerts SET status='RESOLVED', resolved_at=?, resolved_by_shipment_id=? "
                  "WHERE vet_id=? AND vaccine=? AND status='OPEN'", (now(), shipment_id, u["id"], ship["vaccine"]))
    for g in c.execute("SELECT id FROM users WHERE role='government'").fetchall():
        create_notification(c, g["id"], "Vaccine shipment delivered",
            f"Dr. {u['name']} confirmed receipt of {ship['quantity']} {ship['vaccine']} vaccine(s) (shipment #{shipment_id}).",
            "INFO", "SHIPMENT", None, None, {"shipment_id": shipment_id, "vaccine": ship["vaccine"], "quantity": ship["quantity"]})
    c.commit()
    return jsonify(ok=True, shipment_id=shipment_id, status="RECEIVED", vaccine=ship["vaccine"],
                   quantity=ship["quantity"], new_stock=balance)

@app.get("/api/vet/outbreak-map")
@require_role("vet")
def vet_outbreak_map(u,c):
    if reconcile_farmer_vet_assignments(c, notify=False):
        c.commit()
    """Everything in this vet's area, outbreak or not: the vet's own point, every
    village they cover, every farmer assigned to them, and each village's current
    Outbreak Risk Score from the same engine that sends the alerts.
    Positions are only ever real stored coordinates (farmer -> their animal ->
    their village); anything without one is returned with lat/lon = None."""
    since = (datetime.fromisoformat(now()) - timedelta(days=7)).isoformat()

    farmer_rows=[]
    for f in c.execute("""
        SELECT f.id,f.name,f.phone,f.village,
               COALESCE(f.latitude,(SELECT a.latitude FROM animals a WHERE a.farmer_id=f.id AND a.latitude IS NOT NULL LIMIT 1),
                        (SELECT v.latitude FROM animals a JOIN villages v ON v.id=a.village_id WHERE a.farmer_id=f.id AND v.latitude IS NOT NULL LIMIT 1)) lat,
               COALESCE(f.longitude,(SELECT a.longitude FROM animals a WHERE a.farmer_id=f.id AND a.longitude IS NOT NULL LIMIT 1),
                        (SELECT v.longitude FROM animals a JOIN villages v ON v.id=a.village_id WHERE a.farmer_id=f.id AND v.longitude IS NOT NULL LIMIT 1)) lon
        FROM users f WHERE f.role='farmer' AND f.assigned_vet_id=? ORDER BY f.name
    """,(u["id"],)).fetchall():
        farmer_rows.append({
            "id":f["id"],"name":f["name"],"phone":f["phone"],"village":f["village"],
            "lat":f["lat"],"lon":f["lon"],
            "animals":c.execute("SELECT COUNT(*) FROM animals WHERE farmer_id=?",(f["id"],)).fetchone()[0],
            "open_cases":c.execute("SELECT COUNT(*) FROM cases WHERE farmer_id=? AND status!='CLOSED'",(f["id"],)).fetchone()[0],
        })
    farmer_ids=[f["id"] for f in farmer_rows]
    q=",".join("?"*len(farmer_ids)) or "NULL"

    # Villages this vet covers, plus any village where one of their farmers keeps animals.
    village_ids={r[0] for r in c.execute("SELECT village_id FROM vet_villages WHERE vet_id=?",(u["id"],))}
    village_ids|={r[0] for r in c.execute(f"SELECT DISTINCT village_id FROM animals WHERE farmer_id IN ({q})",farmer_ids)}

    village_rows=[]
    for vid in sorted(v for v in village_ids if v is not None):
        v=c.execute("SELECT * FROM villages WHERE id=?",(vid,)).fetchone()
        if not v:
            continue
        lat,lon=v["latitude"],v["longitude"]
        if lat is None or lon is None:
            # Village never geocoded: use the centre of its farmers' real positions.
            pts=[(f["lat"],f["lon"]) for f in farmer_rows if f["lat"] is not None and
                 c.execute("SELECT 1 FROM animals WHERE farmer_id=? AND village_id=? LIMIT 1",(f["id"],vid)).fetchone()]
            if pts:
                lat=sum(p[0] for p in pts)/len(pts); lon=sum(p[1] for p in pts)/len(pts)

        # Current outbreak status: run the engine for each disease reported here this week.
        diseases=set()
        for (pj,) in c.execute("""
            SELECT hr.ai_prediction_json FROM health_reports hr JOIN animals a ON a.id=hr.animal_id
            WHERE a.village_id=? AND hr.reported_at>=?""",(vid,since)):
            try:
                pred=json.loads(pj or "{}")
                d=canonical_disease(((pred.get("predictions") or [{}])[0] or {}).get("disease")) if isinstance(pred,dict) else None
            except Exception:
                d=None
            if d and d!="Healthy":
                diseases.add(d)
        best=None
        for d in sorted(diseases):
            r=calculate_outbreak_risk(c,d,lat,lon,v["name"])
            if best is None or r["outbreak_risk_score"]>best["outbreak_risk_score"]:
                best=r
        confirmed=bool(c.execute("SELECT 1 FROM outbreak_assessments WHERE village_id=? AND confirmed=1 LIMIT 1",(vid,)).fetchone())
        village_rows.append({
            "id":vid,"name":v["name"],"pincode":v["pincode"],"lat":lat,"lon":lon,
            "farmers":c.execute(f"SELECT COUNT(DISTINCT farmer_id) FROM animals WHERE village_id=? AND farmer_id IN ({q})",[vid]+farmer_ids).fetchone()[0],
            "animals":c.execute(f"SELECT COUNT(*) FROM animals WHERE village_id=? AND farmer_id IN ({q})",[vid]+farmer_ids).fetchone()[0],
            "open_cases":c.execute("SELECT COUNT(*) FROM cases cs JOIN animals a ON a.id=cs.animal_id WHERE a.village_id=? AND cs.vet_id=? AND cs.status!='CLOSED'",(vid,u["id"])).fetchone()[0],
            "outbreak_level":best["outbreak_status"] if best else "LOW",
            "outbreak_risk_score":best["outbreak_risk_score"] if best else 0,
            "top_disease":best["disease"] if best else None,
            "confirmed_outbreak":confirmed,
        })

    return jsonify(villages=village_rows, farmers=farmer_rows,
                   vet={"latitude":u["latitude"],"longitude":u["longitude"],"name":u["name"],"address":u["address"]})
@app.get("/api/sync")
def api_sync():
    """Bidirectional sync: push any pending SQLite changes/sync_queue to MongoDB,
    and pull any new/updated MongoDB documents into local SQLite."""
    if not MONGO_URI:
        c = conn()
        pending = c.execute("SELECT COUNT(*) FROM sync_queue WHERE status='PENDING'").fetchone()[0]
        c.close()
        return jsonify(ok=True, synced=0, pulled=0, pending=pending, msg="MongoDB not configured")

    mdb = _get_mongo_db()
    if mdb is None:
        c = conn()
        pending = c.execute("SELECT COUNT(*) FROM sync_queue WHERE status='PENDING'").fetchone()[0]
        c.close()
        return jsonify(ok=True, synced=0, pulled=0, pending=pending, msg="MongoDB temporarily unreachable")

    pushed = _flush_sqlite_tables_to_mongo(SYNC_TABLES)
    synced = pushed
    failed = 0
    pulled = 0

    c = conn()
    try:
        for row in c.execute("SELECT id, collection, document_id, document_json FROM sync_queue WHERE status='PENDING' AND retry_count < 5 ORDER BY created_at LIMIT 100").fetchall():
            try:
                doc = json.loads(row["document_json"])
                doc_id = doc.get("_id", doc.get("id", row["document_id"]))
                doc["_id"] = doc_id
                mdb[row["collection"]].update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
                c.execute("UPDATE sync_queue SET status='SYNCED', synced_at=? WHERE id=?", (now(), row["id"]))
                synced += 1
            except Exception as e:
                c.execute("UPDATE sync_queue SET retry_count=retry_count+1, last_error=? WHERE id=?", (str(e), row["id"]))
                failed += 1
        c.commit()

        pulled = hydrate_sqlite_from_mongo(c, incremental=True)
        if pulled > 0:
            merge_duplicate_open_cases(c)
            backfill_farmer_vet_assignment(c)
            c.commit()
        pending = c.execute("SELECT COUNT(*) FROM sync_queue WHERE status='PENDING'").fetchone()[0]
    except Exception as e:
        print(f"[GramVet] Sync worker error: {e}", flush=True)
        pending = 0
    finally:
        c.close()

    return jsonify(ok=True, synced=synced, pulled=pulled, pending=pending, failed=failed,
                   msg=f"Synced {synced} to MongoDB, pulled {pulled} updates")


if __name__=="__main__":
    init()
    app.run(host=os.environ.get("HOST","127.0.0.1"),port=int(os.environ.get("PORT","5000")),debug=True)
