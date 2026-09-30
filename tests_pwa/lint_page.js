// Static checks on the page's JavaScript (no-undef and other real-bug rules). Usage: node lint_page.js <project>
const fs = require("fs"), path = require("path"), acorn = require("acorn"), { Linter } = require("eslint");
const proj = path.resolve(process.argv[2]);
const html = fs.readFileSync(path.join(proj, "templates", "index.html"), "utf8").replace(/\r/g, "").replace(/\{\{[^}]*\}\}/g, "[]");
const scripts = []; const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g; let m; while ((m = re.exec(html))) scripts.push(m[1]);
const ext = ["static/advisories.js", "static/pwa/db.js", "static/pwa/outbox.js", "static/pwa/tiles.js", "static/pwa/model-data.js", "static/pwa/model.js", "static/pwa/offline-report.js", "static/pwa/offline-vet.js", "static/pwa/voice.js", "static/pwa/i18n-offline.js", "static/pwa/pwa.js"];
// names other scripts put on window (top-level declarations and window.x / g.x assignments)
const globals = { L: "readonly", self: "readonly" };
for (const f of ext) {
  const src = fs.readFileSync(path.join(proj, f), "utf8");
  try { for (const n of acorn.parse(src, { ecmaVersion: 2022 }).body) { if (n.type === "FunctionDeclaration") globals[n.id.name] = "readonly"; if (n.type === "VariableDeclaration") n.declarations.forEach((d) => d.id.name && (globals[d.id.name] = "readonly")); } } catch (e) {}
  for (const x of src.matchAll(/(?:window|g|self)\.([A-Za-z_$][\w$]*)\s*=/g)) globals[x[1]] = "readonly";
}
scripts.forEach((code) => { for (const x of code.matchAll(/window\.([A-Za-z_$][\w$]*)\s*=/g)) globals[x[1]] = "readonly"; });   // window.x = ... creates a global
// DOM elements are reachable by id as globals in browsers: ids in the HTML and in HTML strings built by any script
for (const f of ["templates/index.html", ...ext]) { const src = fs.readFileSync(path.join(proj, f), "utf8"); for (const x of src.matchAll(/\bid\s*=\s*\\?["']([A-Za-z_$][\w$-]*)/g)) if (/^[A-Za-z_$][\w$]*$/.test(x[1])) globals[x[1]] = "readonly"; for (const x of src.matchAll(/getElementById\(["']([\w$]+)/g)) globals[x[1]] = "readonly"; }
// the page's own top-level names, for the PWA scripts that call into it
const pageGlobals = {};
scripts.forEach((code) => { try { for (const n of acorn.parse(code, { ecmaVersion: 2022 }).body) { if (n.type === "FunctionDeclaration") pageGlobals[n.id.name] = "readonly"; if (n.type === "VariableDeclaration") n.declarations.forEach((d) => d.id.name && (pageGlobals[d.id.name] = "readonly")); } } catch (e) {} });
const rules = { "no-undef": "error", "no-dupe-keys": "error", "no-unreachable": "error", "no-dupe-args": "error", "no-func-assign": "error", "no-const-assign": "error",
  "no-unsafe-finally": "error", "use-isnan": "error", "valid-typeof": "error", "no-self-assign": "error", "no-cond-assign": "error", "no-dupe-else-if": "error", "no-dupe-class-members": "error",
  "no-unused-labels": "error", "getter-return": "error", "no-import-assign": "error", "no-loss-of-precision": "error", "no-sparse-arrays": "error", "no-unsafe-negation": "error", "no-constant-condition": ["error", { checkLoops: false }] };
const linter = new Linter(); let total = 0;
scripts.forEach((code, i) => {
  if (code.length < 200) return;
  const msgs = linter.verify(code, { parserOptions: { ecmaVersion: 2022 }, env: { browser: true, es2021: true }, globals, rules });
  const lines = code.split("\n");
  console.log("inline script #" + (i + 1) + ": " + msgs.length + " finding(s)");
  for (const x of msgs) { total++; console.log("  [" + x.ruleId + "] line " + x.line + ": " + x.message + "\n      " + (lines[x.line - 1] || "").trim().slice(0, 150)); }
});
for (const f of ext) {                                   // the PWA scripts too
  const code = fs.readFileSync(path.join(proj, f), "utf8");
  const msgs = linter.verify(code, { parserOptions: { ecmaVersion: 2022 }, env: { browser: true, es2021: true, serviceworker: true }, globals: Object.assign({}, globals, pageGlobals), rules });
  if (msgs.length) { console.log(f + ": " + msgs.length); for (const x of msgs) { total++; console.log("  [" + x.ruleId + "] line " + x.line + ": " + x.message); } }
}
console.log("TOTAL findings:", total);
