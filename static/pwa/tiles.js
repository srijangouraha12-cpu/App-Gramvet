// Pre-loads a small set of map tiles for offline use. Page only: the tiles go through
// the service worker, which stores them (see tile() in sw.js).
// Kept deliberately small - OpenStreetMap's public tile server is for light use.
(function (g) {
  var HOST = "https://tile.openstreetmap.org/";
  var OVERVIEW = { lat: 19.7515, lon: 75.7139, zooms: [6] };   // where the state-level map opens
  var LOCAL_ZOOMS = [8, 9, 10, 11, 12, 13];                    // around the user's registered location
  var DX = 2, DY = 1;                                          // 5 x 3 tiles per zoom level
  var CONCURRENCY = 4, DONE_RATIO = 0.8;

  function xy(lat, lon, z) {
    var n = Math.pow(2, z), r = lat * Math.PI / 180;
    return [
      Math.floor((lon + 180) / 360 * n),
      Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * n)
    ];
  }

  function around(lat, lon, zooms) {
    var out = [];
    zooms.forEach(function (z) {
      var c = xy(lat, lon, z), n = Math.pow(2, z);
      for (var x = c[0] - DX; x <= c[0] + DX; x++)
        for (var y = c[1] - DY; y <= c[1] + DY; y++)
          if (x >= 0 && y >= 0 && x < n && y < n) out.push(HOST + z + "/" + x + "/" + y + ".png");
    });
    return out;
  }

  function hasLocation(u) {
    return !!u && u.latitude != null && u.longitude != null && isFinite(u.latitude) && isFinite(u.longitude);
  }

  function urls(user) {
    var list = around(OVERVIEW.lat, OVERVIEW.lon, OVERVIEW.zooms);
    if (hasLocation(user)) list = list.concat(around(Number(user.latitude), Number(user.longitude), LOCAL_ZOOMS));
    return list;
  }

  function fetchAll(list) {
    var i = 0, ok = 0;
    function next() {
      if (i >= list.length) return Promise.resolve();
      return fetch(list[i++])
        .then(function (r) { if (r.ok) ok++; return r.blob(); })
        .catch(function () {})
        .then(next);
    }
    var workers = [];
    for (var k = 0; k < CONCURRENCY; k++) workers.push(next());
    return Promise.all(workers).then(function () { return ok; });
  }

  // Runs once per registered location. Needs the service worker to be in control,
  // otherwise the tiles would be downloaded but not kept.
  function warm(user) {
    var sw = typeof navigator !== "undefined" && navigator.serviceWorker;
    if (!navigator.onLine || !sw || !sw.controller) return Promise.resolve(false);
    var key = hasLocation(user) ? Number(user.latitude).toFixed(3) + "," + Number(user.longitude).toFixed(3) : "overview";
    return gvdb.metaGet("tilesWarmed").then(function (done) {
      if (done === key) return false;
      var list = urls(user);
      return fetchAll(list).then(function (ok) {
        if (ok < list.length * DONE_RATIO) return false;   // connection dropped part-way: try again later
        return gvdb.metaSet("tilesWarmed", key).then(function () { return true; });
      });
    });
  }

  g.gvtiles = { urls: urls, warm: warm };
})(self);
