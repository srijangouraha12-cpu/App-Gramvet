const L = require("./lib"), fs = require("fs");
const WIDTHS = [[360, 740, true], [768, 1024, false], [1280, 800, false]];
async function settle(page) { await page.waitForNetworkIdle({ idleTime: 350, timeout: 7000 }).catch(() => {}); await L.sleep(250); }
async function overflow(page) {
  return page.evaluate(() => {
    const de = document.documentElement, vw = de.clientWidth, out = { docOver: de.scrollWidth - vw, bodyOver: document.body.scrollWidth - vw, offenders: [] };
    for (const el of document.querySelectorAll("body *")) {
      const r = el.getBoundingClientRect(); if (!r.width || !r.height) continue;
      const cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none" || cs.position === "fixed") continue;
      if (r.right > vw + 1 && !el.closest("[style*='overflow']") ) out.offenders.push((el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : "")) + " right=" + Math.round(r.right) + " w=" + Math.round(r.width));
      if (out.offenders.length >= 6) break;
    }
    return out;
  });
}
(async () => {
  L.prepare("/home/claude/gramvet final - Copy", "/home/claude/e2e_run");
  const srv = L.startServer("/home/claude/e2e_run"); await L.waitUp(20000, srv);
  const b = await L.browser(), report = {};
  for (const role of ["farmer", "vet", "government"]) {
    report[role] = {};
    for (const [w, h, mobile] of WIDTHS) {
      const ctx = await b.createBrowserContext();
      const page = await L.newPage(ctx, mobile ? L.MOBILE : { width: w, height: h });
      await L.login(page, role);
      const screens = await page.$$eval(".nav", (els) => els.filter((e) => e.offsetParent !== null && e.getBoundingClientRect().width > 0).map((e) => e.dataset.p));
      const hidden = await page.$$eval(".nav", (els) => els.filter((e) => !(e.offsetParent !== null && e.getBoundingClientRect().width > 0)).map((e) => e.dataset.p));
      for (const p of screens) {
        page.problems.length = 0;
        await page.evaluate((x) => go(x), p); await settle(page);
        const ov = await overflow(page);
        const key = role + "/" + p + "@" + w;
        report[role][p + "@" + w] = { problems: page.problems.slice(), overflow: ov };
        if (w === 360) await page.screenshot({ path: "/tmp/shots/" + role + "_" + p + "_360.png", fullPage: true });
      }
      report[role]["_nav@" + w] = { visible: screens, hidden };
      await ctx.close();
    }
  }
  await L.closeBrowser(); await L.stopServer(srv);
  fs.writeFileSync("/tmp/audit_screens.json", JSON.stringify(report, null, 1));
  // compact summary
  for (const role of Object.keys(report)) {
    console.log("== " + role);
    for (const k of Object.keys(report[role])) {
      if (k.startsWith("_nav")) { console.log("  " + k + " visible: " + report[role][k].visible.join(",") + (report[role][k].hidden.length ? "  | not shown: " + report[role][k].hidden.join(",") : "")); continue; }
      const r = report[role][k], o = r.overflow, bad = r.problems.length || o.docOver > 1 || o.bodyOver > 1 || o.offenders.length;
      if (bad) console.log("  " + k + "  problems=" + r.problems.length + " docOver=" + o.docOver + " bodyOver=" + o.bodyOver + (o.offenders.length ? " offenders=" + o.offenders.join(" ; ") : "") + (r.problems.length ? "\n      " + r.problems.slice(0, 4).map((x) => x.type + ": " + x.text).join("\n      ") : ""));
    }
  }
  console.log("(only screens with a problem are listed)");
  console.log("server errors:", srv.log.join("").split("\n").filter((l) => /Traceback|Error|500 -/.test(l)).slice(0, 6).join(" | ") || "none");
})().catch(async (e) => { console.log("FAILED:", String(e).slice(0, 600)); await L.closeBrowser(); process.exit(1); });
