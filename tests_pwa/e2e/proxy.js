// A TCP proxy between Chrome and Flask. It can (a) let a request through and DROP the reply, which is what a phone
// losing signal at the wrong moment looks like, and (b) count the requests that reach the server.
const net = require("net");
function startProxy(listenPort, upstreamPort) {
  const state = { dropNext: null, dropped: 0, seen: [], replays: 0, log: [] };   // log: { req, status, bytes } for every reply that came back          // dropNext: regex on the request line, e.g. /^POST \/api\/cases\/report/
  const server = net.createServer((client) => {
    let dropping = false, lastReq = null, want = 0, got = 0, cur = null;
    const up = net.connect(upstreamPort, "127.0.0.1");
    client.on("data", (buf) => {
      const line = buf.toString("latin1", 0, 120).split("\r\n")[0];
      if (/^(GET|POST|PUT|DELETE) /.test(line)) { state.seen.push(line); lastReq = line; if (state.dropNext && state.dropNext.test(line)) { dropping = true; state.dropNext = null; } }
      up.write(buf);
    });
    up.on("data", (buf) => {
      const head = buf.toString("latin1", 0, 800);
      if (/^HTTP\/1\.[01] \d{3}/.test(head)) {                                   // start of a reply: remember status and size
        const st = +head.slice(9, 12), cl = /Content-Length: (\d+)/i.exec(head), hend = head.indexOf("\r\n\r\n");
        cur = { req: lastReq, status: st, bytes: cl ? +cl[1] + (hend > 0 ? hend + 4 : 0) : buf.length }; state.log.push(cur);
        if (!cl) cur.chunked = true;
      } else if (cur && cur.chunked) cur.bytes += buf.length;
      if (/Idempotent-Replay: true/i.test(head)) state.replays++; if (dropping) { state.dropped++; client.destroy(); up.destroy(); } else client.write(buf); });
    const end = () => { client.destroy(); up.destroy(); };
    client.on("error", end); up.on("error", end); client.on("close", end); up.on("close", end);
  }).listen(listenPort, "127.0.0.1");
  return { state, close: () => new Promise((r) => { server.close(r); setTimeout(r, 300); }) };
}
module.exports = { startProxy };
