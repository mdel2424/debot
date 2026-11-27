"""Read-only catalogue adapters for independently measured secondhand tops."""

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from scraper import SearchCancelled, extract_retry_after_seconds, raise_if_cancelled, sleep_with_cancel


@dataclass(frozen=True)
class Retailer:
    name: str
    origin: str
    catalogue: str
    product_prefix: str
    permission_required: bool = True
    scroll_collection: bool = False


RETAILERS = {
    "wildrose": Retailer(
        "Wildrose Vintage", "https://www.wildrosevintage.ca", "/collections/all", "/products/", scroll_collection=True,
    ),
    "la-penderie": Retailer(
        "La Penderie du Paradis", "https://lapenderieduparadis.com",
        "/en/collections/secondhand-men-tees-sweaters-pullovers", "/en/products/", scroll_collection=True,
    ),
    "peoples-thrift": Retailer(
        "People's Thrift", "https://www.peoplesthrift.ca", "/category/men%E2%80%99s-tops", "/product-page/", False,
    ),
}
USER_AGENT = "Debot/1.0"
MAX_COLLECTION_PAGES = 100
MAX_COLLECTION_SCROLLS = 1500
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
REQUEST_INTERVAL = 1.5
CATALOGUE_TTL = 180
MAX_CACHED_PRODUCTS = 1000
TOP = re.compile(
    r"\b(?:"
    r"t[ -]?shirts?|tees?|shirts?|polos?|(?<!high[ -])tops?(?!\s+hats?)|tanks?|singlets?|jerseys?|"
    r"sweaters?|sweatshirts?|crewnecks?|hoodies?|pullovers?|cardigans?|knits?|fleeces?|thermals?|"
    r"button[ -]?ups?|button[ -]?downs?|flannels?|henleys?|rugby|blouses?|tunics?|turtlenecks?|"
    r"jackets?|coats?|vests?|blazers?|parkas?|windbreakers?|anoraks?|overshirts?|shackets?|bombers?|puffers?|"
    r"zip[ -]?ups?|quarter[ -]?zips?|half[ -]?zips?|long[ -]?sleeves?|"
    r"chandails?|chemises?|gilets?|manteaux?|vestes?|blousons?|hauts?|d[ée]bardeurs?|camisoles?"
    r")\b",
    re.I,
)
NON_TOP = re.compile(
    r"\b(?:"
    r"bundles?|lots? (?:de|of)|(?:packs?|sets?) of|\d+[ -]+packs?|multipack|deadstock lot|mystery|"
    r"(?:t[ -]?shirt|shirt|sweater|polo|tank|hoodie|long[ -]?sleeve|short[ -]?sleeve)[ -]+dress(?:es)?|"
    r"robe[ -]+t[ -]?shirt|gift[ -]?cards?|carte[ -]?cadeau|"
    r"[23][ -]+piece\b.*\bsuits?|suit[ -]+sets?|"
    r"(?:pants|shorts|jeans)\b.*\b(?:suit|set)|pajama\s+sets?|pyjama\s+sets?"
    r")\b",
    re.I,
)
WIDTH_LABEL = (
    r"pit[ -]+to[ -]+pit|p2p|aisselles?\s*[àa-]\s*(?:l['’])?aisselles?|"
    r"largeur(?:\s+(?:à plat|sous (?:les )?aisselles|(?:de )?poitrine))?"
)
FLAT_CHEST_LABEL = r"chest|poitrine"
LENGTH_LABEL = r"body length|length|longueur(?:\s+(?:totale|du corps|du dos))?"
NUMBER = r"(\d{1,3}(?:[.,]\d{1,2}|\s+[13]/[24]|[¼½¾])?)"
UNIT = r'(cm\b|inches\b|inch\b|in\b|pouces?\b|po\b|["″”])'


class RetailAccessError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def source_enabled(source_id):
    authorized = {
        entry.strip() for entry in os.getenv("DEBOT_AUTHORIZED_RETAIL_SOURCES", "wildrose,la-penderie").split(",")
    }
    return not RETAILERS[source_id].permission_required or source_id in authorized


