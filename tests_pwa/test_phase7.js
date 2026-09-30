// Run:  npm install jsdom fake-indexeddb   (once, in this folder)
//       node test_phase7.js "<project>\static\pwa"
// Phase 7 tests: real db.js / outbox.js / pwa.js / i18n-offline.js in jsdom + fake IndexedDB, scripted server.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const { IDBFactory, IDBKeyRange } = require("fake-indexeddb");

const PWA = process.argv[2];
const read = (f) => fs.readFileSync(path.join(PWA, f), "utf8");

// The page's own helpers, copied from templates/index.html so behaviour matches.
// The page's real functions, cut out of templates/index.html so the edits themselves are tested.
const HTML = fs.readFileSync(path.join(PWA, "..", "..", "templates", "index.html"), "utf8").replace(/\r/g, "");
function line(marker) { const i = HTML.indexOf(marker); if (i < 0) throw new Error("not in index.html: " + marker); return HTML.slice(i, HTML.indexOf("\n", i)); }
function block(marker) { const i = HTML.indexOf(marker); if (i < 0) throw new Error("not in index.html: " + marker); return HTML.slice(i, HTML.indexOf("\n}", i) + 2); }
const PAGE_FUNCS = [line("async function api(url,opt={})"), line("async function addAction("), line("async function closeCase("), block("async function submitModelReview(")].join("\n");
const PAGE_HELPERS = `
  var currentLang = "en";
  var I18N = { en: {}, hi: {}, mr: {} };
  function t(key, fallback) { var m = I18N[currentLang] || I18N.en; return m[key] || I18N.en[key] || fallback || key; }
  function esc(x){return String(x??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}
  function bar(label,frac){ return "<div>" + esc(label) + " " + Math.round((frac||0)*100) + "%</div>"; }
  function diseaseLabel(d){ return d; }
  function symptomLabel(s){ return s; }
  var animals = [{ id: 7, name: "Lakshmi", species: "Cow", sex: "Female", age: "4" }];
  function closeModal(){ modal.classList.add("hidden"); }
`;

let passed = 0, failed = 0;
function ok(cond, name, extra) {
  if (cond) { passed++; console.log("  ok   " + name); }
  else { failed++; console.log("  FAIL " + name + (extra ? "  -> " + extra : "")); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// A flush that finds another flush running returns { skipped: true } and sends nothing; wait and try again.
async function flushNow(b) { for (let i = 0; i < 50; i++) { const r = await b.w.gvsync.flush(); if (!r || !r.skipped) return r; await sleep(20); } }
async function until(fn, ms = 2000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await fn()) return true; await sleep(10); }
  return false;
}

// Build a fresh "browser": own DOM, own IndexedDB, own scripted server.
async function makeBrowser(opts = {}) {
  const dom = new JSDOM(`<!doctype html><body>
    <div class="topright"></div>
    <div id="modal" class="hidden"><div id="modalBody"></div></div></body>`,
    { runScripts: "outside-only", url: "http://localhost/" });
  const w = dom.window;
  w.indexedDB = new IDBFactory(); w.IDBKeyRange = IDBKeyRange;
  let online = opts.online !== false;
  Object.defineProperty(w.navigator, "onLine", { get: () => online, configurable: true });

  const server = {
    reportStatus: 200, reportError: "boom", gate: null, posts: 0, calls: [], vetStatus: 200, vetError: "nope", dropReply: false, gets: [],
    handler(url, o) {
      if (!o || !o.method || o.method === "GET") server.gets.push(url);
      if (o && o.method === "POST") server.calls.push({ url, headers: o.headers || {}, body: o.body === undefined ? undefined : JSON.parse(o.body) });
      if (/^\/api\/vet\/cases\/\d+\/(action|close|model-review)$/.test(url) && o && o.method === "POST") {
        return server.vetStatus === 200 ? { ok: true, status: 200, json: async () => ({ ok: true }) }
          : { ok: false, status: server.vetStatus, json: async () => ({ error: server.vetError }) };
      }
      if (url === "/api/me") return { ok: true, status: 200, json: async () => ({ authenticated: true, user: { id: 1, role: "farmer" } }) };
      if (url === "/api/cases/report" && o && o.method === "POST") {
        server.posts++;
        const respond = () => server.reportStatus === 200
          ? { ok: true, status: 200, json: async () => ({ case_id: 1 }) }
          : { ok: false, status: server.reportStatus, json: async () => ({ error: server.reportError }) };
        return server.gate ? server.gate.then(respond) : respond();
      }
      return { ok: true, status: 200, json: async () => ({ cases: [], animals: [] }) };
    }
  };
  w.fetch = async (url, o) => {
    // Offline, the service worker still answers /api/me from its seeded session; everything else fails.
    if (!online && url !== "/api/me") throw new w.TypeError("Failed to fetch");
    if (server.dropReply && o && o.method === "POST") { await server.handler(url, o); throw new w.TypeError("Failed to fetch"); }   // the server got it, the reply never arrived
    return server.handler(url, o);
  };
  w.confirm = () => (w.__confirm === undefined ? true : w.__confirm);

  w.eval(PAGE_HELPERS);
  w.modal = w.document.getElementById("modal");
  w.modalBody = w.document.getElementById("modalBody");
  for (const f of ["db.js", "outbox.js", "i18n-offline.js", "offline-vet.js", "pwa.js"]) w.eval(read(f));
  w.eval(PAGE_FUNCS);
  w.__alerts = []; w.__shown = []; w.alert = (m) => w.__alerts.push(m); w.showCase = (id) => w.__shown.push(id);
  w.actType = { value: "CURE" }; w.actDetails = { value: "Recovered well" };
  w.modelCorrect = { value: "yes" }; w.modelPred = { value: "FMD" }; w.modelReviewNotes = { value: "matches" };
  await sleep(30); // let DOMContentLoaded mount the badge and counts() resolve

  return {
    w, server,
    badge: () => w.document.getElementById("gvNet"),
    text: () => w.document.getElementById("gvNet").textContent,
    panel: () => w.document.getElementById("gvSyncPanel"),
    toastText: () => {
      const el = [...w.document.body.children].find((e) => e.getAttribute && e.getAttribute("role") === "status");
      return el && el.style.display !== "none" ? el.textContent : null;
    },
    setOnline(v) { online = v; w.dispatchEvent(new w.Event(v ? "online" : "offline")); },
    enqueueReport(extra) {
      return w.gvsync.enqueue({ url: "/api/cases/report", body: Object.assign({ animal_id: 7, symptoms: ["Fever"] }, extra) });
    },
    close() { w.close(); }
  };
}

