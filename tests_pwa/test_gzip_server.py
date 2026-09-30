"""Compression of replies. Real Flask app, scratch copy.   Run: python test_gzip_server.py <COPY of the project>"""
import os, sys, gzip, json, warnings
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
def client(uid=None):
    c = A.app.test_client()
    if uid:
        with c.session_transaction() as s: s["uid"] = uid
    return c
GZ = {"Accept-Encoding": "gzip, deflate, br"}
vet = client(2)

print("G1  the main page")
plain = client().get("/"); comp = client().get("/", headers=GZ)
ok(comp.headers.get("Content-Encoding") == "gzip" and "Accept-Encoding" in comp.headers.get("Vary", ""), "compressed when the browser accepts gzip, with Vary")
ok(gzip.decompress(comp.data) == plain.data, "identical after decompression")
ok(len(comp.data) < len(plain.data) * 0.4, "much smaller: %d -> %d bytes (%.0f%%)" % (len(plain.data), len(comp.data), 100 * len(comp.data) / len(plain.data)))
ok(int(comp.headers["Content-Length"]) == len(comp.data), "Content-Length matches the compressed body")
ok(plain.headers.get("Content-Encoding") is None, "not compressed when the browser did not ask")

print("G2  JSON API replies")
a = vet.get("/api/cases"); b = vet.get("/api/cases", headers=GZ)
ok(b.headers.get("Content-Encoding") == "gzip" and json.loads(gzip.decompress(b.data)) == a.get_json(), "/api/cases: compressed and the same JSON (%d -> %d bytes)" % (len(a.data), len(b.data)))
m = vet.get("/api/me", headers=GZ)
ok(m.headers.get("Content-Encoding") is None and m.get_json()["authenticated"] is True, "small replies are left alone (/api/me, %d bytes)" % len(m.data))

print("G3  static files, images and audio")
js = client().get("/static/advisories.js"); jsz = client().get("/static/advisories.js", headers=GZ)
ok(jsz.headers.get("Content-Encoding") == "gzip" and gzip.decompress(jsz.data) == js.data, "JavaScript file compressed and identical (%d -> %d bytes)" % (len(js.data), len(jsz.data)))
img = client().get("/media/p3.png", headers=GZ); ok(img.headers.get("Content-Encoding") is None and img.status_code == 200, "images are not touched")
etag = js.headers.get("ETag")
if etag:
    r304 = client().get("/static/advisories.js", headers=dict(GZ, **{"If-None-Match": etag}))
    ok(r304.status_code == 304 and not r304.data, "a conditional request still answers 304 with no body", r304.status_code)
else:
    print("      (no ETag on static files here; 304 check skipped)")

print("G4  the idempotency store keeps the PLAIN reply even when the client accepts gzip")
h = dict(GZ, **{"Idempotency-Key": "gzipcheck0123456789abcdef012345"})
r1 = vet.post("/api/vet/cases/1/action", json={"action_type": "NOTE", "details": "gzip test"}, headers=h)
r2 = vet.post("/api/vet/cases/1/action", json={"action_type": "NOTE", "details": "gzip test"}, headers=h)
ok(r1.status_code == 200 and r2.status_code == 200 and r2.headers.get("Idempotent-Replay") == "true", "second call is a replay", (r1.status_code, r2.status_code))
body = lambda r: json.loads(gzip.decompress(r.data)) if r.headers.get("Content-Encoding") == "gzip" else json.loads(r.data)
ok(body(r1) == body(r2) == {"ok": True}, "and reads back correctly (not double-compressed)", (r2.headers.get("Content-Encoding"), r2.data[:40]))
c = A.conn(); stored = c.execute("SELECT response FROM idempotency_keys WHERE key=?", ("gzipcheck0123456789abcdef012345",)).fetchone(); c.close()
ok(stored and json.loads(stored["response"]) == {"ok": True}, "the stored reply in the database is plain JSON")

print("\n%d passed, %d failed" % (passed, failed)); sys.exit(1 if failed else 0)
