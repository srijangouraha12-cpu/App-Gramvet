"""Vet case writes that can now be queued offline, against the real Flask app in a scratch copy.
Run:  python test_vet_server.py <path to a COPY of the project>   (it writes rows; never use your live folder)"""
import os, sys, warnings
from datetime import datetime, timedelta, timezone
warnings.filterwarnings("ignore")
proj = os.path.abspath(sys.argv[1]); os.chdir(proj); sys.path.insert(0, proj)
os.environ["MONGO_URI"] = "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300"
import app as A

mongo_docs = []
A.mongo_upsert = lambda coll, doc: mongo_docs.append((coll, dict(doc)))

passed = failed = 0
def ok(cond, name, extra=""):
    global passed, failed
    if cond: passed += 1; print("  ok   " + name)
    else: failed += 1; print("  FAIL " + name + ("  -> " + str(extra) if extra else ""))

cl = A.app.test_client()
with cl.session_transaction() as s: s["uid"] = 2          # a vet with open cases 1, 2, 3
def last_action(case_id):
    c = A.conn(); r = c.execute("SELECT * FROM case_actions WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,)).fetchone(); c.close(); return r
def count_actions(case_id):
    c = A.conn(); n = c.execute("SELECT COUNT(*) FROM case_actions WHERE case_id=?", (case_id,)).fetchone()[0]; c.close(); return n
def post(case, kind, body=None): return cl.post("/api/vet/cases/%d/%s" % (case, kind), json=body)
def iso_z(dt): return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
def near(stored, want, secs=3): return abs((datetime.fromisoformat(stored) - want).total_seconds()) < secs
NOW = lambda: datetime.now(timezone.utc)

def client(uid):
    c = A.app.test_client()
    with c.session_transaction() as s: s["uid"] = uid
    return c

print("V1  action_at handling for queued offline actions")
def try_at(raw, label, expect):
    body = {"action_type": "NOTE", "details": "t-" + label}
    if raw is not None: body["action_at"] = raw
    r = post(1, "action", body); row = last_action(1)
    ok(r.status_code == 200, label + ": accepted", r.get_json())
    ok(near(row["action_at"], expect() if callable(expect) else expect), label + ": stored " + row["action_at"])
    return row
two_h = NOW() - timedelta(hours=2)
row = try_at(iso_z(two_h), "2 hours ago (offline action)", two_h)
ok(mongo_docs[-1][1]["action_at"] == row["action_at"], "MongoDB copy carries the same time")
try_at(None, "no action_at (online, unchanged)", NOW)
try_at(iso_z(NOW() + timedelta(days=1)), "future time", NOW)
old30, old59 = NOW() - timedelta(days=30), NOW() - timedelta(days=59)
try_at(iso_z(old30), "30 days old (kept: the window is 60 days)", old30)
try_at(iso_z(old59), "59 days old (kept)", old59)
try_at(iso_z(NOW() - timedelta(days=61)), "61 days old (replaced by now)", NOW)
try_at(iso_z(NOW() - timedelta(days=400)), "400 days old, a badly wrong phone clock (replaced by now)", NOW)
try_at("not-a-date", "garbage", NOW)
try_at("2026-09-29T10:00:00", "no timezone", NOW)

print("V2  close needs a CURE first; CURE then close works (the order the outbox replays in)")
r = post(3, "close"); ok(r.status_code == 400 and "CURE" in r.get_json()["error"], "close without CURE refused", (r.status_code, r.get_json()))
r = post(2, "action", {"action_type": "CURE", "details": "Recovered", "action_at": iso_z(NOW() - timedelta(hours=1))}); ok(r.status_code == 200, "CURE accepted")
r = post(2, "close"); ok(r.status_code == 200, "close accepted after CURE", r.get_json())
c = A.conn(); st = c.execute("SELECT status FROM cases WHERE id=2").fetchone()[0]; c.close(); ok(st == "CLOSED", "case is CLOSED", st)
r = post(2, "close"); ok(r.status_code == 200, "closing twice is harmless (a replay after a lost reply)")

print("V3  model review (approve / reject)")
r = post(1, "model-review", {"correct": True, "predicted": "FMD", "notes": "ok"}); ok(r.status_code == 200, "accepted", r.get_json())
r = post(1, "model-review", {"predicted": "FMD"}); ok(r.status_code == 400, "missing verdict refused", r.status_code)

print("V4  another vet's case is refused (what a replay hits if the case was reassigned)")
r = post(999, "action", {"action_type": "NOTE", "details": "x"}); ok(r.status_code == 404 and "not assigned" in r.get_json()["error"], "404 with a readable reason", r.get_json())

print("V6  a case cannot be closed over a report that is newer than the vet's last CURE")
c = A.conn(); row = c.execute("SELECT animal_id, farmer_id FROM cases WHERE id=3").fetchone(); c.close()
farmer3 = client(row["farmer_id"]); rb = {"animal_id": row["animal_id"], "age_months": 30, "herd_size": 2, "dist_waterbody_km": 1.0, "symptoms": list(A.SYMPTOMS)[:2]}
post(3, "action", {"action_type": "CURE", "details": "cured", "action_at": iso_z(NOW() - timedelta(hours=3))})
rr = farmer3.post("/api/cases/report", json=dict(rb, submitted_at=iso_z(NOW() - timedelta(hours=1))))
ok(rr.status_code == 200 and rr.get_json().get("case_id") == 3, "farmer reports again on the same case, 1 hour ago", (rr.status_code, rr.get_json() and rr.get_json().get("case_id")))
r = post(3, "close")
ok(r.status_code == 400 and "newer report" in r.get_json()["error"], "close refused with a clear message", (r.status_code, r.get_json()))
c = A.conn(); st = c.execute("SELECT status FROM cases WHERE id=3").fetchone()[0]; c.close(); ok(st != "CLOSED", "the case stays open", st)
post(3, "action", {"action_type": "CURE", "details": "re-examined, recovered"})
r = post(3, "close"); ok(r.status_code == 200, "after a new CURE the close is accepted", (r.status_code, r.get_json()))
c = A.conn(); st = c.execute("SELECT status FROM cases WHERE id=3").fetchone()[0]; c.close(); ok(st == "CLOSED", "case is CLOSED", st)

print("V6b a report the vet has already answered does not block the close")
c = A.conn(); row1 = c.execute("SELECT animal_id, farmer_id FROM cases WHERE id=1").fetchone(); c.close()
client(row1["farmer_id"]).post("/api/cases/report", json=dict(rb, animal_id=row1["animal_id"], submitted_at=iso_z(NOW() - timedelta(hours=5))))
post(1, "action", {"action_type": "CURE", "details": "cured after the report", "action_at": iso_z(NOW() - timedelta(minutes=10))})
r = post(1, "close"); ok(r.status_code == 200, "CURE is newer than every report -> close accepted", (r.status_code, r.get_json()))

print("V7  farmer reports keep their real time up to 60 days (was 14)")
def stored_report_time(days):
    farmer = client(86); b = dict(rb, animal_id=2, submitted_at=iso_z(NOW() - timedelta(days=days)))
    farmer.post("/api/cases/report", json=b)
    c = A.conn(); t = c.execute("SELECT reported_at FROM health_reports ORDER BY id DESC LIMIT 1").fetchone()[0]; c.close(); return t
ok(near(stored_report_time(45), NOW() - timedelta(days=45)), "45 days old report keeps its time")
ok(near(stored_report_time(70), NOW()), "70 days old report is replaced by now")

print("V5  FINDING (not a pass/fail): the same request twice")
n0 = count_actions(1)
body = {"action_type": "NOTE", "details": "duplicate-probe", "action_at": iso_z(NOW() - timedelta(minutes=5))}
post(1, "action", body); post(1, "action", body)
print("      rows added by sending one identical action twice:", count_actions(1) - n0, "(2 = a retry after a lost reply would duplicate it)")

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
