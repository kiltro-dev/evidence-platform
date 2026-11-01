// Spec: abort/cancel/timeout cards (audit MEDIUM abort) + skeleton copy.
// abort(reason) rejects fetch with the reason string itself; the UI must
// show the typed card, never the generic "Service issue" card.
'use strict';
const { makeChecks, launchBrowser } = require('../helpers/checks');

module.exports = async function abortPaths(ctx) {
  const { check } = makeChecks('abort-paths');
  const { browser, page, pageErrors } = await launchBrowser(ctx.playwright);
  try {
    await page.goto(ctx.base + '/', { waitUntil: 'networkidle', timeout: 30000 });
    await page.route('**/api/analyze', async route => {
      await new Promise(r => setTimeout(r, 3000));
      await route.continue();
    });
    await page.fill('#claim', 'The GDP of the United States was $27.36 trillion in 2023.');
    await page.click('#run');
    await page.waitForSelector('#cancelBtn', { timeout: 5000 });
    const skel = await page.textContent('#out');
    check('skeleton-150s-copy', skel.includes('150s') && !skel.includes('45s'),
      skel.replace(/\s+/g, ' ').slice(0, 140));
    check('skeleton-single-status',
      await page.locator('#out [role="status"]').count() === 1, 'one role=status');
    await page.click('#cancelBtn');
    await page.waitForFunction(
      () => /cancelled|Service issue/.test((document.getElementById('out') || {}).textContent || ''),
      null, { timeout: 10000 });
    const out = await page.textContent('#out');
    check('cancel-card', out.includes('Analysis cancelled'), out.slice(0, 140));
    check('no-service-issue-on-cancel', !out.includes('Service issue'), 'typed card shown');
    check('cancel-card-announces',
      await page.locator('#out [role="alert"]').count() >= 1, 'role=alert present');
    // Superseded silence: a stale run paints no alert over the newer run.
    // (Direct doAnalyze calls: the Run button is disabled mid-flight, but
    // cross-flow clicks and retries reach the same shared-aborter path.)
    await page.evaluate(() => {
      window.__alerts = []; window.__verdictT = 0; window.__click2 = 0;
      new MutationObserver(muts => {
        for (const m of muts) for (const n of m.addedNodes) {
          if (!(n instanceof Element)) continue;
          if (n.matches('[role=alert]') || n.querySelector('[role=alert]'))
            window.__alerts.push(Math.round(performance.now()));
          if (n.querySelector && n.querySelector('#verdictH'))
            window.__verdictT = Math.round(performance.now());
        }
      }).observe(document.getElementById('out'), { childList: true, subtree: true });
    });
    await page.evaluate(() => { doAnalyze('The GDP of the United States was $27.36 trillion in 2023.', 'auto'); });
    await page.waitForSelector('#cancelBtn', { timeout: 5000 });
    await page.waitForTimeout(300);
    await page.evaluate(() => {
      doAnalyze('Unemployment in Germany was 3.1 percent in 2023.', 'auto');
      window.__click2 = Math.round(performance.now());
    });
    await page.waitForSelector('#verdictH', { timeout: 30000 });
    const sil = await page.evaluate(
      () => ({ alerts: window.__alerts, click2: window.__click2, verdictT: window.__verdictT }));
    const stale = sil.alerts.filter(t => t > sil.click2 && (sil.verdictT === 0 || t < sil.verdictT));
    check('superseded-silent', stale.length === 0, JSON.stringify(sil).slice(0, 200));
    const v2 = await page.textContent('#out');
    check('newest-result-wins', v2.includes('Unemployment in Germany'), 'req2 rendered');
    // The stale finally must not have stolen the controller: cancel works.
    await page.evaluate(() => { doAnalyze('The GDP of the United States was $27.36 trillion in 2023.', 'auto'); });
    await page.waitForSelector('#cancelBtn', { timeout: 5000 });
    await page.click('#cancelBtn');
    await page.waitForFunction(
      () => /Analysis cancelled/.test((document.getElementById('out') || {}).textContent || ''),
      null, { timeout: 10000 });
    check('cancel-after-supersede', true, 'controller survived');
    await page.unroute('**/api/analyze');
    check('no-pageerrors', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | ') || 'clean');
  } finally {
    await browser.close();
  }
};
