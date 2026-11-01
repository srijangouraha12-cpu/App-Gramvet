// Vet actions on a case that can wait for a connection: clinical action, model review, close case.
// Page only. Same idea as offline-report.js: when the request fails because there is no connection,
// keep it in the outbox (sent automatically, in order, when the connection returns) and say so.
//
// Not queued on purpose: outbreak confirmation (it alerts a whole village, so the vet must know it
// was really sent), and anything that moves vaccine stock, assignments or government decisions.
(function (g) {
  function isNetworkError(e) { return e instanceof TypeError; }
  function say(key, fallback) { if (typeof g.gvToast === "function") g.gvToast(g.t(key, fallback)); }

  // POSTs `body` to `url` like api(). Returns the server's answer, or { queued: true } when the
  // request could not leave the phone and was kept for later. Server errors still throw.
  async function send(url, body) {
    // The same key goes with the first attempt and with the queued copy. If the first attempt reached the
    // server but its reply was lost, the queued copy is recognised and not applied a second time.
    var key = gvsync.newKey();
    var opts = { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    try {
      return await api(url, opts);
    } catch (e) {
      if (!isNetworkError(e)) throw e;
      var queued = body === undefined ? null : Object.assign({}, body);
      // A clinical action keeps the time the vet recorded it, not the time it syncs.
      if (queued && /\/action$/.test(url)) queued.action_at = new Date().toISOString();
      try { await gvsync.enqueue({ url: url, body: queued, key: key }); } catch (e2) { throw e2 && e2.name === "QuotaExceededError" ? e2 : e; }   // a full phone is worth saying; anything else stays the network error
      say("vq_saved", "Saved on this phone. It will be sent when you are online.");
      return { queued: true };
    }
  }

  g.gvOfflineVet = { send: send };
})(self);
