// Spec: Judge mode ($0 cached replays), SSE batch progress + POST fallback,
// demo disclosure. Judge examples must render with ZERO analyze/check calls;
// live batch must stream per-claim progress and fall back to POST cleanly.
'use strict';
const fs = require('fs');
const path = require('path');
const { makeChecks, launchBrowser } = require('../helpers/checks');

const DISCLOSURE = "Demo notice: pasted text is sent to Meta's Model API " +
  "(Standard no-training tier); don't paste sensitive data.";

function mockReport(i, stance, text) {
  return {
    report_id: 'rep_mock' + i,
    claim: { raw_text: text, domain: 'economy', extraction_actor: 'heuristic' },
    evidences: [],
    comparisons: [],
    uncertainties: [],
    limitations: [],
    conclusion: { stance, summary: 'Mocked summary for e2e.', actor: 'code',
      support_comparison_ids: [], contradict_comparison_ids: [] },
    audit: [],
    meta: { app_version: '0.4.0' },
    elapsed_ms: 5,
  };
}

function sseBody(events) {
  return events.map(e => 'data: ' + JSON.stringify(e)).join('\n\n') + '\n\n';
}

module.exports = async function judgeMode(ctx) {
  const { check } = makeChecks('judge-mode');
  const { browser, page, pageErrors } = await launchBrowser(ctx.playwright);
  const apiCalls = [];
  page.on('request', r => {
    const u = r.url();
    if (u.includes('/api/')) apiCalls.push(r.method() + ' ' + u);
  });
  const analyzeCalls = calls =>
    calls.filter(u => u.includes('/api/analyze') || u.includes('/api/check'));
  try {
    await page.goto(ctx.base + '/', { waitUntil: 'networkidle', timeout: 30000 });
    // 1. Disclosure visible on page + same line in README demo section.
    check('disclosure-visible',
      await page.locator('#demoNotice').isVisible() &&
      (await page.textContent('#demoNotice')).includes(DISCLOSURE),
      (await page.textContent('#demoNotice')).slice(0, 60));
    const readme = fs.readFileSync(
      path.resolve(__dirname, '..', '..', '..', 'README.md'), 'utf8');
    check('disclosure-in-readme', readme.includes(DISCLOSURE),
      'README demo section carries the line');
    // 2. Judge is the default demo mode.
    check('judge-default',
      await page.getAttribute('input[name="demomode"]:checked', 'value') === 'judge',
      'default judge');
    // 3. Judge single: cached verdict, banner, zero analyze/check calls.
    const beforeS = apiCalls.length;
    await page.locator('#singlePanel .examples button').first().click();
    await page.waitForSelector('#verdictH', { timeout: 10000 });
    const vH = await page.textContent('#verdictH');
    check('judge-single-verdict', /partially supported/i.test(vH), vH.slice(0, 60));
    check('judge-single-banner',
      (await page.textContent('.cachedbar')).includes('Cached demo run'),
      'banner shown');
    const sCalls = analyzeCalls(apiCalls.slice(beforeS));
    check('judge-single-no-network', sCalls.length === 0,
      sCalls.join(' | ') || 'no analyze/check calls');
    check('judge-no-deeplink', !/[?&]report=/.test(page.url()), page.url());
    await page.screenshot({ path: path.join(ctx.outDir, 'judge-single.png') });
    // 4. Judge batch: cached roll-up, zero analyze/check calls.
    await page.check('input[name="mode"][value="batch"]');
    const beforeB = apiCalls.length;
    await page.locator('#batchPanel .examples button').first().click();
    await page.waitForSelector('#rollH', { timeout: 10000 });
    check('judge-batch-rollup',
      /Roll-up: 2 claims/.test(await page.textContent('#out')), 'Roll-up: 2 claims');
    const bCalls = analyzeCalls(apiCalls.slice(beforeB));
    check('judge-batch-no-network', bCalls.length === 0,
      bCalls.join(' | ') || 'no analyze/check calls');
    await page.screenshot({ path: path.join(ctx.outDir, 'judge-batch.png') });
    // 5. SSE progress via mocked stream (live mode): per-claim chips + line,
    // stream primary (no POST fallback when the stream is healthy).
    await page.check('input[name="demomode"][value="live"]');
    const doneBody = { claim_count: 2, elapsed_ms: 10, errors: 0,
      rollup: { supported: 1, partially_supported: 0, contradicted: 1,
        unverifiable: 0, not_assessable: 0 },
      meta: { splitter_actor: 'heuristic' },
      reports: [mockReport(0, 'supported', 'First mocked claim text here.'),
                mockReport(1, 'contradicted', 'Second mocked claim text here.')] };
    await page.route('**/api/check/stream', route => route.fulfill({
      status: 200, contentType: 'text/event-stream',
      body: sseBody([
        { type: 'started', claim_count: 2 },
        { type: 'claim', index: 0, done: 1, total: 2, stance: 'supported' },
        { type: 'claim', index: 1, done: 2, total: 2, stance: 'contradicted' },
        { type: 'done', result: doneBody },
      ]),
    }));
    // Deterministic progress capture: wrap the page's own progress fns
    // (transient DOM text can be overwritten within one pump, before any
    // MutationObserver callback runs — call records cannot be missed).
    await page.evaluate(() => {
      window.__prog = [];
      window.__chips = [];
      const s = window.setBatchProgress;
      const a = window.addBatchClaimItem;
      window.setBatchProgress = t => { window.__prog.push(t); return s(t); };
      window.addBatchClaimItem = e => {
        window.__chips.push('claim ' + e.index + ' done ' + e.done + ' of ' +
          e.total + ' ' + (e.error ? 'error' : e.stance));
        const r = a(e);
        const lis = document.querySelectorAll('#batchClaims li');
        if (lis.length) window.__chips.push('li:' + lis[lis.length - 1].textContent);
        return r;
      };
    });
    const beforeSSE = apiCalls.length;
    await page.fill('#batchText', 'First mocked claim text here. Second mocked claim text here.');
    await page.click('#batchRun');
    await page.waitForSelector('#rollH', { timeout: 15000 });
    const seen = await page.evaluate(
      () => ({ prog: window.__prog, chips: window.__chips }));
    check('sse-progress-line',
      seen.prog.some(t => /Checked 1 of 2/.test(t)) &&
      seen.prog.some(t => /Checked 2 of 2/.test(t)),
      JSON.stringify(seen.prog).slice(0, 160));
    check('sse-progress-chips',
      seen.chips.some(c => /claim 0 done 1 of 2 supported/.test(c)) &&
      seen.chips.some(c => /claim 1 done 2 of 2 contradicted/.test(c)) &&
      seen.chips.some(c => /li:Claim 1: Consistent/.test(c)) &&
      seen.chips.some(c => /li:Claim 2: Contradicted/.test(c)),
      JSON.stringify(seen.chips).slice(0, 220));
    const sseCalls = apiCalls.slice(beforeSSE);
    check('sse-primary',
      sseCalls.some(u => u.includes('/api/check/stream')),
      sseCalls.join(' | ').slice(0, 200));
    check('sse-no-fallback',
      !sseCalls.some(u => u.includes('/api/check') && !u.includes('/api/check/stream')),
      'healthy stream, no POST');
    await page.unroute('**/api/check/stream');
    // 6. Fallback: broken stream (500) still renders via real POST.
    await page.route('**/api/check/stream', route => route.fulfill({
      status: 500, contentType: 'application/json', body: '{}',
    }));
    await page.fill('#batchText', 'The GDP of the United States was $27.36 trillion in 2023.');
    await page.click('#batchRun');
    await page.waitForSelector('#rollH', { timeout: 120000 });
    check('fallback-renders',
      /Roll-up: 1 claim/.test(await page.textContent('#out')),
      'POST fallback rendered');
    await page.unroute('**/api/check/stream');
    // 7. Single-alert contract holds under SSE (mocked 2-error done).
    await page.route('**/api/check/stream', route => route.fulfill({
      status: 200, contentType: 'text/event-stream',
      body: sseBody([
        { type: 'started', claim_count: 2 },
        { type: 'claim', index: 0, done: 1, total: 2, error: 'boom-a' },
        { type: 'claim', index: 1, done: 2, total: 2, error: 'boom-b' },
        { type: 'done', result: { claim_count: 2, elapsed_ms: 3, errors: 2,
          rollup: {}, meta: {},
          reports: [{ claim_text: 'first bad claim text here', error: 'boom-a' },
                    { claim_text: 'second bad claim text here', error: 'boom-b' }] } },
      ]),
    }));
    await page.fill('#batchText', 'First bad claim text here. Second bad claim text here.');
    await page.click('#batchRun');
    await page.waitForSelector('.batcherr', { timeout: 15000 });
    const alerts = await page.locator('#out .batcherr[role="alert"]').count();
    const groups = await page.locator('#out .batcherr[role="group"]').count();
    check('sse-single-alert', alerts === 1 && groups === 1,
      'alerts=' + alerts + ' groups=' + groups);
    await page.unroute('**/api/check/stream');
    // 8. Mobile 375px: judge render, all disclosures open, no h-overflow.
    const small = await launchBrowser(ctx.playwright, 375, 800);
    try {
      await small.page.goto(ctx.base + '/', { waitUntil: 'networkidle', timeout: 30000 });
      await small.page.locator('#singlePanel .examples button').nth(2).click();
      await small.page.waitForSelector('#verdictH', { timeout: 10000 });
      await small.page.evaluate(
        () => document.querySelectorAll('details').forEach(d => { d.open = true; }));
      await small.page.waitForTimeout(200);
      const ov = await small.page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      check('no-h-overflow-375-judge', ov <= 1, 'overflow=' + ov + 'px');
      await small.page.screenshot({ path: path.join(ctx.outDir, 'judge-375.png') });
      check('small-no-pageerrors', small.pageErrors.length === 0,
        small.pageErrors.slice(0, 3).join(' | ') || 'clean');
    } finally {
      await small.browser.close();
    }
    // 9. A11y preserved: named demo radiogroup, #out still not a live region.
    check('demomode-named',
      await page.locator('[aria-label="Demo mode"]').getAttribute('role') === 'radiogroup',
      'Demo mode radiogroup');
    check('out-has-no-aria-live',
      await page.getAttribute('#out', 'aria-live') === null, 'no aria-live');
    check('no-pageerrors', pageErrors.length === 0,
      pageErrors.slice(0, 3).join(' | ') || 'clean');
  } finally {
    await browser.close();
  }
};