async function main() {
  console.log("T1  badge is a button and shows offline + queued");
  {
    const b = await makeBrowser({ online: false });
    ok(b.badge().tagName === "BUTTON", "badge is a <button>");
    ok(b.text() === "Offline", "starts as 'Offline'", b.text());
    await b.enqueueReport();
    ok(b.text() === "Offline · 1 queued", "shows queued count", b.text());
    ok(b.badge().className.includes("warn"), "warn style while queued");
    b.close();
  }

  console.log("T2  reconnect: shows 'Syncing…' while sending, then Online + toast");
  {
    const b = await makeBrowser({ online: false });
    await b.enqueueReport();
    let release; b.server.gate = new Promise((r) => { release = r; });
    b.setOnline(true);
    ok(await until(() => b.text().startsWith("Syncing…")), "pill shows Syncing… mid-flight", b.text());
    ok(b.text().includes("1 left"), "pill shows how many are left", b.text());
    ok(b.toastText() === null, "no toast before it finished");
    release();
    ok(await until(() => b.text() === "Online"), "pill returns to plain Online", b.text());
    ok(b.toastText() === "✓ All queued reports were sent.", "toast confirms delivery", String(b.toastText()));
    ok(b.server.posts === 1, "sent exactly once", String(b.server.posts));
    b.close();
  }

  console.log("T3  no toast on page load or when nothing was queued");
  {
    const b = await makeBrowser({ online: true });
    ok(b.toastText() === null, "no toast at start");
    b.close();
  }

  console.log("T4  server rejects report: failed, no false 'sent' toast, panel explains it");
  {
    const b = await makeBrowser({ online: true });
    b.server.reportStatus = 400; b.server.reportError = "animal_id is required";
    await b.enqueueReport();
    b.w.gvsync.flush();
    ok(await until(() => b.text() === "Online · 1 failed"), "pill shows 1 failed", b.text());
    ok(b.toastText() === null, "no 'all sent' toast when the report failed", String(b.toastText()));
    b.badge().click();
    const p = b.panel();
    ok(!!p && !b.w.modal.classList.contains("hidden"), "tapping the pill opens the panel");
    ok(await until(() => p.textContent.includes("Failed")), "panel lists the failed report", p.textContent);
    ok(p.textContent.includes("Disease report · Lakshmi"), "row names the animal", p.textContent);
    ok(p.textContent.includes("animal_id is required"), "row shows the server's reason");
    ok(!!p.querySelector('[data-act="retry"]') && !!p.querySelector('[data-act="discard"]'), "Retry and Delete offered");

    console.log("T5  retry after the problem is fixed");
    b.server.reportStatus = 200;
    p.querySelector('[data-act="retry"]').click();
    ok(await until(() => b.text() === "Online"), "retry delivers it; pill back to Online", b.text());
    ok(await until(() => p.textContent.includes("Nothing is waiting")), "open panel refreshes to empty state", p.textContent);
    ok(b.toastText() === "✓ All queued reports were sent.", "toast after retry succeeds");
    b.close();
  }

  console.log("T6  delete a failed report (with confirmation)");
  {
    const b = await makeBrowser({ online: true });
    b.server.reportStatus = 400;
    await b.enqueueReport(); await b.w.gvsync.flush();
    await until(() => b.text() === "Online · 1 failed");
    b.badge().click();
    await until(() => b.panel().querySelector('[data-act="discard"]'));
    b.w.__confirm = false;
    b.panel().querySelector('[data-act="discard"]').click();
    await sleep(60);
    ok((await b.w.gvsync.list()).length === 1, "declining the confirmation keeps the report");
    b.w.__confirm = true;
    b.panel().querySelector('[data-act="discard"]').click();
    ok(await until(async () => (await b.w.gvsync.list()).length === 0), "confirming deletes it");
    ok(await until(() => b.text() === "Online"), "failed count clears from the pill", b.text());
    b.close();
  }

  console.log("T7  discard() refuses a report that is still waiting to send");
  {
    const b = await makeBrowser({ online: false });
    const id = await b.enqueueReport();
    await b.w.gvsync.discard(id);
    ok((await b.w.gvsync.list()).length === 1, "pending report survives discard(id)");
    const before = JSON.stringify((await b.w.gvsync.list())[0]);
    await b.w.gvsync.retry(id);
    ok(JSON.stringify((await b.w.gvsync.list())[0]) === before, "retry() on a non-failed item changes nothing");
    b.close();
  }

  console.log("T8  panel while offline: waiting label, Sync now disabled; voice-only label");
  {
    const b = await makeBrowser({ online: false });
    await b.enqueueReport();
    await b.enqueueReport({ symptoms: [], audio_base64: "data:audio/webm;base64,AAAA" });
    b.badge().click();
    const p = b.panel();
    await until(() => p.textContent.includes("Waiting"));
    ok(p.textContent.includes("Waiting for connection"), "offline wording", p.textContent);
    ok(p.textContent.includes("Voice report · Lakshmi") && p.textContent.includes("Disease report · Lakshmi"), "voice and symptom reports labelled apart");
    ok(p.querySelector('[data-act="sync"]').disabled, "Sync now disabled while offline");
    b.server.gate = null;
    b.setOnline(true);
    ok(await until(() => b.text() === "Online"), "both sent after reconnect", b.text());
    ok(b.server.posts === 2, "two POSTs", String(b.server.posts));
    b.close();
  }

  console.log("T9  Sync now button sends while online (e.g. after a server hiccup)");
  {
    const b = await makeBrowser({ online: true });
    b.server.reportStatus = 503;
    await b.enqueueReport();
    await b.w.gvsync.flush();                   // 503 -> stays pending (retry later)
    ok(b.text() === "Online · 1 queued", "still queued after a 503", b.text());
    b.server.reportStatus = 200;
    b.badge().click();
    const syncBtn = () => b.panel().querySelector('[data-act="sync"]');
    ok(await until(() => syncBtn() && !syncBtn().disabled), "Sync now is enabled (online, something waiting)");
    syncBtn().click();
    ok(await until(() => b.text() === "Online"), "Sync now drains the queue", b.text());
    b.close();
  }

  console.log("T10 nothing from the server or a farmer's text can inject markup into the panel");
  {
    const b = await makeBrowser({ online: true });
    b.w.animals[0].name = "<img src=x onerror=alert(1)>";
    b.server.reportStatus = 400; b.server.reportError = "<b>bad</b><script>alert(2)</script>";
    await b.enqueueReport(); await b.w.gvsync.flush();
    await until(() => b.text() === "Online · 1 failed");
    b.badge().click();
    const p = b.panel(); await until(() => p.textContent.includes("Failed"));
    ok(!p.querySelector("img") && !p.querySelector("script") && p.querySelectorAll("b").length === 1, "no injected elements (only the row's own <b> label)", p.innerHTML);
    ok(p.textContent.includes("<img src=x onerror=alert(1)>") && p.textContent.includes("<b>bad</b>"), "shown as plain text");
    b.close();
  }

  console.log("T11 Hindi and Marathi panel text");
  {
    for (const [lang, expect] of [["hi", "सिंक की स्थिति"], ["mr", "सिंक स्थिती"]]) {
      const b = await makeBrowser({ online: false });
      b.w.eval('currentLang = "' + lang + '"');
      b.badge().click();
      await until(() => b.panel() && b.panel().textContent.includes(expect));
      ok(b.panel().textContent.includes(expect), lang + " title", b.panel().textContent);
      b.close();
    }
  }

  console.log("T12 badge outside a dashboard page: no modal on page -> tapping does nothing, no crash");
  {
    const b = await makeBrowser({ online: true });
    b.w.modal = undefined;
    let threw = false;
    try { b.badge().click(); } catch (e) { threw = true; }
    ok(!threw, "no exception");
    b.close();
  }

  console.log("T13 Phase 5 (Option A): offline report with no saved weather uses typical values and says so");
  {
    const b = await makeBrowser({ online: false });
    b.w.eval(read("model-data.js")); b.w.eval(read("model.js")); b.w.eval(read("offline-report.js"));
    b.w._reportWeather = undefined;
    const done = await b.w.gvOfflineReport.submit(7, { animal_id: 7, symptoms: ["Fever", "Lameness"], notes: "" });
    ok(done === true, "report accepted offline");
    ok(b.w.modalBody.textContent.includes("No saved weather for this animal, so typical values were used (28°C, 65% humidity, no rain)"), "modal states the defaults were used", b.w.modalBody.textContent.slice(0, 300));
    ok(b.text() === "Offline · 1 queued", "and the report is queued", b.text());
    b.close();
  }

  console.log("T14 offline: recording with NO symptom ticked is not queued (offline model needs symptoms)");
  {
    const b = await makeBrowser({ online: false });
    b.w.eval(read("model-data.js")); b.w.eval(read("model.js")); b.w.eval(read("offline-report.js"));
    const done = await b.w.gvOfflineReport.submit(7, { animal_id: 7, symptoms: [], audio_base64: "data:audio/webm;base64,AAAA" });
    ok(done === false, "submit() declines a voice-only report");
    ok((await b.w.gvsync.list()).length === 0, "nothing was queued");
    b.close();
  }

  console.log("T15 offline: symptoms + recording -> queued with the audio, farmer told it goes to the vet");
  {
    const b = await makeBrowser({ online: false });
    b.w.eval(read("model-data.js")); b.w.eval(read("model.js")); b.w.eval(read("offline-report.js"));
    const done = await b.w.gvOfflineReport.submit(7, { animal_id: 7, symptoms: ["Fever", "Lameness"], audio_base64: "data:audio/webm;base64,AAAA" });
    ok(done === true, "accepted");
    const q = await b.w.gvsync.list();
    ok(q.length === 1 && q[0].body.audio_base64 === "data:audio/webm;base64,AAAA", "audio travels inside the queued report");
    ok(q[0].body.symptoms.join() === "Fever,Lameness", "with the ticked symptoms");
    ok(b.w.modalBody.textContent.includes("recording will be sent to the vet"), "modal says the recording goes to the vet");
    b.close();
  }

  console.log("T16 offline: symptoms without a recording -> no mention of a recording");
  {
    const b = await makeBrowser({ online: false });
    b.w.eval(read("model-data.js")); b.w.eval(read("model.js")); b.w.eval(read("offline-report.js"));
    await b.w.gvOfflineReport.submit(7, { animal_id: 7, symptoms: ["Fever"] });
    ok(!/recording/i.test(b.w.modalBody.textContent), "no recording note", b.w.modalBody.textContent.slice(0, 200));
    b.close();
  }

  console.log("T17 mic status text no longer promises server-side symptom extraction");
  {
    const b = await makeBrowser({ online: false });
    for (const l of ["en", "hi", "mr"]) {
      const m = b.w.eval("I18N." + l + ".voice_saved");
      ok(m && !/worked out|पहचाने जाएँगे|ओळखली जातील/.test(m), l + " voice_saved is accurate", m);
    }
    b.close();
  }

  console.log("T18 vet offline: clinical action is queued with the time it was recorded");
  {
    const b = await makeBrowser({ online: false });
    const t0 = Date.now();
    await b.w.addAction(12);
    const q = await b.w.gvsync.list();
    ok(q.length === 1 && q[0].url === "/api/vet/cases/12/action", "queued for the right case", JSON.stringify(q.map((x) => x.url)));
    ok(q[0].body.action_type === "CURE" && q[0].body.details === "Recovered well", "with what the vet typed");
    ok(Math.abs(new Date(q[0].body.action_at).getTime() - t0) < 5000, "and the time it was recorded", q[0].body.action_at);
    ok(b.w.__shown.join() === "12" && b.w.__alerts.length === 0, "case screen refreshed, no error alert", JSON.stringify(b.w.__alerts));
    ok(b.toastText() === "✓ Saved on this phone. It will be sent when you are online.", "vet is told it is saved on the phone", String(b.toastText()));
    ok(b.text() === "Offline · 1 queued", "pill shows it", b.text());
    b.close();
  }

  console.log("T19 vet online: unchanged behaviour (sent straight away, nothing queued, no action_at added)");
  {
    const b = await makeBrowser({ online: true });
    await b.w.addAction(12);
    ok(b.server.calls.length === 1 && JSON.stringify(b.server.calls[0].body) === JSON.stringify({ action_type: "CURE", details: "Recovered well" }), "exact original body", JSON.stringify(b.server.calls));
    ok((await b.w.gvsync.list()).length === 0 && b.toastText() === null, "nothing queued, no toast");
    ok(b.w.__shown.join() === "12", "case screen refreshed");
    b.close();
  }

  console.log("T20 vet online: server refuses -> shown to the vet, never queued");
  {
    const b = await makeBrowser({ online: true });
    b.server.vetStatus = 404; b.server.vetError = "Case not found or not assigned to you.";
    await b.w.addAction(12);
    ok(b.w.__alerts.join() === "Case not found or not assigned to you.", "server message shown", JSON.stringify(b.w.__alerts));
    ok((await b.w.gvsync.list()).length === 0 && b.w.__shown.length === 0, "not queued, screen not refreshed");
    b.close();
  }

  console.log("T21 vet offline: CURE then Close replay in order after reconnect");
  {
    const b = await makeBrowser({ online: false });
    await b.w.addAction(12);
    await b.w.closeCase(12);
    const q = await b.w.gvsync.list();
    ok(q.length === 2 && q[1].url === "/api/vet/cases/12/close" && q[1].body === null, "close queued without a body", JSON.stringify(q.map((x) => [x.url, x.body && "body"])));
    b.setOnline(true);
    ok(await until(() => b.text() === "Online"), "both sent", b.text());
    const seq = b.server.calls.map((c) => c.url.split("/").pop());
    ok(seq.join() === "action,close", "order kept: action first, then close", seq.join());
    ok(b.server.calls[1].body === undefined, "close sent with no body, as online");
    b.close();
  }

  console.log("T22 vet offline: model review (approve / reject) is queued");
  {
    const b = await makeBrowser({ online: false });
    await b.w.submitModelReview(12);
    const q = await b.w.gvsync.list();
    ok(q.length === 1 && q[0].url === "/api/vet/cases/12/model-review", "queued");
    ok(JSON.stringify(q[0].body) === JSON.stringify({ correct: true, predicted: "FMD", notes: "matches" }), "with the verdict", JSON.stringify(q[0].body));
    ok(b.w.__shown.join() === "12", "case screen refreshed");
    b.close();
  }

  console.log("T23 a page bug is not mistaken for 'offline' (nothing junk gets queued)");
  {
    const b = await makeBrowser({ online: false });
    b.w.actDetails = null;                    // form field missing -> TypeError while building the request
    await b.w.addAction(12);
    ok((await b.w.gvsync.list()).length === 0, "nothing queued");
    ok(b.w.__alerts.length === 1, "the vet sees the error", JSON.stringify(b.w.__alerts));
    b.close();
  }

  console.log("T24 sync panel names vet items (English + Hindi)");
  {
    const b = await makeBrowser({ online: false });
    await b.w.addAction(12); await b.w.closeCase(12); await b.w.submitModelReview(12);
    b.badge().click();
    const p = b.panel(); await until(() => p.textContent.includes("Waiting"));
    ok(p.textContent.includes("Clinical action (CURE) · Case #12"), "action row", p.textContent);
    ok(p.textContent.includes("Close case · Case #12") && p.textContent.includes("Model review · Case #12"), "close + review rows");
    b.w.eval('currentLang = "hi"'); b.w.closeModal(); b.badge().click();
    await until(() => b.panel() && b.panel().textContent.includes("केस"));
    ok(b.panel().textContent.includes("केस बंद करें · केस #12"), "Hindi label", b.panel().textContent.slice(0, 200));
    b.close();
  }

  console.log("T25 scope guard: only the three agreed vet writes can be queued");
  {
    const n = (HTML.match(/gvOfflineVet\.send\(/g) || []).length;
    ok(n === 3, "exactly 3 call sites in index.html", String(n));
    const out = HTML.slice(HTML.indexOf("async function submitOutbreak("), HTML.indexOf("async function notifications("));
    ok(!out.includes("gvOfflineVet") && out.includes("api(`/api/vet/cases/${id}/outbreak`"), "outbreak assessment stays online-only");
  }

  console.log("K1 every queued request gets its own random key");
  {
    const b = await makeBrowser({ online: false });
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { a: 1 } });
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { a: 2 } });
    const q = await b.w.gvsync.list();
    ok(q.every((x) => /^[0-9a-f]{32}$/.test(x.key)), "32 hex characters each", JSON.stringify(q.map((x) => x.key)));
    ok(q[0].key !== q[1].key, "different for different requests");
    ok(/^[A-Za-z0-9_-]{16,64}$/.test(q[0].key), "accepted by the server's key pattern");
    const app = fs.readFileSync(path.join(PWA, "..", "..", "app.py"), "utf8");
    ok(app.includes('"Idempotency-Key"') && read("outbox.js").includes('"Idempotency-Key"'), "server and client use the same header name");
    b.close();
  }

  console.log("K2 the key is sent on every replay, with Content-Type intact");
  {
    const b = await makeBrowser({ online: false });
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { a: 1 } });
    const key = (await b.w.gvsync.list())[0].key;
    b.setOnline(true);                                        // the page flushes by itself when the connection returns
    ok(await until(() => b.server.calls.length > 0), "the queued request was sent");
    const c = b.server.calls[0] || { headers: {} };
    ok(c.headers["Idempotency-Key"] === key, "header = the queued item's key", JSON.stringify(c.headers));
    ok(c.headers["Content-Type"] === "application/json", "Content-Type still sent");
    b.close();
  }

  console.log("K3 a retry after a server error (or Retry) uses the SAME key");
  {
    const b = await makeBrowser({ online: true });
    b.server.reportStatus = 503;                              // failing before anything is queued: no attempt can slip through early
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { a: 1 } });
    await flushNow(b);
    b.server.reportStatus = 200; await flushNow(b);
    const keys = b.server.calls.map((x) => x.headers["Idempotency-Key"]);
    ok(keys.length >= 2 && new Set(keys).size === 1 && /^[0-9a-f]{32}$/.test(keys[0]), "every attempt (" + keys.length + ") used one key", keys.join());
    b.close();
  }

  console.log("K4 an item queued by an older version (no key) gets one, and keeps it");
  {
    const b = await makeBrowser({ online: true });
    b.server.reportStatus = 503;
    await b.w.gvdb.outboxAdd({ uid: 1, method: "POST", url: "/api/cases/report", body: { a: 1 }, tempId: null, idPath: null, createdAt: new Date().toISOString(), attempts: 0, status: "pending", error: null });
    await flushNow(b);
    const stored = (await b.w.gvsync.list())[0];
    ok(stored && /^[0-9a-f]{32}$/.test(stored.key), "key was saved on the item", JSON.stringify(stored && stored.key));
    b.server.reportStatus = 200; await flushNow(b);
    const keys = b.server.calls.map((x) => x.headers["Idempotency-Key"]);
    ok(keys.length >= 2 && new Set(keys).size === 1, "same key on every retry (" + keys.length + " attempts)", keys.join());
    b.close();
  }

  console.log("K5 vet action, reply lost: first attempt and queued copy carry the SAME key");
  {
    const b = await makeBrowser({ online: true });
    b.server.dropReply = true;
    await b.w.addAction(12);                                  // the server receives it, the phone never hears back
    b.server.dropReply = false;
    const q = await b.w.gvsync.list();
    ok(q.length === 1 && b.server.calls.length === 1, "one request reached the server, one copy queued", b.server.calls.length + "/" + q.length);
    const k1 = b.server.calls[0].headers["Idempotency-Key"];
    ok(/^[0-9a-f]{32}$/.test(k1) && q[0].key === k1, "queued copy has the first attempt's key", k1 + " vs " + q[0].key);
    await flushNow(b);
    ok(b.server.calls.length === 2 && b.server.calls[1].headers["Idempotency-Key"] === k1, "the replay sends that same key", JSON.stringify(b.server.calls.map((c) => c.headers["Idempotency-Key"])));
    b.close();
  }

  console.log("K6 vet action online and working: key still sent, nothing else changed");
  {
    const b = await makeBrowser({ online: true });
    await b.w.addAction(12);
    const c = b.server.calls[0];
    ok(/^[0-9a-f]{32}$/.test(c.headers["Idempotency-Key"]) && c.headers["Content-Type"] === "application/json", "key + Content-Type on the first attempt");
    ok(JSON.stringify(c.body) === JSON.stringify({ action_type: "CURE", details: "Recovered well" }), "body unchanged");
    b.close();
  }

  console.log("K7 report: offline copy keeps the key of the first attempt (page wiring)");
  {
    const fn = HTML.slice(HTML.indexOf("async function submitReport"), HTML.indexOf("const TRIAGE_COLOR"));
    ok(/gvsync\.newKey\(\)/.test(fn) && /"Idempotency-Key":key/.test(fn), "submitReport makes a key and sends it on the first attempt");
    ok(/gvOfflineReport\.submit\(aid,body,key\)/.test(fn), "and hands the same key to the offline copy");
    const b = await makeBrowser({ online: false });
    b.w.eval(read("model-data.js")); b.w.eval(read("model.js")); b.w.eval(read("offline-report.js"));
    await b.w.gvOfflineReport.submit(7, { animal_id: 7, symptoms: ["Fever"] }, "abcdef0123456789abcdef0123456789");
    ok((await b.w.gvsync.list())[0].key === "abcdef0123456789abcdef0123456789", "queued report uses the key it was given");
    b.close();
  }

  // ---- 9c: a queued entry the server refuses ----
  async function waitFailed(b) { for (let i = 0; i < 150; i++) { const l = await b.w.gvsync.list(); if (l.length && l.every((x) => x.status === "failed")) return l; await sleep(20); } return null; }
  async function openPanel(b, needle) { b.badge().click(); const p = b.panel(); await until(() => p && p.textContent.includes(needle)); return p; }
  const MOVED = "Case not found or not assigned to you.";

  console.log("C1 case moved to another vet: clear explanation + the vet's own entry stays readable");
  {
    const b = await makeBrowser({ online: false });
    b.w.actDetails = { value: "Gave 10ml antibiotic, animal recovered\nsecond line" };
    await b.w.addAction(12);
    b.server.vetStatus = 404; b.server.vetError = MOVED; b.setOnline(true);
    const l = await waitFailed(b);
    ok(l && l[0].httpStatus === 404, "the HTTP status is saved on the failed item", JSON.stringify(l && l[0].httpStatus));
    const p = await openPanel(b, "Failed"), T = p.textContent;
    ok(T.includes("no longer assigned to you") && !T.includes("Case not found"), "plain explanation instead of the raw server text", T.slice(0, 300));
    ok(T.includes("Gave 10ml antibiotic, animal recovered") && T.includes("second line"), "the entry is shown, line breaks kept");
    ok(!!p.querySelector('[data-act="retry"]') && !!p.querySelector('[data-act="discard"]'), "Retry and Delete are still offered");
    b.close();
  }

  console.log("C2 same for model review and close");
  {
    const b = await makeBrowser({ online: false });
    await b.w.submitModelReview(12); await b.w.closeCase(12);
    b.server.vetStatus = 404; b.server.vetError = MOVED; b.setOnline(true);
    const l = await waitFailed(b);
    ok(l && l.length === 2 && l.every((x) => x.httpStatus === 404), "both failed with 404", JSON.stringify(l && l.map((x) => x.httpStatus)));
    const T = (await openPanel(b, "Failed")).textContent;
    ok((T.match(/no longer assigned to you/g) || []).length === 2, "explained on both rows");
    ok(T.includes("✓ FMD — matches"), "model review verdict + notes shown", T.slice(0, 400));
    b.close();
  }

  console.log("C3 other refusals keep the server's own message (only a moved case gets the explanation)");
  {
    const b = await makeBrowser({ online: false });
    await b.w.addAction(12);
    b.server.vetStatus = 400; b.server.vetError = "Please enter action details."; b.setOnline(true);
    await waitFailed(b);
    const T = (await openPanel(b, "Failed")).textContent;
    ok(T.includes("Please enter action details.") && !T.includes("no longer assigned"), "400 shows the server message", T.slice(0, 250));
    ok(T.includes("Recovered well"), "entry still shown");
    b.close();
  }

  console.log("C4 a refused report shows its symptoms and notes");
  {
    const b = await makeBrowser({ online: false });
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { animal_id: 7, symptoms: ["Fever"], notes: "limping since morning" } });
    b.server.reportStatus = 400; b.server.reportError = "Select valid symptoms."; b.setOnline(true);
    await waitFailed(b);
    const T = (await openPanel(b, "Failed")).textContent;
    ok(T.includes("Select valid symptoms.") && T.includes("limping since morning") && T.includes(b.w.symptomLabel ? b.w.symptomLabel("Fever") : "Fever"), "server message + notes + symptom", T.slice(0, 300));
    b.close();
  }

  console.log("C5 server errors that ran out of retries keep their status and message");
  {
    const b = await makeBrowser({ online: true });
    b.server.reportStatus = 503;
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { animal_id: 7, symptoms: ["Fever"] } });
    for (let i = 0; i < 8; i++) await flushNow(b);
    const l = await b.w.gvsync.list();
    ok(l.length === 1 && l[0].status === "failed" && l[0].httpStatus === 503 && l[0].error === "Server error 503", "failed with 503 after the retry limit", JSON.stringify(l.map((x) => [x.status, x.httpStatus, x.error])));
    b.close();
  }

  console.log("C6 Retry clears the saved status and error");
  {
    const b = await makeBrowser({ online: false });
    await b.w.addAction(12);
    b.server.vetStatus = 404; b.server.vetError = MOVED; b.setOnline(true);
    const l = await waitFailed(b);
    b.server.vetStatus = 200; await b.w.gvsync.retry(l[0].id);
    const after = (await b.w.gvsync.list())[0];
    ok(after && after.status === "pending" && after.httpStatus === null && after.error === null, "back to pending, status cleared", JSON.stringify(after && [after.status, after.httpStatus, after.error]));
    b.close();
  }

  console.log("C7 typed text is escaped (nothing typed can run as HTML)");
  {
    const b = await makeBrowser({ online: false });
    b.w.actDetails = { value: '<img src=x onerror="window.__pwned=1">' };
    await b.w.addAction(12);
    b.server.vetStatus = 404; b.server.vetError = MOVED; b.setOnline(true);
    await waitFailed(b);
    const p = await openPanel(b, "Failed");
    ok(!p.querySelector("img") && p.textContent.includes("<img src=x"), "shown as text, no element created");
    ok(b.w.__pwned === undefined, "nothing executed");
    b.close();
  }

  console.log("C8 Hindi and Marathi");
  {
    for (const [lang, moved, entry] of [["hi", "अब आपको सौंपा हुआ नहीं है", "आपकी प्रविष्टि"], ["mr", "आता तुमच्याकडे नेमलेला नाही", "तुमची नोंद"]]) {
      const b = await makeBrowser({ online: false });
      await b.w.addAction(12);
      b.server.vetStatus = 404; b.server.vetError = MOVED; b.setOnline(true);
      await waitFailed(b);
      b.w.eval('currentLang = "' + lang + '"');
      const T = (await openPanel(b, entry)).textContent;
      ok(T.includes(moved) && T.includes(entry), lang + ": explanation and label translated", T.slice(0, 200));
      b.close();
    }
  }

  // ---- 9d: mobile data, storage ----
  console.log("D1 offline download: cases list once (not twice), at most once per 10 minutes, but always after a sync");
  {
    const b = await makeBrowser({ online: true });
    await sleep(1700);                                        // let the page's own load-time warm-up finish
    const count = (u) => b.server.gets.filter((x) => x === u).length;
    const setWarm = (uid, agoMs) => b.w.localStorage.setItem("gv_warm", JSON.stringify({ uid, t: Date.now() - agoMs }));
    const online = () => b.w.dispatchEvent(new b.w.Event("online"));

    setWarm(1, 11 * 60 * 1000); b.server.gets.length = 0; online();
    await until(() => count("/api/animals") >= 1); await sleep(200);
    ok(count("/api/cases") === 1, "/api/cases downloaded once per warm-up (was twice)", "count=" + count("/api/cases"));
    ok(count("/api/animals") === 1 && count("/api/notifications") === 1, "the other lists once each");

    b.server.gets.length = 0; online(); await sleep(250);
    ok(!b.server.gets.includes("/api/animals") && !b.server.gets.includes("/api/cases"), "an 'online' event right after is skipped (no re-download)", b.server.gets.join());

    setWarm(99, 60 * 1000); b.server.gets.length = 0; online();
    ok(await until(() => count("/api/animals") >= 1), "a different signed-in person is warmed straight away");

    setWarm(1, 60 * 1000); b.server.gets.length = 0;
    b.setOnline(false);
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { animal_id: 7, symptoms: ["Fever"] } });
    b.server.gets.length = 0; setWarm(1, 60 * 1000);
    b.setOnline(true);
    ok(await until(() => count("/api/animals") >= 1), "after a sync delivered something, data is refreshed even though a warm-up was recent", b.server.gets.join());
    b.close();
  }

  console.log("D2 asks the browser to keep the queue safe when space runs low (once, and never blocks saving)");
  {
    const mk = async (storage) => { const b = await makeBrowser({ online: false }); Object.defineProperty(b.w.navigator, "storage", { value: storage, configurable: true }); return b; };
    let asked = 0;
    let b = await mk({ persisted: async () => false, persist: async () => { asked++; return true; } });
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { a: 1 } }); await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { a: 2 } }); await sleep(30);
    ok(asked === 1, "persistent storage requested exactly once for two saves", asked);
    b.close();
    asked = 0; b = await mk({ persisted: async () => true, persist: async () => { asked++; return true; } });
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { a: 1 } }); await sleep(30);
    ok(asked === 0, "not asked again when the browser already granted it", asked); b.close();
    b = await mk({ persisted: async () => false, persist: () => Promise.reject(new Error("denied")) });
    await b.w.gvsync.enqueue({ url: "/api/cases/report", body: { a: 1 } }); await sleep(30);
    ok((await b.w.gvsync.list()).length === 1, "a refusal does not stop the report being saved"); b.close();
  }

  console.log("D3 phone storage full: the person is told, nothing half-saved");
  {
    const FULL = "Phone storage is full, so this could not be saved. Free some space and try again.";
    const b = await makeBrowser({ online: false });
    b.w.gvdb.outboxAdd = () => Promise.reject(new b.w.DOMException("full", "QuotaExceededError"));
    await b.w.addAction(12);
    ok(b.w.__alerts.join() === FULL, "vet action: the storage message, not 'Failed to fetch'", JSON.stringify(b.w.__alerts));
    ok(b.w.__shown.length === 0, "screen not refreshed as if it had saved");
    b.w.eval(read("model-data.js")); b.w.eval(read("model.js")); b.w.eval(read("offline-report.js"));
    const done = await b.w.gvOfflineReport.submit(7, { animal_id: 7, symptoms: ["Fever"] }, "abcdef0123456789abcdef0123456789");
    ok(done === false && b.w.gvOfflineReport.lastFailure() === FULL, "report: falls back to the error path with the storage message");
    ok(b.w.gvOfflineReport.lastFailure() === null, "the message is given once, not repeated on the next error");
    const fn = HTML.slice(HTML.indexOf("async function submitReport"), HTML.indexOf("const TRIAGE_COLOR"));
    ok(/alert\(\(window\.gvOfflineReport&&gvOfflineReport\.lastFailure/.test(fn), "submitReport shows it (page wiring)");
    b.w.gvdb.outboxAdd = () => Promise.reject(new Error("boom")); b.w.__alerts.length = 0;
    await b.w.addAction(12);
    ok(b.w.__alerts.join() === "Failed to fetch", "any other storage error keeps the original message", JSON.stringify(b.w.__alerts));
    b.w.eval('currentLang = "hi"'); b.w.gvdb.outboxAdd = () => Promise.reject(new b.w.DOMException("full", "QuotaExceededError")); b.w.__alerts.length = 0;
    await b.w.addAction(12);
    ok(b.w.__alerts.join().includes("मेमोरी भरी"), "Hindi", JSON.stringify(b.w.__alerts));
    b.close();
  }

  console.log("\n" + passed + " passed, " + failed + " failed");
  process.exit(failed ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(2); });
