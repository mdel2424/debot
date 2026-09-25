# Code Review: Listing Completeness and Recovery

Reviewed scraping, API orchestration, the parallel worker pool, the job registry,
measurement parsing, React state and rendering, SSE handling, configuration,
dependencies, documentation, and existing tests.

## Findings Fixed

| Priority | Finding | Resolution |
| --- | --- | --- |
| P1 | Current shop responses use `objects` and `page_info`, which were ignored. Scroll budgets could truncate a growing shop. | Read the observed schema, follow its cursor, continue growing DOM collections, and reject repeated cursors or exhausted safety budgets. |
| P1 | Captured 403/429/5xx responses were discarded after partial success. Listing 429s lost Retry-After; navigation failures became missing items. | Preserve errors and retry hints, surface incomplete scans, and use bounded retries. Only actual 404/410 product pages are skipped as missing. |
| P1 | Capture buffers used reusable object IDs and survived category navigation. Existing URLs consumed new-link capacity. | Use weak page references, clear captures on navigation, canonicalize links, exclude previously seen URLs from quotas, and omit sold feeds/purchased items. |
| P1 | The first old listing stopped a category even if newer items followed. | Filter age per item and continue scanning. |
| P1 | Parallel prefetch could consume the whole shop behind a slow first result. Batch cancellation did not interrupt in-flight cooldowns. | Bound submissions by consumed results and pass batch cancellation into workers. |
| P1 | Result streams occupied browser HTTP slots and could starve Stop requests. Immediate closure could lose saved job IDs. | Limit subscriptions to three, register batches before attaching streams, and save job IDs before requests start. |
| P2 | Mixed errors shared one retry counter; the final 429 did not extend the global cooldown. | Separate transient and rate-limit retry budgets; retain cooldown protection after exhaustion. |
| P2 | SSE framing could drop the first event. Permanent HTTP errors retried indefinitely. Client retries reused completed IDs. | Parse SSE data lines and CRLF, terminate rejected requests, release readers, distinguish cancellation, and use new IDs for new attempts. |
| P2 | Bad payloads could allocate jobs that never finished. Queued cancellation waited for an active search to finish. | Validate before allocation, validate batches before dispatch, reuse saved jobs immediately, and make slot waits cancelable. |
| P2 | Sold-count retries invalidated the caller's browser pages. Partially loaded listings could be cached. | Replace only the profile tab and require descriptions after structured-data fallback. |
| P2 | Millimeters, W/L pairs, shared pair units, decimal waists, and explicit UK shoe sizes were mishandled. | Correct conversions and named groups; reject non-US labels in the US filter. |
| P2 | Following-search and SSE implementations diverged. Setup docs referenced missing launchers and an unsupported Node version. | Reuse the seller pipeline and decoder, correct setup docs, remove the unused `24` package, and pin verified Python dependencies. |
| P2 | Dependency audits flagged the frontend lockfile and installed Python transitives. | Apply compatible npm security updates and pin fixed Starlette, AnyIO, Click, and IDNA versions. Both advisory scans are now clear. |

## Regression Coverage

- Scraper fixtures reproduce the current response schema, static 24-item grids
  with 360 available listings, repeated cursors, canonical duplicates, category
  changes, sold feeds, and rate limiting after initial success.
- Retry tests cover 60/180/600 cooldowns, Retry-After, independent transient
  retries, final cooldown extension, and cancellation before retry.
- Parallel tests exercise concurrent execution, bounded prefetch behind a slow
  result, batch cancellation during a wait, and startup resource cleanup.
- Job tests cover invalid payloads without orphan jobs, batch validation,
  saved-payload reuse, queued cancellation, disconnect, and event replay.
- Node tests cover chunked SSE, CRLF/comments, unexpected EOF, stable IDs,
  permanent errors, connection budgets, failed cancellation, and following EOF.
- Playwright tests close and reopen a workspace, verify deduplicated replay
  with the same job ID, check persistence before batch dispatch, and check mobile
  horizontal overflow.

## Verified Results

- Backend discovery: 96 tests, 91 passed and five opt-in checks skipped.
  All five opt-in checks also passed separately: two local browser regressions
  and three live Depop smoke tests.
- Frontend: seven stream tests passed; lint and the production build passed.
  Browser regressions also passed after the frontend security updates.
- Live shop probe: 216 unique listings and 216 cached detail records in 7.15
  seconds. Nine 24-item pages were followed by an empty terminal page with
  `page_info.has_more: false`. No individual product-page requests were needed
  for those details. This is one observed run, not a general timing guarantee.
- The live browse check collected beyond 48 links and parsed a listing; the
  seller smoke check collected 72 links with a one-scroll hint.
- `npm audit`, the installed-environment `pip-audit`, and `pip check` are clean.
  The real Vite-to-FastAPI proxy accepted an empty batch and rejected an invalid
  seller payload with HTTP 422. Backend startup and `git diff --check` passed.

Primary implementation locations: [scraper](../backend/scraper.py),
[orchestration](../backend/main.py), [worker pool](../backend/listing_pool.py),
[request validation](../backend/search_models.py),
[stream client](../frontend/src/hooks/useStream.js), and
[workspace persistence](../frontend/src/App.jsx).

## Operational Limits

Live tests verify the current public site, not a guarantee against future schema
changes or blocking. Server cooldowns are honored and retry exhaustion is an error.
Configured match/link limits still apply. Job history is in memory: keep one
backend process running. The legacy following-account endpoint remains tied to
its HTTP connection; normal workspace seller searches use persistent jobs.
The API is intended for local use and has no authentication; keep it bound to
localhost unless authentication is added at a trusted reverse proxy.
