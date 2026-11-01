// Real-browser harness: your Flask app + headless Chrome. Never point PROJECT at your live folder (it writes to the DB).
const { spawn, execFileSync } = require("child_process"), fs = require("fs"), path = require("path"), http = require("http");
const chromium = require("@sparticuz/chromium").default, puppeteer = require("puppeteer-core");
const PORT = 5055, UP = "http://127.0.0.1:" + PORT, BASE = process.env.E2E_BASE || UP, PASSWORD = "test1234";
const PHONES = { government: "9000000001", vet: "9000000002", farmer: "9000000004" };   // farmer 86 owns animals in the scratch DB
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function prepare(srcProject, dir) {                       // fresh scratch copy with known passwords
  execFileSync("rm", ["-rf", dir]); execFileSync("cp", ["-r", srcProject, dir]);
  execFileSync("python3", ["-W", "ignore", "-c", `
import os,sys; os.chdir(${JSON.stringify(dir)}); sys.path.insert(0,".")
os.environ["MONGO_URI"]="mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300"
import app as A
c=A.conn(); c.execute("update users set password_hash=?", (A.hp(${JSON.stringify(PASSWORD)}),)); c.commit(); c.close()`], { stdio: "ignore" });
}
function startServer(dir) {
  try { execFileSync("pkill", ["-f", "app[.]app[.]run"], { stdio: "ignore" }); } catch (e) {}      // a crashed earlier run must not leave an old server answering
  execFileSync("sleep", ["0.5"]);
  const child = spawn("python3", ["-W", "ignore", "-c", "import app; app.app.run(host='127.0.0.1', port=" + PORT + ", threaded=True, use_reloader=False)"],
    { cwd: dir, env: Object.assign({}, process.env, { MONGO_URI: "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300", PYTHONPATH: dir }), stdio: ["ignore", "pipe", "pipe"] });
  child.log = []; child.stderr.on("data", (d) => child.log.push(String(d))); child.stdout.on("data", (d) => child.log.push(String(d)));
  return child;
}
async function waitUp(ms = 20000, child) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const ok = await new Promise((res) => http.get(UP + "/api/me", (r) => { r.resume(); res(r.statusCode < 500); }).on("error", () => res(false)));
    if (child && child.exitCode !== null) throw new Error("server exited: " + child.log.join("").slice(-400));
    if (ok) return true; await sleep(250);
  }
  throw new Error("server did not start");
}
async function stopServer(child) { if (!child) return; child.kill("SIGKILL"); await sleep(400); }
let _browser = null;
async function browser() {
  if (_browser) return _browser;
  _browser = await puppeteer.launch({ executablePath: await chromium.executablePath(), headless: "shell", args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--no-zygote"] });
  return _browser;
}
async function closeBrowser() { if (_browser) await _browser.close(); _browser = null; }
const MOBILE = { width: 360, height: 740, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };
const UA = "Mozilla/5.0 (Linux; Android 10; SM-A105F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36";

// A page that records everything that goes wrong.
async function newPage(ctx, viewport) {
  const page = await ctx.newPage(); await page.setViewport(viewport || MOBILE); await page.setUserAgent(UA);
  page.problems = [];
  page.on("pageerror", (e) => page.problems.push({ type: "uncaught", text: String(e.message || e).slice(0, 300) }));
  page.on("console", (m) => { if (["error", "warning"].includes(m.type())) page.problems.push({ type: "console." + m.type(), text: m.text().slice(0, 300) }); });
  page.on("requestfailed", (r) => page.problems.push({ type: "requestfailed", text: r.method() + " " + r.url().replace(BASE, "").slice(0, 120) + " " + ((r.failure() || {}).errorText || "") }));
  page.on("response", (r) => { if (r.status() >= 400) page.problems.push({ type: "http" + r.status(), text: r.request().method() + " " + r.url().replace(BASE, "").slice(0, 120) }); });
  return page;
}
async function login(page, role) {
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  const r = await page.evaluate(async (phone, pw) => { const x = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ phone, password: pw }) }); return { s: x.status, j: await x.json().catch(() => ({})) }; }, PHONES[role], PASSWORD);
  if (r.s !== 200) throw new Error("login failed for " + role + ": " + JSON.stringify(r));
  await page.goto(BASE + "/", { waitUntil: "networkidle2" });
  await page.waitForSelector(".nav", { timeout: 10000 });
  page.problems.length = 0;                                  // pre-login noise (e.g. the 401 from /api/me) is not a bug
}
module.exports = { PORT, UP, BASE, PASSWORD, PHONES, sleep, prepare, startServer, waitUp, stopServer, browser, closeBrowser, newPage, login, MOBILE, UA };
