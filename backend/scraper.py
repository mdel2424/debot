"""Depop scraping utilities using Playwright."""

import re
import time
import json
import os
import threading
import datetime as dt
from email.utils import parsedate_to_datetime
from typing import Optional, List, Dict, Any, Callable
from urllib.parse import urljoin, urlparse, urlencode

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
LISTING_READY_TIMEOUT_MS = 1_500
LISTING_READY_SELECTOR = (
    "script[type='application/ld+json'], "
    "p[aria-label='Price'], "
    "time[datetime], "
    "a[aria-label$=\"'s shop\"], "
    "a:has-text('Visit shop'), "
    "img[srcset], img[src]"
)
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
CancelCheck = Optional[Callable[[], bool]]
_PENDING_LOG_COUNTS: Dict[str, int] = {"login_modal_escape": 0}
_NAVIGATION_LOCK = threading.Lock()
_LAST_NAVIGATION_STARTED_AT = 0.0


def _read_float_env(name: str, default: float) -> float:
    """Read a non-negative float environment value with a safe fallback."""
    try:
        value = float(os.environ.get(name, default))
        return value if value >= 0 else default
    except Exception:
        return default


MIN_NAV_INTERVAL_SECONDS = _read_float_env("DEBOT_MIN_NAV_INTERVAL_SECONDS", 3.0)


class SearchCancelled(Exception):
    """Raised when a user cancels an in-flight search."""


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


