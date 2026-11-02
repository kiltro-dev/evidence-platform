# KEYS_NEEDED — official data APIs (V6-3)

No key-gated connector is implemented: on 2026-11-02 the `.env` has
none of the variables below, so category b stays at its declared
LIMIT (0/42) and the FOMC case stays 5×Unverifiable. When a human
adds a key, implement the matching connector (official API only, no
scraping, honor rate limits/terms) with evidence packages + hashed
snapshots, then re-run the FOMC case and category b.

| Variable | API | Unlocks (examples) | Get a key |
|---|---|---|---|
| `FRED_API_KEY` | FRED (St. Louis Fed) | FEDFUNDS, CPIAUCSL, UNRATE, GDPC1 | https://fred.stlouisfed.org/docs/api/api_key.html |
| `BEA_API_KEY` | BEA Data API | GDP/Chicago real GDP, PCE | https://apps.bea.gov/API/signup/ |
| `BLS_API_KEY` | BLS Public API v2 | LNS14000000 (unemployment), CUSR0000SA0 (CPI) | https://www.bls.gov/developers/api_signature_v2.htm |
| `CENSUS_API_KEY` | Census Data API | population, ACS/CPS series | https://api.census.gov/data/key_signup.html |

Notes:

- BLS without key works (25 queries/day) but this repo's robots
  policy refuses `api.bls.gov` live (see `live/V5_CATB.md`); a key
  does not change that policy — BLS needs a terms/robots exception
  decision first.
- FRED without key returns HTTP 400 (`live/swarm/altroutes.py`
  documents the probe); it is the highest-value first key (fed
  funds + CPI + unemployment + real GDP cover most b-like claims).
- Never commit these values: `.env` (chmod 600, git-ignored) only.
