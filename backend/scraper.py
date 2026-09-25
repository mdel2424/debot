"""Depop scraping utilities using Playwright."""

import copy
import math
import re
import time
import json
import os
import threading
import datetime as dt
from collections import OrderedDict
from email.utils import parsedate_to_datetime
from typing import Optional, List, Dict, Any, Callable
from urllib.parse import urljoin, urlparse, urlencode, parse_qsl
from weakref import WeakKeyDictionary, WeakSet

from playwright.sync_api import sync_playwright, Page, BrowserContext

# Constants
PRICE_RX = re.compile(r"([$£€]\s?\d[\d,]*(?:\.\d{2})?)")
RELTIME_RX = re.compile(r"\b(\d+)\s*(minute|hour|day|week|month)s?\s*ago\b", re.I)
CREATED_AT_RX = re.compile(
    r'(?:\\\"|")'
    r'(?:created_at|createdAt|datePublished|dateCreated|published_at|publishedAt)'
    r'(?:\\\"|")\s*:\s*(?:\\\"|")([^"\\]+)(?:\\\"|")',
    re.I,
)
SOLD_COUNT_RX = re.compile(r"(\d[\d,]*)\s*sold\b", re.I)
BROWSE_URL = "https://www.depop.com/ca/category/mens/tops/?sort=newlyListed"
SIZE_LINE_RX = re.compile(r"^\s*size(?:\s*[:\-])?\s+(.+?)\s*$", re.I)
CURRENCY_SYMBOLS = {
    "USD": "US$",
    "CAD": "$",
    "GBP": "£",
    "EUR": "€",
}
SCROLL_STEPS_PER_BATCH = 4
SCROLL_STEP_RATIO = 0.7
MAX_STALLED_SCROLL_STEPS = 3
EARLY_SCROLL_STALL_BUFFER = 2
EARLY_SCROLL_LINK_THRESHOLD = 24
BROWSE_END_SCROLL_WAIT_MS = 2500
LOGIN_MODAL_MAX_ATTEMPTS = 6
LOGIN_MODAL_WAIT_MS = 250
FAST_LISTING_READY_TIMEOUT_MS = 2_500
SHOP_PRODUCTS_CAPTURE_MAX_PAGES = 200
RATE_LIMIT_TEXT_SIGNALS = (
    "too many requests",
    "rate limited",
    "rate limit",
    "request limit",
    "try again later",
    "slow down",
    "unusual traffic",
    "temporarily blocked",
)
RATE_LIMIT_CHALLENGE_SIGNALS = (
    "access denied",
    "verify you are human",
    "checking your browser",
    "attention required",
    "security check",
    "please enable cookies",
)
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
BLOCKED_URL_SIGNALS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.com/tr",
    "connect.facebook.net",
    "hotjar.com",
    "segment.io",
    "segment.com",
    "amplitude.com",
    "mixpanel.com",
    "fullstory.com",
    "datadoghq.com",
    "newrelic.com",
)
BROWSER_INIT_SCRIPT = r"""(() => {
    try {
        Object.defineProperty(Navigator.prototype, "webdriver", {
            configurable: true,
            get: () => undefined,
        });
    } catch (error) {}
})()"""
CancelCheck = Optional[Callable[[], bool]]
_PENDING_LOG_COUNTS: Dict[str, int] = {"login_modal_escape": 0}
_NAVIGATION_LOCK = threading.Lock()
_LAST_NAVIGATION_STARTED_AT = 0.0
_NAVIGATION_PACING_UNTIL_TS = 0.0
_CAPTURED_SHOP_PRODUCT_PAGES = WeakKeyDictionary()
_SHOP_PRODUCTS_CAPTURE_INSTALLED_PAGES = WeakSet()
_CAPTURED_SHOP_PRODUCT_LOCK = threading.Lock()
_LISTING_CACHE: OrderedDict[str, tuple[float, Dict[str, Any]]] = OrderedDict()
_LISTING_CACHE_LOCK = threading.Lock()


def _read_float_env(name: str, default: float) -> float:
    """Read a non-negative float environment value with a safe fallback."""
    try:
        value = float(os.environ.get(name, default))
        return value if math.isfinite(value) and value >= 0 else default
    except Exception:
        return default


def _read_int_env(name: str, default: int) -> int:
    """Read a non-negative integer environment value with a safe fallback."""
    try:
        value = int(os.environ.get(name, default))
        return value if value >= 0 else default
    except Exception:
        return default


MIN_NAV_INTERVAL_SECONDS = _read_float_env("DEBOT_MIN_NAV_INTERVAL_SECONDS", 0.0)
RATE_LIMIT_NAV_INTERVAL_SECONDS = _read_float_env("DEBOT_RATE_LIMIT_NAV_INTERVAL_SECONDS", 1.0)
LISTING_CACHE_TTL_SECONDS = _read_float_env("DEBOT_LISTING_CACHE_TTL_SECONDS", 300.0)
LISTING_CACHE_MAX_ITEMS = _read_int_env("DEBOT_LISTING_CACHE_MAX_ITEMS", 1000)


def get_cached_listing(url: str) -> Optional[Dict[str, Any]]:
    """Return a fresh copy of a recently parsed public listing."""
    if LISTING_CACHE_TTL_SECONDS <= 0 or LISTING_CACHE_MAX_ITEMS <= 0:
        return None

    cache_key = str(url or "").strip()
    if not cache_key:
        return None

    now = time.monotonic()
    with _LISTING_CACHE_LOCK:
        cached = _LISTING_CACHE.get(cache_key)
        if cached is None:
            return None

        cached_at, item = cached
        if now - cached_at > LISTING_CACHE_TTL_SECONDS:
            _LISTING_CACHE.pop(cache_key, None)
            return None

        _LISTING_CACHE.move_to_end(cache_key)
        return copy.deepcopy(item)


def _cache_listing(item: Dict[str, Any]) -> None:
    """Store a successful public listing parse in the bounded process cache."""
    if LISTING_CACHE_TTL_SECONDS <= 0 or LISTING_CACHE_MAX_ITEMS <= 0:
        return

    cache_key = str(item.get("url") or "").strip()
    if not cache_key:
        return

    with _LISTING_CACHE_LOCK:
        _LISTING_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(item))
        _LISTING_CACHE.move_to_end(cache_key)
        while len(_LISTING_CACHE) > LISTING_CACHE_MAX_ITEMS:
            _LISTING_CACHE.popitem(last=False)


def _clear_listing_cache() -> None:
    """Clear cached listings for isolated tests."""
    with _LISTING_CACHE_LOCK:
        _LISTING_CACHE.clear()


class SearchCancelled(Exception):
    """Raised when a user cancels an in-flight search."""


class FetchError(RuntimeError):
    """A page could not be read; never count it as a successful empty scan."""

    code = "fetch_failed"


