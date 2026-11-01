// Report detail view ("View report ›" on each report in a case history).
// Loads the page's REAL code (every top-level function/const parsed out of templates/index.html) into jsdom and
// feeds it a real case response captured from the Flask app (fixtures/case_detail.json, personal data scrubbed).
// Run:  node test_report_detail.js <project folder>        (needs: npm i jsdom acorn)
const fs = require("fs"), path = require("path"), acorn = require("acorn"), { JSDOM } = require("jsdom");
const proj = path.resolve(process.argv[2]);
const FIXTURE = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "case_detail.json"), "utf8"));
const clone = (o) => JSON.parse(JSON.stringify(o));

let passed = 0, failed = 0;
function ok(c, n, x) { if (c) { passed++; console.log("  ok   " + n); } else { failed++; console.log("  FAIL " + n + (x ? "  -> " + String(x).slice(0, 300) : "")); } }

function appScript() {
  const h = fs.readFileSync(path.join(proj, "templates", "index.html"), "utf8").replace(/\r/g, "").replace(/\{\{[^}]*\}\}/g, "[]");
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g; let m; const all = [];
  while ((m = re.exec(h))) all.push(m[1]);
  return all.reduce((a, b) => (a.length >= b.length ? a : b));
}
const SRC = appScript();
const AST = acorn.parse(SRC, { ecmaVersion: 2022 });
const FNS = [], VARS = [], I18N_ADDS = [];
for (const n of AST.body) {
  if (n.type === "FunctionDeclaration") FNS.push(SRC.slice(n.start, n.end));
  else if (n.type === "VariableDeclaration") VARS.push("var" + SRC.slice(n.start + n.kind.length, n.end));   // const/let -> var so tests can reach them
  else if (n.type === "ExpressionStatement" && /^Object\.assign\(I18N\./.test(SRC.slice(n.start, n.start + 30))) I18N_ADDS.push(SRC.slice(n.start, n.end));   // the page's translations added after the base table
}

function makePage(role, fixture) {
  const dom = new JSDOM('<body><div id="modal" class="hidden"><div id="modalBody"></div></div><div id="main"></div></body>',
    { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  const st = { calls: [], fixture: fixture || clone(FIXTURE), alerts: [] };
  w.fetch = async (url) => {
    st.calls.push(url);
    if (/^\/api\/cases\/\d+$/.test(url)) return { ok: true, status: 200, json: async () => ({ case: st.fixture }) };
    if (/notifications/.test(url)) return { ok: true, status: 200, json: async () => ({ notifications: [], unread: 0 }) };
    return { ok: true, status: 200, json: async () => ({}) };
  };
  w.alert = (m) => st.alerts.push(m);
  w.eval(FNS.join("\n"));
  const skipped = [];
  for (const v of VARS) { try { w.eval(v); } catch (e) { skipped.push(v.slice(0, 40)); } }
  for (const a of I18N_ADDS) w.eval(a);
  w.modal = w.document.getElementById("modal"); w.modalBody = w.document.getElementById("modalBody"); w.main = w.document.getElementById("main");
  w.eval('gramvetState.currentUser = { role: "' + role + '", id: 1 }; currentLang = "en";');
  st.pageKeysEn = Object.keys(w.eval("I18N.en"));
  w.eval(fs.readFileSync(path.join(proj, "static", "pwa", "i18n-offline.js"), "utf8"));   // loads AFTER the page table, like the real page
  return { w, st, skipped, body: () => w.modalBody.innerHTML, text: () => w.modalBody.textContent };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function reportRow(f, id) { return f.reports.find((r) => r.id === id); }

(async () => {
  const good = FIXTURE.reports.find((r) => r.prediction);          // a report that has an AI result
  const gid = good.id;

  console.log("R0  the real page code loaded");
  { const p = makePage("vet"); ok(typeof p.w.showPredictionResult === "function" && typeof p.w.showCase === "function", "real functions available", p.skipped.join()); }

  console.log("R1  every report in the case history gets a View button; tapping it opens the report");
  {
    const p = makePage("vet"); await p.w.showCase(FIXTURE.id);
    const btns = [...p.w.modalBody.querySelectorAll("button")].filter((b) => /reportDetailModal\(/.test(b.getAttribute("onclick") || ""));
    ok(btns.length === FIXTURE.reports.length, "one button per report (" + FIXTURE.reports.length + ")", btns.length);
    ok(btns.some((b) => b.getAttribute("onclick") === "reportDetailModal(" + gid + ")"), "button carries the right report id");
    const target = btns.find((b) => b.getAttribute("onclick") === "reportDetailModal(" + gid + ")") || btns[0];
    if (target) { try { p.w.eval(target.getAttribute("onclick").replace("this.closest('.timeline-item')", "null")); } catch (e) { /* reported below */ } }
    ok(p.text().includes("GramVet AI health report"), "opens the AI health report view", p.text().slice(0, 120));
  }

  console.log("R2  it shows the stored result: disease, urgency, outbreak, escalation");
  {
    const p = makePage("vet"); await p.w.showCase(FIXTURE.id); try { p.w.reportDetailModal(gid); } catch (e) {}
    const rp = good.prediction, T = p.text();
    ok(T.includes(p.w.diseaseLabel(rp.predicted_disease)), "disease name: " + p.w.diseaseLabel(rp.predicted_disease));
    ok(T.includes(rp.confidence.toFixed(1) + "%"), "confidence " + rp.confidence.toFixed(1) + "%", T.slice(0, 200));
    ok(T.includes(p.w.statusLabel(rp.triage_risk)), "urgency: " + p.w.statusLabel(rp.triage_risk));
    ok(T.includes(p.w.statusLabel(rp.outbreak_status)) && T.includes(p.w.obScoreText(rp)), "outbreak: " + p.w.statusLabel(rp.outbreak_status) + " " + p.w.obScoreText(rp));
    ok(T.includes(rp.escalate ? p.w.t("c_esc_yes") : p.w.t("c_esc_no")), "escalation line");
  }

  console.log("R3  weather, symptoms and the model inputs are all there");
  {
    const p = makePage("vet"); await p.w.showCase(FIXTURE.id); try { p.w.reportDetailModal(gid); } catch (e) {}
    const T = p.text(), wx = good.weather, inp = good.report_inputs;
    ok(T.includes(String(wx.temperature_c)) && T.includes(String(wx.humidity_pct)), "weather: " + wx.temperature_c + "°C, " + wx.humidity_pct + "%");
    ok(good.symptoms.every((s) => T.includes(p.w.symptomLabel(s))), "all " + good.symptoms.length + " symptoms listed");
    ok(T.includes(String(inp.age_months)) && T.includes("Age"), "age " + inp.age_months);
    ok(T.includes("Herd size") && T.includes(String(inp.herd_size)), "herd size " + inp.herd_size);
    ok(T.includes("Distance to water") && T.includes(String(inp.dist_waterbody_km)), "distance to water " + inp.dist_waterbody_km + " km");
    ok(T.includes("Vaccinations") && T.includes("None"), "vaccinations: none recorded");
  }

  console.log("R4  history wording: no 'just saved / now open' text, has a way back");
  {
    const p = makePage("vet"); await p.w.showCase(FIXTURE.id); try { p.w.reportDetailModal(gid); } catch (e) {}
    const T = p.text();
    ok(T.includes("Case #" + FIXTURE.id) && T.includes(p.w.statusLabel(FIXTURE.status)), "banner names the case and its status");
    ok(!T.includes("now open for veterinary review") && !T.includes(p.w.t("rp_saved")), "no 'saved permanently / now open' wording");
    ok(!T.includes(p.w.t("rp_open_until")), "no 'open until…' line");
    const back = [...p.w.modalBody.querySelectorAll("button")].find((b) => /^showCase\(/.test(b.getAttribute("onclick") || ""));
    ok(!!back, "Back to case button present");
    if (back) await p.w.eval(back.getAttribute("onclick"));
    ok(p.w.modalBody.querySelectorAll('[onclick^="reportDetailModal("]').length === FIXTURE.reports.length, "Back returns to the case history");
  }

  console.log("R5  roles: vet sees the calculation; farmer does not, and no advisory pop-ups fire");
  {
    const pv = makePage("vet"); await pv.w.showCase(FIXTURE.id); try { pv.w.reportDetailModal(gid); } catch (e) {}
    ok(pv.body().includes("<details"), "vet: 'show calculation' present");
    const pf = makePage("farmer"); await pf.w.showCase(FIXTURE.id); pf.st.calls.length = 0; try { pf.w.reportDetailModal(gid); } catch (e) {}
    await sleep(30);
    ok(!pf.body().includes("<details"), "farmer: no calculation section");
    ok(!pf.st.calls.some((u) => /notifications/.test(u)), "farmer: no notification fetch / advisory pop-up", pf.st.calls.join());
    ok(pf.text().includes("GramVet AI health report"), "farmer: sees the report");
  }

  console.log("R6  vaccines are shown when recorded");
  {
    const f = clone(FIXTURE), r = reportRow(f, gid);
    r.report_inputs.vaccination_flags.Vaccinated_FMD = 1; r.report_inputs.vaccination_flags.Vaccinated_HS = 1;
    r.report_inputs.vaccine_dates.FMD = "2026-08-01T00:00:00";
    const p = makePage("vet", f); await p.w.showCase(f.id); try { p.w.reportDetailModal(gid); } catch (e) {}
    ok(p.text().includes("FMD, HS"), "names listed", p.text().slice(-300));
    ok(p.text().includes("FMD 2026-08-01"), "date shown for the one that has it");
  }

  console.log("R7  a report with no stored AI result does not crash and says so");
  {
    const f = clone(FIXTURE), r = reportRow(f, gid); r.prediction = null;
    const p = makePage("vet", f); await p.w.showCase(f.id); try { p.w.reportDetailModal(gid); } catch (e) {}
    ok(p.text().includes("No AI result was stored"), "says so");
    ok(p.text().includes(String(r.report_inputs.age_months)) && p.text().includes("Age"), "inputs still shown");
    ok(!p.text().includes(p.w.t("c_urgency")) && !p.text().includes(p.w.t("c_outbreak")), "no empty result tiles (urgency / outbreak)");
  }

  console.log("R8  unknown report id / no open case: nothing happens");
  {
    const p = makePage("vet"); await p.w.showCase(FIXTURE.id); const before = p.body(); try { p.w.reportDetailModal(999999); } catch (e) {}
    ok(p.body() === before, "unknown id leaves the screen alone");
    const q = makePage("vet"); q.w.eval("window.currentOpenCase = null"); try { q.w.reportDetailModal(1); } catch (e) {} ok(q.body() === "", "no open case -> nothing");
  }

  console.log("R9  Hindi and Marathi");
  {
    for (const [lang, title, age, view] of [["hi", "GramVet AI स्वास्थ्य रिपोर्ट", "आयु", "रिपोर्ट देखें"], ["mr", "GramVet AI आरोग्य अहवाल", "वय", "अहवाल पहा"]]) {
      const p = makePage("vet"); p.w.eval('currentLang = "' + lang + '"');
      await p.w.showCase(FIXTURE.id);
      ok(p.text().includes(view), lang + ": View button label");
      try { p.w.reportDetailModal(gid); } catch (e) {}
      ok(p.text().includes(title), lang + ": title from the page's own table");
      ok(p.text().includes(age), lang + ": input labels translated");
    }
  }

  console.log("R10 guard: my translation keys never overwrite the page's own");
  {
    const p = makePage("vet"), mine = Object.keys(p.w.gvI18nOffline.en), clash = mine.filter((k) => p.st.pageKeysEn.includes(k));
    ok(clash.length === 0, "no key in i18n-offline.js collides with the page's table", clash.join(", "));
    for (const l of ["hi", "mr"]) ok(Object.keys(p.w.gvI18nOffline.en).every((k) => k in p.w.gvI18nOffline[l]), l + ": every English key has a translation");
  }

  console.log("\n" + passed + " passed, " + failed + " failed");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.log("HARNESS ERROR:", e); process.exit(2); });
