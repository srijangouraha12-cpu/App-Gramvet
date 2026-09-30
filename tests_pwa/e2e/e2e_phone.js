// Phone layout and touch behaviour in a real browser (360x740, touch). Regression suite for the responsive fixes.
const L = require("./lib"), sleep = L.sleep;
let passed = 0, failed = 0;
const ok = (c, n, x) => { if (c) { passed++; console.log("  ok   " + n); } else { failed++; console.log("  FAIL " + n + (x !== undefined ? "  -> " + String(x).slice(0, 240) : "")); } };
const settle = async (page) => { await page.waitForNetworkIdle({ idleTime: 300, timeout: 6000 }).catch(() => {}); await sleep(250); };
const rect = (page, sel) => page.$eval(sel, (e) => { const r = e.getBoundingClientRect(); return { l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), b: Math.round(r.bottom), w: Math.round(r.width), h: Math.round(r.height) }; }).catch(() => null);
(async () => {
  L.prepare("/home/claude/gramvet final - Copy", "/home/claude/e2e_run");
  const srv = L.startServer("/home/claude/e2e_run"); await L.waitUp(20000, srv);
  const b = await L.browser(), VW = 360;

  console.log("P1  no sideways scrolling, header and bottom bar intact - every role, every screen");
  for (const role of ["farmer", "vet", "government"]) {
    const ctx = await b.createBrowserContext(), page = await L.newPage(ctx); await L.login(page, role);
    const screens = await page.$$eval(".nav", (els) => els.filter((e) => e.offsetParent !== null && e.getBoundingClientRect().width > 0).map((e) => e.dataset.p));
    const bad = [];
    for (const p of screens) { await page.evaluate((x) => go(x), p); await settle(page); const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth); if (over > 1) bad.push(p + "+" + over + "px"); }
    ok(bad.length === 0, role + ": " + screens.length + " screens fit the phone width", bad.join(", "));
    const lo = await rect(page, "#logoutBtn"); ok(lo && lo.l >= 0 && lo.r <= VW, role + ": Logout button fully on screen", JSON.stringify(lo));
    const nav = await rect(page, "aside"); ok(nav && Math.abs(nav.b - 740) <= 1 && nav.w === VW, role + ": tab bar is pinned to the bottom, full width", JSON.stringify(nav));
    const small = await page.$$eval(".nav", (els) => els.filter((e) => e.offsetParent !== null).map((e) => e.getBoundingClientRect().height).filter((h) => h < 44).length);
    ok(small === 0, role + ": every tab is at least 44 px tall");
    ok(await page.evaluate(() => Array.isArray(window._cases) && Array.isArray(window._vetAnimals)), role + ": _cases and _vetAnimals are always lists (no ReferenceError possible)");
    await ctx.close();
  }

  console.log("P2  tapping the tab bar navigates (real touch events)");
  let ctx = await b.createBrowserContext(), page = await L.newPage(ctx); await L.login(page, "vet");
  await page.tap('.nav[data-p="health"]'); await settle(page);
  ok(await page.$eval(".nav.active", (e) => e.dataset.p) === "health", "tap on the stethoscope opens Health / Cases and marks it active");
  await page.tap('.nav[data-p="notifications"]'); await settle(page);
  ok(await page.$eval(".nav.active", (e) => e.dataset.p) === "notifications", "tap on the bell opens Notifications");

  console.log("P3  case list: status visible without scrolling; tapping the row opens the case exactly once");
  await page.tap('.nav[data-p="health"]'); await settle(page);
  const st = await rect(page, "tbody tr:first-child .phone-only .pill"); ok(st && st.l >= 0 && st.r <= VW && st.h > 0, "status is visible in the first column, no sideways scroll needed", JSON.stringify(st));
  await page.evaluate(() => { window.__sc = 0; const o = window.showCase; window.showCase = function () { window.__sc++; return o.apply(this, arguments); }; });
  await page.tap("tbody tr:first-child td:nth-child(2)"); await sleep(900);
  ok(/Case #\d+/.test(await page.$eval("#modalBody", (e) => e.textContent)), "tapping a row opens the case");
  ok(await page.evaluate(() => window.__sc) === 1, "showCase ran once (" + await page.evaluate(() => window.__sc) + ")");
  await page.evaluate(() => closeModal()); await page.evaluate(() => { window.__sc = 0; [...document.querySelectorAll("tbody tr:first-child button")].find((x) => x.offsetParent).click(); }); await sleep(900);
  ok(await page.evaluate(() => window.__sc) === 1, "the row's own button still works and does not double-fire (" + await page.evaluate(() => window.__sc) + ")");

  console.log("P4  case screen and report view on a phone");
  await page.evaluate(() => showCase(3)); await settle(page);
  const vb = await page.$eval('#modalBody button[onclick^="reportDetailModal("]', (e) => e.getBoundingClientRect().height);
  ok(vb >= 30, "View report button is a real tap target (" + Math.round(vb) + " px tall, was 18)");
  await page.evaluate(() => { document.querySelector(".modalbox").scrollTop = 400; });
  await page.tap('#modalBody button[onclick^="reportDetailModal("]'); await sleep(700);
  ok(/GramVet AI health report/.test(await page.$eval("#modalBody", (e) => e.textContent)), "the report opens");
  ok(await page.$eval(".modalbox", (e) => e.scrollTop) === 0, "and it opens at the top, not scrolled halfway");
  const box = await rect(page, ".modalbox"); ok(box && box.l >= 0 && box.r <= VW, "the modal fits the screen", JSON.stringify(box));
  await page.evaluate(() => document.querySelector(".modalbox").scrollTop = 500);
  await page.tap('#modalBody button[onclick*="showCase"]'); await sleep(900);
  ok(await page.$eval(".modalbox", (e) => e.scrollTop) === 0, "Back to case also returns to the top");
  const closeR = await rect(page, ".modalbox .close"); ok(closeR && closeR.w >= 40 && closeR.h >= 40, "the close button is at least 40x40", JSON.stringify(closeR));
  await page.evaluate(() => closeModal());

  console.log("P5  the sync-status toast is not hidden behind the tab bar");
  await page.evaluate(() => gvToast("Test message")); await sleep(200);
  const toast = await page.$eval('[role=status]', (e) => { const r = e.getBoundingClientRect(); return { b: Math.round(r.bottom), display: getComputedStyle(e).display }; });
  const navTop = (await rect(page, "aside")).t;
  ok(toast.display !== "none" && toast.b <= navTop, "toast sits above the tab bar (toast bottom " + toast.b + " <= bar top " + navTop + ")");
  await ctx.close();

  console.log("P6  tablet and desktop are unchanged: side rail, no tab bar");
  for (const [w, h] of [[768, 1024], [1280, 800]]) {
    const c2 = await b.createBrowserContext(), p2 = await L.newPage(c2, { width: w, height: h }); await L.login(p2, "vet");
    const a = await p2.$eval("aside", (e) => { const r = e.getBoundingClientRect(), cs = getComputedStyle(e); return { pos: cs.position, w: Math.round(r.width), bottom: Math.round(r.bottom), left: Math.round(r.left) }; });
    ok(a.pos !== "fixed" && a.left === 0 && a.w >= 60 && a.w <= 260, w + "px: sidebar stays on the left (" + a.w + " px wide)", JSON.stringify(a));
    ok(await p2.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth) <= 1, w + "px: no sideways scroll");
    await p2.evaluate(() => go("health")); await settle(p2);
    ok(await p2.evaluate(() => { const e = document.querySelector(".phone-only"); return e ? getComputedStyle(e).display : "missing"; }) === "none", w + "px: the phone-only status line is hidden (the Status column is visible instead)");
    await c2.close();
  }
  await L.closeBrowser(); await L.stopServer(srv);
  console.log("\n" + passed + " passed, " + failed + " failed"); process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.log("HARNESS ERROR:", String(e.stack || e).slice(0, 800)); await L.closeBrowser().catch(() => {}); process.exit(2); });
