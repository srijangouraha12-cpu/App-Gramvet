const L = require("./lib"), fs = require("fs");
async function settle(page) { await page.waitForNetworkIdle({ idleTime: 350, timeout: 7000 }).catch(() => {}); await L.sleep(250); }
(async () => {
  L.prepare("/home/claude/gramvet final - Copy", "/home/claude/e2e_run");
  const srv = L.startServer("/home/claude/e2e_run"); await L.waitUp(20000, srv);
  const b = await L.browser(); const out = [];
  for (const [lang, dark] of [["hi", false], ["mr", false], ["en", true], ["hi", true]]) {
    for (const role of ["farmer", "vet", "government"]) {
      const ctx = await b.createBrowserContext(), page = await L.newPage(ctx);
      await page.evaluateOnNewDocument((l) => { try { localStorage.setItem("gramvet_lang", l); } catch (e) {} }, lang);
      await L.login(page, role);
      if (dark) await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
      const screens = await page.$$eval(".nav", (els) => els.filter((e) => e.offsetParent !== null && e.getBoundingClientRect().width > 0).map((e) => e.dataset.p));
      for (const p of screens) {
        page.problems.length = 0; await page.evaluate((x) => go(x), p); await settle(page);
        const r = await page.evaluate(() => { const de = document.documentElement; return { over: de.scrollWidth - de.clientWidth, lang: localStorage.getItem("gramvet_lang"), title: (document.querySelector("main h1") || {}).textContent }; });
        const probs = page.problems.filter((x) => !/openstreetmap|Failed to load resource|ERR_FAILED/.test(x.text));
        if (r.over > 1 || probs.length) out.push(lang + (dark ? "+dark" : "") + " " + role + "/" + p + " over=" + r.over + (probs.length ? " problems: " + probs.slice(0, 2).map((x) => x.type + " " + x.text).join(" | ") : ""));
        if (p === "dashboard" || (role === "vet" && p === "health")) await page.screenshot({ path: "/tmp/shots/var_" + lang + (dark ? "_dark" : "") + "_" + role + "_" + p + ".png" });
      }
      out.push("checked " + lang + (dark ? "+dark" : "") + " " + role + ": " + screens.length + " screens, first title: " + (await page.$eval("main h1", (e) => e.textContent).catch(() => "?")).slice(0, 30));
      await ctx.close();
    }
  }
  await L.closeBrowser(); await L.stopServer(srv);
  console.log(out.filter((x) => !x.startsWith("checked")).join("\n") || "no overflow or errors in any language / theme combination");
  console.log(out.filter((x) => x.startsWith("checked")).slice(0, 5).join("\n"));
})().catch(async (e) => { console.log("FAILED", String(e).slice(0, 500)); await L.closeBrowser(); process.exit(1); });
