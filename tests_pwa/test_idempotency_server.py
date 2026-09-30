"""Idempotency-Key: a request sent again with the same key must not be applied twice.
Real Flask app, scratch copy of the project.   Run:  python test_idempotency_server.py <COPY of the project>
(it writes rows - never point it at your live folder)"""
import os, sys, time, threading, warnings
from datetime import datetime, timezone, timedelta
warnings.filterwarnings("ignore")
proj = os.path.abspath(sys.argv[1]); os.chdir(proj); sys.path.insert(0, proj)
os.environ["MONGO_URI"] = "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300"
import app as A
A.init()                                   # make sure the tables of THIS version of app.py exist
SLOW = {"secs": 0}
def slow_mongo(coll, doc):
    if SLOW["secs"]: time.sleep(SLOW["secs"])
A.mongo_upsert = slow_mongo                # lets a test keep a request "in flight" for a moment

passed = failed = 0
def ok(cond, name, extra=""):
    global passed, failed
    if cond: passed += 1; print("  ok   " + name)
    else: failed += 1; print("  FAIL " + name + ("  -> " + str(extra) if extra else ""))

def client(uid):
    c = A.app.test_client()
    with c.session_transaction() as s: s["uid"] = uid
    return c
VET, F86, F10 = 2, 86, 10
def n_actions(case_id):
    c = A.conn(); n = c.execute("SELECT COUNT(*) FROM case_actions WHERE case_id=?", (case_id,)).fetchone()[0]; c.close(); return n
def n_reports():
    c = A.conn(); n = c.execute("SELECT COUNT(*) FROM health_reports").fetchone()[0]; c.close(); return n
def act(cl, key=None, case=1, details="x", at=None):
    h = {"Idempotency-Key": key} if key is not None else {}
    return cl.post("/api/vet/cases/%d/action" % case, json={"action_type": "NOTE", "details": details}, headers=h)
K = lambda tag: (tag + "0123456789abcdef0123456789")[:32]

vet = client(VET)
print("I1  the same key twice -> applied once, second answer is the first answer")
n0 = n_actions(1)
r1 = act(vet, K("a1")); r2 = act(vet, K("a1"))
ok(r1.status_code == 200 and r2.status_code == 200, "both answered 200", (r1.status_code, r2.status_code))
ok(n_actions(1) - n0 == 1, "exactly one row created", n_actions(1) - n0)
ok(r1.get_json() == r2.get_json(), "same body both times")
ok(r2.headers.get("Idempotent-Replay") == "true" and r1.headers.get("Idempotent-Replay") is None, "second one is marked as a replay")

print("I2  different keys, and no key at all, still work normally")
n0 = n_actions(1)
act(vet, K("b1")); act(vet, K("b2")); act(vet); act(vet)
ok(n_actions(1) - n0 == 4, "4 distinct requests -> 4 rows", n_actions(1) - n0)

print("I3  a malformed key is ignored (request runs as before)")
n0 = n_actions(1)
act(vet, "short"); act(vet, "short"); act(vet, "has spaces in it, not allowed at all!!")
ok(n_actions(1) - n0 == 3, "no de-duplication on a bad key", n_actions(1) - n0)

print("I4  the key belongs to the user: another user with the same key is not answered from someone else's result")
body = {"animal_id": 2, "age_months": 30, "herd_size": 2, "dist_waterbody_km": 1.0, "symptoms": list(A.SYMPTOMS)[:2]}
b0 = n_reports()
ra = client(F86).post("/api/cases/report", json=body, headers={"Idempotency-Key": K("c1")})
body10 = dict(body, animal_id=1)
rb = client(F10).post("/api/cases/report", json=body10, headers={"Idempotency-Key": K("c1")})
ok(ra.status_code == 200 and rb.status_code == 200 and rb.headers.get("Idempotent-Replay") is None, "farmer 10 is not treated as a replay of farmer 86", (ra.status_code, rb.status_code, rb.get_json()))
ok(n_reports() - b0 == 2, "two reports created", n_reports() - b0)

print("I5  a report sent twice with one key -> one report, same case id")
b0 = n_reports(); f = client(F86)
r1 = f.post("/api/cases/report", json=body, headers={"Idempotency-Key": K("d1")}); r2 = f.post("/api/cases/report", json=body, headers={"Idempotency-Key": K("d1")})
ok(n_reports() - b0 == 1, "one report row", n_reports() - b0)
ok((r1.get_json() or {}).get("case_id") is not None and r1.get_json().get("case_id") == r2.get_json().get("case_id"), "second answer carries the same case id")

