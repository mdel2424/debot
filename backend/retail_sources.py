"""Read-only catalogue adapters for independently measured secondhand tees."""

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
MAX_COLLECTION_SCROLLS = 450
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
REQUEST_INTERVAL = 1.5
CATALOGUE_TTL = 180
MAX_CACHED_PRODUCTS = 160
TEE = re.compile(r"\b(?:t[ -]?shirts?|tees?|chandails? à manches courtes)\b", re.I)
NON_TEE = re.compile(
    r"\b(?:bundles?|lots? (?:de|of)|(?:packs?|sets?) of|\d+[ -]+packs?|multipack|deadstock lot|mystery|"
    r"t[ -]?shirt[ -]+dress|robe[ -]+t[ -]?shirt)\b", re.I,
)
WIDTH_LABEL = (
    r"pit[ -]+to[ -]+pit|p2p|aisselles?\s*[àa-]\s*(?:l['’])?aisselles?|"
    r"largeur(?:\s+(?:à plat|sous (?:les )?aisselles|(?:de )?poitrine))?"
)
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
            and re.fullmatch(r"[A-Za-z0-9-]+", slug)):
        return f"{store.origin}{store.product_prefix}{slug}"
    return None


class _Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.text = []
        self.schema = []
        self.price_text = []
        self._price_depth = 0
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
            if tag == "div" and (self._price_depth or "wixui-product-prices-wrapper" in attrs.get("class", "").split()):
                self._price_depth += 1
            if tag == "a" or (tag == "link" and "next" in (attrs.get("rel") or "").lower().split()):
                if href := attrs.get("href"):
                    self.links.append(href)
            if tag in {"br", "p", "div", "li", "td", "th", "h2", "h3", "h4"}:
                self.text.append("\n")

    def handle_data(self, data):
        if self._json is not None:
            self._json.append(data)
        elif not self._hidden:
            self.text.append(data)
            if self._price_depth:
                self.price_text.append(data)

    def handle_endtag(self, tag):
        if tag == self._hidden:
            self._hidden = None
            if self._json is not None:
                try:
                    self.schema.append(json.loads("".join(self._json)))
                except ValueError:
                    pass
                self._json = None
        elif not self._hidden and tag in {"p", "div", "li", "td", "th", "h2", "h3", "h4"}:
            self.text.append("\n")
            if self._price_depth:
                self.price_text.append("\n")
                if tag == "div":
                    self._price_depth -= 1


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


def _labeled_dimension(text, label):
    match = re.search(rf"\b(?:{label})(?:\s*\([^\n)]{{1,50}}\))?[\s:=–—-]*{NUMBER}\s*{UNIT}", text, re.I)
    return _dimension(*match.groups()) if match else None


def _measurements(store, page, product):
    description = product.get("description", "")
    text = "".join(_Page(description).text) if isinstance(description, str) else ""
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
    width = _labeled_dimension(text, WIDTH_LABEL)
    length = _labeled_dimension(text, LENGTH_LABEL)
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
        if not isinstance(title, str) or not TEE.search(title) or NON_TEE.search(title):
            continue
        image = _image_url(store, product.get("image"))
        width, length = _measurements(store, page, product)
        if not image or width is None or length is None:
            continue
        offers = product.get("offers")
        for offer in offers if isinstance(offers, list) else [offers]:
            if not isinstance(offer, dict) or offer.get("priceCurrency") != "CAD":
                continue
            if offer.get("availability") not in {"https://schema.org/InStock", "http://schema.org/InStock", "InStock"}:
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
    for link in links:
        url = _product_url(store, link)
        if not url:
            continue
        slug = urlsplit(url).path.rsplit("/", 1)[-1].replace("-", " ")
        if TEE.search(slug) and not NON_TEE.search(slug):
            candidates[url] = None
            if len(candidates) >= limit:
                break


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


def _scroll_for_links(page, store, candidates, limit, check, delay):
    previous = None
    stable = 0
    for _ in range(MAX_COLLECTION_SCROLLS):
        check()
        links, height, bottom, busy = page.evaluate("""() => {
            const root = document.querySelector('main, [role=main]') || document.body;
            const links = Array.from(root.querySelectorAll('a[href]'), a => a.href);
            links.push(...Array.from(document.querySelectorAll('[rel~=next][href]'), a => a.href));
            const height = document.documentElement.scrollHeight;
            const busy = '[aria-busy="true"]';
            return [links, height, window.scrollY + window.innerHeight >= height - 4,
                root.matches(busy) || !!root.querySelector(busy)];
        }""")
        _add_candidates(store, links, candidates, limit)
        if len(candidates) >= limit:
            return links
        # A viewport without new cards isn't the end unless the page is at the bottom.
        signature = (height, tuple(links))
        stable = stable + 1 if bottom and not busy and signature == previous else 0
        if stable >= 3:
            return links
        previous = signature
        if not busy:
            page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85))")
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

    def check():
        raise_if_cancelled(should_cancel)
        if failure:
            raise failure

    def route_request(route):
        nonlocal failure
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
                return route.fulfill(response=response)
            finally:
                response.dispose()
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
                    if not response or page.url != store.origin + current:
                        raise RetailAccessError("retailer_unavailable", f"{store.name} did not return the expected collection.")
                    links = _scroll_for_links(page, store, candidates, limit, check, delay)
                    current = _next_collection_path(store, links, number)
                    if len(candidates) >= limit or current is None:
                        break
                else:
                    raise RetailAccessError("retailer_unavailable", f"{store.name}'s collection exceeded the page limit.")
    except PlaywrightError as exc:
        check()
        raise RetailAccessError("retailer_unavailable", f"Could not load {store.name}'s collection in Firefox.") from exc
    state.scroll_cache = (time.monotonic() + CATALOGUE_TTL, limit, candidates)
    return candidates


def search_catalogue(source_id, max_links, should_cancel):
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
                links = _Page(read(current)).links
                _add_candidates(store, links, candidates, max_links)
                current = _next_collection_path(store, links, number)
                if len(candidates) >= max_links or current is None:
                    break
            else:
                raise RetailAccessError("retailer_unavailable", f"{store.name}'s collection exceeded the page limit.")
        yield len(candidates)
        for url in candidates:
            raise_if_cancelled(should_cancel)
            path = urlsplit(url).path
            validate_path(path)
            cached = state.products.get(url)
            if cached and cached[0] > time.monotonic():
                yield url, cached[1]
                continue
            html = _read(store, state, path, should_cancel, delay)
            item = parse_product(store, url, html) if html else None
            if len(state.products) >= MAX_CACHED_PRODUCTS and url not in state.products:
                state.products.pop(next(iter(state.products)))
            state.products[url] = (time.monotonic() + CATALOGUE_TTL, item)
            yield url, item
    finally:
        state.lock.release()
