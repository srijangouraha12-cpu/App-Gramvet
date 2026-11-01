// What does a phone REALLY download? Every request, including the service worker's own, seen at a proxy.
process.env.E2E_BASE = "http://127.0.0.1:5056";
const L = require("./lib"), { startProxy } = require("./proxy"), sleep = L.sleep;
const SRC = process.env.E2E_SRC || "/home/claude/gramvet final - Copy";
const kind = (u) => /\/media\//.test(u) ? "images" : /\.js/.test(u) ? "javascript" : /\/api\//.test(u) ? "api" : u === "/" ? "index.html" : /\.(png|jpg)/.test(u) ? "icons/images" : "other";
function summary(rows, label) {
  const by = {}, big = []; let tot = 0;
  for (const r of rows) { const u = ((r.req || "").split(" ")[1] || "?"); const k = kind(u); by[k] = (by[k] || 0) + r.bytes; tot += r.bytes; if (r.bytes > 60000) big.push(u + " " + Math.round(r.bytes / 1024) + "KB (" + r.status + ")"); }
  const n304 = rows.filter((r) => r.status === 304).length;
  console.log(label.padEnd(30) + "TOTAL " + String(Math.round(tot / 1024)).padStart(5) + " KB in " + rows.length + " requests (" + n304 + " were 304 'unchanged')  | " + Object.entries(by).map(([k, v]) => k + "=" + Math.round(v / 1024)).join(" "));
  if (big.length) console.log("      big: " + big.join(", "));
}
(async () => {
  L.prepare(SRC, "/home/claude/e2e_run");
  const srv = L.startServer("/home/claude/e2e_run"); await L.waitUp(20000, srv);
  const proxy = startProxy(5056, L.PORT), b = await L.browser();
  const ctx = await b.createBrowserContext(), page = await L.newPage(ctx);
  await page.goto(L.BASE + "/", { waitUntil: "domcontentloaded" });
  await page.evaluate(async (ph, pw) => { await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ phone: ph, password: pw }) }); }, L.PHONES.vet, L.PASSWORD);
  await page.evaluate(async () => { for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister(); for (const k of await caches.keys()) await caches.delete(k); });
  const cdp = await page.createCDPSession(); await cdp.send("Network.enable"); await cdp.send("Network.clearBrowserCache");   // a genuinely fresh phone: nothing cached
  await page.setCacheEnabled(false); proxy.state.log.length = 0;
  await page.goto(L.BASE + "/", { waitUntil: "domcontentloaded" }); await page.waitForSelector("main .card, main .grid", { timeout: 20000 });
  await page.waitForNetworkIdle({ idleTime: 2500, timeout: 40000 }).catch(() => {}); await sleep(6000);        // let the service worker finish installing
  summary(proxy.state.log.slice(), "FIRST VISIT (incl. worker)");
  await page.setCacheEnabled(true); proxy.state.log.length = 0;
  await page.reload({ waitUntil: "domcontentloaded" }); await page.waitForSelector("main .card, main .grid", { timeout: 20000 }); await page.waitForNetworkIdle({ idleTime: 2500, timeout: 30000 }).catch(() => {}); await sleep(3000);
  summary(proxy.state.log.slice(), "RELOAD (worker + cache)");
  await L.closeBrowser(); await L.stopServer(srv); await proxy.close();
})().catch(async (e) => { console.log("FAILED", String(e.stack || e).slice(0, 500)); await L.closeBrowser().catch(() => {}); process.exit(1); });