print("I6  a FAILED first try is not remembered: fixing the problem and retrying with the same key works")
b0 = n_reports()
bad = f.post("/api/cases/report", json=dict(body, symptoms=[]), headers={"Idempotency-Key": K("e1")})
good = f.post("/api/cases/report", json=body, headers={"Idempotency-Key": K("e1")})
ok(bad.status_code == 400, "first try refused (400)", bad.status_code)
ok(good.status_code == 200 and good.headers.get("Idempotent-Replay") is None and n_reports() - b0 == 1, "retry with the same key runs for real", (good.status_code, n_reports() - b0))

print("I7  a copy that arrives while the first is still running is not run a second time")
SLOW["secs"] = 0.6; n0 = n_actions(1); out = []
def go(): out.append(act(client(VET), K("f1")).status_code)
t1 = threading.Thread(target=go); t1.start(); time.sleep(0.15)
r_mid = act(client(VET), K("f1"))
t1.join(); SLOW["secs"] = 0
safe = (r_mid.status_code == 503 and r_mid.headers.get("Retry-After")) or (r_mid.status_code == 200 and r_mid.headers.get("Idempotent-Replay") == "true")
ok(safe, "second copy is never run again: told to retry (503) or given the stored result (200 replay)", (r_mid.status_code, dict(r_mid.headers)))
ok(out == [200], "the first copy finished normally", out)
r_after = act(client(VET), K("f1"))
ok(r_after.status_code == 200 and r_after.headers.get("Idempotent-Replay") == "true", "a retry afterwards gets the stored result")
ok(n_actions(1) - n0 == 1, "only one row in the end", n_actions(1) - n0)

print("I7b an unfinished claim that is still fresh -> 503 with Retry-After, and nothing runs")
c = A.conn(); c.execute("INSERT INTO idempotency_keys(key,user_id,status,created_at) VALUES(?,?,0,?)", (K("j1"), VET, datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
n0 = n_actions(1); r = act(vet, K("j1"))
ok(r.status_code == 503 and r.headers.get("Retry-After") == "2" and "already being processed" in r.get_json()["error"], "503 + Retry-After", (r.status_code, r.get_json()))
ok(n_actions(1) == n0, "nothing was run")
c = A.conn(); still = c.execute("SELECT status FROM idempotency_keys WHERE key=?", (K("j1"),)).fetchone(); c.close()
ok(still is not None and still["status"] == 0, "the other request's claim is left alone")

print("I8  a crashed first request (claim never finished) does not block the key forever")
old = (datetime.now(timezone.utc) - timedelta(seconds=A.IDEM_STALE_SECONDS + 30)).isoformat()
c = A.conn(); c.execute("INSERT INTO idempotency_keys(key,user_id,status,created_at) VALUES(?,?,0,?)", (K("g1"), VET, old)); c.commit(); c.close()
n0 = n_actions(1); r = act(vet, K("g1"))
ok(r.status_code == 200 and n_actions(1) - n0 == 1, "stale claim is taken over and the request runs", (r.status_code, n_actions(1) - n0))

print("I9  old remembered results are cleaned up")
old = (datetime.now(timezone.utc) - timedelta(days=A.IDEM_KEEP_DAYS + 2)).isoformat()
c = A.conn(); c.execute("INSERT INTO idempotency_keys(key,user_id,status,response,created_at) VALUES(?,?,200,'{}',?)", (K("h1"), VET, old)); c.commit(); c.close()
act(vet, K("h2"))
c = A.conn(); left = c.execute("SELECT COUNT(*) FROM idempotency_keys WHERE key=?", (K("h1"),)).fetchone()[0]; c.close()
ok(left == 0, "a result older than %d days is removed" % A.IDEM_KEEP_DAYS, left)

print("I10 not signed in: no claim is made and the route answers as before")
anon = A.app.test_client(); r = anon.post("/api/vet/cases/1/action", json={"action_type": "NOTE", "details": "x"}, headers={"Idempotency-Key": K("i1")})
c = A.conn(); left = c.execute("SELECT COUNT(*) FROM idempotency_keys WHERE key=?", (K("i1"),)).fetchone()[0]; c.close()
ok(r.status_code in (401, 403) and left == 0, "refused, nothing stored", (r.status_code, left))

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