def _product_url(store, link):
    if not isinstance(link, str):
        return None
    url = urlsplit(urljoin(store.origin, link))
    path = url.path
    if store.product_prefix.endswith("/products/"):
        locale = store.product_prefix.removesuffix("/products/")
        path = re.sub(rf"^{re.escape(locale)}/collections/[A-Za-z0-9-]+(?=/products/)", locale, path)
        if locale and path.startswith("/products/"):
            path = locale + path
    slug = path.removeprefix(store.product_prefix).strip("/")
    if (f"{url.scheme}://{url.netloc}" == store.origin
            and path.startswith(store.product_prefix)
            and re.fullmatch(r"[A-Za-z0-9_-]+", slug)):
        return f"{store.origin}{store.product_prefix}{slug}"
    return None


class _Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.link_entries = []
        self.text = []
        self.schema = []
        self.price_text = []
        self.description_text = []
        self.has_active_add_to_cart = False
        self._price_depth = 0
        self._desc_depth = 0
        self._anchor_href = None
        self._anchor_text = None
        self._submit_disabled = False
        self._submit_text = None
        self._hidden = None
        self._json = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self._hidden = tag
            if attrs.get("type") == "application/ld+json":
                self._json = []
        elif not self._hidden:
            classes = attrs.get("class", "").split()
            if tag == "div" and (self._price_depth or "wixui-product-prices-wrapper" in classes):
                self._price_depth += 1
            if tag == "div" and (self._desc_depth or "product-single__description" in classes):
                self._desc_depth += 1
            if tag == "button" and attrs.get("name") == "add" and "product-form__submit" in classes:
                self._submit_disabled = "disabled" in attrs
                self._submit_text = []
            if tag == "a":
                if href := attrs.get("href"):
                    self.links.append(href)
                    self._anchor_href = href
                    label = attrs.get("title") or attrs.get("aria-label") or ""
                    self._anchor_text = [label] if label else []
            elif tag == "link" and "next" in (attrs.get("rel") or "").lower().split():
                if href := attrs.get("href"):
                    self.links.append(href)
            if tag in {"br", "p", "div", "li", "td", "th", "h2", "h3", "h4"}:
                self.text.append("\n")
                if self._desc_depth:
                    self.description_text.append("\n")

    def handle_data(self, data):
        if self._json is not None:
            self._json.append(data)
        elif not self._hidden:
            self.text.append(data)
            if self._price_depth:
                self.price_text.append(data)
            if self._desc_depth:
                self.description_text.append(data)
            if self._anchor_text is not None:
                self._anchor_text.append(data)
            if self._submit_text is not None:
                self._submit_text.append(data)

    def handle_endtag(self, tag):
        if tag == self._hidden:
            self._hidden = None
            if self._json is not None:
                try:
                    self.schema.append(json.loads("".join(self._json)))
                except ValueError:
                    pass
                self._json = None
        elif not self._hidden:
            if tag == "a" and self._anchor_href is not None:
                label = " ".join("".join(self._anchor_text or ()).split())
                self.link_entries.append((self._anchor_href, label))
                self._anchor_href = None
                self._anchor_text = None
            elif tag == "button" and self._submit_text is not None:
                label = " ".join("".join(self._submit_text).split()).lower()
                if not self._submit_disabled and not re.search(r"\b(?:sold out|out of stock|épuisé|rupture)\b", label):
                    self.has_active_add_to_cart = True
                self._submit_text = None
            if tag in {"p", "div", "li", "td", "th", "h2", "h3", "h4"}:
                self.text.append("\n")
                if self._price_depth:
                    self.price_text.append("\n")
                    if tag == "div":
                        self._price_depth -= 1
                if self._desc_depth:
                    self.description_text.append("\n")
                    if tag == "div":
                        self._desc_depth -= 1


def _schema_products(value):
    if isinstance(value, list):
        for entry in value:
            yield from _schema_products(entry)
    elif isinstance(value, dict):
        kind = value.get("@type")
        if kind == "Product" or (isinstance(kind, list) and "Product" in kind):
            yield value
        yield from _schema_products(value.get("@graph"))
        yield from _schema_products(value.get("hasVariant"))


