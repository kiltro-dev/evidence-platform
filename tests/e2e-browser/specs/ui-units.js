// Spec: pure UI helpers tested against the REAL web/index.html source
// (prefix-eval harness — no copies, no DOM needed). Covers display
// humanization (prettySummary), meta formatting (fmtMetaVal), and the
// shared META_KEYS allowlist. No server, no browser.
'use strict';
const fs = require('fs');
const path = require('path');
const { makeChecks } = require('../helpers/checks');

module.exports = async function uiUnits() {
  const { check } = makeChecks('ui-units');
  const html = fs.readFileSync(
    path.resolve(__dirname, '..', '..', '..', 'web', 'index.html'), 'utf8');
  const src = html.split('/* ---------- label maps')[0].split('<script>')[1];
  if (!src || !src.includes('function prettySummary')) {
    throw new Error('harness anchor broken: prettySummary not found before label maps');
  }
  eval(src + ';globalThis.__t = {prettySummary, fmtMetaVal, META_KEYS, safeMsg};');
  const { prettySummary, fmtMetaVal, META_KEYS } = globalThis.__t;
  delete globalThis.__t;

  const nums = { cNum: { cmp_abc123: 'C1', cmp_def456: 'C2' } };
  check('cmp-tokens-mapped',
    prettySummary('consistent [cmp_abc123] [cmp_def456].', nums) === 'consistent [C1] [C2].',
    'uuids become display numbers');
  check('unknown-token-kept',
    prettySummary('see [note] and [cmp_zzz].', nums) === 'see [note] and [cmp_zzz].',
    'passthrough verbatim');
  check('stances-spaced',
    prettySummary('stance partially_supported, not not_assessable.', nums) ===
    'stance partially supported, not not assessable.', 'stances');
  check('big-enotation-grouped',
    prettySummary('gap 4.51517e+11 USD.', nums) === 'gap 451,517,000,000 USD.',
    'grouped magnitudes');
  check('small-enotation-kept', prettySummary('delta 1.5e-7.', nums) === 'delta 1.5e-7.',
    'no precision loss');
  check('empty-safe', prettySummary('', nums) === '' && prettySummary(null, nums) === '',
    'empty input');
  check('meta-keys-real',
    META_KEYS.includes('usage_extract') && META_KEYS.includes('usage_summary') &&
    !META_KEYS.includes('usage'), META_KEYS.join(','));
  check('usage-compact',
    fmtMetaVal('usage_extract', { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120, cached_tokens: 40 }) ===
    'total 120 (in 100, out 20, cached 40)', 'compact usage line');
  check('usage-nocache',
    fmtMetaVal('usage_summary', { total_tokens: 5, prompt_tokens: 3, completion_tokens: 2 }) ===
    'total 5 (in 3, out 2)', 'no cached bit');
  check('plain-passthrough', fmtMetaVal('model', 'muse-spark-1.3') === 'muse-spark-1.3', 'plain');
};