class CollectionIncompleteError(FetchError):
    """The collection safety limit was reached before the page was exhausted."""

    code = "incomplete_collection"


class RateLimitError(Exception):
    """Raised when Depop is rate limiting or temporarily blocking requests."""

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        retry_after_seconds: Optional[int] = None,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.code = "rate_limited"


def flush_debug_logs() -> None:
    """Flush any buffered log summaries."""
    escape_count = _PENDING_LOG_COUNTS.get("login_modal_escape", 0)
    if escape_count:
        suffix = f" x{escape_count}" if escape_count > 1 else ""
        print(f"[login-modal] Pressed Escape to dismiss modal{suffix}")
        _PENDING_LOG_COUNTS["login_modal_escape"] = 0


def log_debug(message: str, *, aggregate_key: Optional[str] = None) -> None:
    """Print debug logs while collapsing repeated noisy messages."""
    if aggregate_key:
        _PENDING_LOG_COUNTS[aggregate_key] = _PENDING_LOG_COUNTS.get(aggregate_key, 0) + 1
        return

    flush_debug_logs()
    print(message)


def raise_if_cancelled(should_cancel: CancelCheck = None) -> None:
    """Raise when the caller has requested cancellation."""
    if should_cancel and should_cancel():
        raise SearchCancelled("Search cancelled")


def sleep_with_cancel(
    delay_seconds: float,
    should_cancel: CancelCheck = None,
    interval_seconds: float = 0.1,
) -> None:
    """Sleep in short intervals so long waits can be interrupted promptly."""
    remaining = max(delay_seconds, 0.0)
    while remaining > 0:
        raise_if_cancelled(should_cancel)
        chunk = min(interval_seconds, remaining)
        time.sleep(chunk)
        remaining -= chunk
    raise_if_cancelled(should_cancel)


def mark_navigation_pacing(duration_seconds: float) -> None:
    """Temporarily enable slower navigation pacing after a rate-limit event."""
    global _NAVIGATION_PACING_UNTIL_TS

    duration = max(float(duration_seconds or 0), 0.0)
    if duration <= 0:
        return

    with _NAVIGATION_LOCK:
        _NAVIGATION_PACING_UNTIL_TS = max(
            _NAVIGATION_PACING_UNTIL_TS,
            time.monotonic() + duration,
        )


def _current_navigation_interval_seconds() -> float:
    """Return the active minimum interval between navigation starts."""
    interval = max(float(MIN_NAV_INTERVAL_SECONDS or 0), 0.0)
    if time.monotonic() < _NAVIGATION_PACING_UNTIL_TS:
        interval = max(interval, max(float(RATE_LIMIT_NAV_INTERVAL_SECONDS or 0), 0.0))
    return interval


def wait_for_navigation_slot(should_cancel: CancelCheck = None) -> None:
    """Share pacing between page navigations and public pagination requests."""
    global _LAST_NAVIGATION_STARTED_AT

    while True:
        raise_if_cancelled(should_cancel)
        if _NAVIGATION_LOCK.acquire(timeout=0.1):
            break
    try:
        interval = _current_navigation_interval_seconds()
        now = time.monotonic()
        delay = interval - (now - _LAST_NAVIGATION_STARTED_AT)
        if delay > 0:
            sleep_with_cancel(delay, should_cancel)
        _LAST_NAVIGATION_STARTED_AT = time.monotonic()
    finally:
        _NAVIGATION_LOCK.release()

    raise_if_cancelled(should_cancel)


def guarded_goto(
    page: Page,
    url: str,
    *,
    wait_until: str = "domcontentloaded",
    timeout: int = 60_000,
    should_cancel: CancelCheck = None,
):
    """Space navigation starts without holding a lock during network requests."""
    wait_for_navigation_slot(should_cancel)
    clear_shop_products_capture(page)
    return page.goto(url, wait_until=wait_until, timeout=timeout)


def should_block_request(request: Any) -> bool:
    """Return whether a Playwright request should be aborted to keep pages light."""
    try:
        resource_type = str(getattr(request, "resource_type", "") or "").lower()
        if resource_type in BLOCKED_RESOURCE_TYPES:
            return True
    except Exception:
        pass

    try:
        url = str(getattr(request, "url", "") or "").lower()
    except Exception:
        url = ""

    return any(signal in url for signal in BLOCKED_URL_SIGNALS)


def install_resource_blocking(ctx: BrowserContext) -> None:
    """Install a best-effort route that blocks heavy assets and trackers."""
    def handle_route(route):
        try:
            if should_block_request(route.request):
                route.abort()
                return
            route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    ctx.route("**/*", handle_route)


def extract_rate_limit_message(
    text: str,
    status: Optional[int] = None,
    expected_content_missing: bool = True,
) -> Optional[str]:
    """Return a user-facing rate-limit message when text/status looks blocked."""
    normalized = re.sub(r"\s+", " ", text or "").strip().lower()

    if status == 429:
        return "Depop returned HTTP 429 Too Many Requests."

    if status == 403:
        return "Depop returned HTTP 403 Forbidden."

    if expected_content_missing and any(signal in normalized for signal in RATE_LIMIT_TEXT_SIGNALS):
        return "Depop appears to be rate limiting requests right now."

    if expected_content_missing and any(signal in normalized for signal in RATE_LIMIT_CHALLENGE_SIGNALS):
        return "Depop appears to be temporarily blocking requests right now."

    return None


def _response_status(response: Any) -> Optional[int]:
    """Safely read a Playwright response status when one exists."""
    try:
        status = getattr(response, "status", None)
        return int(status) if status is not None else None
    except Exception:
        return None


def _parse_retry_after_seconds(value: Optional[str]) -> Optional[int]:
    """Parse a Retry-After header into whole seconds when possible."""
    if not value:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    try:
        seconds = int(raw)
        return max(seconds, 0)
    except Exception:
        pass

    try:
        retry_at = parsedate_to_datetime(raw)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=dt.timezone.utc)
        else:
            retry_at = retry_at.astimezone(dt.timezone.utc)
        delta = retry_at - dt.datetime.now(dt.timezone.utc)
        return max(math.ceil(delta.total_seconds()), 0)
    except Exception:
        return None


def extract_retry_after_seconds(response: Any) -> Optional[int]:
    """Extract Retry-After seconds from a Playwright response when present."""
    if response is None:
        return None

    header_value = None
    try:
        header_value = response.header_value("retry-after")
    except Exception:
        try:
            header_value = response.headers.get("retry-after")
        except Exception:
            header_value = None

    return _parse_retry_after_seconds(header_value)


def _page_has_selector(page: Page, selector: str) -> bool:
    """Best-effort check for whether a selector exists on the page."""
    try:
        return page.locator(selector).first.count() > 0
    except Exception:
        return False


