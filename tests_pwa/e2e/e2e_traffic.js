// Data use, as a phone would experience it: measured at a proxy, so the service worker's own downloads count.
process.env.E2E_BASE = "http://127.0.0.1:5056";
const L = require("./lib"), { startProxy } = require("./proxy"), sleep = L.sleep;
let passed = 0, failed = 0;
const ok = (c, n, x) => { if (c) { passed++; console.log("  ok   " + n); } else { failed++; console.log("  FAIL " + n + (x !== undefined ? "  -> " + String(x).slice(0, 300) : "")); } };
const path = (r) => ((r.req || "").split(" ")[1] || "?"), kb = (rows) => Math.round(rows.reduce((a, r) => a + r.bytes, 0) / 1024);
const LOGIN_ONLY = /\/media\/(p1|p1_dark|p2_dark|p3|p4)\.(jpg|png)/;
(async () => {
  L.prepare("/home/claude/gramvet final - Copy", "/home/claude/e2e_run");
  let srv = L.startServer("/home/claude/e2e_run"); await L.waitUp(20000, srv);
  const proxy = startProxy(5056, L.PORT), b = await L.browser(), log = proxy.state.log;

  console.log("T1  a brand-new phone, signed-in vet: first visit");
  let ctx = await b.createBrowserContext(), page = await L.newPage(ctx);
  await page.goto(L.BASE + "/", { waitUntil: "domcontentloaded" });
  await page.evaluate(async (ph, pw) => { await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ phone: ph, password: pw }) }); }, L.PHONES.vet, L.PASSWORD);
  await page.evaluate(async () => { for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister(); for (const k of await caches.keys()) await caches.delete(k); });
  const cdp = await page.createCDPSession(); await cdp.send("Network.enable"); await cdp.send("Network.clearBrowserCache"); await page.setCacheEnabled(false); log.length = 0;
  await page.goto(L.BASE + "/", { waitUntil: "domcontentloaded" }); await page.waitForSelector("main .card, main .grid", { timeout: 20000 });
  await page.waitForNetworkIdle({ idleTime: 2500, timeout: 40000 }).catch(() => {}); await sleep(6000);        // the service worker finishes installing
  const first = log.slice();
  ok(kb(first) < 600, "everything the phone downloads, service worker included: " + kb(first) + " KB (the 9d version: 2,488 KB)", kb(first));
  ok(!first.some((r) => LOGIN_ONLY.test(path(r))), "none of the login page's pictures were downloaded", first.filter((r) => LOGIN_ONLY.test(path(r))).map(path).join(" "));
  const idx = first.filter((r) => path(r) === "/" && r.status === 200).map((r) => Math.round(r.bytes / 1024));
  ok(idx.length > 0 && idx.every((k) => k < 80), "the main page arrives compressed (" + idx.join(", ") + " KB, was 211 KB)", idx.join());
  ok(first.some((r) => /p2\.png/.test(path(r))), "the app's own logo is still fetched");

  console.log("T2  reloading is cheap");
  await page.setCacheEnabled(true); log.length = 0;
  await page.reload({ waitUntil: "domcontentloaded" }); await page.waitForSelector("main .card, main .grid"); await page.waitForNetworkIdle({ idleTime: 2500, timeout: 30000 }).catch(() => {}); await sleep(3000);
  ok(kb(log) < 120, "a reload downloads " + kb(log) + " KB (was 261 KB)", kb(log));
  await ctx.close();

  console.log("T3  signed out: the login page still gets its pictures, remembers them, and works offline afterwards");
  ctx = await b.createBrowserContext(); page = await L.newPage(ctx); log.length = 0;
  await page.goto(L.BASE + "/", { waitUntil: "domcontentloaded" }); await page.waitForSelector("#phone", { visible: true, timeout: 15000 });
  await page.waitForNetworkIdle({ idleTime: 2000, timeout: 40000 }).catch(() => {}); await sleep(3000);
  ok(log.some((r) => /p1\.jpg/.test(path(r)) && r.status === 200), "the hero picture is downloaded now that the login page is shown");
  await page.reload({ waitUntil: "networkidle2" }); await sleep(2500);          // a return visit: the service worker now controls the page and remembers what it shows
  const cached = await page.evaluate(async () => { const out = []; for (const k of await caches.keys()) { const c = await caches.open(k); for (const q of await c.keys()) if (/p1\.jpg|p4\.png/.test(q.url)) out.push(new URL(q.url).pathname); } return out; });
  ok(cached.includes("/media/p1.jpg") && cached.includes("/media/p4.png"), "on a return visit the pictures are saved for next time: " + cached.join(", "));
  await L.stopServer(srv); srv = null; await page.setOfflineMode(true); await sleep(400);
  await page.reload({ waitUntil: "domcontentloaded" }).catch(() => {}); await sleep(1500);
  const off = await page.evaluate(async () => { const img = await new Promise((res) => { const i = new Image(); i.onload = () => res(true); i.onerror = () => res(false); i.src = "/media/p1.jpg"; setTimeout(() => res(false), 4000); }); const f = document.querySelector("#phone"); return { img, form: !!f && f.offsetParent !== null }; });
  ok(off.form, "with no connection the login form still appears");
  ok(off.img, "and its hero picture comes from the phone's saved copy");
  await L.closeBrowser(); await proxy.close();
  console.log("\n" + passed + " passed, " + failed + " failed"); process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.log("HARNESS ERROR:", String(e.stack || e).slice(0, 800)); await L.closeBrowser().catch(() => {}); process.exit(2); });
