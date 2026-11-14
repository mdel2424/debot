# Retrieval and Rate-Limit Benchmark

## Measured Results

Bounded live Firefox probes on November 14 used men's tops, target P2P 21 inches,
length 27 inches, and tolerances 0.75 and 1 inch. The baseline used the old
full-collection search flow with normal pacing disabled. Payload decoding had
already been optimized, so this is not a pristine-commit CPU benchmark.

For `theloopmtl`, both runs processed the same 72-link budget and returned zero
matches. This comparison measures retrieval work rather than changes in matches.

| Measurement | Baseline | Updated |
| --- | --- | --- |
| First scan | 5.405 seconds | 6.390 seconds |
| Repeat scan | 4.271 seconds | 0.169 seconds |
| Product API responses, first/repeat | 3 / 3 | 3 / 0 |
| First-party data responses, first/repeat | 30 / 26 | 25 / 0 |
| Individual product-page navigations | 0 | 0 |
| HTTP 403/429 responses | 0 | 0 |

The paced first scan is slower; its cached repeat is approximately 25 times
faster. The two scans together reduced completed first-party data responses
from 56 to 25. These counts exclude static assets and are not total HTTP traffic.

A separate `thethriftingjesus` probe requested up to 240 links and five matches:

- First match: 6.142 seconds; all five matches returned in 7.086 seconds.
- Collection stopped at 96 links, with 78 processed and four product API pages.
- The repeat returned the same five matches in 0.164 seconds, with no network
  responses and no individual product-page navigations.
- Neither run encountered HTTP 403 or 429.

A final check through the running Vite/FastAPI HTTP endpoint used distinct
search IDs, so it exercised a new search rather than replaying a retained job.
It returned the same five matches in 7.811 seconds cold and 0.065 seconds on
repeat. Both summaries reported 78 processed / 96 collected and `match_limit`.

These short probes do not establish Depop's quota or guarantee a long batch will
never be blocked. The probe stops at the first blocked response instead of
trying to establish a limit by repeatedly exceeding it.

## Implementation

- Cursor fetches and uncached product navigations share one request-start
  interval, initially one second across threads. Existing fixed navigation
  spacing and post-limit recovery spacing still apply.
- Snapshot caching is independent of fit filters and scoped to seller URL,
  category, and gender. It has a five-minute TTL, a 5,000-record budget, and a
  64-entry bound. Partial snapshots are never presented as complete inventories.
- Matching starts as API records arrive. A cached prefix can satisfy a match
  limit; otherwise collection continues. Streamed matches are not parsed or
  emitted twice, and completed counters survive metadata updates.
- Fully cached searches use neither a browser nor a network search slot, so
  they remain available during shared network cooldowns.
- HTTP recovery keeps the existing browser and cookies. It retries the failed
  cursor instead of revisiting successful pages. New jobs reuse anonymous
  browser storage in memory; user agents are not rotated.
- First-party telemetry collectors are blocked along with existing heavyweight
  assets and third-party trackers. The active product feed remains enabled.
- Native capture records status and Retry-After at the response event, then
  reads successful bodies at request completion. Reading bodies before they
  finished caused long waits in routed Firefox tests. Browser and native
  captures are merged without losing a later Retry-After header.
- Browser capture queues are drained and each unchanged API payload is decoded
  once. Closing a page releases capture state without running JavaScript.
- Permanent shop 404s no longer rebuild a browser and retry three times.

## Reproduction

```bash
.venv/bin/python backend/benchmark_scraper.py theloopmtl --max-links 72 --runs 2 --output /tmp/debot-benchmark.json
.venv/bin/python backend/benchmark_scraper.py thethriftingjesus --max-links 240 --max-matches 5 --runs 2
DEBOT_PLAYWRIGHT_TESTS=1 .venv/bin/python -m unittest backend.tests.test_scraper_playwright -v
```

The real-browser fixtures supply every HTTP response locally. They verify an
injected 429 with Retry-After 240, recovery of exactly the failed cursor, no
reloaded document, no duplicate matches, early stopping, and a zero-request
repeat. Unit coverage also checks shared pacing, expiry and memory budgets,
partial-cache misses, cookie reuse, cached execution during cooldown, and
stopping a live probe on its first blocked response.

Final combined verification ran 131 Python tests: 128 passed and three optional
external live smoke tests were skipped. This includes all five frontend browser
regressions and both scraping browser fixtures. Eight Node stream tests, lint,
the production build, and `git diff --check` passed as well. Live results above
come from separate bounded probes, not the skipped smoke suite.
