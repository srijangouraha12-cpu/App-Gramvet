"""Upload size limit on the report route. Real Flask app, scratch copy.   Run: python test_limits_server.py <COPY of the project>"""
import os, sys, warnings
warnings.filterwarnings("ignore")
proj = os.path.abspath(sys.argv[1]); os.chdir(proj); sys.path.insert(0, proj)
os.environ["MONGO_URI"] = "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300"
import app as A
A.init()
passed = failed = 0
def ok(cond, name, extra=""):
    global passed, failed
    if cond: passed += 1; print("  ok   " + name)
    else: failed += 1; print("  FAIL " + name + ("  -> " + str(extra) if extra else ""))
cl = A.app.test_client()
with cl.session_transaction() as s: s["uid"] = 86
def n_reports():
    c = A.conn(); n = c.execute("SELECT COUNT(*) FROM health_reports").fetchone()[0]; c.close(); return n
def audio(chars): return "data:audio/webm;base64," + "A" * chars          # chars must be a multiple of 4
body = lambda a: {"animal_id": 2, "age_months": 30, "herd_size": 2, "dist_waterbody_km": 1.0, "symptoms": list(A.SYMPTOMS)[:2], "audio_base64": a}
K = lambda t: (t + "0123456789abcdef0123456789")[:32]

print("L1  a normal 60-second recording (about 1.3 MB) still works")
n0 = n_reports(); r = cl.post("/api/cases/report", json=body(audio(1_300_000)))
ok(r.status_code == 200 and n_reports() - n0 == 1, "accepted and saved", (r.status_code, (r.get_json() or {}).get("error")))

print("L2  a 17 MB upload is refused before it is read, with a message the phone can show")
n0 = n_reports(); r = cl.post("/api/cases/report", json=body(audio(17_000_000)))
j = r.get_json(silent=True) or {}
ok(r.status_code == 413, "413", r.status_code)
ok("too large" in str(j.get("error", "")), "JSON error the outbox can show", r.get_data(as_text=True)[:120])
ok(n_reports() == n0, "nothing was saved")

print("L3  the limit sits between 'big recording' and 'abuse'")
n0 = n_reports(); r = cl.post("/api/cases/report", json=body(audio(15_000_000)))
ok(r.status_code == 200, "a 15 MB request is still accepted", r.status_code)

print("L4  a refused upload does not burn its Idempotency-Key")
k = K("z9"); n0 = n_reports()
big = cl.post("/api/cases/report", json=body(audio(17_000_000)), headers={"Idempotency-Key": k})
small = cl.post("/api/cases/report", json=body(audio(400_000)), headers={"Idempotency-Key": k})
ok(big.status_code == 413 and small.status_code == 200 and small.headers.get("Idempotent-Replay") is None and n_reports() - n0 == 1,
   "same key works after a smaller retry", (big.status_code, small.status_code, n_reports() - n0))

print("\n%d passed, %d failed" % (passed, failed)); sys.exit(1 if failed else 0)
