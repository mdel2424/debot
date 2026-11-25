# Compliant inventory monitoring

Checked November 25, 2026. **A readable page, sitemap, Shopify variant record, proxy or shopping account is not permission to automate a supplier.** The supplier table captures observed technical signals and labels missing permissions, feeds, quotas and timestamps as unknown. No crawling adapter, login, signup or transaction was attempted.

## Integration priority

| Order | Source / method | Machine-readable evidence | Permission and complexity |
|---:|---|---|---|
| 1 | **Local partner CSV / manual upload** | Stable own SKU, actual pictures, stock and measurements or carton manifest as agreed. Start with ItsADeal/People's Thrift for singles and a sorter for bulk. | Low; ask for fields and license. Data voluntarily delivered to you is preferable to site polling. Confirm image reuse rights. |
| 2 | **[eBay Browse API](https://developer.ebay.com/develop/api/buy/browse_api/item/search)** | Search, price, condition, seller, item ID and marketplace options. Fetch selected item detail; dimensions only if seller provides them. | Medium; [production approval and licensing](https://developer.ebay.com/api-docs/buy/static/buy-requirements.html) required for intended use. Check [marketplace support](https://developer.ebay.com/api-docs/buy/static/ref-marketplace-supported.html) and [quota](https://www.developer.ebay.com/develop/api/buy/api_call_limits). Docs list 5,000 Browse calls/day for most methods, not a polling target. |
| 3 | **Domestic merchant export / approved storefront** | Category and price filters at [ItsADeal](https://itsadeal.ca/collections/mens-tops) and [La Penderie](https://lapenderieduparadis.com/collections/homme-friperie-en-ligne). [People's Thrift](https://www.peoplesthrift.ca/product-page/pop-man-men-s-graphic-print-t-shirt-m) shows SKU, stock and Size & Fit. | Low-medium if a feed exists. Merchant consent first. [Shopify Storefront docs](https://shopify.dev/docs/api/storefront/latest) cover access modes; having a Shopify store does not authorize third-party access. |
| 4 | **Detauri / authorized inventory import** | [Official guide](https://wholesale.nkonline-tool.com/guide) describes item photo and measurement data with individual purchases and grading. Public catalogue format and data rights before purchase not verified. | Medium; ask if they can publish a SKU feed *before* purchase. Validate men's tee assortment and Canada/proxy delivery first. |
| 5 | **Lot notifications / partner feeds** | [Raghouse](https://raghouse.com/collections/t-shirts/products/graphic-sports-t-shirts-unsorted-71-pcs) shows SKU, variant ID, price, weight and availability. [ShopGoodwill](https://shopgoodwill.com/help/faqdetail/why-cant-i-combine-some-items-for-shipping) has listing IDs, deadlines and seller locations. | Low for native notifications or manual ingestion; medium for feed negotiation. **Raghouse prohibits scraping/data extraction** in its [terms](https://raghouse.com/policies/terms-of-service). Use approved export only. |

### Five measurement-based automation candidates

1. **ItsADeal** (feasibility **5/10**, pending permission): item-specific chest and length on three sampled tee links. Normalize chest as half width only if merchant confirms circumference and method. Track SKU and stock in approved export.
2. **People's Thrift** (**5/10**, pending permission): normalize Size & Fit and condition. Ask for separate width, length and listing timestamp. Wix-style paths do not imply an available buyer API.
3. **Detauri** (**6/10**, conditional): strong fit for an export partnership with item photos and dimensions. Ask for pre-purchase data access, CSV/TSV format, individual listing IDs and stock transitions.
4. **2nd Street Japan** (**5/10**, conditional): item/store IDs, size fields and [measurement definitions](https://www.2ndstreet.jp/guide/buy/onlinestore/paypay) make good parser fixtures. No authorized search API verified; start with manual links.
5. **RAGTAG** (**5/10**, conditional): rendered dimensions, store stock, materials and sold status. Good QA source when licensed, not proof of low cost. The black XL example is 54 × 68.5 cm and ¥1,600 before logistics; verify measurement tolerance and landed price.

### Other technical checks

| Source | Observed | Decision |
|---|---|---|
| Raghouse | Embedded Shopify variant JSON includes SKU, weight, price, stock and ID; not proof of JSON-LD. robots fetch unavailable. | Manual browsing, authorized files or vendor notifications. |
| KOOP / Black Cat Ko. | KOOP exposes an [HTML sitemap](https://ilovekoop.com/pages/avada-sitemap). Black Cat has category filters. robots retrieval unavailable for both. | Ask before monitoring; HTML navigation is not XML sitemap permission or an inventory feed. |
| Mercari Japan | Product IDs, descriptions and sold status. [robots](https://jp.mercari.com/robots.txt) excludes account, purchase, sell and transaction routes, among others. [Global policy](https://gl.help.jp.mercari.com/en/guide/articles/gl_0053/) restricts outside commercial use without written consent. | Manual search/approved proxy. No unattended catalogue access recommended. |
| Yahoo Japan Auctions / ZOZOUSED | Listing IDs, keyword/category pages and item dimensions. Auction and sold state can change. | Native alerts or licensed integration only; no API eligibility claimed. |
| Hard Off Netmall | Store filters and pagination observed. | Browse by branch for potential shipping savings. |
| Poshmark Canada | [Rules](https://poshmark.ca/terms) prohibit automated collection. | Manual bundles and native alerts only. |
| ShopTheSalvationArmy | Search index exposed lot records; direct requests returned 403. | Stop direct automation research there; use native tools if eligible. |
| Shopify stores generally | Readable collections, some embedded metadata, Shopify hints. Third-party inventory CSV, RSS, JSON-LD coverage, XML sitemaps, timestamps and request limits generally **not verified**. | Merchant agreements or exports. See [robots guidance](https://developers.google.com/search/docs/crawling-indexing/robots/intro); obey both site policy and applicable restrictions. |

Do not bypass authentication, CAPTCHAs, blocks or request limits. Back off on 429 using the provided Retry-After, stop on access objections or policy change, and retry only explicit transient transport errors. Keep an allowlist with permission date, terms URL, approved API key if applicable, quota and owner. For authorized integrations, prefer conditional requests or incremental cursors. Store immutable IDs plus last seen, validated updates, sale/tombstone events and a review queue. No auto-checkout without independent platform permission and explicit spending authorization; first surface a revalidated quote and availability.

## Normalizing fit

Store raw text, locale, listing ID, value, unit, dimension type, method, source and confidence. Save separate fields for flat chest, chest circumference, body length, shoulder width, sleeve length and tagged size. Record flat versus stretched, and whether length begins at high shoulder, neck seam, front or back. Sleeve may start at shoulder or collar on raglan styles. Don't combine unlike methods.

| Locale | Flat chest | Length | Shoulders | Sleeve | Ambiguities |
|---|---|---|---|---|---|
| English | pit-to-pit, P2P, flat chest, underarm to underarm | body length, shoulder-to-hem, back length | shoulder width, seam-to-seam | sleeve length | plain “chest” may be circumference or a human size chart |
| French | aisselle à aisselle, largeur poitrine, à plat | longueur du vêtement, épaule à l'ourlet | largeur d'épaules | longueur de manche | tour de poitrine is circumference; taille may be tag or waist |
| Japanese | 身幅, 脇下, 平置き実寸 | 着丈, 身丈, 総丈 | 肩幅 | 袖丈, 裄丈 for neck-to-wrist | 胸囲 usually circumference; 着丈 and 身丈 may use different starts |
| Korean | 가슴단면, 실측, 가슴 너비 | 총장, 기장 | 어깨, 어깨너비 | 소매길이, 팔길이 | 단면 is flat; 둘레 is circumference |
| Traditional Chinese | 胸寬, 胸寬平量, 腋下 | 衣長, 前衣長, 後衣長 | 肩寬 | 袖長 | 胸圍 may be circumference; do not halve 胸寬 |
| German / Spanish | Brustweite einfach, Achsel zu Achsel / ancho de pecho en plano | Rückenlänge / largo de la prenda | Schulterbreite / ancho de hombros | Ärmellänge / largo de manga | Brustumfang / contorno de pecho are circumferences |
| Thai / Malay | อกวัดราบ / lebar dada, ukur rata | ความยาวเสื้อ / panjang baju | ไหล่ / lebar bahu | แขนยาว / panjang lengan | รอบอก / lilit dada are circumference; explicit inch/metric unit preferred |

Normalize Unicode, decimal separators, fractions, fullwidth digits and ㎝. Match dimension, value and unit in local context. Convert with **1 in = 2.54 cm**. Debot's target pit-to-pit 20.5–21.25″ is 52.07–53.975 cm; length 26.5–27.25″ is 67.31–69.215 cm. Do not round before gating. A 54 cm width is 21.26″: use a review band when source tolerance exceeds the difference. A tag or generic chart is not an item measurement.

- **High confidence:** structured actual item table with known method, explicit flat measurement, or validated tape photo.
- **Medium:** item description unambiguously labels value/unit, but no method is given.
- **Derived:** an explicit garment circumference can be halved if the method is known. Otherwise record the derivation as unconfirmed, as with ItsADeal's “Chest” samples.
- **Review only:** handwritten card/OCR, uncertain unit, stretched fabric, inconsistent values or ambiguous length origin. Never upgrade an estimate to verified.
- **Unavailable:** no numeric estimate from unscaled photographs; silhouette ranking for manual review is fine.

### Visual evidence and quality

Require front, back, tag and sleeve/hem details from the actual garment when condition or era drives value. A classifier can propose graphic themes, fading, material tag text or potential single stitching as **candidates**. Stitch count requires unobscured seam detail. Lighting can mimic fade, a backdrop can resemble pinholes, and a copyright year is not a manufacture date. For tape photos, endpoints and a legible scale should be in the same plane. Validate extraction against delivered samples and record errors by dimension and method. Never treat apparent vintage authenticity as verified without human inspection.

## Adapter checklist

Record supplier agreement, terms URL, approved transport, product IDs, price/currency, pagination, update/stock semantics, per-host limits, photo retention rights and stop conditions. Version the field map. Keep fixed price, timed bid, closed auction, sold out, sample photo and quote-only distinct. Validate data even if it came from an HTML embed or structured markup.
