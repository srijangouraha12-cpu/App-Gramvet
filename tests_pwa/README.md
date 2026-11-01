# Running the tests

Never run these against your live folder: they write to the database. Work on a copy.

    npm i jsdom fake-indexeddb acorn eslint@8 puppeteer-core @sparticuz/chromium     # install together (npm i --no-save removes others)
    pip install pyflakes

| What | Command (from this folder) |
|---|---|
| JavaScript syntax + real-bug lint | `node parsecheck.js <project>`   `node lint_page.js <project>` |
| Browser-side logic (jsdom) | `node test_phase7.js <project>/static/pwa` |
| Report detail screen | `node test_report_detail.js <project>` |
| Server logic | `python test_voice_server.py <copy>` `test_vet_server.py` `test_idempotency_server.py` `test_limits_server.py` `test_gzip_server.py` |
| Hostile-input fuzz (must print 0 crash sites) | `python fuzz_server.py <copy>` |
| **Real Chrome** (starts your Flask app itself, on a scratch copy) | `node e2e/e2e_offline.js` `e2e_phone.js` `e2e_startup.js` `e2e_traffic.js` `e2e_diag.js` `audit_screens.js` `audit_modals.js` `audit_variants.js` |
| Load time / data use on an emulated slow phone | `node e2e/measure_load.js` `e2e/sw_traffic.js` |

The e2e scripts point at `/home/claude/gramvet final - Copy` – edit the path near the top of `e2e/lib.js` callers (search for it) for your machine.
