"""Send every route every role's worst inputs and list anything that crashes (HTTP 500 / unhandled exception).
Real Flask app, scratch copy.   Run: python fuzz_server.py <COPY of the project>"""
import os, sys, re, json, warnings, itertools, traceback
warnings.filterwarnings("ignore")
proj = os.path.abspath(sys.argv[1]); os.chdir(proj); sys.path.insert(0, proj)
os.environ["MONGO_URI"] = "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=200"
import app as A
from flask import got_request_exception
A.init()
A.app.config["PROPAGATE_EXCEPTIONS"] = False
errors = []
def on_exc(sender, exception, **kw):
    tb = traceback.extract_tb(exception.__traceback__)
    frame = [f for f in tb if proj in f.filename]
    last = frame[-1] if frame else tb[-1]
    errors.append((type(exception).__name__, str(exception)[:120], "%s:%d %s" % (os.path.basename(last.filename), last.lineno, (last.line or "")[:90])))
got_request_exception.connect(on_exc, A.app)

def ids(table, col="id"):
    c = A.conn(); r = [x[0] for x in c.execute("SELECT %s FROM %s ORDER BY id LIMIT 3" % (col, table))]; c.close(); return r
IDS = {"case_id": ids("cases"), "animal_id": ids("animals"), "user_id": ids("users"), "id": ids("resource_requests") + ids("vaccine_shipments") + ids("notifications")}
ROLES = {"government": 1, "vet": 2, "farmer86": 86, "farmer10": 10, "anon": None}
PAYLOADS = [None, {}, [], "text", 7, {"a": 1}, {"symptoms": "x", "animal_id": "abc"}, {"animal_id": None, "symptoms": None, "notes": None}, {"quantity": -5, "resources": "x", "name": None}, {"animal_id": 99999999, "symptoms": ["nonsense"]}]
SKIP = re.compile(r"^/(static|media)/|^/api/logout$")

def fill(rule, pick):
    url = rule
    for conv, name in re.findall(r"<(?:(\w+):)?(\w+)>", rule):
        val = pick.get(name, (IDS.get(name) or IDS["id"])[0] if (IDS.get(name) or IDS["id"]) else 1)
        url = re.sub(r"<(?:\w+:)?%s>" % name, str(val), url)
    return url

def client(uid):
    c = A.app.test_client()
    if uid:
        with c.session_transaction() as s: s["uid"] = uid
    return c

seen = {}; calls = 0
for rule in A.app.url_map.iter_rules():
    if SKIP.search(rule.rule) or "GET" not in rule.methods and "POST" not in rule.methods and "PUT" not in rule.methods and "DELETE" not in rule.methods: continue
    picks = [{}, {n: 999999 for n in re.findall(r"<(?:\w+:)?(\w+)>", rule.rule)}, {n: 0 for n in re.findall(r"<(?:\w+:)?(\w+)>", rule.rule)}]
    for pick, (role, uid) in itertools.product(picks, ROLES.items()):
        url = fill(rule.rule, pick); cl = client(uid)
        for method in sorted(rule.methods & {"GET", "POST", "PUT", "DELETE"}):
            for payload in ([None] if method == "GET" else PAYLOADS):
                errors.clear()
                try:
                    if payload is None: r = cl.open(url, method=method)
                    elif isinstance(payload, str) and payload == "text": r = cl.open(url, method=method, data="not json", content_type="application/json")
                    else: r = cl.open(url, method=method, json=payload)
                except Exception as e:
                    errors.append((type(e).__name__, str(e)[:120], "client raised")); r = None
                calls += 1
                if (r is not None and r.status_code >= 500) or errors:
                    key = (method, rule.rule, errors[0][0] if errors else "HTTP%d" % r.status_code, errors[0][2] if errors else "")
                    seen.setdefault(key, {"n": 0, "example": (role, url, json.dumps(payload)[:60] if payload is not None else "", errors[0][1] if errors else "")})["n"] += 1
print("requests sent: %d   distinct crash sites: %d" % (calls, len(seen)))
for (method, rule, exc, where), v in sorted(seen.items()):
    print("  %-6s %-42s %-16s x%-3d  %s\n         e.g. as %s -> %s payload=%s  (%s)" % (method, rule, exc, v["n"], where, v["example"][0], v["example"][1], v["example"][2], v["example"][3]))
sys.exit(1 if seen else 0)
