(function () {
  // 1. Service worker
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function (e) {
        console.warn("[GramVet PWA] service worker not registered:", e);
      });
    });
  }

  // 2. Local database
  if (window.gvdb) {
    gvdb.open()
      .then(function () { return gvdb.metaGet("installedAt"); })
      .then(function (v) { if (!v) return gvdb.metaSet("installedAt", new Date().toISOString()); })
      .catch(function (e) { console.warn("[GramVet PWA] IndexedDB unavailable:", e); });
  }

  // 3. Status badge (top-right, next to the theme toggle): Online/Offline + queued writes
  var badge = document.createElement("button");
  badge.id = "gvNet"; badge.type = "button";
  badge.style.cssText = "border:0;cursor:pointer;font-family:inherit";
  badge.onclick = function () { openPanel(); };
  var counts = { pending: 0, failed: 0 }, syncing = false;
  function render() {
    var on = navigator.onLine, txt = on ? "Online" : "Offline";
    if (syncing && counts.pending) txt = "Syncing… · " + counts.pending + " left";
    else if (counts.pending) txt += " · " + counts.pending + " queued";
    if (counts.failed) txt += " · " + counts.failed + " failed";
    badge.textContent = txt;
    badge.className = "pill" + (!on || counts.pending || counts.failed ? " warn" : "");
  }
  function mount() {
    var host = document.querySelector(".topright");
    if (host) host.insertBefore(badge, host.firstChild);
  }
  render();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();

  // 3b. Sync panel (tap the badge) and the "all sent" toast.
  function tr(key, fallback) { return typeof window.t === "function" ? window.t(key, fallback) : fallback; }
  function safe(v) { return typeof window.esc === "function" ? window.esc(v) : String(v == null ? "" : v).replace(/[&<>"']/g, function (c) { return "&#" + c.charCodeAt(0) + ";"; }); }

  var toastEl = null, toastTimer = null;
  function toast(msg) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.setAttribute("role", "status");
      toastEl.style.cssText = "position:fixed;left:50%;bottom:calc(24px + var(--gv-nav,0px));transform:translateX(-50%);z-index:1000;background:var(--green);" +
        "color:#fff;padding:10px 16px;border-radius:12px;font-size:13px;font-weight:750;box-shadow:0 6px 20px #0003;max-width:90vw";
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = "✓ " + msg; toastEl.style.display = "block";
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { toastEl.style.display = "none"; }, 4000);
  }

  window.gvToast = toast;

  function itemLabel(it) {
    var b = it.body || {}, list = typeof animals !== "undefined" && Array.isArray(animals) ? animals : [];
    var a = list.find(function (x) { return x.id === b.animal_id; });
    if (it.method === "POST" && it.url === "/api/cases/report") {
      return ((b.symptoms && b.symptoms.length) ? tr("sp_report", "Disease report") : tr("sp_voice", "Voice report")) + (a ? " · " + a.name : "");
    }
    var v = /^\/api\/vet\/cases\/(\d+)\/(action|model-review|close)$/.exec(it.url);
    if (it.method === "POST" && v) {
      var kind = v[2] === "action" ? tr("sp_action", "Clinical action") + (b.action_type ? " (" + b.action_type + ")" : "")
        : v[2] === "close" ? tr("sp_close", "Close case") : tr("sp_review", "Model review");
      return kind + " · " + tr("sp_case", "Case") + " #" + v[1];
    }
    return it.method + " " + it.url;
  }

  // What the person typed, so an entry the server refused can still be read and passed on.
  function entryText(it) {
    var b = it.body || {};
    if (it.url === "/api/cases/report") {
      var sym = (b.symptoms || []).map(function (x) { return typeof window.symptomLabel === "function" ? window.symptomLabel(x) : x; }).join(", ");
      return [sym, b.notes].filter(Boolean).join("\n");
    }
    if (/\/action$/.test(it.url)) return b.details || "";
    if (/\/model-review$/.test(it.url)) return ((b.correct ? "✓ " : "✗ ") + [b.predicted, b.notes].filter(Boolean).join(" — ")).trim();
    return "";
  }
  // When a case moves to another vet, every write to it answers 404. Say what that means.
  function failReason(it) {
    if (it.httpStatus === 404 && /^\/api\/vet\/cases\/\d+\//.test(it.url))
      return tr("cf_moved", "This case is no longer assigned to you (it may have been moved to a closer vet), so this was not saved to the case. Your entry is shown below in case you need to pass it on.");
    return it.error || "";
  }

  function panelHost() {
    var h = document.getElementById("gvSyncPanel");
    return h && window.modal && !modal.classList.contains("hidden") ? h : null;
  }

  function drawPanel() {
    if (!panelHost() || !window.gvsync) return;
    gvsync.list().then(function (items) {
      var host = panelHost(); if (!host) return;
      var on = navigator.onLine;
      items.sort(function (a, b) { return a.id - b.id; });
      var rows = items.map(function (it) {
        var failed = it.status === "failed", state;
        if (failed) state = '<span class="pill bad">' + safe(tr("sp_failed", "Failed")) + '</span>';
        else if (syncing) state = '<span class="pill info">' + safe(tr("sp_sending", "Sending…")) + '</span>';
        else state = '<span class="pill warn">' + safe(on ? tr("sp_wait", "Waiting to send") : tr("sp_wait_net", "Waiting for connection")) + '</span>';
        var when = it.createdAt && !isNaN(new Date(it.createdAt)) ? new Date(it.createdAt).toLocaleString() : "";
        return '<div class="card" style="margin-top:10px"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center">' +
          '<b>' + safe(itemLabel(it)) + '</b>' + state + '</div>' +
          (when ? '<div class="muted2">' + safe(when) + '</div>' : '') +
          (failed && failReason(it) ? '<div class="muted2" style="margin-top:6px">' + safe(failReason(it)) + '</div>' : '') +
          (failed && entryText(it) ? '<div class="muted2" style="margin-top:6px">' + safe(tr("cf_entry", "Your entry")) + '</div>' +
            '<div style="margin-top:2px;padding:8px;border:1px solid var(--line);border-radius:8px;white-space:pre-wrap;user-select:text">' + safe(entryText(it)) + '</div>' : '') +
          (failed ? '<div style="margin-top:8px;display:flex;gap:8px">' +
            '<button class="btn secondary" data-act="retry" data-id="' + it.id + '">' + safe(tr("sp_retry", "Retry")) + '</button>' +
            '<button class="btn secondary" data-act="discard" data-id="' + it.id + '">' + safe(tr("sp_discard", "Delete")) + '</button></div>' : '') +
          '</div>';
      }).join("");
      var waiting = items.some(function (i) { return i.status !== "failed"; });
      host.innerHTML = '<h2>' + safe(tr("sp_title", "Sync status")) + '</h2>' +
        (rows || '<div class="muted" style="margin-top:10px">' + safe(tr("sp_empty", "Nothing is waiting to be sent.")) + '</div>') +
        '<div style="margin-top:14px;display:flex;gap:8px">' +
          '<button class="btn" data-act="sync"' + (on && waiting && !syncing ? "" : " disabled") + '>' + safe(tr("sp_now", "Sync now")) + '</button>' +
          '<button class="btn secondary" data-act="close">' + safe(tr("rp_done", "Done")) + '</button></div>' +
        '<div style="margin-top:12px;font-size:12px"><a href="/static/pwa/diagnostics.html" style="color:var(--muted,#5b6f62)">' + safe(tr("sp_diag", "Phone check (for testing)")) + '</a></div>';
    }).catch(function () {});
  }

  function openPanel() {
    if (!window.modal || !window.modalBody || !window.gvsync) return;
    modalBody.innerHTML = '<div id="gvSyncPanel"></div>';
    modal.classList.remove("hidden");
    document.getElementById("gvSyncPanel").onclick = function (e) {
      var b = e.target.closest ? e.target.closest("button[data-act]") : null;
      if (!b || b.disabled) return;
      var act = b.getAttribute("data-act"), id = Number(b.getAttribute("data-id"));
      if (act === "sync") sync();
      else if (act === "close") closeModal();
      else if (act === "retry") gvsync.retry(id).then(sync);
      else if (act === "discard" && confirm(tr("sp_confirm", "Delete this report from the phone? It has not reached the server and cannot be recovered."))) gvsync.discard(id);
    };
    drawPanel();
  }

  // 4. Offline writes: replay when the connection returns, and keep the last-known
  //    data of the signed-in user's dashboards ready for offline use.
  var WARM = {
    farmer: ["/api/animals", "/api/cases", "/api/notifications"],
    vet: ["/api/vet/overview", "/api/vet/cases", "/api/cases", "/api/animals", "/api/notifications",
          "/api/outbreak/summary", "/api/vet/outbreak-map", "/api/vet/vaccine-inventory", "/api/vet/resource-status"],
    government: ["/api/govt/overview", "/api/govt/farmers", "/api/govt/vaccine-inventory", "/api/govt/vaccine-shipments",
                 "/api/notifications", "/api/outbreak/summary", "/api/cases", "/api/animals"]
  };
  var MAX_CASE_DETAILS = 20;

  // One weather reading is enough: all of a farmer's animals share the registered location.
  // The service worker keeps it for animals whose report form was never opened online.
  function warmWeather() {
    if (!window.caches) return;
    caches.open("gv-api").then(function (c) { return c.match("/api/animals"); }).then(function (r) {
      return r ? r.json() : null;
    }).then(function (j) {
      var a = j && j.animals && j.animals[0];
      if (a) return fetch("/api/weather?animal_id=" + a.id);
    }).catch(function () {});
  }

  // Downloading everything for offline use costs mobile data. Do it at most once per WARM_GAP_MS for the same
  // person (a connection that keeps dropping fires "online" again and again), unless a sync just changed the data.
  var WARM_GAP_MS = 10 * 60 * 1000;
  function lastWarm() { try { return JSON.parse(localStorage.getItem("gv_warm") || "null"); } catch (e) { return null; } }
  function setLastWarm(v) { try { if (v) localStorage.setItem("gv_warm", JSON.stringify(v)); else localStorage.removeItem("gv_warm"); } catch (e) {} }

  function warm(force) {
    if (!navigator.onLine) return;
    fetch("/api/me").then(function (r) { return r.json(); }).then(function (m) {
      if (!m.authenticated) return;
      var last = lastWarm();
      if (force !== true && last && last.uid === m.user.id && Date.now() - last.t < WARM_GAP_MS) return;
      setLastWarm({ uid: m.user.id, t: Date.now() });
      var list = (WARM[m.user.role] || []).filter(function (u) { return u !== "/api/cases"; });   // the cases list is fetched once, below
      if (window.gvtiles && (m.user.role === "vet" || m.user.role === "government")) gvtiles.warm(m.user).catch(function () {});
      return Promise.all(list.map(function (u) { return fetch(u).catch(function () {}); })).then(function () {
        if (m.user.role === "farmer") warmWeather();
        return fetch("/api/cases").then(function (r) { return r.ok ? r.json() : { cases: [] }; });
      }).then(function (j) {
        (j.cases || []).slice(0, MAX_CASE_DETAILS).forEach(function (c) { fetch("/api/cases/" + c.id).catch(function () {}); });
        if (!navigator.onLine) setLastWarm(null);            // the connection dropped part-way: try again next time
      });
    }).catch(function () { setLastWarm(null); });
  }

  function sync() {
    if (!navigator.onLine || !window.gvsync || syncing) return;
    syncing = true; render(); drawPanel();
    gvsync.flush().then(function (r) { if (r && r.synced) warm(true); }).catch(function () {})
      .then(function () { syncing = false; render(); drawPanel(); });
  }

  if (window.gvsync) {
    gvsync.onChange(function (c) {
      var was = counts; counts = c; render(); drawPanel();
      // Everything that was waiting got through (also fires when the service worker synced in the background).
      if (was.pending > 0 && c.pending === 0 && c.failed <= was.failed) toast(tr("sp_all_sent", "All queued reports were sent."));
    });
    gvsync.counts().then(function (c) { counts = c; render(); }).catch(function () {});
    setInterval(function () { if (counts.pending) sync(); }, 30000);
  }
  window.addEventListener("online", function () { render(); drawPanel(); sync(); warm(); });
  window.addEventListener("offline", function () { render(); drawPanel(); });
  window.addEventListener("load", function () { sync(); setTimeout(warm, 1500); });
})();
