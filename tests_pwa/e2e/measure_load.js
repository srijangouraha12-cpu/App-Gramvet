// How heavy is the first load on a slow phone? Fast 3G + 4x slower CPU, real Chrome, cold cache and warm (service worker) reload.
const L = require("./lib"), { PredefinedNetworkConditions } = require("puppeteer-core"), sleep = L.sleep;
const SRC = process.env.E2E_SRC || "/home/claude/gramvet final - Copy";
(async () => {
  L.prepare(SRC, "/home/claude/e2e_run");
  const srv = L.startServer("/home/claude/e2e_run"); await L.waitUp(20000, srv);
  const b = await L.browser(), out = {};
  for (const role of ["vet", "farmer"]) {
    const ctx = await b.createBrowserContext(), page = await L.newPage(ctx); await L.login(page, role);
    // make it truly cold: no service worker, no caches, no HTTP cache
    await page.evaluate(async () => { for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister(); for (const k of await caches.keys()) await caches.delete(k); });
    const cdp = await page.createCDPSession(); await cdp.send("Network.enable"); await cdp.send("Network.clearBrowserCache");
    await page.setCacheEnabled(false);
    await page.emulateNetworkConditions(PredefinedNetworkConditions["Fast 3G"]); await page.emulateCPUThrottling(4);
    let bytes = 0, reqs = 0, decoded = 0; cdp.on("Network.loadingFinished", (e) => { bytes += e.encodedDataLength; reqs++; }); cdp.on("Network.dataReceived", (e) => { decoded += e.dataLength; });
    const t0 = Date.now(); await page.goto(L.BASE + "/", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("main .card, main .grid", { timeout: 90000 }); const tUsable = Date.now() - t0;
    await page.waitForNetworkIdle({ idleTime: 1500, timeout: 90000 }).catch(() => {});
    out[role + " cold"] = { seconds: (tUsable / 1000).toFixed(1), transferredKB: Math.round(bytes / 1024), decodedKB: Math.round(decoded / 1024), requests: reqs };
    // warm: the service worker now has the shell
    await page.setCacheEnabled(true); await sleep(1500); bytes = 0; reqs = 0; decoded = 0;
    const t1 = Date.now(); await page.reload({ waitUntil: "domcontentloaded" }); await page.waitForSelector("main .card, main .grid", { timeout: 90000 }); const tWarm = Date.now() - t1;
    await page.waitForNetworkIdle({ idleTime: 1500, timeout: 60000 }).catch(() => {});
    out[role + " reload"] = { seconds: (tWarm / 1000).toFixed(1), transferredKB: Math.round(bytes / 1024), decodedKB: Math.round(decoded / 1024), requests: reqs };
    await ctx.close();
  }
  await L.closeBrowser(); await L.stopServer(srv);
  for (const k of Object.keys(out)) console.log(k.padEnd(14), "usable after " + out[k].seconds + " s | " + out[k].transferredKB + " KB over the network (" + out[k].decodedKB + " KB after decompression) | " + out[k].requests + " requests");
})().catch(async (e) => { console.log("FAILED", String(e.stack || e).slice(0, 600)); await L.closeBrowser().catch(() => {}); process.exit(1); });
