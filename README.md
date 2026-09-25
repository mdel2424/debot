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

- Shop collection reads the current `objects` / `page_info` responses and follows
  the supplied cursor. DOM scrolling is the fallback. Failed requests surface as
  errors rather than successful partial scans.
- Complete details already returned by the shop are cached, avoiding individual
  product requests. Missing details use a bounded parallel Playwright pool.
- The UI collects up to 1,000 links and returns up to 40 matches per seller.
  API callers can set `maxLinks` and `maxItems` up to 10,000. Collection has an
  additional 200-page safety bound.
- Listings older than 80 days are filtered individually; an old item does not
  stop the rest of the scan.
- HTTP 403/429 triggers shared 60, 180, and 600-second cooldowns, extended by
  `Retry-After`. Final exhaustion remains an error and preserves the shared pause.
- Closing the browser detaches seller searches. Reopening restores saved job IDs
  and replays results. Stop explicitly cancels active or queued jobs.
- Jobs are process-local. Keep one backend process running and avoid `--reload`
  during searches. A restart loses in-memory jobs; a saved request can start
  again on reconnect. The legacy following-account API remains connection-owned.

## Configuration

| Environment Variable | Default | Purpose |
| --- | --- | --- |
| `DEBOT_PARSE_WORKERS` | `4` | Default parser workers, clamped to 1-6 |
| `DEBOT_CONCURRENT_SEARCH_JOBS` | `1` | Concurrent seller jobs |
| `DEBOT_MIN_NAV_INTERVAL_SECONDS` | `0` | Optional fixed request spacing |
| `DEBOT_RATE_LIMIT_NAV_INTERVAL_SECONDS` | `1` | Temporary recovery spacing |
| `DEBOT_LISTING_CACHE_TTL_SECONDS` | `300` | Listing cache lifetime |
| `DEBOT_LISTING_CACHE_MAX_ITEMS` | `1000` | Listing cache capacity |
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

Opt-in live site checks:

```bash
DEPOP_LIVE_SMOKE=1 .venv/bin/python -m unittest backend.tests.test_live_depop_smoke
```

Live availability and throttling depend on Depop. Offline regressions exercise
blocked pages, partial-page failures, retries, and cancellation deterministically.
See [the code review](docs/code-review.md) for findings and coverage.