def check_page_for_rate_limit(
    page: Page,
    response_status: Optional[int] = None,
    expect_product_links: bool = False,
    expect_listing: bool = False,
    retry_after_seconds: Optional[int] = None,
) -> None:
    """Inspect the current page and raise when it looks rate limited."""
    text_parts: List[str] = []

    try:
        text_parts.append(page.title() or "")
    except Exception:
        pass

    try:
        text_parts.append(page.inner_text("body", timeout=1_000) or "")
    except Exception:
        pass

    expected_content_missing = True
    if expect_product_links:
        expected_content_missing = not _page_has_selector(page, 'a[href*="/products/"]')
    elif expect_listing:
        expected_content_missing = not any(
            _page_has_selector(page, selector)
            for selector in (
                "script[type='application/ld+json']",
                "p[aria-label='Price']",
                "time[datetime]",
                "a[aria-label$=\"'s shop\"]",
                "a:has-text('Visit shop')",
            )
        )

    message = extract_rate_limit_message(
        "\n".join(text_parts),
        status=response_status,
        expected_content_missing=expected_content_missing,
    )
    if message:
        raise RateLimitError(
            message,
            status=response_status,
            retry_after_seconds=retry_after_seconds,
        )

    if response_status is not None and response_status >= 400:
        raise FetchError(f"Depop returned HTTP {response_status} while loading {page.url}.")


def parse_iso_datetime(ts: str) -> Optional[dt.datetime]:
    """Parse an ISO datetime string to a UTC-aware datetime."""
    try:
        clean = ts.strip()
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        dt_val = dt.datetime.fromisoformat(clean)
        if dt_val.tzinfo is None:
            dt_val = dt_val.replace(tzinfo=dt.timezone.utc)
        else:
            dt_val = dt_val.astimezone(dt.timezone.utc)
        return dt_val
    except Exception:
        return None


def age_days_from(ts_val: dt.datetime) -> float:
    """Calculate age in days from a datetime."""
    delta = dt.datetime.now(dt.timezone.utc) - ts_val
    return max(delta.total_seconds() / 86400.0, 0.0)


def parse_relative_time(text: str) -> Optional[dt.datetime]:
    """Convert relative phrases like '3 hours ago' to UTC datetime."""
    try:
        clean = text or ""
        now = dt.datetime.now(dt.timezone.utc)
        if re.search(r"\b(?:listed|posted|published)\s+today\b", clean, re.I):
            return now
        if re.search(r"\b(?:listed|posted|published)\s+yesterday\b", clean, re.I):
            return now - dt.timedelta(days=1)

        m = RELTIME_RX.search(clean)
        if not m:
            return None
        qty = int(m.group(1))
        unit = m.group(2).lower()

        deltas = {
            "minute": dt.timedelta(minutes=qty),
            "hour": dt.timedelta(hours=qty),
            "day": dt.timedelta(days=qty),
            "week": dt.timedelta(weeks=qty),
            "month": dt.timedelta(days=qty * 30),
        }
        delta = next((v for k, v in deltas.items() if unit.startswith(k)), None)
        return now - delta if delta else None
    except Exception:
        return None


def build_seller_url(seller: str, groups: str = "tops", gender: str = "male") -> str:
    """Build a Depop seller URL with filters."""
    base = f"https://www.depop.com/{seller.strip().lstrip('@').strip('/')}/"
    params = {"sort": "recent", "groups": groups}
    if gender:
        params["gender"] = gender
    return base + "?" + urlencode(params)


def build_browse_url(groups: str = "tops", gender: str = "male") -> str:
    """Build a Depop category browse URL."""
    gender_segment = "mens" if (gender or "").lower() == "male" else "womens"
    return f"https://www.depop.com/ca/category/{gender_segment}/{groups}/?sort=newlyListed"


def extract_created_at_from_html(html: str) -> Optional[str]:
    """Extract a created_at timestamp from page hydration HTML."""
    m = CREATED_AT_RX.search(html or "")
    return m.group(1) if m else None


def extract_created_at_from_json_ld(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract common publish timestamp keys from product JSON-LD."""
    if not isinstance(payload, dict):
        return None

    for key in ("datePublished", "dateCreated", "createdAt", "created_at", "publishedAt", "published_at"):
        value = payload.get(key)
        if value:
            return str(value)

    return None


def extract_seller_username_from_href(href: Optional[str]) -> Optional[str]:
    """Extract a seller username from a relative Depop shop link."""
    if not href:
        return None

    path = urlparse(href).path.strip("/")
    if not path:
        return None

    first_segment = path.split("/", 1)[0].strip()
    if not first_segment or first_segment.lower() == "products":
        return None
    return first_segment


def extract_seller_sold_count_from_text(text: str) -> Optional[int]:
    """Extract the seller sold-count from visible page text or HTML."""
    m = SOLD_COUNT_RX.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except Exception:
        return None


def _pick_product_json_ld(page: Page) -> Optional[Dict[str, Any]]:
    """Return the product JSON-LD payload when available."""
    try:
        texts = page.locator("script[type='application/ld+json']").all_inner_texts()
    except Exception:
        texts = []

    for text in texts:
        try:
            payload = json.loads(text)
        except Exception:
            continue

        entries = list(payload) if isinstance(payload, list) else [payload]
        while entries:
            entry = entries.pop(0)
            if not isinstance(entry, dict):
                continue
            if entry.get("@type") == "Product":
                return entry
            if "description" in entry and "offers" in entry:
                return entry
            graph = entry.get("@graph")
            if isinstance(graph, list):
                entries.extend(graph)

    return None


def extract_size_label_from_text(text: str) -> Optional[str]:
    """Extract the visible Depop size label from listing text."""
    if not text:
        return None

    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    for idx, line in enumerate(lines):
        match = SIZE_LINE_RX.match(line)
        if match:
            candidate = match.group(1).strip(" :.-")
            if candidate:
                return candidate

            if idx + 1 < len(lines):
                follow_up = lines[idx + 1].strip(" :.-")
                if follow_up and len(follow_up) <= 20:
                    return follow_up

    inline_match = re.search(
        r"\bsize(?:\s*[:\-])?\s+("
        r"us\s*\d+(?:\.\d+)?|"
        r"\d+(?:\.\d+)?\"?|"
        r"xxxs|xxs|xs|s|m|l|xl|xxl|xxxl|"
        r"one size|o/s|os"
        r")\b",
        text,
        re.I,
    )
    if inline_match:
        return inline_match.group(1).strip()

    return None


def _format_price_from_offer(offers: Any) -> str:
    """Format a JSON-LD offer block to match the UI's display needs."""
    offer = offers[0] if isinstance(offers, list) and offers else offers
    if not isinstance(offer, dict):
        return ""

    price = str(offer.get("price") or "").strip()
    currency = str(offer.get("priceCurrency") or "").upper().strip()
    if not price:
        return ""

    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} " if currency else "")
    if re.fullmatch(r"\d+(?:\.\d+)?", price):
        try:
            return f"{symbol}{float(price):.2f}"
        except Exception:
            pass
    return f"{symbol}{price}".strip()


