// Offline write queue. Loaded by the page and by the service worker (needs db.js first).
//
// A queued item is a request to replay against the existing Flask API:
//   { method, url, body, tempId?, idPath? }
// If an item creates something other items depend on, give it a tempId (gvsync.tempId())
// and the response path of the new id (e.g. "animal_id"). Later items may use that tempId
// in their url or as an exact body value; it is swapped for the real id when replayed.
(function (g) {
  var RETRY_LIMIT = 5;
  var TEMP_RE = /gvtmp:[A-Za-z0-9-]+/g;
  var channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("gramvet") : null;
  var listeners = [], flushing = false;

  function notify(c) { listeners.forEach(function (fn) { try { fn(c); } catch (e) {} }); }
  if (channel) channel.onmessage = function (e) { notify(e.data); };

  function counts() {
    return gvdb.outboxAll().then(function (items) {
      var c = { pending: 0, failed: 0 };
      items.forEach(function (i) { if (i.status === "failed") c.failed++; else c.pending++; });
      return c;
    });
  }
  function announce() {
    return counts().then(function (c) {
      notify(c);
      if (channel) channel.postMessage(c);
      return c;
    });
  }

  // Failed items stay in the outbox until the farmer retries or deletes them.
  function findFailed(id) {
    return gvdb.outboxAll().then(function (items) {
      return items.find(function (i) { return i.id === id && i.status === "failed"; }) || null;
    });
  }
  function retry(id) {
    return findFailed(id).then(function (it) {
      if (!it) return null;
      it.status = "pending"; it.attempts = 0; it.error = null; it.httpStatus = null;
      return gvdb.outboxPut(it);
    }).then(announce);
  }
  function discard(id) {
    return findFailed(id).then(function (it) { return it ? gvdb.outboxDelete(id) : null; }).then(announce);
  }

  // One random key per request. The server answers a repeat of the same key with the first result,
  // so a retry after a lost reply is never applied twice. 32 hex characters.
  function newKey() {
    var b = new Uint8Array(16);
    if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(b);
    else for (var i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
    return Array.prototype.map.call(b, function (x) { return (x < 16 ? "0" : "") + x.toString(16); }).join("");
  }

  function tempId() {
    var r = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
    return "gvtmp:" + r;
  }

  function currentUid() {
    return fetch("/api/me").then(function (r) { return r.json(); }).then(function (j) {
      if (!j.authenticated) throw new Error("Not signed in");
      return j.user.id;
    });
  }

  // Queued reports live only on this phone. Ask the browser not to clear them when space runs low (once).
  var persistAsked = false;
  function askPersist() {
    if (persistAsked || typeof navigator === "undefined" || !navigator.storage || !navigator.storage.persist) return;
    persistAsked = true;
    try {
      var p = navigator.storage.persisted ? navigator.storage.persisted() : Promise.resolve(false);
      Promise.resolve(p).then(function (yes) { return yes || navigator.storage.persist(); }).catch(function () {});
    } catch (e) {}
  }
  function isQuota(e) { return !!e && (e.name === "QuotaExceededError" || e.code === 22); }
  function fullError() {
    var e = new Error(typeof g.t === "function" ? g.t("st_full", "Phone storage is full, so this could not be saved. Free some space and try again.")
                                                : "Phone storage is full, so this could not be saved. Free some space and try again.");
    e.name = "QuotaExceededError";
    return e;
  }

  function enqueue(item) {
    askPersist();
    return currentUid().then(function (uid) {
      return gvdb.outboxAdd({
        uid: uid, method: item.method || "POST", url: item.url,
        body: item.body === undefined ? null : item.body,
        tempId: item.tempId || null, idPath: item.idPath || null,
        key: item.key || newKey(),
        createdAt: new Date().toISOString(), attempts: 0, status: "pending", error: null
      });
    }).then(function (id) {
      // Ask the browser to sync in the background, even if the app gets closed.
      if (typeof navigator !== "undefined" && navigator.serviceWorker) {
        navigator.serviceWorker.ready
          .then(function (reg) { return reg.sync && reg.sync.register("gv-outbox"); })
          .catch(function () {});
      }
      return announce().then(function () { return id; });
    }).catch(function (e) { if (isQuota(e)) throw fullError(); throw e; });
  }

  function subst(v, map) {
    if (typeof v === "string") return Object.prototype.hasOwnProperty.call(map, v) ? map[v] : v;
    if (Array.isArray(v)) return v.map(function (x) { return subst(x, map); });
    if (v && typeof v === "object") {
      var o = {};
      Object.keys(v).forEach(function (k) { o[k] = subst(v[k], map); });
      return o;
    }
    return v;
  }
  function substUrl(u, map) {
    return u.replace(TEMP_RE, function (m) { return Object.prototype.hasOwnProperty.call(map, m) ? map[m] : m; });
  }
  function hasTemp(x) { return /gvtmp:/.test(JSON.stringify(x)); }
  function pick(obj, path) {
    return String(path || "").split(".").reduce(function (o, k) { return o == null ? o : o[k]; }, obj);
  }
  function fail(item, msg, httpStatus) { item.status = "failed"; item.error = msg; item.httpStatus = httpStatus || null; return gvdb.outboxPut(item); }
  function errText(res) {
    return res.json().then(function (j) { return j.error || ("HTTP " + res.status); }, function () { return "HTTP " + res.status; });
  }

  async function process(uid, summary) {
    var idmap = (await gvdb.metaGet("idmap")) || {};
    var items = (await gvdb.outboxAll())
      .filter(function (i) { return i.status === "pending" && i.uid === uid; })
      .sort(function (a, b) { return a.id - b.id; });

    for (var n = 0; n < items.length; n++) {
      var it = items[n];
      var url = substUrl(it.url, idmap), body = subst(it.body, idmap);
      if (hasTemp(url) || hasTemp(body)) {
        await fail(it, "Depends on an earlier item that did not sync");
        summary.failed++; continue;
      }
      if (!it.key) { it.key = newKey(); await gvdb.outboxPut(it); }   // queued by an older version: give it one now, kept for retries
      var res;
      try {
        res = await fetch(url, {
          method: it.method,
          headers: { "Content-Type": "application/json", "Idempotency-Key": it.key },
          body: body === null ? undefined : JSON.stringify(body)
        });
      } catch (e) { summary.stopped = "offline"; break; }

      if (res.ok) {
        if (it.tempId) {
          var j = await res.json().catch(function () { return null; });
          var real = j ? pick(j, it.idPath) : null;
          if (real != null) { idmap[it.tempId] = real; await gvdb.metaSet("idmap", idmap); }
        }
        await gvdb.outboxDelete(it.id);
        summary.synced++;
      } else if (res.status === 401) {
        summary.stopped = "signed-out"; break;
      } else if (res.status >= 500) {
        it.attempts = (it.attempts || 0) + 1;
        if (it.attempts >= RETRY_LIMIT) { await fail(it, "Server error " + res.status, res.status); summary.failed++; }
        else { await gvdb.outboxPut(it); summary.stopped = "server"; break; }
      } else {
        await fail(it, await errText(res), res.status); summary.failed++;
      }
    }
    if (!(await gvdb.outboxAll()).length) await gvdb.metaSet("idmap", {});
  }

  function run() {
    var summary = { synced: 0, failed: 0, stopped: null };
    return fetch("/api/me").then(function (r) { return r.json(); }).then(function (me) {
      if (!me.authenticated) { summary.stopped = "signed-out"; return; }
      return process(me.user.id, summary);
    }).catch(function () { summary.stopped = summary.stopped || "offline"; })
      .then(function () { return announce(); })
      .then(function (c) { summary.pending = c.pending; summary.failedTotal = c.failed; return summary; });
  }

  // One replay at a time across page and service worker.
  function flush() {
    var locks = typeof navigator !== "undefined" && navigator.locks;
    if (locks) {
      return locks.request("gv-outbox-flush", { ifAvailable: true }, function (lock) {
        return lock ? run() : { skipped: true };
      });
    }
    if (flushing) return Promise.resolve({ skipped: true });
    flushing = true;
    return run().then(function (s) { flushing = false; return s; }, function (e) { flushing = false; throw e; });
  }

  g.gvsync = {
    enqueue: enqueue, flush: flush, counts: counts, tempId: tempId, newKey: newKey, retry: retry, discard: discard,
    list: function () { return gvdb.outboxAll(); },
    onChange: function (fn) { listeners.push(fn); }
  };
})(self);
