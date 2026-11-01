// The phone-check page in a real browser: it must run without errors and report facts that match reality.
process.env.E2E_BASE = "http://127.0.0.1:5056";
const L = require("./lib"), { startProxy } = require("./proxy"), sleep = L.sleep;
let passed = 0, failed = 0;
const ok = (c, n, x) => { if (c) { passed++; console.log("  ok   " + n); } else { failed++; console.log("  FAIL " + n + (x !== undefined ? "  -> " + String(x).slice(0, 300) : "")); } };
const text = (page) => page.$eval("#out", (e) => e.innerText);
(async () => {
  L.prepare("/home/claude/gramvet final - Copy", "/home/claude/e2e_run");
  let srv = L.startServer("/home/claude/e2e_run"); await L.waitUp(20000, srv);
  const proxy = startProxy(5056, L.PORT), b = await L.browser();
  const ctx = await b.createBrowserContext(), page = await L.newPage(ctx); await L.login(page, "farmer");
  await page.reload({ waitUntil: "networkidle2" }); await page.waitForSelector(".nav"); await sleep(2500);

  console.log("D1  reachable from the sync panel");
  await page.click("#gvNet"); await sleep(400);
  const href = await page.$eval('#gvSyncPanel a[href*="diagnostics"]', (a) => a.getAttribute("href")).catch(() => null);
  ok(href === "/static/pwa/diagnostics.html", "the sync panel links to the phone check", href);

  console.log("D2  online, signed in");
  const dp = await L.newPage(ctx, L.MOBILE); await dp.goto(L.BASE + "/static/pwa/diagnostics.html", { waitUntil: "networkidle2" }); await sleep(800);
  let t = await text(dp);
  ok(dp.problems.filter((p) => !/openstreetmap/.test(p.text)).length === 0, "the page itself has no errors", JSON.stringify(dp.problems.slice(0, 2)));
  ok(/Secure connection[\s\S]*yes/.test(t), "secure context reported (localhost)");
  ok(/Installed and active[\s\S]*activated/.test(t), "service worker reported active");
  ok(/gv-v13-shell/.test(t) && /gv-api/.test(t), "cache names and counts listed");
  ok(/0 waiting, 0 failed/.test(t), "queue is empty");
  ok(/Reached the server[\s\S]*ms/.test(t) && /Signed in[\s\S]*farmer/.test(t), "server reached and signed-in role shown");
  ok(/Phone clock vs server clock/.test(t) && /(ahead|behind)/.test(t), "clock difference is computed");
  ok(/Background sync[\s\S]*available/.test(t) && /Web Locks[\s\S]*available/.test(t), "capabilities reported");
  await dp.screenshot({ path: "/tmp/shots/diag_phone.png", fullPage: true });
  const report = await dp.$eval("#report", (e) => e.value); ok(/^GramVet phone check/.test(report) && /\[OK\]/.test(report), "the copyable report text is built");

  console.log("D3  a wrong phone clock is called out");
  await dp.evaluate(() => { const real = Date.now; Date.now = () => real() + 20 * 60 * 1000; }); await dp.click("#again"); await sleep(1200);
  t = await text(dp); ok(/phone is 1[12]\d\d s ahead/.test(t) && /more than 5 minutes/.test(t), "20 minutes fast -> flagged with the reason", (t.match(/Phone clock vs server clock[^\n]*\n[^\n]*/) || [""])[0]);

  console.log("D4  offline with a report waiting: opens from the phone's cache and shows the queue");
  await L.stopServer(srv); srv = null; await page.setOfflineMode(true); await sleep(500);
  await page.evaluate(() => gvsync.enqueue({ url: "/api/cases/report", body: { animal_id: 7, symptoms: ["Fever"] } })); await sleep(300);
  await dp.setOfflineMode(true); await dp.goto(L.BASE + "/static/pwa/diagnostics.html", { waitUntil: "domcontentloaded" }); await sleep(1500);
  t = await text(dp);
  ok(/Installed and active/.test(t) && /✗\s*Reached the server|!\s*Reached the server/.test(t.replace(/\n/g, " ")) || /Reached the server\s+no \(/.test(t), "loads with no connection and says the server did NOT answer", (t.match(/Reached the server[^\n]*\n?[^\n]*/) || [""])[0]);
  ok(/last known/.test(t), "and labels the signed-in role as last known, not confirmed");
  ok(/1 waiting, 0 failed/.test(t) && /POST \/api\/cases\/report/.test(t) && /key ok/.test(t), "lists the waiting report with its key");
  await dp.screenshot({ path: "/tmp/shots/diag_offline.png", fullPage: true });
  await L.closeBrowser(); await proxy.close();
  console.log("\n" + passed + " passed, " + failed + " failed"); process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.log("HARNESS ERROR:", String(e.stack || e).slice(0, 800)); await L.closeBrowser().catch(() => {}); process.exit(2); });