def _extract_seller_name(page: Page, html: str) -> str:
    """Extract the seller username from stable shop-link selectors."""
    selectors = [
        "a[aria-label$=\"'s shop\"]",
        "a:has-text('Visit shop')",
    ]

    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count():
                text = (loc.inner_text(timeout=500) or "").strip().lstrip("@")
                if text and text.lower() != "visit shop":
                    return text

                href = loc.get_attribute("href")
                username = extract_seller_username_from_href(href)
                if username:
                    return username
        except Exception:
            continue

    hrefs: List[Optional[str]] = []
    try:
        hrefs = page.eval_on_selector_all(
            "a[href]",
            """els => els
                .map(el => el.getAttribute('href'))
                .filter(Boolean)
                .filter(href => href.includes('productId=') || /^\\/[A-Za-z0-9._-]+\\/?$/.test(href))
            """,
        )
    except Exception:
        pass

    for href in hrefs:
        username = extract_seller_username_from_href(href)
        if username:
            return username

    m = re.search(r"item listed by ([A-Za-z0-9._-]+)", html or "", re.I)
    if m:
        return m.group(1)

    return ""


def extract_seller_sold_count(page: Page) -> Optional[int]:
    """Extract the seller's sold count from a seller page."""
    try:
        body_text = page.inner_text("body", timeout=1_500) or ""
        sold_count = extract_seller_sold_count_from_text(body_text)
        if sold_count is not None:
            return sold_count
    except Exception:
        pass

    try:
        html = page.content()
        sold_count = extract_seller_sold_count_from_text(html)
        if sold_count is not None:
            return sold_count
    except Exception:
        pass

    return None


def accept_cookies(page: Page) -> None:
    """Dismiss cookie consent dialogs."""
    for text in ["Accept", "I agree", "Agree", "OK", "Got it"]:
        try:
            button = page.get_by_role("button", name=text, exact=True).first
            if not button.count() or not button.is_visible():
                continue
            button.click(timeout=1000)
            return
        except Exception:
            continue