def guarded_goto(page: Page, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 60_000):
    """Serialize Playwright navigations and space their start times."""
    global _LAST_NAVIGATION_STARTED_AT

    with _NAVIGATION_LOCK:
        interval = max(float(MIN_NAV_INTERVAL_SECONDS or 0), 0.0)
        now = time.monotonic()
        delay = interval - (now - _LAST_NAVIGATION_STARTED_AT)
        if delay > 0:
            time.sleep(delay)
        _LAST_NAVIGATION_STARTED_AT = time.monotonic()

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
    expected_content_missing: bool = False,
) -> Optional[str]:
    """Return a user-facing rate-limit message when text/status looks blocked."""
    normalized = re.sub(r"\s+", " ", text or "").strip().lower()

    if status == 429:
        return "Depop returned HTTP 429 Too Many Requests."

    if any(signal in normalized for signal in RATE_LIMIT_TEXT_SIGNALS):
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
        return max(int(delta.total_seconds()), 0)
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

    expected_content_missing = False
    if expect_product_links:
        expected_content_missing = not _page_has_selector(page, 'a[href^="/products/"]')
    elif expect_listing:
        expected_content_missing = not any(
            _page_has_selector(page, selector)
            for selector in (
                "script[type='application/ld+json']",
                "p[aria-label='Price']",
                "time[datetime]",
                "a[aria-label$=\"'s shop\"]",
                "a:has-text('Visit shop')",
                "img[srcset], img[src]",
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

        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("@type") == "Product":
                return entry
            if "description" in entry and "offers" in entry:
                return entry

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
            page.locator(f"button:has-text('{text}')").first.click(timeout=1500)
            return
        except Exception:
            continue


def dismiss_login_modal(page: Page) -> None:
    """Dismiss login/signup modal popup if it appears ('Want in?' modal)."""
    try:
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


def collect_listing_links(
    page: Page,
    max_scrolls: int = 2,
    per_scroll_wait_ms: int = 1200,
    max_links: Optional[int] = None,
    should_cancel: CancelCheck = None,
    aggressive_end_scroll: bool = False,
) -> List[str]:
    """Collect product listing links from the current page."""
    seen: set = set()
    ordered: List[str] = []
    
    u = urlparse(page.url)
    origin = f"{u.scheme}://{u.netloc}"
    
    selectors = ['a[href^="/products/"]']

    def stall_limit() -> int:
        if len(seen) < EARLY_SCROLL_LINK_THRESHOLD:
            return MAX_STALLED_SCROLL_STEPS + EARLY_SCROLL_STALL_BUFFER
        return MAX_STALLED_SCROLL_STEPS

    def collect_visible_links() -> None:
        for sel in selectors:
            try:
                hrefs = page.eval_on_selector_all(sel, """
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
            except Exception:
                hrefs = []

            for href in hrefs:
                if not href:
                    continue
                full = urljoin(origin, href)
                if full not in seen:
                    seen.add(full)
                    ordered.append(full)
                    if max_links and len(seen) >= max_links:
                        return

    if aggressive_end_scroll:
        total_batches = max(max_scrolls, 1)
        stalled_batches = 0
        last_count = 0
        wait_ms = max(per_scroll_wait_ms, BROWSE_END_SCROLL_WAIT_MS)

        for batch in range(total_batches):
            raise_if_cancelled(should_cancel)
            collect_visible_links()
            if max_links and len(seen) >= max_links:
                return ordered

            if len(seen) == last_count:
                stalled_batches += 1
            else:
                stalled_batches = 0
            last_count = len(seen)

            if batch == total_batches - 1 or stalled_batches >= stall_limit():
                break

            try:
                page.keyboard.press("End")
            except Exception:
                pass
            try:
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            page.wait_for_timeout(wait_ms)
    else:
        total_steps = max(max_scrolls, 0) * SCROLL_STEPS_PER_BATCH
        total_steps = max(total_steps, 1)
        stalled_steps = 0
        last_count = 0

        for step in range(total_steps):
            raise_if_cancelled(should_cancel)
            collect_visible_links()
            if max_links and len(seen) >= max_links:
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


def parse_listing(
    page: Page,
    url: str,
    should_cancel: CancelCheck = None,
) -> Optional[Dict[str, Any]]:
    """Parse a single listing page and extract item details."""
    try:
        raise_if_cancelled(should_cancel)
        response = guarded_goto(page, url, wait_until="domcontentloaded", timeout=60_000)
        raise_if_cancelled(should_cancel)
        accept_cookies(page)
        try:
            page.wait_for_selector(LISTING_READY_SELECTOR, timeout=LISTING_READY_TIMEOUT_MS)
        except Exception:
            pass
        dismiss_login_modal(page)

        check_page_for_rate_limit(
            page,
            response_status=_response_status(response),
            expect_listing=True,
            retry_after_seconds=extract_retry_after_seconds(response),
        )
        product_json_ld = _pick_product_json_ld(page)
        page_html = ""
        body_text = ""

        # Description
        desc = ""
        if isinstance(product_json_ld, dict):
            desc = str(product_json_ld.get("description") or "").strip()

        if not desc:
            for selector in [
                "p[class*='styles_textWrapper__']",
                "[data-testid*='description'], [itemprop='description']",
                "article, [class*='description']"
            ]:
                try:
                    loc = page.locator(selector).first
                    if loc.count():
                        desc = (loc.inner_text(timeout=1_000) or "").strip()
                        if desc:
                            break
                except Exception:
                    pass

        # Price
        price_text = ""
        if isinstance(product_json_ld, dict):
            price_text = _format_price_from_offer(product_json_ld.get("offers"))

        if not price_text:
            try:
                ploc = page.locator("p[aria-label='Price']").first
                if ploc.count():
                    price_text = (ploc.inner_text(timeout=800) or "").strip()
            except Exception:
                pass

        if not price_text:
            for sel in ["[data-testid*='price']", "[class*='price']", "[itemprop='price']"]:
                try:
                    loc = page.locator(sel).first
                    if loc.count():
                        price_text = (loc.inner_text(timeout=800) or "").strip()
                        if price_text:
                            break
                except Exception:
                    pass
        
        if not price_text:
            try:
                all_txt = page.inner_text("body", timeout=800)
                m = PRICE_RX.search(all_txt or "")
                if m:
                    price_text = m.group(1)
            except Exception:
                    pass

        # Image
        image_url = None
        if isinstance(product_json_ld, dict):
            images = product_json_ld.get("image")
            if isinstance(images, list) and images:
                image_url = images[0]
            elif isinstance(images, str):
                image_url = images

        if not image_url:
            try:
                img = page.locator("img.styles_imageItem__UWJs6").first
                if img.count():
                    image_url = img.get_attribute("src")
            except Exception:
                pass

        if not image_url:
            try:
                img2 = page.locator("img[srcset], img[src]").first
                if img2.count():
                    srcset = img2.get_attribute("srcset")
                    if srcset:
                        parts = [p.strip() for p in srcset.split(',') if p.strip()]
                        if parts:
                            image_url = parts[-1].split()[0]
                    if not image_url:
                        image_url = img2.get_attribute("src")
            except Exception:
                    pass

        # Seller info
        seller_name = _extract_seller_name(page, "")
        if not seller_name:
            page_html = page.content()
            seller_name = _extract_seller_name(page, page_html)

        try:
            body_text = page.inner_text("body", timeout=1_500) or ""
        except Exception:
            body_text = ""

        size_label = extract_size_label_from_text(body_text)
        if not size_label:
            size_label = extract_size_label_from_text(desc)

        # Listing time
        listed_at_iso: Optional[str] = None
        age_days: Optional[float] = None
        try:
            tloc = page.locator("time[datetime]").first
            if tloc.count():
                dt_attr = tloc.get_attribute("datetime")
                if dt_attr:
                    listed_at_iso = dt_attr
                    parsed_dt = parse_iso_datetime(dt_attr)
                    if parsed_dt:
                        age_days = age_days_from(parsed_dt)
                
                if age_days is None:
                    time_text = tloc.inner_text(timeout=400) or ""
                    rel_dt = parse_relative_time(time_text)
                    if rel_dt:
                        age_days = age_days_from(rel_dt)
                        listed_at_iso = rel_dt.isoformat()
        except Exception:
            pass

        if age_days is None:
            rel_dt = parse_relative_time(body_text)
            if rel_dt:
                age_days = age_days_from(rel_dt)
                listed_at_iso = rel_dt.isoformat()

        if listed_at_iso is None:
            created_at = extract_created_at_from_json_ld(product_json_ld)
            parsed_dt = parse_iso_datetime(created_at or "")
            if parsed_dt:
                listed_at_iso = parsed_dt.isoformat()
                age_days = age_days_from(parsed_dt)

        if listed_at_iso is None:
            if not page_html:
                page_html = page.content()
            created_at = extract_created_at_from_html(page_html)
            parsed_dt = parse_iso_datetime(created_at or "")
            if parsed_dt:
                listed_at_iso = parsed_dt.isoformat()
                age_days = age_days_from(parsed_dt)

        if age_days is None and page_html:
            rel_dt = parse_relative_time(page_html)
            if rel_dt:
                age_days = age_days_from(rel_dt)
                listed_at_iso = rel_dt.isoformat()

        if not any([desc, price_text, image_url, seller_name]):
            check_page_for_rate_limit(
                page,
                response_status=_response_status(response),
                expect_listing=True,
            )

        return {
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
    except SearchCancelled:
        raise
    except RateLimitError:
        raise
    except Exception:
        return None


def create_browser_context(pw, headless: bool = True, slowmo: int = 0) -> tuple:
    """Create a browser and context with anti-detection settings."""
    # Use Firefox - harder to fingerprint than Chromium
    browser = pw.firefox.launch(
        headless=headless,
        slow_mo=slowmo,
    )
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
    )
    try:
        install_resource_blocking(ctx)
    except Exception as exc:
        log_debug(f"[browser] Failed to install resource blocking: {exc}")
    return browser, ctx


def get_following_list(page: Page, username: str) -> List[str]:
    """
    Navigate to a user's profile, click the following button to open modal,
    and extract all usernames they are following.
    """
    profile_url = f"https://www.depop.com/{username.strip().lstrip('@').strip('/')}/"
    log_debug(f"[following] Navigating to {profile_url}")
    
    guarded_goto(page, profile_url, wait_until="domcontentloaded", timeout=60000)
    accept_cookies(page)
    page.wait_for_load_state("networkidle", timeout=60000)
    
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
                
    except Exception as e:
        log_debug(f"[following] Error extracting following list: {e}")
    
    log_debug(f"[following] Found {len(following_usernames)} accounts")
    return following_usernames
