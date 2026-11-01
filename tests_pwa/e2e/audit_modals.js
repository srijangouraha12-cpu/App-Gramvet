const L = require("./lib"), fs = require("fs");
async function settle(page) { await page.waitForNetworkIdle({ idleTime: 300, timeout: 6000 }).catch(() => {}); await L.sleep(250); }
async function modalInfo(page) {
  return page.evaluate(() => {
    const m = document.getElementById("modal"), box = m && m.querySelector(".modalbox"); if (!m || m.classList.contains("hidden") || !box) return { open: false };
    const vw = document.documentElement.clientWidth, vh = window.innerHeight, r = box.getBoundingClientRect();
    const over = []; for (const el of box.querySelectorAll("*")) { const b = el.getBoundingClientRect(); if (b.width && b.right > vw + 1 && !el.closest(".tablewrap")) { over.push(el.tagName.toLowerCase() + (el.className ? "." + String(el.className).split(" ")[0] : "") + " r=" + Math.round(b.right)); if (over.length > 3) break; } }
    const small = []; for (const el of box.querySelectorAll("button,select,input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,a[onclick]")) { const b = el.getBoundingClientRect(); if (b.width && b.height && (b.height < 34 || b.width < 34)) small.push((el.tagName.toLowerCase() + ":" + (el.textContent || el.placeholder || el.type || "").trim().slice(0, 14)) + " " + Math.round(b.width) + "x" + Math.round(b.height)); }
    return { open: true, boxLeft: Math.round(r.left), boxRight: Math.round(r.right), vw, boxH: Math.round(r.height), vh, scrolls: box.scrollHeight > box.clientHeight + 1, hOver: box.scrollWidth - box.clientWidth, over, small: small.slice(0, 8), title: (box.querySelector("h2,h3") || {}).textContent };
  });
}
(async () => {
  L.prepare("/home/claude/gramvet final - Copy", "/home/claude/e2e_run");
  const srv = L.startServer("/home/claude/e2e_run"); await L.waitUp(20000, srv);
  const b = await L.browser(); const R = [];
  const note = (name, info, page) => { R.push({ name, info, problems: page.problems.filter((x) => !/openstreetmap|Failed to load resource/.test(x.text)) }); page.problems.length = 0; };

  // ---- vet: case modal -> View report -> back -> close
  let ctx = await b.createBrowserContext(), page = await L.newPage(ctx); await L.login(page, "vet");
  await page.evaluate(() => go("health")); await settle(page);
  await page.evaluate(() => showCase(3)); await settle(page);
  note("vet: case modal", await modalInfo(page), page);
  await page.screenshot({ path: "/tmp/shots/m_vet_case.png" });
  const hasBtn = await page.$('#modalBody button[onclick^="reportDetailModal("]');
  if (hasBtn) { await hasBtn.tap ? hasBtn.tap() : hasBtn.click(); await settle(page); }
  note("vet: View report", await modalInfo(page), page);
  await page.screenshot({ path: "/tmp/shots/m_vet_report.png", fullPage: false });
  const back = await page.$('#modalBody button[onclick^="showCase("]'); if (back) { await back.click(); await settle(page); }
  note("vet: back to case", await modalInfo(page), page);
  await page.click(".modalbox .close"); await L.sleep(200);
  R.push({ name: "vet: modal closes with x", info: { closed: await page.evaluate(() => document.getElementById("modal").classList.contains("hidden")) }, problems: [] });
  await ctx.close();

  // ---- farmer: report form, add-livestock form, sync panel
  ctx = await b.createBrowserContext(); page = await L.newPage(ctx); await L.login(page, "farmer");
  await page.evaluate(() => go("livestock")); await settle(page);
  const rb = await page.$$eval("button", (bs) => bs.filter((x) => /report/i.test(x.textContent) && x.offsetParent).map((x) => x.textContent.trim()).slice(0, 3));
  R.push({ name: "farmer: report buttons found", info: rb, problems: [] });
  const opened = await page.evaluate(() => { const bt = [...document.querySelectorAll("button")].find((x) => /report/i.test(x.textContent) && x.offsetParent); if (bt) { bt.click(); return true; } return false; });
  await settle(page); note("farmer: report form (opened=" + opened + ")", await modalInfo(page), page);
  await page.screenshot({ path: "/tmp/shots/m_farmer_report.png", fullPage: false });
  await page.evaluate(() => closeModal());
  await page.evaluate(() => animalModal()); await settle(page); note("farmer: add livestock form", await modalInfo(page), page);
  await page.evaluate(() => closeModal());
  await page.click("#gvNet"); await L.sleep(400); note("farmer: sync panel", await modalInfo(page), page);
  await page.screenshot({ path: "/tmp/shots/m_farmer_sync.png" });
  await ctx.close();

  await L.closeBrowser(); await L.stopServer(srv);
  for (const r of R) {
    const i = r.info || {}; const flags = [];
    if (i.open === false) flags.push("NOT OPEN"); if (i.hOver > 1) flags.push("hOver=" + i.hOver); if (i.over && i.over.length) flags.push("over: " + i.over.join(", "));
    if (i.boxRight > i.vw + 1 || i.boxLeft < -1) flags.push("box outside viewport " + i.boxLeft + ".." + i.boxRight + "/" + i.vw);
    console.log((flags.length || r.problems.length ? "!! " : "ok ") + r.name + (i.title ? "  [" + String(i.title).slice(0, 40) + "]" : "") + (i.boxH ? "  box " + i.boxH + "/" + i.vh + "px" + (i.scrolls ? " scrolls" : "") : "") + (flags.length ? "  " + flags.join(" | ") : "") + (i.small && i.small.length ? "\n      small tap targets: " + i.small.join("; ") : "") + (r.problems.length ? "\n      " + r.problems.slice(0, 3).map((p) => p.type + ": " + p.text).join("\n      ") : "") + (Array.isArray(i) ? "  " + JSON.stringify(i) : ""));
  }
  console.log("server errors:", srv.log.join("").split("\n").filter((l) => /Traceback|Error|500 -/.test(l)).slice(0, 4).join(" | ") || "none");
})().catch(async (e) => { console.log("FAILED:", String(e).slice(0, 700)); await L.closeBrowser(); process.exit(1); });
