// GramVet service worker.
//   Phase 1: app shell caching.
//   Phase 2: last-known API data for offline reads, session seeding, offline write queue.
//   Phase 3: map tile cache (OpenStreetMap).
//   Phase 5: weather cache rules (see apiGet / storeApi).
//   Phase 4: offline disease model files are part of the app shell.
// Bump VERSION whenever a precached file changes.
importScripts("/static/pwa/db.js", "/static/pwa/outbox.js");

const VERSION = "gv-v13";
const SHELL = VERSION + "-shell";
// API snapshots are not versioned, so an app update does not wipe the offline data.
const API = "gv-api";
// Map tiles: also unversioned, so an app update does not throw the downloaded map away.
const TILES = "gv-tiles";
const TILE_HOST = /^(?:[abc]\.)?tile\.openstreetmap\.org$/;
const TILE_ORIGIN = "https://tile.openstreetmap.org";
const TILE_MAX = 1500;                       // roughly 20-30 MB
const TILE_TTL_MS = 30 * 24 * 3600 * 1000;
let tilePuts = 0;
const API_TIMEOUT_MS = 8000;
const AUTH_PATHS = ["/api/login", "/api/signup", "/api/logout"];
// A farmer's animals all resolve to the farmer's registered location, so they share one
// weather reading. This key holds the newest real one, for animals never opened online.
const WEATHER_LAST = "/gv/last-weather";
const OFFLINE_URL = "/static/pwa/offline.html";
const NAV_TIMEOUT_MS = 4000;

const LOCAL_SHELL = [
  "/",
  OFFLINE_URL,
  "/static/pwa/diagnostics.html",
  "/static/advisories.js",
  "/static/pwa/db.js",
  "/static/pwa/outbox.js",
  "/static/pwa/tiles.js",
  "/static/pwa/model-data.js",
  "/static/pwa/model.js",
  "/static/pwa/offline-report.js",
  "/static/pwa/offline-vet.js",
  "/static/pwa/voice.js",
  "/static/pwa/i18n-offline.js",
  "/static/pwa/pwa.js",
  "/static/pwa/manifest.json",
  "/static/pwa/icons/icon-192.png",
  "/media/p2.png"          // the app's header logo. The login page's pictures (about 1.6 MB) are cached the first time that page is shown.
];
// Leaflet is loaded from a CDN by index.html; keep a copy so the app opens offline.
const CDN_SHELL = [
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
  "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  "https://unpkg.com/leaflet@1.9.4/dist/images/layers.png",
  "https://unpkg.com/leaflet@1.9.4/dist/images/layers-2x.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    await cache.addAll(LOCAL_SHELL);
    // CDN files are best-effort so a slow CDN cannot block installation.
    await Promise.allSettled(CDN_SHELL.map((u) => cache.add(u)));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const keep = [SHELL, API, TILES];
    for (const k of await caches.keys()) if (!keep.includes(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);

  if (req.method === "POST" && url.origin === location.origin && AUTH_PATHS.includes(url.pathname)) {
    return e.respondWith(authRequest(req, url.pathname));
  }
  if (req.method !== "GET") return;

  if (req.mode === "navigate") return e.respondWith(navigate(req));

  if (url.origin === location.origin) {
    if (url.pathname === "/api/sync" || url.pathname.startsWith("/media/audio/")) return;
    if (url.pathname.startsWith("/api/")) return e.respondWith(apiGet(req));
    if (url.pathname.startsWith("/static/") || url.pathname.startsWith("/media/")) {
      return e.respondWith(staleWhileRevalidate(req));
    }
    return;
  }
  if (url.hostname === "unpkg.com") return e.respondWith(cacheFirst(req));
  if (TILE_HOST.test(url.hostname)) return e.respondWith(tile(url, req));
  // Nominatim (place search) needs the internet and is not touched.
});

// Background Sync: replay the outbox when the connection returns, even if the app is closed.
self.addEventListener("sync", (e) => {
  if (e.tag !== "gv-outbox") return;
  e.waitUntil(gvsync.flush().then((r) => {
    if (r.stopped === "offline" || r.stopped === "server") throw new Error("retry later");
  }));
});

