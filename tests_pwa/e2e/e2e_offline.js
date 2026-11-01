// 9e in a real browser: service worker, offline, reconnect, lost reply. Real Flask + real headless Chrome (phone-sized).
process.env.E2E_BASE = "http://127.0.0.1:5056";
const L = require("./lib"), { startProxy } = require("./proxy"), { execFileSync } = require("child_process");
const DIR = "/home/claude/e2e_run", sleep = L.sleep;
const q = (sql) => JSON.parse(execFileSync("python3", ["-c", `import sqlite3,json; c=sqlite3.connect("${DIR}/gramvet.db"); c.row_factory=sqlite3.Row; print(json.dumps([dict(r) for r in c.execute(${JSON.stringify(sql)})]))`]).toString());
let passed = 0, failed = 0; const notes = [];
const ok = (c, n, x) => { if (c) { passed++; console.log("  ok   " + n); } else { failed++; console.log("  FAIL " + n + (x !== undefined ? "  -> " + String(x).slice(0, 260) : "")); } };
async function poll(fn, ms = 20000, gap = 250) { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { const v = await fn(); if (v) return v; } catch (e) {} await sleep(gap); } return false; }
const pill = (page) => page.$eval("#gvNet", (e) => e.textContent.trim()).catch(() => null);
const queue = (page) => page.evaluate(() => gvsync.list());
async function tickAndSave(page, animalIndex = 0) {
  await page.evaluate(() => go("livestock")); await page.waitForNetworkIdle({ idleTime: 300, timeout: 6000 }).catch(() => {});
  await page.evaluate((i) => { [...document.querySelectorAll("button")].filter((x) => /report/i.test(x.textContent) && x.offsetParent)[i].click(); }, animalIndex);
  await page.waitForSelector("#modalBody input[type=checkbox]", { timeout: 6000 });
  await page.evaluate(() => { const boxes = [...document.querySelectorAll("#modalBody input[type=checkbox]")].slice(0, 2); boxes.forEach((b) => { if (!b.checked) b.click(); }); });
  await page.evaluate(() => { [...document.querySelectorAll("#modalBody button")].find((b) => /Save report|सहेजें|जतन/i.test(b.textContent)).click(); });
}