def _dimension(value, unit):
    fractions = {"¼": .25, "½": .5, "¾": .75}
    if value[-1] in fractions:
        amount = int(value[:-1]) + fractions[value[-1]]
    elif "/" in value:
        whole, fraction = value.split()
        numerator, denominator = fraction.split("/")
        amount = int(whole) + int(numerator) / int(denominator)
    else:
        amount = float(value.replace(",", "."))
    inches = amount / (2.54 if unit.lower() == "cm" else 1)
    return round(inches, 3) if 8 <= inches <= 60 else None


def _labeled_dimension_match(text, label, *, require_unit=True):
    unit_part = rf"\s*{UNIT}" if require_unit else r"(?![0-9.,/%])"
    match = re.search(rf"\b(?:{label})(?:\s*\([^\n)]{{1,50}}\))?[\s:=–—-]*{NUMBER}{unit_part}", text, re.I)
    if not match:
        return None, None
    return match.group(1), (match.group(2) if require_unit else None)


def _labeled_dimension(text, label):
    value, unit = _labeled_dimension_match(text, label, require_unit=True)
    return _dimension(value, unit) if value and unit else None


def _measurements(store, page, product):
    description = product.get("description", "")
    text = "".join(_Page(description).text) if isinstance(description, str) else ""
    if store.product_prefix == "/en/products/":
        # Shopify's strip_html can concatenate block lines (e.g. "Size: LargeChest: 22\"").
        text = re.sub(
            r"(?<=\S)(?=(?:Chest|Length|Sleeves?|Poitrine|Longueur|Manches?)\s*:)",
            "\n", text, flags=re.I,
        )
        if page.description_text:
            text = f"{''.join(page.description_text)}\n{text}"
    fit = ""
    if store.product_prefix == "/product-page/":
        visible = "".join(page.text)
        section = re.search(
            r"\bSIZE\s*&\s*FIT\b\s*([\s\S]{0,400}?)(?=SHIPPING|RETURNS?|Measurements are approximate|\Z)",
            visible, re.I,
        )
        if section:
            fit = section[1]
        else:
            # Keep the fallback inside the product's own size section.
            heading = re.search(r"\bSIZE\s*&\s*FIT\b", visible, re.I)
            if heading:
                fit = visible[heading.end():heading.end() + 160]
    text += f"\n{fit}"

    w_val, w_unit = _labeled_dimension_match(text, WIDTH_LABEL, require_unit=True)
    used_flat_chest = False
    if w_val is None and store.product_prefix == "/en/products/":
        w_val, w_unit = _labeled_dimension_match(text, FLAT_CHEST_LABEL, require_unit=True)
        used_flat_chest = w_val is not None
    l_val, l_unit = _labeled_dimension_match(text, LENGTH_LABEL, require_unit=True)

    # When one measurement states an explicit unit and its sibling omits the mark
    # (e.g. Wildrose "Pit to Pit - 20”" with "Length - 25.25"), reuse the stated unit.
    if w_val is None and l_unit:
        w_val, _ = _labeled_dimension_match(text, WIDTH_LABEL, require_unit=False)
        if w_val is None and store.product_prefix == "/en/products/":
            w_val, _ = _labeled_dimension_match(text, FLAT_CHEST_LABEL, require_unit=False)
            used_flat_chest = w_val is not None
        if w_val is not None:
            w_unit = l_unit
    elif l_val is None and w_unit:
        l_val, _ = _labeled_dimension_match(text, LENGTH_LABEL, require_unit=False)
        if l_val is not None:
            l_unit = w_unit

    width = _dimension(w_val, w_unit) if w_val and w_unit else None
    if used_flat_chest and width is not None and width > 32:
        width = None
    length = _dimension(l_val, l_unit) if l_val and l_unit else None

    if fit:
        w = re.search(rf"{NUMBER}\s*{UNIT}\s*W\b", fit, re.I)
        l = re.search(rf"{NUMBER}\s*{UNIT}\s*L\b", fit, re.I)
        if width is None and w:
            width = _dimension(*w.groups())
        if length is None and l:
            length = _dimension(*l.groups())
    return width, length


def _image_url(store, value):
    if isinstance(value, list):
        return next((url for entry in value if (url := _image_url(store, entry))), None)
    if isinstance(value, dict):
        return _image_url(store, value.get("contentUrl") or value.get("url"))
    if isinstance(value, str) and value:
        url = urljoin(store.origin, value)
        if urlsplit(url).scheme == "https":
            return url
    return None


