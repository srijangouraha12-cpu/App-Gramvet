// Farmer report submitted without a connection: screen it with the offline model, keep it
// in the outbox (sent automatically later) and show the farmer a result right away.
// Page only. Uses the page's own helpers: animals, modalBody, t, esc, bar, closeModal ...
(function (g) {
  var failure = null;   // why the last submit() could not save, when the person should be told
  function isNetworkError(e) { return e instanceof TypeError; }

  // The last saved weather reading, with today's season. Temperature, humidity and rain
  // hardly change the answer, but a season label from an old reading can (the server
  // always uses the current season).
  function currentWeather() {
    var w = Object.assign({}, window._reportWeather || {});
    w.season = gvml.season(new Date());
    return w;
  }

  // body = the exact request body the online path would have sent to /api/cases/report.
  // Returns true when the report was saved and the result shown, false to fall back to the normal error.
  async function submit(aid, body, key) {
    failure = null;
    var animal = animals.find(function (x) { return x.id === aid; });
    // The offline model runs on the ticked symptoms. A recording is only context for the vet.
    if (!(body.symptoms && body.symptoms.length)) return false;
    var result;
    try {
      result = gvml.run({
        animal: animal, symptoms: body.symptoms, weather: currentWeather(),
        reportInputs: body, herdCount: animals.length, now: new Date()
      });
    } catch (e) { return false; }
    try {
      // submitted_at keeps the time the farmer filled the form in, not the time it syncs.
      await gvsync.enqueue({ url: "/api/cases/report", key: key, body: Object.assign({}, body, { submitted_at: new Date().toISOString() }) });
    } catch (e) { failure = e && e.name === "QuotaExceededError" ? e.message : null; return false; }
    render(animal, body, result);
    return true;
  }

  function weatherLine(w, defaulted) {
    if (defaulted) return esc(t("of_wx_default", "No saved weather for this animal, so typical values were used (28°C, 65% humidity, no rain)."));
    var when = w.fetched_at && !isNaN(new Date(w.fetched_at)) ? new Date(w.fetched_at).toLocaleString() : "";
    return esc(t("of_wx_used", "Weather used") + ": " + (w.temperature_c ?? "—") + "°C · " + (w.humidity_pct ?? "—") + "% · " + (w.rainfall_mm ?? "—") + " mm") +
      (when ? " · " + esc(t("of_wx_saved", "last updated") + " " + when) : "");
  }

  function render(animal, body, r) {
    window._reportBusy = false;
    var top = r.predictions.slice(0, 3).map(function (p) { return bar(diseaseLabel(p.disease), p.probability, "#2e9d5b"); }).join("");
    modalBody.innerHTML =
      '<h2>' + esc(t("of_title", "Report saved on this phone")) + (animal ? " · " + esc(animal.name) : "") + '</h2>' +
      '<div class="notice amber"><b>' + esc(t("of_offline", "You are offline.")) + '</b> ' +
        esc(t("of_will_send", "Your report will be sent automatically when the connection returns, and the vet will be alerted then.")) + '</div>' +
      '<div class="card" style="margin-top:14px"><div class="eyebrow">' + esc(t("of_prelim", "Preliminary AI screening (works offline)")) + '</div>' +
        '<div class="big" style="font-size:22px">' + esc(diseaseLabel(r.predicted_disease)) + '</div>' +
        '<div class="muted">' + r.confidence.toFixed(1) + '% ' + esc(t("as_conf", "confidence")) + '</div>' + top + '</div>' +
      '<div class="muted2" style="margin-top:10px">' + weatherLine(window._reportWeather || {}, r.weather_defaulted) + '</div>' +
      (body.audio_base64 ? '<div class="muted2" style="margin-top:10px">' + esc(t("of_voice_note_alt", "Your recording will be sent to the vet for context when the connection returns.")) + '</div>' : '') +
      '<div class="card" style="margin-top:14px"><h3>' + esc(t("rp_symptoms", "Symptoms")) + '</h3><p>' +
        esc((body.symptoms || []).map(symptomLabel).join(" · ")) + '</p></div>' +
      '<div class="notice blue" style="margin-top:14px">' + esc(t("of_note", "Urgency, outbreak check and vet alert are worked out when the report reaches the server. AI-assisted screening only: a veterinarian must review the animal.")) + '</div>' +
      '<div style="margin-top:14px"><button class="btn" onclick="closeModal()">' + esc(t("rp_done", "Done")) + '</button></div>';
  }

  // The report form's weather card is titled "Live weather". When the reading is a saved
  // one (offline, or older than STALE_MS) say so and show when it was saved.
  var STALE_MS = 30 * 60 * 1000;
  function wrapWeatherCard() {
    var original = g.loadReportWeather;
    if (typeof original !== "function") return;
    g.loadReportWeather = async function (aid) {
      await original.apply(this, arguments);
      var box = document.getElementById("reportWeather"), w = g._reportWeather;
      if (!box) return;
      var has = !!(w && w.temperature_c != null);
      var saved = has && w.fetched_at ? new Date(w.fetched_at) : null;
      var old = saved && !isNaN(saved) ? Date.now() - saved.getTime() > STALE_MS : !navigator.onLine;
      if (!has && !navigator.onLine) {
        box.innerHTML = '<div class="notice amber" style="margin:12px 0">' +
          esc(t("of_wx_default", "No saved weather for this animal, so typical values were used (28°C, 65% humidity, no rain).")) + '</div>';
      } else if (has && old) {
        w.season = gvml.season(new Date());
        var when = saved && !isNaN(saved) ? saved.toLocaleString() : "";
        box.innerHTML = weatherCardHTML(w, null, false) +
          '<div class="muted2" style="margin:-4px 0 10px">' + esc(t("of_wx_saved_note", "Saved weather, not live") + (when ? " · " + t("of_wx_saved", "last updated") + " " + when : "")) + '</div>';
      }
    };
  }
  wrapWeatherCard();

  g.gvOfflineReport = { isNetworkError: isNetworkError, submit: submit, lastFailure: function () { var f = failure; failure = null; return f; } };
})(self);
