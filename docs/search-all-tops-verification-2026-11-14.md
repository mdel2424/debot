# All-Tops Live Verification

Ran the configured 26-seller list through the running backend's batch/stream API.
The live browser integration was unavailable, so this was not a click-through of
the user's existing browser tab. Used the configured tops defaults: male,
tops plus coats/jackets, P2P 21 +/- 0.75 inches, length 27 +/- 1 inch, up to
40 matches and 1,000 collected links per seller. Removed accounts were excluded.

## Findings and Fixes

- Empty shop/category pages displayed 12 recommendations belonging to other
  sellers. DOM collection incorrectly mixed them into authoritative shop API
  results and persisted them. Shop API inventory now takes precedence, even
  when empty. Contaminated cached scopes are discarded. Seller searches and
  frontend result restoration/ingestion reject known different-seller items.
- A live measurement format, `24' inch pit to pit`, failed width extraction.
  The existing partial-measurement policy then admitted oversized garments on
  length alone. Quoted units, full inch units before labels, short `pit`, and
  shoulder-to-bottom length labels now parse correctly.
- `5outvintage` returned HTTP 404. It remains in the configured list; no account
  was removed automatically.

## Results

The initial run completed in 300 seconds: 25 successful seller searches and one
404, with no reported rate-limit pause. A second live verification used the
recommendation fix in an isolated backend and a copy of the daily cache. It
completed in 110 seconds, again with 25 successful searches and the same 404.
This is mixed cached/live timing, not a cold-search performance benchmark.

After correcting measurement extraction, reprocessed the captured inventory
offline without another full fetch: **410 results**, no wrong-seller results,
no duplicate URLs within a seller, and every available parsed measurement inside
the requested tolerance. **Two results have one genuinely absent measurement**;
the existing partial-measurement matching policy was not changed. Thus 408
results have both dimensions verified. Larger shops stopped at the configured
40-match limit; this does not claim every listing in every shop was parsed.

| Seller | Results |
| --- | ---: |
| onthemarkco | 2 |
| reducereuseclothes | 0 |
| topleftvintage | 7 |
| refinedvtg | 40 |
| newtmrw | 40 |
| asknr | 8 |
| heavyvintage | 20 |
| iheartvintageco | 0 |
| vtgthriftshit | 12 |
| shittyvintageplug | 40 |
| thethriftingjesus | 35 |
| 5outvintage | HTTP 404 |
| waybackco | 13 |
| rutlandretros | 0 |
| flashyfashion | 27 |
| vintagequebec_ | 19 |
| bestchoicevintage | 40 |
| y2ktodayy | 7 |
| zedsvintage | 2 |
| dead_threads00 | 0 |
| ryan_is_garments | 0 |
| thriftsnspliffs | 40 |
| theloopmtl | 11 |
| mountainvintagethrifts | 2 |
| secondlifeinc | 30 |
| vintagebysherman | 15 |

Full audited item URLs, prices, measurements and per-seller outcomes are saved
locally in `backend/.cache/search-all-tops-audited-results.json` (git-ignored).
These runs populated inventory caches, not browser localStorage. The user's
original non-reloading backend was left running; restart it and refresh the
frontend to apply the code fixes to normal searches.