def parse_product(store, url, html):
    """Require a CAD offer, an actual photo, and two unit-bearing dimensions."""
    page = _Page(html)
    product_found = False
    for product in _schema_products(page.schema):
        canonical = product.get("url")
        if canonical and _product_url(store, canonical) != url:
            continue
        product_found = True
        title = product.get("name")
        if not isinstance(title, str) or not TOP.search(title) or NON_TOP.search(title):
            continue
        image = _image_url(store, product.get("image"))
        width, length = _measurements(store, page, product)
        if not image or width is None or length is None:
            continue
        offers = product.get("offers")
        for offer in offers if isinstance(offers, list) else [offers]:
            if not isinstance(offer, dict) or offer.get("priceCurrency") != "CAD":
                continue
            in_stock = offer.get("availability") in {"https://schema.org/InStock", "http://schema.org/InStock", "InStock"}
            # La Penderie's product template emits schema.org/OutOfStock even when the main Add to cart button is active.
            if not in_stock and store.product_prefix == "/en/products/" and page.has_active_add_to_cart:
                in_stock = True
            if not in_stock:
                continue
            try:
                price = float(offer["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(price) and price > 0:
                # Wix can advertise the regular price in JSON-LD and a sale in the product price block.
                if store.product_prefix == "/product-page/":
                    amounts = re.findall(r"(Original|Sale) price\s*\$([0-9]+\.\d{2})", "".join(page.price_text))
                    if len(amounts) == 2 and dict(amounts).keys() == {"Original", "Sale"}:
                        displayed = {key: float(value) for key, value in amounts}
                        if displayed["Original"] == price and 0 < displayed["Sale"] < price:
                            price = displayed["Sale"]
                return {"url": url, "title": title, "image": image, "price": price, "p2p": width, "length": length}
    if not product_found:
        raise RetailAccessError("retailer_unavailable", f"{store.name} did not return the expected product page.")
    return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoRedirect())


@dataclass
class _OriginState:
    lock: Lock = field(default_factory=Lock)
    last_request: float = 0
    blocked_until: float = 0
    catalogues: dict = field(default_factory=dict)
    products: dict = field(default_factory=dict)
    scroll_cache: tuple | None = None


_ORIGINS = {source_id: _OriginState() for source_id in RETAILERS}
_ACCESS_BLOCKS = {401, 403, 429}


def _pause_origin(store, state, status, response):
    retry_after = extract_retry_after_seconds(response)
    state.blocked_until = time.monotonic() + max(retry_after or 0, 600)
    return RetailAccessError("retailer_paused", f"{store.name} declined catalogue access (HTTP {status}).")


def _read(store, state, path, should_cancel, delay=REQUEST_INTERVAL):
    """Read one fixed catalogue route, sharing pacing and a small cache across jobs."""
    raise_if_cancelled(should_cancel)
    if state.blocked_until > time.monotonic():
        raise RetailAccessError("retailer_paused", f"{store.name} has paused catalogue access. Try again later.")
    now = time.monotonic()
    cached = state.catalogues.get(path)
    if cached and now < cached[0]:
        return cached[1]
    sleep_with_cancel(state.last_request + delay - now, should_cancel)
    request = Request(store.origin + path, headers={"User-Agent": USER_AGENT})
    state.last_request = time.monotonic()
    try:
        with _OPENER.open(request, timeout=10) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise RetailAccessError("retailer_unavailable", f"{store.name} returned an oversized catalogue response.")
            body = data.decode("utf-8", errors="replace")
    except HTTPError as exc:
        exc.close()
        if exc.code in _ACCESS_BLOCKS:
            raise _pause_origin(store, state, exc.code, exc) from exc
        if exc.code in {404, 410} and path.startswith(store.product_prefix):
            body = ""
        else:
            raise RetailAccessError("retailer_unavailable", f"{store.name} returned HTTP {exc.code}.") from exc
    except (URLError, OSError) as exc:
        raise RetailAccessError("retailer_unavailable", f"Could not reach {store.name}.") from exc
    raise_if_cancelled(should_cancel)
    if not path.startswith(store.product_prefix):
        ttl = 900 if path == "/robots.txt" else CATALOGUE_TTL
        state.catalogues[path] = (time.monotonic() + ttl, body)
    return body


def _allowed(robots, url):
    # urllib parses the groups but applies only the first matching plain prefix.
    agent = USER_AGENT.split("/", 1)[0].lower()
    groups = [
        (len(token), entry)
        for entry in robots.entries for token in entry.useragents
        if token.lower() in agent
    ]
    specificity = max((length for length, _ in groups), default=0)
    entries = [entry for length, entry in groups if length == specificity] or [robots.default_entry]
    route = urlsplit(url)
    target = route.path + ("?" + route.query if route.query else "")
    target = unquote(target)
    matches = []
    for entry in filter(None, entries):
        for rule in entry.rulelines:
            pattern = unquote(rule.path)
            prefix = pattern.removesuffix("$")
            expression = ".*".join(re.escape(part) for part in prefix.split("*"))
            if re.match(expression + ("$" if pattern.endswith("$") else ""), target):
                matches.append((len(prefix.replace("*", "")), rule.allowance))
    return max(matches, default=(0, True))[1]


def _add_candidates(store, links, candidates, limit):
    grouped = {}
    for entry in links:
        if isinstance(entry, (tuple, list)) and len(entry) >= 2:
            link, label = entry[0], entry[1] or ""
            price = entry[2] if len(entry) >= 3 and isinstance(entry[2], (int, float)) else None
        else:
            link, label, price = entry, "", None
        url = _product_url(store, link)
        if not url:
            continue
        labels, best_price = grouped.setdefault(url, ([], None))
        if label:
            labels.append(label)
        if price is not None and math.isfinite(price) and price > 0:
            if best_price is None or price < best_price:
                grouped[url] = (labels, float(price))
    for url, (labels, best_price) in grouped.items():
        if url not in candidates and len(candidates) >= limit:
            break
        slug = re.sub(r"[-_]+", " ", urlsplit(url).path.rsplit("/", 1)[-1])
        texts = [slug, *labels]
        if any(TOP.search(text) for text in texts) and not any(NON_TOP.search(text) for text in texts):
            existing = candidates.get(url)
            if url not in candidates:
                candidates[url] = best_price
            elif best_price is not None and (existing is None or best_price < existing):
                candidates[url] = best_price


def _next_collection_path(store, links, page_number):
    for link in links:
        next_url = urlsplit(urljoin(store.origin + store.catalogue, link))
        if (f"{next_url.scheme}://{next_url.netloc}" != store.origin
                or unquote(next_url.path) != unquote(store.catalogue)):
            continue
        page_values = [value for key, value in parse_qsl(next_url.query, keep_blank_values=True) if key == "page"]
        if page_values == [str(page_number + 1)]:
            return f"{store.catalogue}?{next_url.query}"
    return None


def _scroll_for_links(page, store, candidates, limit, check, delay, is_fetching=lambda: False):
    previous = None
    stable = 0
    for _ in range(MAX_COLLECTION_SCROLLS):
        check()
        anchors, next_links, height, bottom, at_exact_bottom, dom_busy = page.evaluate("""() => {
            const root = document.querySelector('main, [role=main]') || document.body;
            const anchors = Array.from(root.querySelectorAll('a[href]'), a => {
                const card = a.closest('.card-wrapper, li.grid__item, .gitem');
                let price = null;
                const priceEl = card && card.querySelector('.price__container, .priceWraper');
                if (priceEl && !/\\b(?:USD|EUR|GBP)\\b/i.test(priceEl.textContent)) {
                    const nums = Array.from(
                        priceEl.textContent.matchAll(/\\$([0-9]+(?:,[0-9]{3})*(?:\\.[0-9]{2})?)/g),
                        m => parseFloat(m[1].replace(/,/g, '')),
                    ).filter(n => Number.isFinite(n) && n > 0);
                    if (nums.length) price = Math.min(...nums);
                }
                return [
                    a.href,
                    (a.textContent || a.getAttribute('title') || a.getAttribute('aria-label') || '').trim(),
                    price,
                ];
            });
            const nextLinks = Array.from(document.querySelectorAll('[rel~=next][href]'), a => a.href);
            const height = document.documentElement.scrollHeight;
            const pos = window.scrollY + window.innerHeight;
            const busy = '[aria-busy="true"], .custom-loader';
            return [anchors, nextLinks, height, pos >= height - 4, pos >= height,
                root.matches(busy) || !!root.querySelector(busy)];
        }""")
        busy = dom_busy or is_fetching()
        _add_candidates(store, anchors, candidates, limit)
        links = [href for href, _, _ in anchors] + next_links
        if len(candidates) >= limit:
            return links
        # A viewport without new cards isn't the end unless the page is at the bottom and idle.
        signature = (height, tuple(links))
        stable = stable + 1 if bottom and not busy and signature == previous else 0
        if stable >= 3:
            return links
        previous = signature
        if not busy and not bottom:
            page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85))")
            check()
            page.wait_for_timeout(min(250, max(100, int(delay * 250))))
        else:
            if not busy and bottom and not at_exact_bottom:
                page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            for _ in range(max(1, math.ceil(delay * 4))):
                check()
                page.wait_for_timeout(250)
    raise RetailAccessError("retailer_unavailable", f"{store.name}'s collection did not finish scrolling.")


