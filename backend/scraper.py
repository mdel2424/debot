"""Depop scraping utilities using Playwright."""

import re
import datetime as dt
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin, urlparse, urlencode

from playwright.sync_api import sync_playwright, Page, BrowserContext

# Constants
PRICE_RX = re.compile(r"([$£€]\s?\d[\d,]*(?:\.\d{2})?)")
RELTIME_RX = re.compile(r"\b(\d+)\s*(minute|hour|day|week|month)s?\s*ago\b", re.I)
BROWSE_URL = "https://www.depop.com/ca/category/mens/tops/?sort=newlyListed"


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
        m = RELTIME_RX.search(text or "")
        if not m:
            return None
        qty = int(m.group(1))
        unit = m.group(2).lower()
        now = dt.datetime.now(dt.timezone.utc)
        
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
        # Wait up to 3 seconds for the modal to appear, checking periodically
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
        
        # Try for 3 seconds (6 attempts x 500ms)
        for attempt in range(60):
            # Check each selector
            for selector in close_selectors:
                try:
                    close_btn = page.locator(selector).first
                    if close_btn.count() and close_btn.is_visible(timeout=300):
                        close_btn.click(timeout=2000)
                        print(f"[login-modal] Dismissed login modal via: {selector}")
                        page.wait_for_timeout(500)
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
                    print("[login-modal] Dismissed login modal via JS click")
                    page.wait_for_timeout(500)
                    return
            except Exception:
                pass
            
            # Wait before next attempt
            page.wait_for_timeout(500)
        
        # Try pressing Escape key as final fallback
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            print("[login-modal] Pressed Escape to dismiss modal")
        except Exception:
            pass
        except Exception:
            pass
            
    except Exception as e:
        print(f"[login-modal] Error dismissing login modal: {e}")


def remove_sold_sections(page: Page) -> None:
    """Remove sold items section from page."""
    try:
        page.evaluate("""() => {
            const headings = Array.from(document.querySelectorAll(
                "p._text_bevez_41._shared_bevez_6._bold_bevez_47.styles_headingText__YfI1k"
            ));
            for (const h of headings) {
                if ((h.textContent || "").trim().toLowerCase() === "sold items") {
                    const section = h.closest("section") || h.parentElement;
                    if (section) section.remove();
                }
            }
        }""")
    except Exception:
        pass


def collect_listing_links(
    page: Page,
    max_scrolls: int = 2,
    per_scroll_wait_ms: int = 1200,
    max_links: Optional[int] = None
) -> List[str]:
    """Collect product listing links from the current page."""
    seen: set = set()
    ordered: List[str] = []
    
    u = urlparse(page.url)
    origin = f"{u.scheme}://{u.netloc}"
    
    selectors = [
        'li.styles_listItem__Uv9lb a.styles_unstyledLink__DsttP[href^="/products/"]',
        'li.styles_listItem__Uv9lb a[href^="/products/"]',
        'a[href^="/products/"]',
    ]
    
    last_count = -1
    for _ in range(max_scrolls):
        for sel in selectors:
            try:
                hrefs = page.eval_on_selector_all(sel, """
                    els => els
                        .filter(e => {
                            const listItem = e.closest('li');
                            if (!listItem) return true;
                            const text = listItem.textContent || '';
                            if (text.toLowerCase().includes('sold')) return false;
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
                        return ordered
        
        page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        page.wait_for_timeout(per_scroll_wait_ms)
        
        if len(seen) == last_count:
            try:
                page.keyboard.press("End")
            except Exception:
                pass
            page.evaluate("window.scrollBy(0, 200)")
            page.wait_for_timeout(400)
            if len(seen) == last_count:
                break
        last_count = len(seen)
    
    return ordered


def parse_listing(page: Page, url: str) -> Optional[Dict[str, Any]]:
    """Parse a single listing page and extract item details."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        
        # Description
        desc = ""
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
        seller_name = ""
        sold_count = 0
        try:
            sloc = page.locator("a.styles_username__zh8fr").first
            if sloc.count():
                seller_name = (sloc.inner_text(timeout=500) or "").strip()
            
            sold_loc = page.locator("div.styles_signal__D2W6L p").filter(has_text=re.compile(r"\d+\s*sold")).first
            if sold_loc.count():
                txt = sold_loc.inner_text(timeout=500)
                m = re.search(r"(\d+)\s*sold", txt)
                if m:
                    sold_count = int(m.group(1))
        except Exception:
            pass
        
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
        
        return {
            "url": url,
            "description": desc,
            "image": image_url,
            "price": price_text,
            "listedAt": listed_at_iso,
            "ageDays": age_days,
            "seller": seller_name,
            "soldCount": sold_count,
        }
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
    return browser, ctx


def get_following_list(page: Page, username: str) -> List[str]:
    """
    Navigate to a user's profile, click the following button to open modal,
    and extract all usernames they are following.
    """
    profile_url = f"https://www.depop.com/{username.strip().lstrip('@').strip('/')}/"
    print(f"[following] Navigating to {profile_url}")
    
    page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
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
        print(f"[following] Error extracting following list: {e}")
    
    print(f"[following] Found {len(following_usernames)} accounts")
    return following_usernames
