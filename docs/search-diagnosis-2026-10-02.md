# Search Diagnosis: Missing Seller Results

## Evidence

The supplied run collected 28 links from `onthemarkco` and 89 from
`topleftvintage`. Every one was skipped by the unconditional 80-day age filter.
The shop cache calculates `ageDays` from `created_at`, so these messages describe
the original listing date, not whether the seller is active or the item is sold.
An available listing can still be a useful match after 80 days.

The `newtmrw` collection received a 3,593-second retry delay. The current retry
logic extends its normal 60/180/600-second delays when `Retry-After` is longer.
A bounded live check of an affected shop returned HTTP 429 with
`Retry-After: 900`. Further product requests were stopped. The shop's HTTP 200
document response did not imply that its product API was available.

The default backend allows one active seller job. A blocked job therefore keeps
the others queued; a queued seller is not a completed search with zero matches.
The provided log does not establish why every other named seller had no results.

After the cooldown expired, the resumed `thethriftingjesus` job completed all
320 collected listings and returned 38 matches. Its saved event history confirms
that 31 matches were older than 80 days and would have been rejected by the old
default. This is a live verification beyond `refinedvtg`, not just a fixture.

## Changes

- Remove the implicit creation-age cutoff from seller and browse searches.
  `maxAgeDays` is an optional, validated API parameter. Sold and inactive product
  filtering remains in the collector.
- Show the shared cooldown deadline on queued jobs without launching browsers.
  Jobs start after the cooldown, and cancellation interrupts the wait.
- Preserve the server's full Retry-After rather than immediately retrying.
- Parse `Pit2Pit`, `PIT 2 PIT`, and `Pit-2-Pit` as chest-width labels.
- Merge the new sellers into existing saved lists once, keeping custom names,
  deduplicating usernames, and preserving deletions on subsequent loads.

## Seller Additions

Public Depop pages were researched on October 2. After the product API cooldown,
the three profiles were also checked directly in an isolated Playwright context
with every product API request blocked. All three document responses were HTTP
200 and exposed the activity labels below. These are activity snapshots, not a
guarantee of continued activity; their complete inventories were not scanned.

| Seller | Fit | Public Evidence |
| --- | --- | --- |
| `thriftsnspliffs` | Canadian vintage, streetwear, sportswear, and measured tops | [Live profile](https://www.depop.com/thriftsnspliffs/): Active this week, 874 sold. [A measured vintage tee](https://www.depop.com/products/thriftsnspliffs-vintage-racing-tshirt-pepsi/) provides numeric pit-to-pit and length labels. |
| `theloopmtl` | Canadian vintage and secondhand menswear, graphic tees, and jackets | [Live profile](https://www.depop.com/theloopmtl/): Active today, 347 sold. [A measured jacket](https://www.depop.com/products/theloopmtl-vintage-montreal-impact-bomber-jacket/) shows the same measurement format used by this app. |
| `mountainvintagethrifts` | Canada-based vintage shop | [Live profile](https://www.depop.com/mountainvintagethrifts/): Active today, 1,294 sold. |

Existing sellers were retained because an older listing does not establish that
their shops are inactive.

## Verification

- Backend discovery: 104 tests, 97 passed, seven optional browser/live checks
  skipped. The four local browser tests passed separately.
- Regression fixtures match available listings aged 338 and 385 days with the
  requested 21-inch chest width and 27-inch length ranges. An explicit 80-day
  filter still excludes them.
- Worker tests verify cooldown publication, no browser launch while blocked,
  cancellation, and automatic startup after the deadline.
- Browser tests verify the seller migration, preservation of custom names and
  deletions, cooldown display, reconnection, and mobile layout.
- Seven frontend stream tests, lint, and the production build passed.
- Live saved-job replay confirmed 320 processed listings, 38 matches, and
  completion for `thethriftingjesus` after its cooldown. Fresh match counts were
  not verified for every original seller. Recovery honored the cooldown.

Run the backend without `--reload` during searches. Its persistent jobs are
process-local, so a reload or restart loses that job registry.