def _browser_candidates(store, state, robots, limit, should_cancel, delay):
    cached = state.scroll_cache
    if cached and cached[0] > time.monotonic() and cached[1] == limit:
        return cached[2]
    candidates = {}
    failure = None
    in_flight = 0
    page_cache = {}
    fetched_paths = set()
    fetched_pages = set()

    def check():
        raise_if_cancelled(should_cancel)
        if failure:
            raise failure

    def route_request(route):
        nonlocal failure, in_flight
        request = route.request
        url = urlsplit(request.url)
        same_origin = f"{url.scheme}://{url.netloc}" == store.origin
        data_request = request.resource_type in {"document", "xhr", "fetch"}
        collection_request = url.path.rstrip("/") == store.catalogue
        if failure or (should_cancel and should_cancel()) or request.method != "GET":
            return route.abort()
        if not same_origin:
            shopify_asset = url.scheme == "https" and url.netloc == "cdn.shopify.com"
            if shopify_asset and request.resource_type in {"script", "stylesheet"}:
                return route.continue_()
            return route.abort()
        if not _allowed(robots, request.url):
            if data_request and collection_request:
                failure = RetailAccessError("retailer_restricted", f"{store.name}'s robots.txt restricts this catalogue path.")
            return route.abort()
        if data_request:
            if not collection_request:
                return route.abort()
            route_path = url.path + (f"?{url.query}" if url.query else "")
            fetched_paths.add(route_path)
            if request.resource_type in {"xhr", "fetch"}:
                for key, value in parse_qsl(url.query, keep_blank_values=True):
                    if key == "page" and value.isdigit():
                        fetched_pages.add(int(value))
            if route_path in page_cache:
                status, content_type, body = page_cache[route_path]
                return route.fulfill(status=status, content_type=content_type, body=body)
            in_flight += 1
            try:
                try:
                    sleep_with_cancel(state.last_request + delay - time.monotonic(), should_cancel)
                except SearchCancelled:
                    return route.abort()
                state.last_request = time.monotonic()
                # Do not follow redirects from catalogue requests, including redirects to login pages.
                try:
                    response = route.fetch(max_redirects=0, timeout=20000)
                except PlaywrightError:
                    failure = RetailAccessError("retailer_unavailable", f"Could not reach {store.name}'s collection.")
                    return route.abort()
                try:
                    if response.status in _ACCESS_BLOCKS:
                        failure = _pause_origin(store, state, response.status, response)
                    elif response.status >= 300 and response.status != 304:
                        failure = RetailAccessError("retailer_unavailable", f"{store.name} returned HTTP {response.status}.")
                    if failure:
                        return route.abort()
                    if response.status == 304:
                        return route.fulfill(response=response)
                    body = response.body()
                    if len(body) > MAX_RESPONSE_BYTES:
                        failure = RetailAccessError("retailer_unavailable", f"{store.name} returned an oversized catalogue response.")
                        return route.abort()
                    content_type = response.headers.get("content-type", "text/html; charset=utf-8")
                    # Deduplicate infinite-scroll apps that probe ?page=N and then refetch it immediately.
                    page_cache[route_path] = (response.status, content_type, body)
                    return route.fulfill(status=response.status, content_type=content_type, body=body)
                finally:
                    response.dispose()
            finally:
                in_flight -= 1
        elif request.resource_type not in {"script", "stylesheet"}:
            return route.abort()
        route.continue_()

    try:
        with sync_playwright() as playwright, playwright.firefox.launch() as browser:
            with browser.new_context(user_agent=USER_AGENT, service_workers="block") as context:
                page = context.new_page()
                page.route("**/*", route_request)
                current = store.catalogue
                for number in range(1, MAX_COLLECTION_PAGES + 1):
                    check()
                    response = page.goto(store.origin + current, wait_until="domcontentloaded", timeout=20000)
                    check()
                    if not response or unquote(page.url) != unquote(store.origin + current):
                        raise RetailAccessError("retailer_unavailable", f"{store.name} did not return the expected collection.")
                    links = _scroll_for_links(page, store, candidates, limit, check, delay, lambda: in_flight > 0)
                    next_path = _next_collection_path(store, links, number)
                    if (len(candidates) >= limit
                            or next_path is None
                            or next_path in fetched_paths
                            or (number + 1) in fetched_pages):
                        break
                    current = next_path
                else:
                    raise RetailAccessError("retailer_unavailable", f"{store.name}'s collection exceeded the page limit.")
    except PlaywrightError as exc:
        check()
        raise RetailAccessError("retailer_unavailable", f"Could not load {store.name}'s collection in Firefox.") from exc
    state.scroll_cache = (time.monotonic() + CATALOGUE_TTL, limit, candidates)
    return candidates


