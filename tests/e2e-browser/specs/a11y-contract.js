// Spec: a11y announcement contract (audit HIGH-3/HIGH-4) + batch fixes:
// skip link, focus-following jumps, focusable tables, named controls,
// single batch alert, touch floors, reduced-motion guard, no favicon 404.
// #out is NOT a live region; errors announce via role=alert; results
// announce via focus move. Fails if anyone re-adds aria-live to #out.
'use strict';
const { makeChecks, launchBrowser } = require('../helpers/checks');

module.exports = async function a11yContract(ctx) {
  const { check } = makeChecks('a11y-contract');
  const { browser, page, pageErrors } = await launchBrowser(ctx.playwright);
  const requested = [];
  page.on('request', r => requested.push(r.url()));
  try {
    await page.goto(ctx.base + '/', { waitUntil: 'networkidle', timeout: 30000 });
    // Static contract: skip link, motion guard, builder markup.
    check('skip-link-target', await page.getAttribute('.skiplink', 'href') === '#main',
      'skip to #main');
    await page.locator('.skiplink').focus();
    const skipVisible = await page.evaluate(() => {
      const r = document.querySelector('.skiplink').getBoundingClientRect();
      return r.left >= 0 && r.top >= 0;
    });
    check('skip-link-visible-on-focus', skipVisible, 'offscreen until focus');
    await page.locator('.skiplink').press('Enter');
    await page.waitForTimeout(200);
    const afterSkip = await page.evaluate(() => document.activeElement && document.activeElement.id);
    check('skip-link-moves-focus', afterSkip === 'main', 'activeElement=' + afterSkip);
    const motionGuard = await page.evaluate(() => {
      for (const sh of document.styleSheets) {
        let rules = [];
        try { rules = [...sh.cssRules]; } catch { continue; }
        for (const r of rules) {
          // cssText serializes `animation:none` longhand, so match loosely.
          if (r.conditionText && r.conditionText.includes('prefers-reduced-motion') &&
              r.cssText.includes('.flash') && r.cssText.includes('animation'))
            return r.cssText.slice(0, 120);
        }
      }
      return '';
    });
    check('reduced-motion-guard', !!motionGuard, motionGuard || 'no guard rule');
    const markup = await page.evaluate(() => {
      const ev = evidenceHTML({ evidences: [{ evidence_id: 'e1',
        excerpt: 'x'.repeat(300), source_name: 'S', source_kind: 'primary',
        evidence_kind: 'direct', connector: 'worldbank', locator: 'L',
        retrieved_at: '2026-01-01T00:00:00Z', source_url: 'https://example.com/x',
        payload: { source_vintage: '2026-10-08', obs_status: 'A', source_decimal: '2' } }] },
        { eNum: { e1: 'E1' } }, '');
      const v = verdictHTML({ conclusion: { stance: 'contradicted', summary: 'x',
        support_comparison_ids: ['a'], contradict_comparison_ids: ['b'] },
        evidences: [], comparisons: [], elapsed_ms: 5 },
        { cNum: { a: 'C1', b: 'C2' }, eNum: {} });
      return { ev,
        alert1: batchErrorHTML({ claim_text: 't', error: 'e' }, 0, 2, true),
        group2: batchErrorHTML({ claim_text: 't', error: 'e' }, 1, 2, false),
        pills: v };
    });
    check('exbtn-aria', markup.ev.includes('aria-expanded="false"') &&
      markup.ev.includes('aria-controls="ex-E1"'), 'expanded+controls wired');
    check('vintage-markup', markup.ev.includes('Source vintage:') &&
      markup.ev.includes('data vintage 2026-10-08') &&
      markup.ev.includes('status A') && markup.ev.includes('decimals 2'),
      'vintage/obs/decimal shown');
    check('batch-first-alert-only', markup.alert1.includes('role="alert"') &&
      markup.group2.includes('role="group"') && !markup.group2.includes('role="alert"'),
      'one assertive card');
    check('pill-names', markup.pills.includes('aria-label="C1, consistent check') &&
      markup.pills.includes('aria-label="C2, contradicting check'), 'pills named');
    check('out-has-no-aria-live',
      await page.getAttribute('#out', 'aria-live') === null,
      'aria-live=' + await page.getAttribute('#out', 'aria-live'));
    check('formErr-role-alert',
      await page.getAttribute('#formErr', 'role') === 'alert', 'role set');
    check('batchErr-role-alert',
      await page.getAttribute('#batchErr', 'role') === 'alert', 'role set');
    const cd = await page.getAttribute('#claim', 'aria-describedby') || '';
    check('claim-describedby-formErr', cd.split(/\s+/).includes('formErr'), cd);
    const bd = await page.getAttribute('#batchText', 'aria-describedby') || '';
    check('batchText-describedby-batchErr', bd.split(/\s+/).includes('batchErr'), bd);
    // Announced validation error on empty submit.
    await page.fill('#claim', '   ');
    await page.click('#run');
    await page.waitForFunction(
      () => !document.getElementById('formErr').hidden, null, { timeout: 5000 });
    check('formErr-announces-on-empty', true, await page.textContent('#formErr'));
    // Result announcement happens via focus move to the verdict heading.
    await page.fill('#claim', 'The GDP of the United States was $27.36 trillion in 2023.');
    await page.click('#run');
    await page.waitForSelector('#verdictH', { timeout: 90000 });
    await page.waitForTimeout(400);
    const focused = await page.evaluate(() => document.activeElement && document.activeElement.id);
    check('focus-moves-to-verdict', focused === 'verdictH', 'activeElement=' + focused);
    // Traceability jumps move focus to the target check.
    const pillCount = await page.locator('.pillbtn[data-cmp]').count();
    check('pills-rendered', pillCount >= 1, pillCount + ' pills');
    const cn = await page.locator('.pillbtn[data-cmp]').first().getAttribute('data-cmp');
    await page.locator('.pillbtn[data-cmp]').first().click();
    await page.waitForTimeout(600);
    const jumped = await page.evaluate(() => document.activeElement && document.activeElement.id);
    check('jump-moves-focus', jumped === 'cmp-' + cn, 'activeElement=' + jumped);
    // Tables scroll for keyboards too.
    const tw = page.locator('.tblwrap').first();
    check('tblwrap-region', await tw.getAttribute('role') === 'region' &&
      await tw.getAttribute('tabindex') === '0', 'focusable region');
    await tw.focus();
    const twFocused = await page.evaluate(
      () => document.activeElement && document.activeElement.className);
    check('tblwrap-focusable', String(twFocused).includes('tblwrap'), String(twFocused));
    // Named + live controls.
    check('copyMsg-live', await page.getAttribute('#copyMsg', 'role') === 'status',
      'link feedback announces');
    check('vsHide-named', ((await page.getAttribute('#vsHide', 'aria-label')) || '').includes('Dismiss'),
      await page.getAttribute('#vsHide', 'aria-label'));
    const delCount = await page.locator('[data-del]').count();
    check('history-has-entry', delCount >= 1, delCount + ' rows');
    const delName = await page.locator('[data-del]').first().getAttribute('aria-label') || '';
    check('delete-named', /^Delete report rep_/.test(delName), delName);
    // Touch floors: small controls >= 32px tall.
    const pillH = (await page.locator('.pillbtn').first().boundingBox() || {}).height || 0;
    const cbH = (await page.locator('.checksbar button').first().boundingBox() || {}).height || 0;
    check('touch-min32', pillH >= 32 && cbH >= 32, 'pill=' + pillH + ' bar=' + cbH);
    // One assertive card per batch (fabricated 2-error batch body).
    await page.route('**/api/check', async route => {
      await route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ claim_count: 2, elapsed_ms: 3, errors: 2,
          rollup: {}, meta: {},
          reports: [{ claim_text: 'first bad claim text here', error: 'boom-a' },
                    { claim_text: 'second bad claim text here', error: 'boom-b' }] }) });
    });
    await page.check('input[name="mode"][value="batch"]');
    await page.fill('#batchText', 'The GDP of the United States was $27.36 trillion in 2023. The population of Japan was 125 million in 2023.');
    await page.click('#batchRun');
    await page.waitForSelector('.batcherr', { timeout: 15000 });
    const alerts = await page.locator('#out .batcherr[role="alert"]').count();
    const groups = await page.locator('#out .batcherr[role="group"]').count();
    check('batch-live-single-alert', alerts === 1 && groups === 1,
      'alerts=' + alerts + ' groups=' + groups);
    await page.unroute('**/api/check');
    check('favicon-never-requested', !requested.some(u => u.endsWith('/favicon.ico')),
      requested.length + ' requests, no favicon');
    check('no-pageerrors', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | ') || 'clean');
  } finally {
    await browser.close();
  }
};
