# Ontario Seller Scan

Checked November 14, 2026. Research only: the app's default seller list is unchanged.

## Updated Filter: More Than 100 Sold

The user subsequently required **more than 100 sold listings**. The earlier Wave Two, Curated Grails, Quest Pony, and Third Leg recommendations/watchlist entries below do not qualify under this new requirement.

A second bounded Playwright check covered Vintage by Sherman, Second Life Vintage, Wetsausages, and Snakes and Robbers. Each received one products request with at most 24 sampled tops; no 403/429 was observed. Sold counts below are live **Depop** profile totals, not followers, reviews, current inventory counts, or combined sales across marketplaces.

| Seller | Sold | Activity | Sampled Tops With Width + Length | Current CAD Prices | Location Evidence |
| --- | --- | --- | --- | --- | --- |
| [Snakes and Robbers: @snakesandrobbers](https://www.depop.com/snakesandrobbers/) | 5,136 | This week | 24/24 | Median C$57.50; full range C$35-575; 13/24 at C$35-60 | Their own [website](https://snakesvintage.com/) states Toronto, Canada and explicitly links the same Depop handle. This resolves the location uncertainty in the first scan. |
| [Vintage by Sherman: @vintagebysherman](https://www.depop.com/vintagebysherman/) | 110 | This week | 24/24 | Median C$23; full range C$18-50 | Depop bio says Burlington; sampled listings are CAD. Ontario is a reasonable inference from the Canadian context, but the bio does not spell out the province. Confirm dispatch location if strictly Ontario-only. |
| [Second Life Vintage: @secondlifeinc](https://www.depop.com/secondlifeinc/) | 590 | Today | 24/24 | Median C$47.50; full range C$29.75-155; 16/24 at or below C$60 | Official [company story](https://secondlifevtg.com/pages/our-story) states founded in Brampton, Ontario and links this Depop account. Some eBay listings use US item locations; Ontario dispatch for current orders is **not established**. |

### Ranking Under the New Requirement

1. **Snakes and Robbers** is the strongest fully Ontario-supported, established-seller match. Vintage graphic/single-stitch tees, sportswear, band pieces, explicit fabric information, and individual flaw disclosures. Sampled items have 5-8 photos. Premium collectors' pieces are much more expensive; use a price ceiling. The [website](https://snakesvintage.com/) now supplies location evidence absent from the first scan.
2. **Vintage by Sherman** is the best price match and clears the sales threshold, though only just. Sports/graphic tees, Nike, Adidas, Reebok, Roots, and sweatshirts; consistent labelled width/length template; 5-8 photos per sampled item. Bio advertises C$10-20 shipping with quotes by message. Confirm Ontario rather than treating the unqualified city name as independent province verification.
3. **Second Life Vintage** is a promising conditional option: wider vintage/streetwear selection, numeric chest/length template, condition grading, and 7-8 photos per sampled item. The official site describes measurement and inspection practices. Depop prices in this sample are CAD even though the standalone website can display USD. Confirm the parcel's actual origin before treating it as local Ontario inventory.

One sample from Sherman fits the earlier measurement window: [Pittsburgh Steelers Got Rings tee](https://www.depop.com/products/vintagebysherman-vintage-y2k-pittsburgh-steelers-got/), C$23, 21.5 x 28 inches. This is current listing evidence, not a promise of availability at checkout.

Snakes and Robbers example: [Benetton Eye Chart tee](https://www.depop.com/products/snakesanbrobbers-90s-vintage-benetton-eye-chart-8e94/), C$45, 21 x 27 inches. The seller discloses a shoulder stain, tiny tag hole, and some stretching; do not equate vintage curation with pristine condition.

### Above 100 Sold, but Not Recommended for This Search

- [@wetsausages](https://www.depop.com/wetsausages/): Toronto bio, 104 sold, active this week, but the filtered men's-tops response returned zero products. Relevant current inventory/measurement coverage is not established.
- [@eleventhvintage](https://www.depop.com/eleventhvintage/): Toronto, 110 sold, but the earlier check returned only one relevant C$70.20 top without numeric measurements.
- [@slothshack](https://www.depop.com/slothshack/): 283 sold, but older activity, zero returned men's tops, and southern Ontario sourcing rather than confirmed seller location.

## Original Scan

## Scope and Method

- Compared the existing 27-account default list, particularly RefinedVTG, Thrift Jesus, and Best Choice Vintage, for vintage/Y2K tees, sportswear, knits, and workwear.
- Used web discovery followed by read-only Playwright checks of ten candidate profiles and RefinedVTG as a price/measurement baseline.
- Allowed at most one shop-products response per seller, with a maximum of 24 products. No inventory pagination, product-detail browsing, or retry loops. No 403/429 was observed in these checks; this does not establish a safe sustained scraping rate.
- Requested men's tops, sorted recent. Counts describe the returned available product sample, not the whole shop or a random sample. An empty filtered response does not establish an empty shop.
- Prices are current displayed asking prices, including discounts, before shipping and tax. Different item mixes and sale discounts limit comparisons.
- Measurement coverage means numeric garment width and length in the descriptions, manually checked for shortlisted sellers. It does not validate the accuracy of the seller's tape measurements or the app's extraction.
- Activity is the live profile label at the time of the check, not a guarantee of regular new uploads or fast dispatch. Location is seller-stated, not independently authenticated. Local pickup is not assumed.
- Quality judgments are provisional: material/brand details, explicit flaw disclosure, multiple listing photos, and transaction history are useful signals, not authentication or an in-person condition inspection.

## Earlier Candidates: Below New Sales Threshold

### 1. Wave Two Vintage: @wavetwovtgg

[Depop profile](https://www.depop.com/wavetwovtgg/)

- Location: Ottawa, Ontario, stated in the profile and listing descriptions.
- Activity: active this week; 32 sold and 7 reviews.
- Measurements: 24/24 sampled tops have numeric pit-to-pit and top-to-bottom measurements.
- Price: median C$40; 22/24 sampled items cost C$25-60. Full sample range C$25-220, including a C$90 hoodie and C$220 designer jumper.
- Style: Polo Ralph Lauren, Nike/Champion sweatshirts, Oakley, Exco, sportswear, and graphic/band tees. Closest everyday-value fit among the active, Ontario-confirmed candidates.
- Quality signals: 4-8 photos per sampled item; specific pinholes, marks, cracking, and affected photo numbers are disclosed on applicable listings.
- Shipping: listings state a C$15 Canada-wide flat rate. A C$40 purchase therefore starts at C$55 delivered before tax; Ontario location alone does not make postage cheap.
- Caveat: a small transaction/review history; higher median price than RefinedVTG's current sale sample.

Examples:

| Item | Asking Price | Width x Length, Inches | Source |
| --- | --- | --- | --- |
| Y2K Nike grey crewneck | C$40 | 21 x 26.5 | [Listing](https://www.depop.com/products/wavexwovtgg-y2k-distressed-nike-grey-crewneck-d6c4/) |
| Polo Ralph Lauren linen button-up | C$30 | 20 x 29 | [Listing](https://www.depop.com/products/wavetwcvtgg-polo-ralph-lauren-baby-blue-b7ba/) |
| Oakley navy hoodie | C$40 | 22 x 28 | [Listing](https://www.depop.com/products/wavetwoetgg-oakley-navy-poison-skull-hoodie-4837/) |

The Nike crewneck falls inside the earlier search's 21 +/- 0.75 inch width and 27 +/- 0.75 inch length window. Its title says distressed; inspect the photos even though the description reports no noted flaws.

### 2. Curated Grails: @curatedgrailss

[Depop profile](https://www.depop.com/curatedgrailss/)

- Location: Ottawa, Ontario, explicitly stated in the live profile.
- Activity: active today; 30 sold and 5 reviews.
- Measurements: 24/24 sampled tops provide numeric width and length. Most specify pit-to-pit; one jersey uses generic width. A signed jersey provides separate front/back lengths.
- Price: median C$60; 14/24 sampled items cost C$30-60. Full range C$30-400. Collector jerseys and designer/rare tees make this a selective budget fit, not an entirely inexpensive shop.
- Style: particularly strong overlap with band tees, Harley-Davidson, vintage hockey/sports tees, jerseys, and streetwear.
- Quality signals: 5-8 photos per sampled item and specific disclosures for collar wear, marks, snags, holes, and cropped hems where applicable.
- Caveat: a small transaction/review history. Shipping cost was not verified. Seller authenticity claims and memorabilia authentication were not independently checked.

Examples:

| Item | Asking Price | Width x Length, Inches | Source |
| --- | --- | --- | --- |
| 90s Toronto Maple Leafs tee | C$40 | 21 x 27.5 | [Listing](https://www.depop.com/products/curdtedgrailss-vintage-90s-toronto-maple-leafs-292f/) |
| Dream Theater 2001 band tee | C$60 | 21 x 27 | [Listing](https://www.depop.com/products/curatedgrailws-vintage-dream-theater-2001-metropolis-d3ed/) |
| Patagonia Uprisal crewneck | C$50 | 23 x 27 | [Listing](https://www.depop.com/products/curatedgraylss-atagonia-uprisal-logo-crewneck-sweatshirt-bb52/) |

The first two fall inside the earlier search's measurement window. The Leafs tee discloses light back-collar wear.

## Toronto Watchlist

These fit the earlier location and stock criteria, but have fewer than 100 sold and do not qualify for the updated shortlist.

| Seller | Live Activity | Measurement Coverage | Observed Prices | Assessment |
| --- | --- | --- | --- | --- |
| [@questponyvintage](https://www.depop.com/questponyvintage/) | Active over a week ago | 10/10 width + length | C$18.70-33.15; median C$24.65 | Best budget match. Toronto bio; 10 sold, 2 reviews; souvenir/biker/graphic tees and Tommy rugby. Confirm the seller is still fulfilling orders before purchasing. |
| [@thirdlegvintage_co](https://www.depop.com/thirdlegvintage_co/) | Active over a week ago | 23/24 width + shoulder-to-bottom | **US$20-90**; median **US$30** | Toronto bio; 26 sold, 7 reviews. Good vintage sports/souvenir stock and flaw disclosures. USD pricing must not be compared directly with CAD. |

Quest Pony example: [Renegades graphic tee](https://www.depop.com/products/questpony-double-sided-distressed-renegades-graphic/), C$20, 21 x 27 inches. Fits the previous measurement window, but paint marks, fading, and discoloration are disclosed. Not a pristine-condition recommendation.

Third Leg example: [Clearwater Beach tee](https://www.depop.com/products/thirdlegvintage_co-clearwater-beach-florida-vintage-sunshine/), US$25, 21 x 27.5 inches.

## Other Candidates Checked

| Seller | Finding | Why Not a Main Recommendation |
| --- | --- | --- |
| [@snakesandrobbers](https://www.depop.com/snakesandrobbers/) | Active this week; 5,136 sold, 1,757 reviews; 24/24 measured tops; median C$57.50, range C$35-575 | Initially held for missing Ontario evidence; subsequently confirmed Toronto through their own website and promoted to the updated shortlist above. |
| [@eleventhvintage](https://www.depop.com/eleventhvintage/) | Toronto; active this week; one returned men's top at C$70.20, without numeric measurements | Too little relevant measured inventory in this filtered check; more designer-focused. Not a whole-shop measurement claim. |
| [@newishyyc](https://www.depop.com/newishyyc/) | Toronto despite the YYC handle; active this week; one returned men's top at C$65, without numeric measurements | Thin relevant sample and missing measurements on that item. |
| [@secondsoulvintage](https://www.depop.com/secondsoulvintage/) | Toronto; GTA meetups advertised; no activity label captured; zero returned men's tops | Could suit other categories, but no evidence of relevant current inventory from this check. |
| [@slothshack](https://www.depop.com/slothshack/) | Active over a week ago; bio says stock sourced from southern Ontario; zero returned men's tops | Sourcing region does not prove shipping origin; activity and inventory fit were weak. |
| [@d3ad_grails](https://www.depop.com/d3ad_grails/) | Indexed Ontario bio, but live profile returned 404 | Do not add an account based on stale search results. |

Also considered through indexed listings: [Lost Boys Vintage](https://www.depop.com/lostboysvintage/) is Toronto-based, but inspected examples were C$72-189, and one had blank measurement placeholders. It is not a close value/consistency match on that evidence.

## Price Baseline

[RefinedVTG](https://www.depop.com/refinedvtg/) was active today, with 2,190 sold. Its 24-product sample had 24/24 measured tops, a current median of **C$26.33**, and a C$5.21-225 range. This includes sale prices and is not a full-shop price index.

Wave Two is broadly affordable but more expensive than that sale baseline. Curated Grails is worth searching with a price ceiling rather than treating all inventory as comparably priced. Quest Pony is the closest sampled price match, with weaker activity evidence.

## App Compatibility Note

Description coverage is not the same as successful app extraction. This scan exposed two cases worth a separate parser regression fix before relying on all candidates in automated fit searches:

- Curated Grails writes measurements like `27 Inches Pit To Pit x 32 Inches Length`; the current parser returned width 32 instead of 27 in the inspected example. Other inline pairs showed the same wrong-width behavior.
- Third Leg's `Shoulder to Bottom` label was missed in many descriptions despite a numeric length being present. Only 5/24 samples returned two non-null parser fields, whereas manual description review found two measurements in 23/24.

No parser or seller-list changes were made as part of this research request.
