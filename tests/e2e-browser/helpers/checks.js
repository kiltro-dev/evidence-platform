// Minimal check collector: specs record named PASS/FAIL lines and the
// runner summarizes. A spec throws on first failure (fail-fast) after
// logging; run.js reports per-spec status + totals and exits nonzero.
'use strict';

function makeChecks(specName) {
  const rows = [];
  return {
    rows,
    check(name, ok, detail) {
      const line = (ok ? 'PASS' : 'FAIL') + ' | ' + specName + ' | ' + name +
        ' | ' + String(detail === undefined ? '' : detail).slice(0, 220);
      rows.push(line);
      console.log(line);
      if (!ok) throw new Error('[' + specName + '] ' + name + ': ' + detail);
    },
  };
}

// Launch helper: uses the playwright-bundled chromium (headless shell OK).
async function launchBrowser(playwright, width = 1280, height = 900) {
  const browser = await playwright.chromium.launch();
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', e => pageErrors.push(String((e && e.message) || e)));
  return { browser, page, pageErrors };
}

module.exports = { makeChecks, launchBrowser };
