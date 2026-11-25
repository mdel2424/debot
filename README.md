# Debot

Search public Depop seller listings by clothing measurements or shoe size.
The React workspace saves sellers, filters, and results locally. Seller searches
continue in the Python backend when the browser tab closes.

## Setup

Use Python 3.13 (see `.python-version`) and Node 22.12+ or Node 20.19+.
From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m playwright install firefox
.venv/bin/python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open the URL printed by Vite, normally <http://127.0.0.1:5173>.
Vite proxies `/api` to the backend. For a separate API origin, set
`VITE_API_BASE` and the backend's `DEBOT_ALLOWED_ORIGINS`.
A production frontend needs an `/api` reverse proxy or an explicit API base.

## Search Behavior

- **Tops > Retailers** searches up to 36 T-shirts per store with the same fit and
  CAD price filters. Listings need an individual photo and both listed
  measurements. Prices are before shipping and tax.
- People's Thrift uses its public product sitemap. Only after obtaining written
  permission, enable Wildrose Vintage or La Penderie du Paradis with
  `DEBOT_AUTHORIZED_RETAIL_SOURCES=wildrose,la-penderie` (include only stores that
  granted permission). Retail searches respect robots.txt, cache recent responses,
  and stop on access blocks.

- Shop collection reads the current `objects` / `page_info` responses and follows
  the supplied cursor. DOM scrolling is the fallback. Failed requests surface as
  errors rather than successful partial scans.
- Complete details already returned by the shop are cached, avoiding individual
  product requests. Missing details use a bounded parallel Playwright pool.
- Matches stream while shop pages are collected. Once the requested match
  count is reached, remaining pages and categories are not fetched.
- Shop inventory is saved in `backend/.cache/shops.sqlite3` for the backend's
  local calendar day, including across backend restarts. Reruns within five
  minutes reuse it without network requests. Later reruns refresh newest pages,
  update observed prices/descriptions/sold status, and merge the cached tail.
  Refresh stops after two consecutive fully known pages when the previous scan
  covered the full inventory; incomplete scans continue as needed. Changes deep
  in older stock may not be seen until the next day's fresh scan, subject to the
  configured search limits. Cache capacity
  eviction can also require a fresh scan. All rate-limit cooldowns still apply.
- Same-day reruns with unchanged filters retain and update earlier UI matches,
  deduplicated by listing URL. Changed filters or a new calendar day start a new
  result set; explicitly observed sold/nonmatching items are removed. Cached
  searches can finish during a network cooldown while their snapshot is fresh.
- The UI collects up to 1,000 links and returns up to 40 matches per seller.
  API callers can set `maxLinks` and `maxItems` up to 10,000. Collection has an
  additional 200-page safety bound.
- Available listings are searched regardless of their original creation date.
  API callers can opt into an age filter with `maxAgeDays`; it is disabled by
  default because an older listing is not evidence of an inactive seller.
- HTTP 403/429 triggers shared 60, 180, and 600-second cooldowns, extended by
  `Retry-After`. Final exhaustion remains an error and preserves the shared pause.
  Queued sellers show the shared cooldown and start automatically when it ends.
- Cursor requests and uncached product navigations share one-second start
  spacing. Recovery keeps cookies and retries the failed cursor; successful
  pages are retained. Anonymous browser storage is reused between jobs.
- Closing the browser detaches seller searches. Reopening restores saved job IDs
  and replays results. Stop explicitly cancels active or queued jobs.
- New curated seller additions are merged into saved lists once. Custom names,
  existing sellers, and subsequent removals are preserved.
- Jobs are process-local. Keep one backend process running and avoid `--reload`
  during searches. A restart loses in-memory jobs; a saved request can start
  again on reconnect. The legacy following-account API remains connection-owned.

## Configuration

| Environment Variable | Default | Purpose |
| --- | --- | --- |
| `DEBOT_PARSE_WORKERS` | `4` | Default parser workers, clamped to 1-6 |
| `DEBOT_CONCURRENT_SEARCH_JOBS` | `1` | Concurrent seller jobs |
| `DEBOT_MIN_NAV_INTERVAL_SECONDS` | `0` | Optional fixed request spacing |
| `DEBOT_REQUEST_INTERVAL_SECONDS` | `1` | Shared spacing for cursor requests and uncached product navigations |
| `DEBOT_RATE_LIMIT_NAV_INTERVAL_SECONDS` | `1` | Temporary recovery spacing |
| `DEBOT_LISTING_CACHE_TTL_SECONDS` | `300` | Listing cache lifetime |
| `DEBOT_LISTING_CACHE_MAX_ITEMS` | `1000` | Listing cache capacity |
| `DEBOT_SHOP_CACHE_TTL_SECONDS` | `300` | Time before checking for inventory updates; same-day data is retained; 0 disables reuse |
| `DEBOT_SHOP_CACHE_MAX_ITEMS` | `50000` | Total detail records retained in daily shop snapshots |
| `DEBOT_SHOP_CACHE_MAX_ENTRIES` | `256` | Seller/category/gender snapshots retained within the item budget |
| `DEBOT_SHOP_CACHE_PATH` | `backend/.cache/shops.sqlite3` | Daily inventory database; empty disables disk persistence |
| `DEBOT_SEARCH_JOB_HISTORY` | `500` | Retained jobs; completed jobs pruned first |
| `DEBOT_ALLOWED_ORIGINS` | Localhost Vite origins | Allowed browser origins |

## Verification

From the repository root:

```bash
.venv/bin/python -m unittest discover -s backend/tests
```

From `frontend/`:

```bash
npm test
npm run lint
npm run build
```

Browser persistence tests use local API fixtures. Start Vite, then run from the root:

```bash
DEBOT_FRONTEND_URL=http://127.0.0.1:5173 .venv/bin/python -m unittest backend.tests.test_frontend_smoke
```

Real Firefox scraping tests with locally fulfilled responses, including an
injected 429 and cursor recovery:

```bash
DEBOT_PLAYWRIGHT_TESTS=1 .venv/bin/python -m unittest backend.tests.test_scraper_playwright
```

Bounded live request and parsing benchmark:

```bash
.venv/bin/python backend/benchmark_scraper.py theloopmtl --max-links 72 --runs 2
```

The benchmark stops on the first 403/429, reports Retry-After, and does not run
automatic cooldown retries. It caps a run at 240 links and a probe at three
runs. Its deadline is cancelable; an already-running navigation may take until
its Playwright timeout to return. Request counts include locally blocked
telemetry; response counts show completed first-party data responses.

Opt-in live site checks:

```bash
DEPOP_LIVE_SMOKE=1 .venv/bin/python -m unittest backend.tests.test_live_depop_smoke
```

Live availability and throttling depend on Depop. Offline regressions exercise
blocked pages, partial-page failures, retries, and cancellation deterministically.
See [the code review](docs/code-review.md) for findings and coverage.
See [the October search diagnosis](docs/search-diagnosis-2026-10-02.md) for the
listing-age fix, cooldown behavior, and new seller sources.
See [the November benchmark](docs/rate-limit-benchmark-2026-11-14.md) for measured
request reductions, early results, and recovery tests.
