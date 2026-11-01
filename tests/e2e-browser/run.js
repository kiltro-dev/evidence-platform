// Runner: boots one throwaway server, runs every spec in ./specs,
// prints a summary, exits nonzero on any failure. Browser specs share
// the server; ui-units needs neither server nor browser.
// Usage: npm test [-- spec-name-substring...]  (filters specs)
//        SHIPCHECK_E2E_LIVE=1 npm test         (full-model mode; needs key)
'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { startServer } = require('./helpers/server');

(async () => {
  const t0 = Date.now();
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'shipcheck-e2e-'));
  const filter = process.argv.slice(2);
  const files = fs.readdirSync(path.join(__dirname, 'specs'))
    .filter(f => f.endsWith('.js'))
    .filter(f => !filter.length || filter.some(s => f.includes(s)))
    .sort();
  if (!files.length) throw new Error('no specs matched: ' + filter.join(' '));

  let playwright;
  try {
    playwright = require('playwright');
  } catch {
    throw new Error('missing dependency: run `npm ci` in tests/e2e-browser, then `npm run install-browser` on fresh machines');
  }
  const needsServer = files.some(f => f !== 'ui-units.js');
  const server = needsServer ? await startServer({ live: !!process.env.SHIPCHECK_E2E_LIVE }) : null;
  console.log('server: ' + (server ? server.base + ' (' + server.mode + ')' : 'not needed') +
    ' | out: ' + outDir);
  const ctx = { base: server ? server.base : null, playwright, outDir };
  let failed = 0;
  for (const f of files) {
    const name = path.basename(f, '.js');
    console.log('--- spec: ' + name + ' ---');
    try {
      await require(path.join(__dirname, 'specs', f))(ctx);
      console.log('SPEC-PASS | ' + name);
    } catch (e) {
      failed++;
      console.log('SPEC-FAIL | ' + name + ' | ' + String((e && e.message) || e).slice(0, 300));
    }
  }
  if (server) await server.stop();
  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  console.log(`E2E-SUMMARY | ${files.length - failed}/${files.length} specs passed in ${secs}s | out: ${outDir}`);
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error('E2E-RUNNER-FAILED: ' + ((e && e.message) || e)); process.exit(2); });
