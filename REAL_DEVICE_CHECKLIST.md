# GramVet – real phone checklist

Everything below was tested in a real desktop Chrome (phone-sized screen, real service worker, a real stopped server).
What could **not** be tested there, and needs a real phone: Background Sync with the app closed, real signal changes,
the phone's own storage clean-up, speed on low-end hardware, and the microphone.

## 0. Before you start – HTTPS is required
A phone opening `http://192.168.x.x:5000` is **not** a secure connection. Chrome will then refuse the offline service,
the microphone and persistent storage, and everything will look broken for no visible reason. Use one of:
1. Deploy it (any host with HTTPS) – best.
2. A tunnel: `ngrok http 5000` (or a Cloudflare tunnel) gives an `https://` address that reaches your PC.
3. Testing only: in Chrome on the phone open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add
   `http://<PC-address>:5000`, enable, relaunch. (Run Flask with `host="0.0.0.0"`.)

## 1. Install and check
1. Open the address in **Chrome on Android**, sign in, wait ~10 seconds, reload once.
2. Chrome menu → **Install app** (or Add to Home screen). Open it from the home screen.
3. Tap the round status pill (top of screen) → **Phone check (for testing)**.
   Everything should be ✓. `!` or `✗` lines say what to do. **Tap "Ask the phone to keep saved data".**
   Note "Phone clock vs server clock": it must be within 2 minutes (set date/time to automatic).

## 2. Tests (write down ✓ / ✗ and what you saw)
| # | Do this | Expected |
|---|---|---|
| 1 | Farmer: airplane mode on. Report an animal (tick 2 symptoms), Save. | "Report saved on this phone", an AI result, pill says "Offline · 1 queued". |
| 2 | Wait 2 minutes offline, then airplane mode off. Don't touch the app. | Pill goes to "Online" by itself within ~30 s; the vet sees exactly **one** report with the *offline* time. |
| 3 | Vet: open a case with signal, then airplane mode on. Open the same case again. | The case opens from the phone. Record a Note. "Saved on this phone". |
| 4 | Airplane mode off. | The note syncs once; the case timeline shows it with the time you recorded it. |
| 5 | **Background sync:** farmer saves a report offline, then swipe the app away completely. Turn signal on. Wait 2–5 min. | Open the app: the queue is empty and the vet already has the report (Chrome may delay this; note how long). |
| 6 | **Bad moment:** Save a report and switch airplane mode on/off right as you tap (try 5 times). | Never two copies of the same report on the vet's side. |
| 7 | Phone check → after test 1: "Unsent reports" shows the waiting item with "key ok". | Yes. |
| 8 | Slow network: Chrome DevTools remote debugging (`chrome://inspect`) → Network → Slow 3G. Clear site data, sign in, reload. | Usable in ~4–6 s; about 0.4 MB downloaded the first time. |
| 9 | Voice (over HTTPS): record 10 s, tick a symptom, Save offline, sync. | The vet can play the recording in the case. |
| 10 | Leave the app in the background for an hour, then open the Phone check. | "Last offline-data download" is not repeating every few minutes. |
| 11 | Phone almost full (fill it with a large file), then save a report offline. | Either it saves, or you see "Phone storage is full…" – never a silent loss. |
| 12 | Vet: try to close a case after a farmer reported again since your last CURE. | Refused with "A newer report arrived after your last CURE…". |
| 13 | Rotate the phone; open every screen in Hindi and dark mode. | Nothing cut off, no sideways scrolling. |

## 3. What to send back
The text from **Phone check → Copy this report**, the test number that failed, and what you saw.
