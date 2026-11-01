"""Voice + report route, against the real Flask app in a scratch copy of the project.
Run:  python test_voice_server.py <path to a COPY of the project>
(The test writes cases and audio files, so never point it at your live folder.)"""
import base64, os, sys, warnings
warnings.filterwarnings("ignore")
proj = os.path.abspath(sys.argv[1]); os.chdir(proj); sys.path.insert(0, proj)
os.environ["MONGO_URI"] = "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300"
os.environ.pop("GEMINI_API_KEY", None)
import app as A

gemini_calls = []
real_post = A.requests.post
def spy(url, *a, **k):
    if "generativelanguage" in str(url) or "gemini" in str(url).lower():
        gemini_calls.append(url)
    return real_post(url, *a, **k)
A.requests.post = spy

passed = failed = 0
def ok(cond, name, extra=""):
    global passed, failed
    if cond: passed += 1; print("  ok   " + name)
    else: failed += 1; print("  FAIL " + name + ("  -> " + str(extra) if extra else ""))

cl = A.app.test_client()
with cl.session_transaction() as s: s["uid"] = 86
AUDIO = "data:audio/webm;base64," + base64.b64encode(b"fake-webm-bytes-for-test" * 40).decode()
SYM = list(A.SYMPTOMS)[:2]
base = {"animal_id": 2, "age_months": 48, "herd_size": 3, "dist_waterbody_km": 1.2}

print("R1  symptoms ticked + recording -> saved, recording attached")
r = cl.post("/api/cases/report", json=dict(base, symptoms=SYM, audio_base64=AUDIO))
j = r.get_json() or {}
ok(r.status_code == 200, "report accepted (200)", (r.status_code, j.get("error")))
ok(not gemini_calls, "no Gemini call made")
cid = j.get("case_id") or (j.get("case") or {}).get("id")
det = cl.get("/api/cases/%s" % cid).get_json() if cid else {}
reps = (det.get("case") or det).get("reports") or []
url = next((x.get("audio_url") for x in reps if x.get("audio_url")), None)
ok(bool(url), "case history has an audio_url for the player", str(list(det)[:6]))
if url:
    ok(cl.get(url).status_code == 200, "the recording file is served back", url)

print("R2  recording only, nothing ticked")
r = cl.post("/api/cases/report", json=dict(base, symptoms=[], audio_base64=AUDIO))
print("      server answered:", r.status_code, (r.get_json() or {}).get("error"))
ok(r.status_code == 400, "clear 400 (not a 503 that the phone would retry)", r.status_code)
ok("symptom" in str((r.get_json() or {}).get("error", "")).lower(), "message asks for symptoms")
ok(not gemini_calls, "still no Gemini call")

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