def dismiss_login_modal(page: Page) -> None:
    """Dismiss login/signup modal popup if it appears ('Want in?' modal)."""
    try:
        if not page.locator('[role="dialog"], [class*="Modal"]').count():
            return
        # Try briefly in case the modal appears, but don't stall every page load.
        close_selectors = [
            # The X button in the modal - look for buttons near the modal content
            "button:has-text('×')",
            "button:has-text('✕')",
            "button:has-text('X')",
            # SVG close buttons
            "button svg[class*='close']",
            "button[class*='close']",
            "button[aria-label='Close']",
            "button[aria-label='close']",
            # Look for button that's a sibling/near "Want in?" text
            "[class*='Modal'] button:not(:has-text('Sign up')):not(:has-text('Log in'))",
        ]
        
        for _ in range(LOGIN_MODAL_MAX_ATTEMPTS):
            # Check each selector
            for selector in close_selectors:
                try:
                    close_btn = page.locator(selector).first
                    if close_btn.count() and close_btn.is_visible(timeout=300):
                        close_btn.click(timeout=2000)
                        log_debug(f"[login-modal] Dismissed login modal via: {selector}")
                        page.wait_for_timeout(LOGIN_MODAL_WAIT_MS)
                        return
                except Exception:
                    continue
            
            # Try JavaScript approach
            try:
                clicked = page.evaluate("""() => {
                    const modal = document.querySelector('[class*="Modal"], [role="dialog"]');
                    if (!modal) return false;
                    
                    const buttons = modal.querySelectorAll('button');
                    for (const btn of buttons) {
                        const text = (btn.textContent || '').trim().toLowerCase();
                        if (text.includes('sign up') || text.includes('log in')) continue;
                        if (btn.offsetParent !== null) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                if clicked:
                    log_debug("[login-modal] Dismissed login modal via JS click")
                    page.wait_for_timeout(LOGIN_MODAL_WAIT_MS)
                    return
            except Exception:
                pass
            
            # Wait before next attempt
            page.wait_for_timeout(LOGIN_MODAL_WAIT_MS)
        
        # Try pressing Escape key as final fallback
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(LOGIN_MODAL_WAIT_MS)
            log_debug("[login-modal] Pressed Escape to dismiss modal", aggregate_key="login_modal_escape")
        except Exception:
            pass
            
    except Exception as e:
        log_debug(f"[login-modal] Error dismissing login modal: {e}")


def remove_sold_sections(page: Page) -> None:
    """Mark sold item sections so link collection can skip them safely."""
    try:
        page.evaluate("""() => {
            const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6, p, span, div"));
            for (const heading of headings) {
                if ((heading.textContent || "").trim().toLowerCase() === "sold items") {
                    const root =
                        heading.closest("section, article, ul, ol") ||
                        heading.parentElement;
                    if (root) {
                        root.setAttribute("data-debot-sold-root", "true");
                    }
                }
            }
        }""")
    except Exception:
        pass


SHOP_PRODUCTS_CAPTURE_SCRIPT = r"""(() => {
    if (window.__debotShopProductsCaptureInstalled) return;
    window.__debotShopProductsCaptureInstalled = true;
    window.__debotShopProductPages = Array.isArray(window.__debotShopProductPages)
        ? window.__debotShopProductPages
        : [];

    const maxPages = 200;
    const shouldCapture = (url) => {
        try {
            const parsed = new URL(url, window.location.origin);
            return /(^|\.)depop\.com$/.test(parsed.hostname) &&
                /\/(?:api\/v3\/shop|presentation\/api\/v1\/shops)\/[^/]+\/products\/?$/.test(parsed.pathname);
        } catch { return false; }
    };
    const record = (url, status, text, retryAfter) => {
        if (!shouldCapture(url) || typeof text !== "string") return;
        const pages = window.__debotShopProductPages;
        const key = String(url);
        const existing = pages.findIndex((page) => page && page.key === key);
        const entry = { key, url: key, status: Number(status) || 0, text, retryAfter };
        if (existing >= 0) pages[existing] = entry;
        else pages.push(entry);
        if (pages.length > maxPages) {
            pages.splice(0, pages.length - maxPages);
        }
    };

    const originalFetch = window.fetch;
    if (typeof originalFetch === "function") {
        window.fetch = async function(...args) {
            const response = await originalFetch.apply(this, args);
            try {
                const input = args[0];
                const url = input && input.url ? input.url : input;
                if (shouldCapture(url)) {
                    response.clone().text().then((text) => {
                        record(url, response.status, text, response.headers.get("retry-after"));
                    }).catch(() => {});
                }
            } catch (error) {}
            return response;
        };
    }

    const OriginalXHR = window.XMLHttpRequest;
    if (OriginalXHR && OriginalXHR.prototype && !window.__debotShopProductsXHRPatched) {
        window.__debotShopProductsXHRPatched = true;
        const originalOpen = OriginalXHR.prototype.open;
        const originalSend = OriginalXHR.prototype.send;
        OriginalXHR.prototype.open = function(method, url, ...rest) {
            this.__debotShopProductsUrl = url;
            return originalOpen.call(this, method, url, ...rest);
        };
        OriginalXHR.prototype.send = function(...args) {
            try {
                this.addEventListener("load", function() {
                    try {
                        let responseBody = "";
                        try {
                            if (!this.responseType || this.responseType === "text") {
                                responseBody = this.responseText || "";
                            } else if (typeof this.response === "string") {
                                responseBody = this.response;
                            } else if (this.response) {
                                responseBody = JSON.stringify(this.response);
                            }
                        } catch (error) {}
                        record(this.__debotShopProductsUrl, this.status, responseBody, this.getResponseHeader("retry-after"));
                    } catch (error) {}
                });
            } catch (error) {}
            return originalSend.apply(this, args);
        };
    }
})()"""


def _is_shop_product_api_url(url: Any) -> bool:
    """Return whether a URL is the seller products API used by Depop shops."""
    parsed = urlparse(str(url or ""))
    return bool(
        parsed.hostname
        and (parsed.hostname == "depop.com" or parsed.hostname.endswith(".depop.com"))
        and re.fullmatch(
            r"/(?:api/v3/shop|presentation/api/v1/shops)/[^/]+/products/?",
            parsed.path,
        )
    )


def _store_captured_shop_product_page(
    page: Page, url: Any, status: Any, text: Any, retry_after: Optional[int] = None,
) -> None:
    """Store a captured seller product API response for later link extraction."""
    if not _is_shop_product_api_url(url) or not isinstance(text, str):
        return

    try:
        normalized_status = int(status or 0)
    except Exception:
        normalized_status = 0

    with _CAPTURED_SHOP_PRODUCT_LOCK:
        pages = _CAPTURED_SHOP_PRODUCT_PAGES.setdefault(page, [])
        pages[:] = [entry for entry in pages if entry["url"] != str(url)]
        pages.append({"url": str(url), "status": normalized_status, "text": text, "retryAfter": retry_after})
        if len(pages) > SHOP_PRODUCTS_CAPTURE_MAX_PAGES:
            del pages[:len(pages) - SHOP_PRODUCTS_CAPTURE_MAX_PAGES]


def install_shop_products_capture(page: Page) -> None:
    """Install hooks that record seller product API responses made by the page."""
    with _CAPTURED_SHOP_PRODUCT_LOCK:
        _CAPTURED_SHOP_PRODUCT_PAGES.setdefault(page, [])
        already_installed = page in _SHOP_PRODUCTS_CAPTURE_INSTALLED_PAGES
        if not already_installed:
            _SHOP_PRODUCTS_CAPTURE_INSTALLED_PAGES.add(page)

    if already_installed:
        return

    if not already_installed:
        try:
            def capture_response(response):
                try:
                    response_url = getattr(response, "url", "")
                    if not _is_shop_product_api_url(response_url):
                        return
                    try:
                        text = response.text()
                    except Exception:
                        # Firefox can omit routed response bodies. The page hook
                        # records those bodies; keep HTTP errors here regardless.
                        text = ""
                    _store_captured_shop_product_page(
                        page,
                        response_url,
                        getattr(response, "status", 0),
                        text,
                        extract_retry_after_seconds(response),
                    )
                except Exception:
                    pass

            page.on("response", capture_response)
            page.on("close", lambda: clear_shop_products_capture(page))
        except Exception:
            pass

    try:
        page.add_init_script(SHOP_PRODUCTS_CAPTURE_SCRIPT)
    except Exception:
        pass

    try:
        page.evaluate(SHOP_PRODUCTS_CAPTURE_SCRIPT)
    except Exception:
        pass


def _read_captured_shop_product_pages(page: Page) -> List[Dict[str, Any]]:
    """Return captured seller product API response payloads from the browser page."""
    with _CAPTURED_SHOP_PRODUCT_LOCK:
        captured_pages = list(_CAPTURED_SHOP_PRODUCT_PAGES.get(page, []))

    try:
        browser_pages = page.evaluate("""() => Array.isArray(window.__debotShopProductPages)
            ? window.__debotShopProductPages.slice()
            : []
        """)
        if isinstance(browser_pages, list):
            captured_pages = [entry for entry in browser_pages if isinstance(entry, dict)] + captured_pages
    except Exception:
        pass

    return captured_pages


def clear_shop_products_capture(page: Page) -> None:
    """Do not carry a previous shop/category's responses into a new navigation."""
    with _CAPTURED_SHOP_PRODUCT_LOCK:
        if page not in _CAPTURED_SHOP_PRODUCT_PAGES:
            return
        _CAPTURED_SHOP_PRODUCT_PAGES[page] = []
    try:
        page.evaluate("() => { window.__debotShopProductPages = []; }")
    except Exception:
        pass


def _shop_product_payload_products(payload: Any) -> List[Dict[str, Any]]:
    """Extract product records from known seller API payload shapes."""
    if not isinstance(payload, dict):
        return []

    for key in ("objects", "products", "results", "items"):
        products = payload.get(key)
        if isinstance(products, list):
            return [product for product in products if isinstance(product, dict)]

    data = payload.get("data")
    if isinstance(data, dict):
        return _shop_product_payload_products(data)

    return []


def _captured_shop_payloads(page: Page):
    """Read successful active-product pages and surface failed pagination."""
    entries = {}
    for entry in _read_captured_shop_product_pages(page):
        url = entry.get("url", "")
        if url and not _is_shop_product_api_url(url):
            continue
        key = url or str(len(entries))
        previous = entries.get(key, {})
        if int(previous.get("status") or 0) >= 400:
            continue
        if entry.get("text") or int(entry.get("status") or 0) >= 400 or key not in entries:
            entries[key] = entry

    for entry in entries.values():
        status = int(entry.get("status") or 0)
        text = entry.get("text") or ""
        if status in (403, 429):
            raise RateLimitError(
                f"Depop returned HTTP {status} while loading more listings.",
                status=status,
                retry_after_seconds=_parse_retry_after_seconds(entry.get("retryAfter")),
            )
        if status >= 400:
            raise FetchError(f"Depop returned HTTP {status} while loading more listings.")
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise FetchError("Depop returned an unreadable shop products response.") from exc
        if not isinstance(payload, dict):
            raise FetchError("Depop returned an unexpected shop products response.")
        yield entry.get("url", ""), payload


def _cache_shop_product(page: Page, product: Dict[str, Any], url: str) -> None:
    """Reuse complete product data already delivered to the public shop page."""
    description = product.get("description")
    if not isinstance(description, str) or not description.strip():
        return
    pricing = product.get("pricing") or {}
    headline = (pricing.get("display_price") or {}).get("headline_price") or {}
    price = _format_price_from_offer({
        "price": headline.get("amount"),
        "priceCurrency": headline.get("currency") or pricing.get("currency"),
    })
    preview = product.get("preview") or next(iter(product.get("pictures") or []), {})
    image = ((preview.get("formats") or {}).get("P0") or {}).get("url")
    created = parse_iso_datetime(str(product.get("created_at") or ""))
    sizes = product.get("sizes") or product.get("variants_all") or []
    size = next((str(s.get("name") or s.get("variant")) for s in sizes
                 if isinstance(s, dict) and (s.get("name") or s.get("variant"))), None)
    seller = extract_seller_username_from_href(page.url)
    if not price or not image or not created or not seller:
        return
    _cache_listing({
        "url": url, "description": description.strip(), "price": price,
        "image": image, "seller": seller, "sizeLabel": size,
        "listedAt": created.isoformat(), "ageDays": age_days_from(created),
        "soldCount": None,
    })


def extract_captured_shop_product_hrefs(page: Page) -> List[str]:
    """Extract product hrefs from captured seller API responses."""
    hrefs: List[str] = []
    seen_slugs: set[str] = set()

    for _, payload in _captured_shop_payloads(page):
        for product in _shop_product_payload_products(payload):
            if product.get("sold") is True:
                continue
            status_text = str(product.get("status") or "").strip().lower()
            if status_text and any(signal in status_text for signal in ("sold", "purchased", "deleted", "removed")):
                continue
            if product.get("active_status") not in (None, "active"):
                continue

            slug = str(product.get("slug") or "").strip().strip("/")
            if not slug or slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            hrefs.append(f"/products/{slug}/")
            _cache_shop_product(page, product, f"https://www.depop.com/products/{slug}/")

    return hrefs


def normalize_product_listing_href(href: Optional[str], origin: str) -> Optional[str]:
    """Normalize Depop product href variants to absolute product URLs."""
    if not href:
        return None

    try:
        origin_parts = urlparse(origin)
        parsed = urlparse(urljoin(origin, str(href).strip()))
    except Exception:
        return None

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    host = parsed.netloc.lower()
    origin_host = origin_parts.netloc.lower()
    if host != origin_host and not host.endswith(".depop.com"):
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    try:
        products_index = next(
            index for index, part in enumerate(path_parts)
            if part.lower() == "products"
        )
    except StopIteration:
        return None

    if products_index != len(path_parts) - 2:
        return None

    slug = path_parts[products_index + 1].strip()
    if not slug or slug.lower() == "create":
        return None

    return f"https://www.depop.com/products/{slug}/"


def _next_shop_products_url(page: Page) -> tuple[Optional[str], bool]:
    """Follow the cursor supplied by the current public shop response."""
    pages = list(_captured_shop_payloads(page))
    for url, payload in reversed(pages):
        info = payload.get("page_info")
        if not isinstance(info, dict) or not _is_shop_product_api_url(url):
            continue
        if info.get("has_more") is False:
            return None, True
        if info.get("has_more") is not True:
            continue
        cursor = info.get("last")
        if not isinstance(cursor, str) or not cursor:
            raise CollectionIncompleteError("Shop reports more listings but supplied no next-page cursor.")
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if query.get("after") == cursor:
            raise CollectionIncompleteError("Shop pagination returned a repeated cursor.")
        query["after"] = cursor
        return parsed._replace(query=urlencode(query)).geturl(), False
    return None, False


def _load_shop_products_page(page: Page, url: str, should_cancel: CancelCheck) -> None:
    wait_for_navigation_slot(should_cancel)
    response = page.evaluate("""async (url) => {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch(url, { signal: controller.signal });
            return {
                status: response.status,
                text: await response.text(),
                retryAfter: response.headers.get('retry-after'),
            };
        } finally { clearTimeout(timeout); }
    }""", url)
    raise_if_cancelled(should_cancel)
    if not isinstance(response, dict):
        raise FetchError("Unable to read the next shop products page.")
    _store_captured_shop_product_page(
        page, url, response.get("status"), response.get("text", ""),
        _parse_retry_after_seconds(response.get("retryAfter")),
    )


def collect_listing_links(
    page: Page,
    max_scrolls: int = 2,
    per_scroll_wait_ms: int = 1200,
    max_links: Optional[int] = None,
    should_cancel: CancelCheck = None,
    aggressive_end_scroll: bool = False,
    excluded_urls: Optional[set[str]] = None,
    before_request: Optional[Callable[[], None]] = None,
) -> List[str]:
    """Collect product listing links from the current page."""
    seen: set = set()
    ordered: List[str] = []
    excluded = excluded_urls or set()
    
    u = urlparse(page.url)
    origin = f"{u.scheme}://{u.netloc}"
    
    def stall_limit() -> int:
        if len(seen) <= EARLY_SCROLL_LINK_THRESHOLD:
            return MAX_STALLED_SCROLL_STEPS + EARLY_SCROLL_STALL_BUFFER
        return MAX_STALLED_SCROLL_STEPS

    def collect_visible_links() -> None:
        hrefs: List[Any] = []

        hrefs.extend(extract_captured_shop_product_hrefs(page))

        try:
            dom_hrefs = page.eval_on_selector_all("a[href]", """
                els => els
                    .filter(e => {
                        if (e.closest('[data-debot-sold-root="true"]')) {
                            return false;
                        }
                        const listItem = e.closest('li');
                        if (listItem) {
                            const text = (listItem.textContent || '').toLowerCase();
                            if (text.includes('sold out')) return false;
                        }
                        return true;
                    })
                    .map(e => e.getAttribute('href'))
            """)
            if isinstance(dom_hrefs, list):
                hrefs.extend(dom_hrefs)
        except Exception:
            pass

        for href in hrefs:
            full = normalize_product_listing_href(href, origin)
            if not full:
                continue
            if full not in seen:
                seen.add(full)
                if full not in excluded:
                    ordered.append(full)
                if max_links and len(ordered) >= max_links:
                    return

    if aggressive_end_scroll:
        # A scroll hint must never truncate a growing shop to 12/24 items.
        total_batches = max(SHOP_PRODUCTS_CAPTURE_MAX_PAGES, max_scrolls)
        stalled_batches = 0
        last_count = 0
        wait_ms = max(per_scroll_wait_ms, BROWSE_END_SCROLL_WAIT_MS)
        requested_pages = set()

        for batch in range(total_batches):
            raise_if_cancelled(should_cancel)
            collect_visible_links()
            if max_links and len(ordered) >= max_links:
                return ordered

            next_url, exhausted = _next_shop_products_url(page)
            if exhausted:
                return ordered
            if next_url:
                if next_url in requested_pages:
                    raise CollectionIncompleteError("Shop pagination stopped advancing before all listings were collected.")
                requested_pages.add(next_url)
                if before_request:
                    before_request()
                _load_shop_products_page(page, next_url, should_cancel)
                continue

            if len(seen) == last_count:
                stalled_batches += 1
            else:
                stalled_batches = 0
            last_count = len(seen)

            if stalled_batches >= stall_limit():
                check_page_for_rate_limit(page, expect_product_links=True)
                return ordered

            if before_request:
                before_request()

            try:
                page.keyboard.press("End")
            except Exception:
                pass
            try:
                page.evaluate("() => window.scrollBy(0, -Math.max(window.innerHeight, 800))")
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            page.wait_for_timeout(wait_ms)
        collect_visible_links()
        if max_links and len(ordered) >= max_links:
            return ordered
        raise CollectionIncompleteError("Collection reached its safety limit before the shop was exhausted.")
    else:
        total_steps = max(max_scrolls, 0) * SCROLL_STEPS_PER_BATCH
        total_steps = max(total_steps, 1)
        stalled_steps = 0
        last_count = 0

        for step in range(total_steps):
            raise_if_cancelled(should_cancel)
            collect_visible_links()
            if max_links and len(ordered) >= max_links:
                return ordered

            if len(seen) == last_count:
                stalled_steps += 1
            else:
                stalled_steps = 0
            last_count = len(seen)

            if step == total_steps - 1 or stalled_steps >= stall_limit():
                break

            try:
                viewport_height = page.evaluate(
                    "() => window.innerHeight || document.documentElement.clientHeight || 800"
                )
            except Exception:
                viewport_height = 800

            scroll_amount = int((viewport_height or 800) * SCROLL_STEP_RATIO)
            if scroll_amount <= 0:
                scroll_amount = 560

            page.evaluate("(amount) => window.scrollBy(0, amount)", scroll_amount)
            page.wait_for_timeout(per_scroll_wait_ms)

    if not ordered:
        check_page_for_rate_limit(page, expect_product_links=True)
    
    return ordered


LISTING_DOM_EXTRACTOR = r"""() => {
    const clean = (value) => (value || "").replace(/[ \t\r\f\v]+/g, " ").trim();
    const keepLines = (value) => (value || "")
        .replace(/\r/g, "\n")
        .split("\n")
        .map((line) => clean(line))
        .filter(Boolean)
        .join("\n")
        .trim();
    const textOf = (selector) => {
        const element = document.querySelector(selector);
        return element ? keepLines(element.innerText || element.textContent || "") : "";
    };
    const firstPrice = (text) => {
        const match = (text || "").match(/[$£€]\s?\d[\d,]*(?:\.\d{2})?/);
        return match ? match[0] : "";
    };
    const usernameFromHref = (href) => {
        try {
            const url = new URL(href || "", window.location.origin);
            const first = url.pathname.replace(/^\/+|\/+$/g, "").split("/")[0] || "";
            if (!first || first.toLowerCase() === "products") return "";
            return first;
        } catch (error) {
            return "";
        }
    };

    const bodyText = document.body ? document.body.innerText || "" : "";
    const description = [
        ...document.querySelectorAll(
            "p[class*='styles_textWrapper__'], [data-testid*='description'], [itemprop='description']"
        ),
    ]
        .map((element) => keepLines(element.innerText || element.textContent || ""))
        .filter((text) => text.length > 8)
        .sort((a, b) => b.length - a.length)[0] || "";

    const imageElement = [
        ...document.querySelectorAll("img.styles_imageItem__UWJs6[src], img[src*='media-photos.depop.com']")
    ].find((image) => {
        const src = image.getAttribute("src") || "";
        const className = String(image.className || "");
        return src.includes("media-photos.depop.com") &&
            !className.includes("_userImage") &&
            !src.includes("/U1.");
    });

    const anchors = [...document.querySelectorAll("a[href]")];
    const shopAnchor =
        anchors.find((anchor) => /'s shop$/i.test(anchor.getAttribute("aria-label") || "")) ||
        anchors.find((anchor) => clean(anchor.innerText || anchor.textContent || "").toLowerCase() === "visit shop") ||
        anchors.find((anchor) => (anchor.getAttribute("href") || "").includes("productId="));
    let seller = usernameFromHref(shopAnchor && shopAnchor.getAttribute("href"));
    if (!seller) {
        const sellerImage = [...document.querySelectorAll("img[alt^='item listed by ']")][0];
        const alt = sellerImage ? sellerImage.getAttribute("alt") || "" : "";
        const match = alt.match(/^item listed by\s+([A-Za-z0-9._-]+)/i);
        seller = match ? match[1] : "";
    }

    const timeElement = document.querySelector("time[datetime]");
    return {
        title: document.title || "",
        bodyText,
        description,
        price: textOf("p[aria-label='Price']") ||
            textOf("[data-testid*='price']") ||
            textOf("[class*='price']") ||
            textOf("[itemprop='price']") ||
            firstPrice(bodyText),
        image: imageElement ? (
            imageElement.getAttribute("src") ||
            ((imageElement.getAttribute("srcset") || "").split(",").pop() || "").trim().split(/\s+/)[0] ||
            ""
        ) : "",
        seller,
        datetime: timeElement ? timeElement.getAttribute("datetime") || "" : "",
        timeText: timeElement ? keepLines(timeElement.innerText || timeElement.textContent || "") : "",
    };
}"""


LISTING_READY_FUNCTION = r"""() => {
    const body = (document.body && document.body.innerText || "").toLowerCase();
    return Boolean(
        document.querySelector("p[class*='styles_textWrapper__'], p[aria-label='Price'], img.styles_imageItem__UWJs6") ||
        body.includes("too many requests") ||
        body.includes("rate limit") ||
        body.includes("checking your browser") ||
        body.includes("access denied")
    );
}"""


def _extract_listing_dom_details(page: Page) -> Dict[str, Any]:
    """Extract listing fields with one browser round-trip."""
    try:
        details = page.evaluate(LISTING_DOM_EXTRACTOR)
        return details if isinstance(details, dict) else {}
    except Exception:
        return {}


def parse_listing(
    page: Page,
    url: str,
    should_cancel: CancelCheck = None,
) -> Optional[Dict[str, Any]]:
    """Parse a single listing page and extract item details."""
    try:
        raise_if_cancelled(should_cancel)
        cached_item = get_cached_listing(url)
        if cached_item is not None:
            return cached_item

        response = guarded_goto(
            page,
            url,
            wait_until="domcontentloaded",
            timeout=60_000,
            should_cancel=should_cancel,
        )
        raise_if_cancelled(should_cancel)

        response_status = _response_status(response)
        if response_status in (404, 410):
            return None
        if response_status is not None and response_status >= 400:
            check_page_for_rate_limit(
                page, response_status=response_status, expect_listing=True,
                retry_after_seconds=extract_retry_after_seconds(response),
            )

        try:
            page.wait_for_function(LISTING_READY_FUNCTION, timeout=FAST_LISTING_READY_TIMEOUT_MS)
        except Exception:
            pass

        dom_details = _extract_listing_dom_details(page)
        desc = str(dom_details.get("description") or "").strip()
        price_text = str(dom_details.get("price") or "").strip()
        image_url = str(dom_details.get("image") or "").strip() or None
        seller_name = str(dom_details.get("seller") or "").strip()
        body_text = str(dom_details.get("bodyText") or "")

        # Listing time
        listed_at_iso: Optional[str] = None
        age_days: Optional[float] = None
        dt_attr = str(dom_details.get("datetime") or "").strip()
        if dt_attr:
            listed_at_iso = dt_attr
            parsed_dt = parse_iso_datetime(dt_attr)
            if parsed_dt:
                age_days = age_days_from(parsed_dt)

        if age_days is None:
            rel_dt = parse_relative_time(str(dom_details.get("timeText") or ""))
            if rel_dt:
                age_days = age_days_from(rel_dt)
                listed_at_iso = rel_dt.isoformat()

        if listed_at_iso is None or not all((desc, price_text, image_url)):
            try:
                product_json_ld = _pick_product_json_ld(page)
            except Exception:
                product_json_ld = None
            if isinstance(product_json_ld, dict):
                if not desc:
                    desc = str(product_json_ld.get("description") or "").strip()
                if not price_text:
                    price_text = _format_price_from_offer(product_json_ld.get("offers"))
                if not image_url:
                    images = product_json_ld.get("image")
                    if isinstance(images, list) and images:
                        image_url = images[0]
                    elif isinstance(images, str):
                        image_url = images

                created_at = extract_created_at_from_json_ld(product_json_ld)
                parsed_dt = parse_iso_datetime(created_at or "")
                if parsed_dt and age_days is None:
                    listed_at_iso = parsed_dt.isoformat()
                    age_days = age_days_from(parsed_dt)

        if not desc:
            check_page_for_rate_limit(
                page,
                response_status=response_status,
                expect_listing=True,
                retry_after_seconds=extract_retry_after_seconds(response),
            )
        if not desc:
            raise FetchError(f"The listing description did not load at {url}.")

        size_label = extract_size_label_from_text(body_text) or extract_size_label_from_text(desc)

        item = {
            "url": url,
            "description": desc,
            "image": image_url,
            "price": price_text,
            "listedAt": listed_at_iso,
            "ageDays": age_days,
            "seller": seller_name,
            "sizeLabel": size_label,
            "soldCount": None,
        }
        if any([desc, price_text, image_url, seller_name]):
            _cache_listing(item)
        return item
    except SearchCancelled:
        raise
    except RateLimitError:
        raise
    except Exception:
        raise


def create_browser_context(
    pw,
    headless: bool = True,
    slowmo: int = 0,
    storage_state: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Create a browser and context with anti-detection settings."""
    browser = pw.firefox.launch(
        headless=headless,
        slow_mo=slowmo,
    )
    context_options: Dict[str, Any] = {
        "viewport": {"width": 1280, "height": 900},
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }
    if storage_state:
        context_options["storage_state"] = storage_state

    try:
        ctx = browser.new_context(**context_options)
        ctx.add_init_script(BROWSER_INIT_SCRIPT)
    except Exception:
        browser.close()
        raise
    try:
        install_resource_blocking(ctx)
    except Exception as exc:
        log_debug(f"[browser] Failed to install resource blocking: {exc}")
    return browser, ctx


def get_following_list(page: Page, username: str, should_cancel: CancelCheck = None) -> List[str]:
    """
    Navigate to a user's profile, click the following button to open modal,
    and extract all usernames they are following.
    """
    profile_url = f"https://www.depop.com/{username.strip().lstrip('@').strip('/')}/"
    log_debug(f"[following] Navigating to {profile_url}")
    
    response = guarded_goto(page, profile_url, wait_until="domcontentloaded", timeout=60000, should_cancel=should_cancel)
    check_page_for_rate_limit(page, response_status=_response_status(response), retry_after_seconds=extract_retry_after_seconds(response))
    accept_cookies(page)
    
    # Dismiss login modal if it pops up
    dismiss_login_modal(page)
    
    following_usernames: List[str] = []
    
    try:
        # Click the following button to open the modal
        # button class="styles_followCount__UzSsn styles_followCountOwnShop__LrExh"
        follow_btn = page.locator("button.styles_followCount__UzSsn").first
        if not follow_btn.count():
            # Try alternative selector
            follow_btn = page.locator("button:has-text('Following')").first
        
        if follow_btn.count():
            follow_btn.click(timeout=5000)
            page.wait_for_timeout(1500)  # Wait for modal to open
            
            # Scroll the modal to load all following
            modal_selector = "[class*='Modal'], [role='dialog'], [class*='modal']"
            max_scroll_attempts = 20
            last_count = 0
            
            for _ in range(max_scroll_attempts):
                raise_if_cancelled(should_cancel)
                # Get usernames from modal
                # <p class="_text_bevez_41 _shared_bevez_6 _normal_bevez_51 _caption1_bevez_55">@username</p>
                usernames = page.eval_on_selector_all(
                    "p._text_bevez_41._shared_bevez_6._normal_bevez_51._caption1_bevez_55",
                    "els => els.map(e => e.textContent || '').filter(t => t.startsWith('@'))"
                )
                
                for uname in usernames:
                    clean_name = uname.strip().lstrip('@')
                    if clean_name and clean_name not in following_usernames:
                        following_usernames.append(clean_name)
                
                # Try scrolling the modal
                try:
                    page.evaluate(f"""() => {{
                        const modal = document.querySelector("{modal_selector}");
                        if (modal) {{
                            const scrollable = modal.querySelector('[class*="scroll"], [style*="overflow"]') || modal;
                            scrollable.scrollTop = scrollable.scrollHeight;
                        }}
                    }}""")
                except Exception:
                    pass
                
                page.wait_for_timeout(800)
                
                # Check if we've loaded more
                if len(following_usernames) == last_count:
                    break
                last_count = len(following_usernames)
            
            # Close modal by pressing Escape or clicking outside
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
                
    except (SearchCancelled, RateLimitError):
        raise
    except Exception as e:
        raise FetchError(f"Unable to read the following list for @{username}.") from e
    
    log_debug(f"[following] Found {len(following_usernames)} accounts")
    return following_usernames