def search_catalogue(source_id, max_links, should_cancel, max_price_cad=None):
    """Yield a candidate count followed by (url, parsed listing or None) pairs."""
    store = RETAILERS[source_id]
    if not source_enabled(source_id):
        raise RetailAccessError("retailer_permission_required", f"{store.name} requires permission for automated access.")
    state = _ORIGINS[source_id]
    while not state.lock.acquire(timeout=0.1):
        raise_if_cancelled(should_cancel)
    try:
        robots_url = store.origin + "/robots.txt"
        robots = RobotFileParser(robots_url)
        robots_text = _read(store, state, "/robots.txt", should_cancel).lstrip("\ufeff")
        if not re.search(r"^\s*User-agent\s*:", robots_text, re.I | re.M):
            raise RetailAccessError("retailer_unavailable", f"{store.name}'s robots.txt could not be verified.")
        robots.parse(robots_text.splitlines())
        delay = max(REQUEST_INTERVAL, robots.crawl_delay(USER_AGENT) or 0)
        rate = robots.request_rate(USER_AGENT)
        if rate and rate.requests:
            delay = max(delay, rate.seconds / rate.requests)

        def validate_path(path):
            if not _allowed(robots, store.origin + path):
                raise RetailAccessError("retailer_restricted", f"{store.name}'s robots.txt restricts this catalogue path.")

        def read(path):
            validate_path(path)
            return _read(store, state, path, should_cancel, delay)

        if store.scroll_collection:
            validate_path(store.catalogue)
            candidates = _browser_candidates(store, state, robots, max_links, should_cancel, delay)
        else:
            candidates = {}
            current = store.catalogue
            for number in range(1, MAX_COLLECTION_PAGES + 1):
                parsed_page = _Page(read(current))
                _add_candidates(store, parsed_page.link_entries, candidates, max_links)
                current = _next_collection_path(store, parsed_page.links, number)
                if len(candidates) >= max_links or current is None:
                    break
            else:
                raise RetailAccessError("retailer_unavailable", f"{store.name}'s collection exceeded the page limit.")
        yield len(candidates)
        for url, card_price in candidates.items():
            raise_if_cancelled(should_cancel)
            path = urlsplit(url).path
            validate_path(path)
            cached = state.products.get(url)
            if cached and cached[0] > time.monotonic():
                yield url, cached[1]
                continue
            if max_price_cad is not None and card_price is not None and card_price > max_price_cad:
                yield url, None
                continue
            html = _read(store, state, path, should_cancel, delay)
            item = parse_product(store, url, html) if html else None
            if len(state.products) >= MAX_CACHED_PRODUCTS and url not in state.products:
                state.products.pop(next(iter(state.products)))
            state.products[url] = (time.monotonic() + CATALOGUE_TTL, item)
            yield url, item
    finally:
        state.lock.release()