(async () => {
  L.prepare("/home/claude/gramvet final - Copy", DIR);
  let srv = L.startServer(DIR); await L.waitUp(20000, srv);
  const proxy = startProxy(5056, L.PORT), seen = () => proxy.state.seen;
  const b = await L.browser();
  const ctx = await b.createBrowserContext(), page = await L.newPage(ctx);
  await L.login(page, "farmer");

  console.log("E1  service worker and storage in a real browser");
  await page.reload({ waitUntil: "networkidle2" }); await page.waitForSelector(".nav");
  const sw = await page.evaluate(async () => { const reg = await navigator.serviceWorker.getRegistration(); await navigator.serviceWorker.ready; const est = await navigator.storage.estimate(); return { registered: !!reg, active: !!(reg && reg.active), controlled: !!navigator.serviceWorker.controller, caches: await caches.keys(), persisted: await navigator.storage.persisted(), usageKB: Math.round(est.usage / 1024), quotaMB: Math.round(est.quota / 1048576), locks: !!navigator.locks, bgsync: "SyncManager" in window }; });
  ok(sw.registered && sw.active, "service worker registered and active", JSON.stringify(sw));
  ok(sw.controlled, "the page is controlled by it after a reload");
  ok(sw.caches.some((c) => /gv-v13-shell/.test(c)) && sw.caches.includes("gv-api"), "current cache version present: " + sw.caches.join(", "));
  notes.push("storage: usage " + sw.usageKB + " KB of quota " + sw.quotaMB + " MB | persisted=" + sw.persisted + " | Web Locks=" + sw.locks + " | Background Sync API=" + sw.bgsync);

  console.log("E2  data warm-up: once, and throttled on reconnect bursts");
  await sleep(3500);                                          // first load: the warm-up runs once
  proxy.state.seen.length = 0;
  await page.reload({ waitUntil: "networkidle2" }); await page.waitForSelector(".nav"); await sleep(3500);   // a reload minutes-or-seconds later
  const reloadGets = seen().filter((l) => /^GET \/api\//.test(l)).map((l) => l.split(" ")[1]);
  const casesGets = reloadGets.filter((u) => u === "/api/cases").length;
  notes.push("requests on a plain reload: " + reloadGets.join(", "));
  ok(casesGets <= 1, "a reload asks for /api/cases " + casesGets + " time(s) (the dashboard's own; the warm-up is skipped: it ran seconds ago)", reloadGets.join(", "));
  ok(reloadGets.filter((u) => /\/api\/cases\/\d+/.test(u)).length === 0, "and does not re-download every case detail");
  proxy.state.seen.length = 0;
  await page.evaluate(() => { for (let i = 0; i < 3; i++) window.dispatchEvent(new Event("online")); });
  await sleep(2000);
  ok(!seen().some((l) => /^GET \/api\/animals HTTP/.test(l)) , "three 'online' events in a row caused no re-download", seen().join(" | "));

  console.log("E3  farmer saves a report with NO connection (server stopped + airplane mode)");
  const before = q("select count(*) n from health_reports")[0].n;
  await L.stopServer(srv); srv = null; await page.setOfflineMode(true); await sleep(600);
  const tSave = Date.now();
  await tickAndSave(page);
  const queued = await poll(async () => (await queue(page)).length === 1, 8000);
  ok(queued, "report is in the phone's queue");
  const item = (await queue(page))[0] || {};
  ok(/^[0-9a-f]{32}$/.test(item.key || ""), "queued with an idempotency key", item.key);
  ok(item.body && item.body.symptoms && item.body.symptoms.length === 2 && /^\d{4}-/.test(item.body.submitted_at || ""), "with the symptoms and the time it was filled in", JSON.stringify(item.body && [item.body.symptoms, item.body.submitted_at]));
  await sleep(300); ok(/queued/i.test(await pill(page) || ""), "the pill says so: '" + (await pill(page)) + "'");
  const modalText = await page.evaluate(() => document.getElementById("modalBody").textContent);
  ok(/saved on this phone|offline/i.test(modalText) && /confidence|Disease/i.test(modalText), "the farmer still gets an AI result offline", modalText.slice(0, 120));
  await page.screenshot({ path: "/tmp/shots/e2e_farmer_offline_result.png" });
  await page.evaluate(() => closeModal());

  console.log("E4  connection returns: it syncs by itself, keeps the original time, no duplicate");
  await sleep(5000);                                          // stay offline a few seconds so the original time is distinguishable
  const tBack = Date.now();
  srv = L.startServer(DIR); await L.waitUp(20000, srv); await page.setOfflineMode(false);
  const synced = await poll(async () => (await queue(page)).length === 0 && /^Online/.test(await pill(page) || ""), 30000);
  ok(synced, "queue drained and the pill is back to Online: '" + (await pill(page)) + "'");
  const rows = q("select id, reported_at from health_reports order by id desc limit 3"), after = q("select count(*) n from health_reports")[0].n;
  ok(after === before + 1, "exactly one new report on the server (" + before + " -> " + after + ")");
  const rt = new Date(rows[0].reported_at).getTime();
  ok(Math.abs(rt - tSave) < 4000 && tBack - rt > 4000, "stored with the time it was saved offline, not the sync time (" + Math.round((tBack - rt) / 1000) + " s earlier than the reconnect)", rows[0].reported_at);
  ok(seen().filter((l) => /^POST \/api\/cases\/report/.test(l)).length === 1, "the server received it once");

  console.log("E5  LOST REPLY: the server saves the report but the phone never hears back");
  const b5 = q("select count(*) n from health_reports")[0].n; proxy.state.seen.length = 0; proxy.state.replays = 0; proxy.state.dropNext = /^POST \/api\/cases\/report/;
  await tickAndSave(page, 1);
  const drained = await poll(async () => proxy.state.dropped >= 1 && (await queue(page)).length === 0 && /^Online/.test(await pill(page) || ""), 40000);
  ok(proxy.state.dropped === 1, "the reply really was dropped (" + proxy.state.dropped + ")");
  ok(drained, "the phone queued it and re-sent it by itself");
  const posts = seen().filter((l) => /^POST \/api\/cases\/report/.test(l)).length;
  ok(posts >= 2, "the server was asked more than once (" + posts + " POSTs)");
  ok(q("select count(*) n from health_reports")[0].n === b5 + 1, "but exactly ONE report was created (" + b5 + " -> " + q("select count(*) n from health_reports")[0].n + ")");
  ok(proxy.state.replays >= 1, "the server answered the repeat from its stored result (" + proxy.state.replays + " replay header(s))");

  console.log("E6  vet reads a case and records an action with NO connection");
  const vctx = await b.createBrowserContext(), vp = await L.newPage(vctx); await L.login(vp, "vet");
  await vp.reload({ waitUntil: "networkidle2" }); await vp.waitForSelector(".nav"); await sleep(3500);     // warm-up caches the case details
  const va = q("select count(*) n from case_actions")[0].n;
  await L.stopServer(srv); srv = null; await vp.setOfflineMode(true); await sleep(500);
  await vp.evaluate(() => showCase(3)); await sleep(1200);
  const caseText = await vp.evaluate(() => document.getElementById("modalBody").textContent);
  ok(/Case #3/.test(caseText), "the case opens from the phone's cache while offline", caseText.slice(0, 80));
  const tAct = Date.now();
  await vp.evaluate(() => { document.getElementById("actType").value = "NOTE"; document.getElementById("actDetails").value = "Checked the animal in the field, no signal"; [...document.querySelectorAll("#modalBody button")].find((x) => /Save clinical action/i.test(x.textContent)).click(); });
  await poll(async () => (await queue(vp)).length === 1, 8000);
  const vq = await queue(vp); ok(vq.length === 1 && /\/action$/.test(vq[0].url) && /^[0-9a-f]{32}$/.test(vq[0].key), "the action is queued with a key", JSON.stringify(vq.map((x) => x.url)));
  await vp.screenshot({ path: "/tmp/shots/e2e_vet_offline_action.png" });
  await sleep(5000);
  srv = L.startServer(DIR); await L.waitUp(20000, srv); await vp.setOfflineMode(false);
  const vs = await poll(async () => (await queue(vp)).length === 0, 30000);
  ok(vs, "synced after reconnect");
  const ar = q("select action_at, details from case_actions order by id desc limit 1")[0];
  ok(q("select count(*) n from case_actions")[0].n === va + 1, "exactly one new action");
  ok(Math.abs(new Date(ar.action_at).getTime() - tAct) < 4000, "with the time the vet recorded it", ar.action_at);

  const bad = [];
  for (const pg of [page, vp]) for (const pr of pg.problems) if (!/openstreetmap|Failed to load resource|ERR_INTERNET_DISCONNECTED|ERR_CONNECTION|ERR_FAILED|ERR_NETWORK_CHANGED|Failed to fetch/.test(pr.text) && pr.type !== "requestfailed" && !/^http(4|5)/.test(pr.type)) bad.push(pr.type + ": " + pr.text);
  console.log("E7  nothing unexpected in the browser console during all of this");
  ok(bad.length === 0, "no uncaught errors / unexpected console errors (" + bad.length + ")", bad.slice(0, 4).join(" || "));

  await L.closeBrowser(); if (srv) await L.stopServer(srv); await proxy.close();
  console.log("\n" + notes.join("\n")); console.log("\n" + passed + " passed, " + failed + " failed");
  process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.log("HARNESS ERROR:", String(e.stack || e).slice(0, 900)); await L.closeBrowser().catch(() => {}); process.exit(2); });
