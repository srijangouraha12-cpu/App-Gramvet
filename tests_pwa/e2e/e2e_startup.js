// Start-up behaviour in a real browser: signed-out sees the login page, signed-in never downloads it.
const L = require("./lib"), sleep = L.sleep;
let passed = 0, failed = 0;
const ok = (c, n, x) => { if (c) { passed++; console.log("  ok   " + n); } else { failed++; console.log("  FAIL " + n + (x !== undefined ? "  -> " + String(x).slice(0, 240) : "")); } };
const HERO = /\/media\/(p1|p1_dark|p2_dark|p3|p4)\.(jpg|png)/;
async function track(page) {
  const cdp = await page.createCDPSession(); await cdp.send("Network.enable"); const s = { urls: [], bytes: 0 };
  cdp.on("Network.requestWillBeSent", (e) => s.urls.push(e.request.url.replace(L.BASE, ""))); cdp.on("Network.loadingFinished", (e) => { s.bytes += e.encodedDataLength; });
  await page.evaluateOnNewDocument(() => { window.__authShown = false; new MutationObserver(() => { const a = document.getElementById("auth"); if (a && !a.classList.contains("hidden")) window.__authShown = true; }).observe(document, { subtree: true, attributes: true, childList: true, attributeFilter: ["class"] }); });
  return s;
}
(async () => {
  L.prepare("/home/claude/gramvet final - Copy", "/home/claude/e2e_run");
  const srv = L.startServer("/home/claude/e2e_run"); await L.waitUp(20000, srv);
  const b = await L.browser();

  console.log("S1  signed out: the login page appears, complete with its pictures");
  let ctx = await b.createBrowserContext(), page = await L.newPage(ctx); let t = await track(page);
  await page.goto(L.BASE + "/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("#phone", { visible: true, timeout: 10000 });
  ok(await page.$eval("#bootSplash", () => true).catch(() => false) === false, "the 'GramVet…' splash is gone");
  ok(await page.$eval("#app", (e) => e.classList.contains("hidden")), "the app is hidden");
  await page.waitForNetworkIdle({ idleTime: 800, timeout: 20000 }).catch(() => {});
  ok(t.urls.some((u) => /p1\.jpg/.test(u)) && t.urls.some((u) => /p4\.png/.test(u)), "hero image and logos are fetched now that the page is shown", t.urls.filter((u) => /media/.test(u)).join(" "));
  const bg = await page.$eval(".auth-hero-bg", (e) => getComputedStyle(e).backgroundImage); ok(/p1/.test(bg), "the hero background is applied: " + bg.slice(0, 50));
  await page.screenshot({ path: "/tmp/shots/s_login_phone.png" });
  await page.click("#signupSwitch"); await sleep(300);
  ok(await page.$eval("#authBody", (e) => e.querySelectorAll("input").length) >= 3, "switching to Sign up shows the sign-up form");
  await page.click("#loginSwitch"); await sleep(300);
  await page.type("#phone", "9000000002"); await page.type("#password", "test1234");
  await page.evaluate(() => [...document.querySelectorAll("#authBody button")].find((x) => /login\(\)/.test(x.getAttribute("onclick") || "")).click());
  const opened = await page.waitForFunction(() => document.getElementById("auth").classList.contains("hidden") && !document.getElementById("app").classList.contains("hidden"), { timeout: 10000 }).then(() => true).catch(() => false);
  ok(opened, "typing a phone and password and tapping Sign in opens the app and hides the login page");
  ok(await page.$eval("#role", (e) => e.textContent.trim().length > 0).catch(() => false), "the app is filled in for the signed-in user");
  await ctx.close();

  console.log("S2  signed in, cold start: no login page, none of its pictures, much less data");
  ctx = await b.createBrowserContext(); page = await L.newPage(ctx);
  await page.goto(L.BASE + "/", { waitUntil: "domcontentloaded" });
  await page.evaluate(async (ph, pw) => { await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ phone: ph, password: pw }) }); }, L.PHONES.vet, L.PASSWORD);
  await page.evaluate(async () => { for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister(); for (const k of await caches.keys()) await caches.delete(k); });
  t = await track(page); await page.setCacheEnabled(false);
  await page.goto(L.BASE + "/", { waitUntil: "domcontentloaded" }); await page.waitForSelector(".nav", { timeout: 15000 }); await page.waitForNetworkIdle({ idleTime: 1500, timeout: 30000 }).catch(() => {});
  ok(!t.urls.some((u) => HERO.test(u) && !/p2\.png/.test(u)), "no login-page picture was requested", t.urls.filter((u) => HERO.test(u)).join(" "));
  ok(await page.evaluate(() => window.__authShown) === false, "the login page was never visible, not even for a moment");
  ok(await page.$eval("#bootSplash", () => true).catch(() => false) === false, "the splash was removed");
  const kb = Math.round(t.bytes / 1024); ok(kb < 800, "cold start transfers " + kb + " KB (was 1831 KB)", kb);
  const next = await page.$eval("aside", () => true); ok(next, "the app is usable");

  console.log("S3  sign out returns to the login page");
  await page.evaluate(() => logout()); await page.waitForSelector("#phone", { visible: true, timeout: 15000 }).catch(() => {});
  ok(await page.$eval("#phone", (e) => !!e.offsetParent).catch(() => false), "the login form is shown again after logging out");
  await ctx.close();
  await L.closeBrowser(); await L.stopServer(srv);
  console.log("\n" + passed + " passed, " + failed + " failed"); process.exit(failed ? 1 : 0);
})().catch(async (e) => { console.log("HARNESS ERROR:", String(e.stack || e).slice(0, 800)); await L.closeBrowser().catch(() => {}); process.exit(2); });
