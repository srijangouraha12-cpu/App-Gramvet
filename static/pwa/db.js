// Local database shared by the page and the service worker.
//   meta   : small key/value items (install time, temp-id map)
//   outbox : writes made offline, waiting to be replayed to the server
(function (g) {
  var DB_NAME = "gramvet", DB_VERSION = 2, dbp = null;

  function open() {
    if (dbp) return dbp;
    dbp = new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains("meta")) db.createObjectStore("meta");
        if (!db.objectStoreNames.contains("outbox")) db.createObjectStore("outbox", { keyPath: "id", autoIncrement: true });
      };
      req.onsuccess = function () {
        var db = req.result;
        // Let a newer version (another tab / the service worker) upgrade the database.
        db.onversionchange = function () { db.close(); dbp = null; };
        resolve(db);
      };
      req.onerror = function () { dbp = null; reject(req.error); };
    });
    return dbp;
  }

  function tx(store, mode, fn) {
    return open().then(function (db) {
      return new Promise(function (resolve, reject) {
        var t = db.transaction(store, mode), r = fn(t.objectStore(store));
        t.oncomplete = function () { resolve(r && r.result); };
        t.onerror = t.onabort = function () { reject(t.error); };
      });
    });
  }

  g.gvdb = {
    open: open,
    metaGet: function (k) { return tx("meta", "readonly", function (s) { return s.get(k); }); },
    metaSet: function (k, v) { return tx("meta", "readwrite", function (s) { return s.put(v, k); }); },
    outboxAdd: function (item) { return tx("outbox", "readwrite", function (s) { return s.add(item); }); },
    outboxPut: function (item) { return tx("outbox", "readwrite", function (s) { return s.put(item); }); },
    outboxAll: function () { return tx("outbox", "readonly", function (s) { return s.getAll(); }); },
    outboxDelete: function (id) { return tx("outbox", "readwrite", function (s) { return s.delete(id); }); }
  };
})(self);
