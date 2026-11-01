// Voice symptom input for the farmer's report form. Page only.
//
// Offline-first voice recording for disease reports. Records until the farmer taps the mic again
// (or MAX_MS = 60 seconds). Needs no internet connection.
//
// The recording is kept in the report as a data URL in the format the browser produced
// (webm/opus on Chrome, mp4 on Safari). The recording is sent with the report for the vet to
// review. Symptom ticking (not speech recognition) drives the ML model.
(function (g) {
  var cfg = { MAX_MS: 60000, SILENCE_MS: 3000, TICK_MS: 500 };
  var rec = null, chunks = [], stream = null, recognition = null;
  var silenceTimer = null, capTimer = null, tickTimer = null, startedAt = 0;
  var transcript = "", limitHit = false, discard = false;

  function el(id) { return document.getElementById(id); }
  function tr(key, fallback) { return typeof g.t === "function" ? g.t(key, fallback) : fallback; }
  function say(msg) { var s = el("voiceStatus"); if (s) { s.style.display = "block"; s.innerText = msg; } }
  function clock(ms) { var s = Math.floor(ms / 1000); return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2); }
  function speechLang() {
    var l = (el("langApp") || {}).value || "en";
    return l === "hi" ? "hi-IN" : l === "mr" ? "mr-IN" : "en-IN";
  }

  function start(btn) {
    if (rec && rec.state === "recording") return stop();
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) { begin(s, btn); }, function (err) {
      alert(tr("voice_mic_denied", "Microphone permission is needed to record.") + (err && err.name ? " (" + err.name + ")" : ""));
    });
  }

  function begin(s, btn) {
    stream = s; chunks = []; transcript = ""; limitHit = false; discard = false;
    rec = new MediaRecorder(s);            // the browser's own format; the server accepts webm, mp4, ogg ...
    rec.ondataavailable = function (e) { if (e.data && e.data.size > 0) chunks.push(e.data); };
    rec.onstop = finish;

    var b = btn || el("micBtn");
    if (b) { b.dataset.idle = b.innerHTML; b.textContent = "\u23F9"; b.style.background = "var(--red)"; }
    var box = el("voiceTranscript"); if (box) { box.style.display = "block"; box.innerText = ""; }

    // Live text only when online. Offline (or if recognition fails) the recording just carries on.
    var SR = g.SpeechRecognition || g.webkitSpeechRecognition;
    if (navigator.onLine && SR) {
      try {
        recognition = new SR(); recognition.continuous = true; recognition.interimResults = true; recognition.lang = speechLang();
        recognition.onresult = function (ev) {
          var fin = "", interim = "";
          for (var i = 0; i < ev.results.length; i++) {
            if (ev.results[i].isFinal) fin += ev.results[i][0].transcript; else interim += ev.results[i][0].transcript;
          }
          transcript = fin;
          var tb = el("voiceTranscript"); if (tb) tb.innerText = fin + " " + interim;
          clearTimeout(silenceTimer); silenceTimer = setTimeout(stop, cfg.SILENCE_MS);   // farmer stopped talking
        };
        recognition.onerror = function () { clearTimeout(silenceTimer); };
        recognition.start();
      } catch (e) { recognition = null; }
    }

    rec.start();
    startedAt = Date.now();
    capTimer = setTimeout(function () { limitHit = true; stop(); }, cfg.MAX_MS);
    tickTimer = setInterval(function () { say(tr("voice_rec", "Recording… speak the symptoms. Tap the mic again when you finish.") + "  " + clock(Date.now() - startedAt)); }, cfg.TICK_MS);
    say(tr("voice_rec", "Recording… speak the symptoms. Tap the mic again when you finish.") + "  0:00");
  }

  function stop(drop) {
    discard = drop === true;
    clearTimeout(silenceTimer); clearTimeout(capTimer); clearInterval(tickTimer);
    if (rec && rec.state === "recording") { try { rec.stop(); } catch (e) {} }
    if (stream) { stream.getTracks().forEach(function (tk) { tk.stop(); }); stream = null; }
    if (recognition) { try { recognition.stop(); } catch (e) {} recognition = null; }
    var b = el("micBtn");
    if (b && b.dataset.idle != null) { b.innerHTML = b.dataset.idle; b.style.background = ""; }
  }

  function finish() {
    if (discard) return;
    var type = ((rec && rec.mimeType) || (chunks[0] && chunks[0].type) || "audio/webm").split(";")[0] || "audio/webm";
    var blob = new Blob(chunks, { type: type });
    if (!blob.size) return say(tr("voice_empty", "Nothing was recorded. Please try again."));
    var reader = new FileReader();
    reader.onloadend = function () {
      g._lastAudioBase64 = "data:" + type + ";base64," + (String(reader.result).split(",")[1] || "");
      var text = transcript.trim();
      if (text && navigator.onLine && typeof g.matchSymptomsFromTranscript === "function") {
        say("Audio recorded! Matching symptoms...");
        g.matchSymptomsFromTranscript(text);          // the page's own matcher ticks the boxes
      } else {
        say(tr("voice_saved", "✓ Recording saved. It will be sent to the vet with your report. Please also tick the symptoms.") +
            (limitHit ? "  " + tr("voice_limit", "Recording stopped at the time limit.") : ""));
      }
    };
    reader.readAsDataURL(blob);
  }

  // Closing the form must not leave the microphone running.
  var closeOriginal = g.closeModal;
  if (typeof closeOriginal === "function") {
    g.closeModal = function () { stop(true); return closeOriginal.apply(this, arguments); };
  }

  g.startSymptomVoiceRecording = start;
  g.stopSymptomVoiceRecording = stop;
  g.gvVoice = { cfg: cfg };
})(self);