// Login/signup: drop the previous user's data and seed /api/me so a signed-in user
// is still recognised when the app is opened offline. Logout wipes the snapshots.
async function authRequest(req, path) {
  const isLogout = path === "/api/logout";
  let res;
  try {
    res = await fetch(req);
  } catch (err) {
    if (isLogout) await caches.delete(API);
    throw err;
  }
  if (isLogout || res.ok) await caches.delete(API);
  if (!isLogout && res.ok) {
    const j = await res.clone().json().catch(() => null);
    if (j && j.user) {
      const me = new Response(JSON.stringify({ authenticated: true, user: j.user }),
        { headers: { "Content-Type": "application/json" } });
      await (await caches.open(API)).put("/api/me", me);
    }
  }
  return res;
}

// Network first; when the network is down or slow, answer with the last good response.
async function apiGet(req) {
  const url = new URL(req.url);
  const isWeather = url.pathname === "/api/weather";
  const cache = await caches.open(API);
  const hit = await cache.match(req, { ignoreVary: true });
  const network = fetch(req).then((res) => {
    if (res.ok) storeApi(cache, req, res.clone(), isWeather && url.searchParams.has("animal_id")).catch(() => {});
    return res;
  });
  network.catch(() => {});
  if (!hit) {
    if (!isWeather || !url.searchParams.has("animal_id")) return network;
    try { return await network; } catch (err) {
      const last = await cache.match(WEATHER_LAST);
      if (last) return last;
      throw err;
    }
  }
  try {
    return await Promise.race([network, new Promise((_, rej) => setTimeout(rej, API_TIMEOUT_MS))]);
  } catch (_) {
    return hit;
  }
}

// The weather endpoint answers 200 with {available:false} when the weather service is
// down. That must not replace the last real reading, so it is not stored.
async function storeApi(cache, req, res, isAnimalWeather) {
  if (new URL(req.url).pathname !== "/api/weather") return cache.put(req, res);
  const j = await res.clone().json().catch(() => null);
  if (!j || !j.weather || j.weather.available === false) return;
  await cache.put(req, res.clone());
  if (isAnimalWeather) await cache.put(WEATHER_LAST, res);
}

async function navigate(req) {
  const cache = await caches.open(SHELL);
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), NAV_TIMEOUT_MS);
    const res = await fetch(req, { signal: ctrl.signal });
    clearTimeout(t);
    if (res.ok && new URL(req.url).pathname === "/") cache.put("/", res.clone());
    return res;
  } catch (_) {
    return (await cache.match(req)) || (await cache.match("/")) || (await cache.match(OFFLINE_URL));   // the exact cached page first (e.g. the phone check), else the app
  }
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(req);
  const refresh = fetch(req).then((res) => {
    if (res.ok) cache.put(req, res.clone());
    return res;
  }).catch(() => null);
  return hit || (await refresh) || Response.error();
}

async function cacheFirst(req) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(req);
  if (hit) return hit;
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (_) {
    return Response.error();
  }
}

// Map tiles. Leaflet spreads requests over a./b./c. subdomains; all three are the same
// tile, so they share one cache entry. Tiles are fetched in CORS mode (OSM allows it) so
// they are stored as normal responses; opaque ones count ~7 MB each against the quota.
async function tile(url, req) {
  const key = TILE_ORIGIN + url.pathname;
  const cache = await caches.open(TILES);
  const hit = await cache.match(key);
  const fresh = hit && Date.now() - Number(hit.headers.get("x-gv-cached-at") || 0) < TILE_TTL_MS;
  if (fresh) return hit;
  try {
    const res = await fetch(key, { mode: "cors", credentials: "omit" });
    if (res.ok) { storeTile(cache, key, res.clone()).catch(() => {}); return res; }
    return hit || res;                        // e.g. rate-limited: an old tile beats no tile
  } catch (_) {
    if (hit) return hit;
    // Offline, or the CORS fetch was refused: try the browser's own request once, so
    // the map can never do worse than it did without this service worker.
    try { return await fetch(req); } catch (__) { return Response.error(); }
  }
}

async function storeTile(cache, key, res) {
  const headers = new Headers(res.headers);
  headers.set("x-gv-cached-at", String(Date.now()));
  await cache.put(key, new Response(await res.blob(), { status: 200, headers }));
  if (++tilePuts % 50 === 0) {                // keep the cache bounded, dropping the oldest tiles
    const keys = await cache.keys();
    for (const k of keys.slice(0, Math.max(0, keys.length - TILE_MAX))) await cache.delete(k);
  }
}
