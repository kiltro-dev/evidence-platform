// Spec: render fidelity (audit MEDIUM readability/meta/request_id).
// Displayed text is humanized while stored data keeps machine tokens;
// error cards carry the server request_id; meta shows real keys only.
'use strict';
const { makeChecks, launchBrowser } = require('../helpers/checks');

module.exports = async function renderFidelity(ctx) {
  const { check } = makeChecks('render-fidelity');
  const { browser, page, pageErrors } = await launchBrowser(ctx.playwright);
  try {
    await page.goto(ctx.base + '/', { waitUntil: 'networkidle', timeout: 30000 });
    await page.fill('#claim', 'The GDP of the United States was $27.36 trillion in 2023.');
    await page.click('#run');
    await page.waitForSelector('#verdictH', { timeout: 90000 });
    await page.waitForTimeout(400);
    const verdict = await page.locator('#out section').nth(0).textContent();
    const unc = await page.locator('#out section').nth(1).textContent().catch(() => '');
    const shown = verdict + '\n' + unc;
    check('no-snake-in-render',
      !shown.includes('partially_supported') && !shown.includes('not_assessable'),
      'stances spaced');
    check('no-cmpuuid-in-render', !/\[cmp_[A-Za-z0-9_-]{6,}\]/.test(shown), 'tokens mapped');
    check('no-enotation-in-render', !/\d+\.\d+e[+-]?\d+/i.test(shown), 'grouped');
    const outText = await page.textContent('#out');
    check('vintage-surfaced', outText.includes('Source vintage:'),
      (outText.match(/Source vintage:[^\n]*/) || ['absent'])[0].slice(0, 90));
    // Stored summary keeps machine tokens for the prose_traceability gate.
    const m = (page.url().match(/[?&]report=([^&]+)/)) || [];
    check('deep-link-set', !!m[1], page.url().slice(-40));
    const rep = await (await page.request.get(ctx.base + '/api/reports/' + m[1])).json();
    const stored = ((rep.conclusion || {}).summary) || '';
    check('stored-keeps-machine-tokens', /\[cmp_[A-Za-z0-9_-]+\]/.test(stored),
      stored.slice(0, 120));
    // Meta section: real keys, no phantom "Usage:" row.
    await page.locator('details summary', { hasText: 'Audit trail' }).first().click();
    const meta = await page.textContent('#out');
    check('no-phantom-usage-row',
      !/Usage:\s*\S/.test(meta.replace(/Usage \(/g, '').replace(/Usage extract/g, '')),
      'allowlist aligned');
    check('meta-has-version', meta.includes('App version'), 'version shown');
    // request_id ref on a forced server-400 card.
    await page.route('**/api/analyze', async route => {
      await route.fulfill({ status: 400, contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'empty_claim', message: 'Empty claim text.', request_id: 'req_e2e1' } }) });
    });
    await page.fill('#claim', 'The GDP of the United States was $27.36 trillion in 2023.');
    await page.click('#run');
    await page.waitForFunction(
      () => ((document.getElementById('out') || {}).textContent || '').includes('ref:'),
      null, { timeout: 10000 });
    const errCard = await page.textContent('#out');
    check('ref-shown-on-card', errCard.includes('req_e2e1'), errCard.slice(0, 160));
    await page.unroute('**/api/analyze');
    // 375px: a 200-char unbroken token must wrap, never overflow.
    const small = await launchBrowser(ctx.playwright, 375, 800);
    try {
      await small.page.goto(ctx.base + '/', { waitUntil: 'networkidle', timeout: 30000 });
      await small.page.fill('#claim', 'The value of ' + 'x'.repeat(200) + ' was 5 in 2023.');
      await small.page.click('#run');
      await small.page.waitForSelector('#verdictH', { timeout: 90000 });
      await small.page.waitForTimeout(400);
      // Open every disclosure: collapsed sections must not hide overflow.
      await small.page.evaluate(() => document.querySelectorAll('details').forEach(d => { d.open = true; }));
      await small.page.waitForTimeout(200);
      const ov = await small.page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      check('no-h-overflow-375', ov <= 1, 'overflow=' + ov + 'px');
    } finally {
      await small.browser.close();
    }
    check('no-pageerrors', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | ') || 'clean');
  } finally {
    await browser.close();
  }
};
