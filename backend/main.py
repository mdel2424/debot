import sys
import asyncio
import math
import re
import datetime as dt
from fractions import Fraction
from typing import Optional, Tuple, List, Dict, Any
from urllib.parse import urljoin, urlparse, urlencode
import json
import threading

from fastapi import FastAPI, Request, Body
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright

if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

app = FastAPI()

# Allow local dev frontends to connect directly (bypass dev proxy buffering for SSE)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost",
        "http://127.0.0.1",
        "*",  # dev-only broad allow
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)
 
# Simple in-memory cancellation flags for active streams
CANCEL_FLAGS: Dict[str, bool] = {}

RELTIME_RX = re.compile(r"\b(\d+)\s*(minute|hour|day|week|month)s?\s*ago\b", re.I)

def parse_iso_datetime(ts: str) -> Optional[dt.datetime]:
    """Parse an ISO datetime string (with optional trailing Z) to a UTC-aware datetime."""
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
    delta = dt.datetime.now(dt.timezone.utc) - ts_val
    return max(delta.total_seconds() / 86400.0, 0.0)

def parse_relative_time(text: str) -> Optional[dt.datetime]:
    """Convert relative phrases like 'Listed 3 hours ago' to an approximate UTC datetime."""
    try:
        m = RELTIME_RX.search(text or "")
        if not m:
            return None
        qty = int(m.group(1))
        unit = m.group(2).lower()
        now = dt.datetime.now(dt.timezone.utc)
        if unit.startswith("minute"):
            delta = dt.timedelta(minutes=qty)
        elif unit.startswith("hour"):
            delta = dt.timedelta(hours=qty)
        elif unit.startswith("day"):
            delta = dt.timedelta(days=qty)
        elif unit.startswith("week"):
            delta = dt.timedelta(weeks=qty)
        elif unit.startswith("month"):
            delta = dt.timedelta(days=qty * 30)
        else:
            return None
        return now - delta
    except Exception:
        return None

class MeasurementParser:
    NUM  = r'(?P<val>\d+(?:\.\d+)?(?:\s+\d\/\d)?)'
    UNIT = r'(?P<unit>\s*(?:cm|mm|in|inch|inches|["″”]))?'
    P2P_LABELS    = r'(?:p2p|pit\s*[- ]?to\s*[- ]?pit|pit[- ]?to[- ]?pit|pit\s*to\s*pit|chest|width|across\s*chest)'
    LENGTH_LABELS = r'(?:length|top\s*to\s*bottom|back\s*length|hps\s*to\s*hem)'
    RE_P2P    = re.compile(rf'\b{P2P_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_LENGTH = re.compile(rf'\b{LENGTH_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_PAIR_X = re.compile(
        r'\b'
        r'(?P<w>\d+(?:\.\d+)?)(?P<u1>\s*(?:cm|mm|in|inch|inches|["″”]))?'
        r'\s*[x×]\s*'
        r'(?P<l>\d+(?:\.\d+)?)(?P<u2>\s*(?:cm|mm|in|inch|inches|["″”]))?'
        r'\b', re.I)

    def to_inches(self, num_str: str, unit_str: str = "") -> float:
        s = (num_str or "").strip().replace("″", '"').replace("”", '"')
        if " " in s and "/" in s:
            a, b = s.split(None, 1)
            value = float(a) + float(Fraction(b))
        elif "/" in s:
            value = float(Fraction(s))
        else:
            value = float(s)
        u = (unit_str or "").lower().strip()
        if u.startswith("cm"):
            return value / 2.54
        return value

    def extract_tops(self, text: str) -> Tuple[Optional[float], Optional[float]]:
        t = (text or "").lower().replace("”", '"').replace("″", '"')
        p2p_vals = [self.to_inches(m.group("val"), m.group("unit") or "") for m in self.RE_P2P.finditer(t)]
        len_vals = [self.to_inches(m.group("val"), m.group("unit") or "") for m in self.RE_LENGTH.finditer(t)]
        for m in self.RE_PAIR_X.finditer(t):
            w = self.to_inches(m.group("w"), m.group("u1") or "")
            l = self.to_inches(m.group("l"), m.group("u2") or "")
            if l < w: w, l = l, w
            p2p_vals.append(w)
            len_vals.append(l)
        p2p = p2p_vals[0] if p2p_vals else None
        length = len_vals[0] if len_vals else None
        if p2p is None or length is None:
            sp = re.compile(rf'\b{self.P2P_LABELS}\b.*?{self.NUM}{self.UNIT}', re.I)
            sl = re.compile(rf'\b{self.LENGTH_LABELS}\b.*?{self.NUM}{self.UNIT}', re.I)
            for line in t.splitlines():
                if p2p is None:
                    m = sp.search(line)
                    if m:
                        try: p2p = self.to_inches(m.group("val"), m.group("unit") or "")
                        except: pass
                if length is None:
                    m = sl.search(line)
                    if m:
                        try: length = self.to_inches(m.group("val"), m.group("unit") or "")
                        except: pass
                if p2p is not None and length is not None: break
        return p2p, length

    def within(self, val: Optional[float], target: Optional[float], tol: float) -> bool:
        if target is None: return True
        if val is None:    return False
        return abs(val - target) <= tol

parser = MeasurementParser()

def filter_tops(items: List[Dict[str, Any]], p2p: Optional[float], length: Optional[float], tol: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
    # Only use description for measurements (ignore title)
        text = f"{it.get('description','')}"
        w, L = parser.extract_tops(text)
        if parser.within(w, p2p, tol) and parser.within(L, length, tol):
            it2 = dict(it)
            it2.update({"p2p": w, "length": L})
            out.append(it2)
    return out

def accept_cookies_if_any(page) -> None:
    for text in ["Accept", "I agree", "Agree", "OK", "Got it"]:
        try:
            page.locator(f"button:has-text('{text}')").first.click(timeout=1500)
            return
        except Exception:
            continue

def remove_sold_sections(page) -> None:
    """Remove the sold-items section so sold listings are not rendered/scanned."""
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

def collect_listing_links(page, max_scrolls: int = 2, per_scroll_wait_ms: int = 1200, max_links: Optional[int] = None) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    def page_origin() -> str:
        u = urlparse(page.url)
        return f"{u.scheme}://{u.netloc}"
    origin = page_origin()
    last_count = -1
    selectors = [
        'li.styles_listItem__Uv9lb a.styles_unstyledLink__DsttP[href^="/products/"]',
        'li.styles_listItem__Uv9lb a[href^="/products/"]',
        'a[href^="/products/"]',
        'a[href*="/products/"]',
        'a[href*="/listing/"]',
    ]
    for _ in range(max_scrolls):
        for sel in selectors:
            try:
                # Filter out sold items by checking for sold indicators in parent elements
                hrefs = page.eval_on_selector_all(sel, """
                    els => els
                        .filter(e => {
                            // Check if item is marked as sold
                            const listItem = e.closest('li');
                            if (!listItem) return true;
                            
                            // Look for sold indicators in the list item
                            const text = listItem.textContent || '';
                            if (text.toLowerCase().includes('sold')) return false;
                            
                            // Check for sold class names
                            const classes = listItem.className || '';
                            if (classes.includes('sold') || classes.includes('Sold')) return false;
                            
                            // Check for aria-labels or data attributes indicating sold status
                            if (listItem.getAttribute('aria-label')?.toLowerCase().includes('sold')) return false;
                            if (listItem.getAttribute('data-sold') === 'true') return false;
                            
                            return true;
                        })
                        .map(e => e.getAttribute('href'))
                """)
            except Exception:
                hrefs = []
            for href in hrefs:
                if not href: continue
                full = urljoin(origin, href)
                if full in seen:
                    continue
                seen.add(full)
                ordered.append(full)
                if max_links and len(seen) >= max_links:
                    return ordered
        page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        page.wait_for_timeout(per_scroll_wait_ms)
        if len(seen) == last_count:
            try: page.keyboard.press("End")
            except Exception: pass
            page.evaluate("window.scrollBy(0, 200)")
            page.wait_for_timeout(400)
            if len(seen) == last_count: break
        last_count = len(seen)
    return ordered

PRICE_RX = re.compile(r"([$£€]\s?\d[\d,]*(?:\.\d{2})?)")

def parse_listing(page, url: str) -> Optional[Dict[str, Any]]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        desc = ""
        dloc = page.locator("p[class*='styles_textWrapper__']").first
        if dloc.count():
            try: desc = dloc.inner_text(timeout=1_000) or ""
            except Exception: pass
        if not desc:
            generic1 = page.locator("[data-testid*='description'], [itemprop='description']").first
            if generic1.count():
                try: desc = (generic1.inner_text(timeout=1_000) or "").strip()
                except Exception: pass
        if not desc:
            generic2 = page.locator("article, [class*='description']").first
            if generic2.count():
                try: desc = (generic2.inner_text(timeout=1_000) or "").strip()
                except Exception: pass

        # Price (prefer explicit selector provided)
        price_text = ""
        try:
            ploc = page.locator("p._text_bevez_41._shared_bevez_6._normal_bevez_51.styles_price__H8qdh[aria-label='Price']").first
            if ploc.count():
                price_text = (ploc.inner_text(timeout=800) or "").strip()
        except Exception:
            price_text = ""
        if not price_text:
            for sel in ["[data-testid*='price']", "[class*='price']", "[itemprop='price']", "[aria-label*='price']"]:
                loc = page.locator(sel).first
                if loc.count():
                    try:
                        price_text = (loc.inner_text(timeout=800) or "").strip()
                        if price_text:
                            break
                    except Exception:
                        pass
        if not price_text:
            # fallback: find a currency-like token in entire page text
            try:
                all_txt = page.inner_text("body", timeout=800)
                m = PRICE_RX.search(all_txt or "")
                if m:
                    price_text = m.group(1)
            except Exception:
                pass

        # Image (prefer explicit selector provided)
        image_url = None
        try:
            img = page.locator("img.styles_imageItem__UWJs6.styles_imageItemNonSquare__VJ0R6").first
            if img.count():
                image_url = img.get_attribute("src")
        except Exception:
            image_url = None
        if not image_url:
            # fallback: get from generic image element
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
                image_url = None

        # Listing time and age
        listed_at_iso: Optional[str] = None
        listed_ts: Optional[float] = None
        age_days: Optional[float] = None
        try:
            tloc = page.locator("time._text_bevez_41._shared_bevez_6._normal_bevez_51._caption2_bevez_61.styles_text__AMrZL[datetime]").first
            if not tloc.count():
                tloc = page.locator("time[datetime]").first
            if tloc.count():
                dt_attr = tloc.get_attribute("datetime")
                time_text = ""
                try:
                    time_text = tloc.inner_text(timeout=400) or ""
                except Exception:
                    time_text = ""
                if dt_attr:
                    listed_at_iso = dt_attr
                    parsed_dt = parse_iso_datetime(dt_attr)
                    if parsed_dt:
                        listed_ts = parsed_dt.timestamp()
                        age_days = age_days_from(parsed_dt)
                if listed_ts is None and time_text:
                    rel_dt = parse_relative_time(time_text)
                    if rel_dt:
                        listed_ts = rel_dt.timestamp()
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
            "listedTs": listed_ts,
            "ageDays": age_days,
        }
    except Exception:
        return None

def scrape_descriptions_sync(query: str, max_items: int = 30) -> List[Dict[str, Any]]:
    # Placeholder: ignoring query for now, using category page for new listings
    return scrape_depop(max_items=max_items, headless=True)

def build_seller_url(seller: str, groups: str = "tops", gender: Optional[str] = "male") -> str:
    base = f"https://www.depop.com/{seller.strip().lstrip('@').strip('/')}/"
    params = {"sort": "recent", "groups": groups}
    if gender:
        params["gender"] = gender
    return base + "?" + urlencode(params)


def scrape_depop(max_items: int, headless: bool = True, slowmo_ms: int = 0, max_scrolls: int = 2,
                 seller: Optional[str] = None, groups: str = "tops", gender: Optional[str] = "male",
                 max_links: Optional[int] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=slowmo_ms)
        ctx = browser.new_context(user_agent="Depop-Fit-Finder/0.1 (+contact)", viewport={"width": 1200, "height": 800})
        page = ctx.new_page()
        if seller:
            search_url = build_seller_url(seller, groups=groups, gender=gender)
        else:
            search_url = "https://www.depop.com/ca/category/mens/tops/tshirts/?sort=newlyListed"
        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        accept_cookies_if_any(page)
        page.wait_for_load_state("networkidle", timeout=60000)
        remove_sold_sections(page)
        links = collect_listing_links(page, max_scrolls=max_scrolls, max_links=max_links)
        for url in links:
            if len(out) >= max_items: break
            item = parse_listing(page, url)
            if item and item.get("description"): out.append(item)
        ctx.close(); browser.close()
    return out

# ---- API

@app.post("/api/crawl")
async def crawl(payload: Dict[str, Any] = Body(...)):
    query = str(payload.get("query") or "")
    max_items = int(payload.get("maxItems") or 20)
    items = await asyncio.to_thread(scrape_descriptions_sync, query, max_items)
    return {"count": len(items), "items": items}


@app.post("/api/search")
async def search(request: Request):
    """
    Expects your frontend payload:
    {
      "category": "tops" | "bottoms",
      "measurements": { "first": number, "second": number },
      "tolerance": number?   // optional, defaults 0.5"
    }

    For MVP, we treat tops: first = P2P, second = Length.
    """
    payload = await request.json()
    print("Received payload:", payload)  # debug

    category = (payload.get("category") or "tops").lower()
    ms = payload.get("measurements") or {}
    tol = float(payload.get("tolerance") or 0.5)

    first = ms.get("first")
    second = ms.get("second")
    target_p2p = float(first) if first is not None else None
    target_length = float(second) if second is not None else None

    if category != "tops":
        return {"count": 0, "items": []}

    seller = (payload.get("seller") or "").strip()
    max_items = int(payload.get("maxItems") or 40)
    headless = bool(payload.get("headless", True))
    slowmo = int(payload.get("slowmo") or 0)
    max_scrolls = int(payload.get("maxScrolls") or 2)
    gender = payload.get("gender") or "male"
    groups = payload.get("groups") or "tops"
    max_links = int(payload.get("maxLinks") or 1000)

    def _job():
        raw = scrape_depop(max_items=max_items, headless=headless, slowmo_ms=slowmo,
                           max_scrolls=max_scrolls, seller=seller or None,
                           groups=groups, gender=gender, max_links=max_links)
        return filter_tops(raw, target_p2p, target_length, tol)

    filtered = await asyncio.to_thread(_job)
    # Only return requested fields
    items = [{
        "url": it.get("url"),
        "image": it.get("image"),
        "price": it.get("price"),
        "p2p": it.get("p2p"),
        "length": it.get("length"),
        "ageDays": it.get("ageDays"),
        "listedAt": it.get("listedAt"),
    } for it in filtered]
    return {"count": len(items), "items": items}


def _sse(data: Dict[str, Any]) -> bytes:
    # Encode a single SSE data event as bytes.
    return (f"data: {json.dumps(data, ensure_ascii=False)}\n\n").encode("utf-8")

# A preamble to nudge proxies (and the browser/dev proxy) to start streaming.
# Many proxies buffer small responses; a 2KB comment safely exceeds typical thresholds.
SSE_PREAMBLE: bytes = (":" + (" " * 2048) + "\n").encode("utf-8")


@app.post("/api/search/stream")
async def search_stream(request: Request):
    """
    Server-Sent Events (SSE) streaming endpoint.

    Request payload (subset):
    - category: "tops"
    - measurements: { first: number|null, second: number|null }  # first=P2P, second=Length
    - tolerance: number (default 0.5)
    - seller: string (seller handle)
    - searchId: string (used to cancel an in-flight stream)
    - maxItems, maxLinks, headless, slowmo, maxScrolls, gender, groups

    Events:
    - data: { type: "meta", links, seller?, searchId? }
    - data: { type: "match", item: { url, image, price, p2p, length }, searchId? }
    - data: { type: "cancelled", searchId }
    - data: { type: "error", message, searchId? }
    - data: { type: "done", searchId? }
    """
    payload = await request.json()
    print("[stream] payload:", payload)

    category = (payload.get("category") or "tops").lower()
    ms = payload.get("measurements") or {}
    tol = float(payload.get("tolerance") or 0.5)
    first = ms.get("first")
    second = ms.get("second")
    target_p2p = float(first) if first is not None else None
    target_length = float(second) if second is not None else None
    if category != "tops":
        async def empty_gen():
            yield SSE_PREAMBLE
            yield _sse({"type": "done"})
        return StreamingResponse(empty_gen(), media_type="text/event-stream")

    seller = (payload.get("seller") or "").strip()
    max_items = int(payload.get("maxItems") or 40)
    headless = bool(payload.get("headless", True))
    slowmo = int(payload.get("slowmo") or 0)
    max_scrolls = int(payload.get("maxScrolls") or 8)
    max_links = int(payload.get("maxLinks") or 1000)
    gender = payload.get("gender") or "male"
    groups = payload.get("groups") or "tops"
    search_id = str(payload.get("searchId") or "")
    if search_id:
        CANCEL_FLAGS[search_id] = False

    def generator():
        # Use a sync generator that yields bytes directly - no queue, no thread pool
        try:
            # Yield preamble and hello immediately
            yield SSE_PREAMBLE
            yield _sse({"type": "hello", "searchId": search_id or None, "ts": dt.datetime.utcnow().isoformat()})
            
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=headless, slow_mo=slowmo)
                ctx = browser.new_context(user_agent="Depop-Fit-Finder/0.1 (+contact)", viewport={"width": 1200, "height": 800})
                page = ctx.new_page()
                
                if seller:
                    search_url = build_seller_url(seller, groups=groups, gender=gender)
                else:
                    search_url = "https://www.depop.com/ca/category/mens/tops/tshirts/?sort=newlyListed"
                    
                print(f"[stream] navigating: {search_url}")
                page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                accept_cookies_if_any(page)
                page.wait_for_load_state("networkidle", timeout=60000)
                remove_sold_sections(page)
                
                # Send progress after landing
                yield _sse({"type": "progress", "phase": "landing", "processed": 0, "total": None, "matches": 0, "searchId": search_id or None})
                
                links = collect_listing_links(page, max_scrolls=max_scrolls, max_links=max_links)
                total = len(links)
                print(f"[stream] collected {total} links")
                
                # Send meta immediately after collecting links
                yield _sse({"type": "meta", "links": total, "seller": seller or None, "searchId": search_id or None})

                matches = 0
                processed = 0
                
                for i, url in enumerate(links):
                    # Check cancellation flag
                    if search_id and CANCEL_FLAGS.get(search_id):
                        print(f"[stream] cancelled {search_id}")
                        yield _sse({"type": "cancelled", "searchId": search_id})
                        break
                        
                    try:
                        item = parse_listing(page, url)
                        if not item:
                            processed += 1
                            # Send progress update immediately after each failed parse
                            yield _sse({"type": "progress", "phase": "parsing", "processed": processed, "total": total, "matches": matches, "searchId": search_id or None})
                            try:
                                sys.stdout.write(f"\r[stream] processed {processed}/{total} matches={matches}")
                                sys.stdout.flush()
                            except Exception:
                                pass
                            continue
                            
                        text = f"{item.get('description','')}"
                        w, L = parser.extract_tops(text)
                        
                        if parser.within(w, target_p2p, tol) and parser.within(L, target_length, tol):
                            item2 = {
                                "url": item.get("url"),
                                "image": item.get("image"),
                                "price": item.get("price"),
                                "p2p": w,
                                "length": L,
                                "ageDays": item.get("ageDays"),
                                "listedAt": item.get("listedAt"),
                            }
                            try:
                                sys.stdout.write("\n")
                            except Exception:
                                pass
                            print(f"[stream] MATCH {processed+1}/{total} p2p={w} len={L} price={item2['price']} ageDays={item2.get('ageDays')}")
                            
                            # Send match immediately
                            yield _sse({"type": "match", "item": item2, "searchId": search_id or None})
                            matches += 1
                            
                        processed += 1
                        
                        # Send progress update immediately after each item
                        yield _sse({"type": "progress", "processed": processed, "total": total, "matches": matches, "searchId": search_id or None})
                        
                        try:
                            sys.stdout.write(f"\r[stream] processed {processed}/{total} matches={matches}")
                            sys.stdout.flush()
                        except Exception:
                            pass
                            
                        if matches >= max_items:
                            print(f"[stream] reached maxItems={max_items}")
                            break
                            
                    except Exception as e:
                        processed += 1
                        try:
                            sys.stdout.write(f"\r[stream] processed {processed}/{total} matches={matches}")
                            sys.stdout.flush()
                        except Exception:
                            pass
                        continue
                        
                try:
                    sys.stdout.write("\n")
                except Exception:
                    pass
                    
                # Send done event
                yield _sse({"type": "done", "searchId": search_id or None})
                ctx.close()
                browser.close()
                
        except Exception as e:
            print("[stream] fatal:", e)
            yield _sse({"type": "error", "message": str(e), "searchId": search_id or None})
            yield _sse({"type": "done", "searchId": search_id or None})
        finally:
            # Cleanup cancel flag
            if search_id and search_id in CANCEL_FLAGS:
                try:
                    del CANCEL_FLAGS[search_id]
                except Exception:
                    pass

    async def async_generator():
        # Run the sync generator in a thread and yield results asynchronously
        import queue
        import threading
        
        result_queue = queue.Queue()
        exception_holder = [None]
        
        def sync_worker():
            try:
                for chunk in generator():
                    result_queue.put(('data', chunk))
                result_queue.put(('done', None))
            except Exception as e:
                exception_holder[0] = e
                result_queue.put(('error', None))
        
        # Start the sync generator in a background thread
        worker_thread = threading.Thread(target=sync_worker, daemon=True)
        worker_thread.start()
        
        while True:
            # Check for cancellation
            try:
                if await request.is_disconnected():
                    CANCEL_FLAGS[search_id] = True
                    break
            except Exception:
                pass
                
            # Get next item with a short timeout to allow cancellation checks
            try:
                import time
                start_time = time.time()
                while True:
                    try:
                        msg_type, data = result_queue.get_nowait()
                        break
                    except queue.Empty:
                        if time.time() - start_time > 0.1:  # 100ms timeout
                            # Check if worker is still alive
                            if not worker_thread.is_alive():
                                if exception_holder[0]:
                                    raise exception_holder[0]
                                # Worker finished cleanly, check queue one more time
                                try:
                                    msg_type, data = result_queue.get_nowait()
                                    break
                                except queue.Empty:
                                    return  # Done
                            await asyncio.sleep(0.01)  # Small async sleep
                            start_time = time.time()
                        else:
                            await asyncio.sleep(0.001)
                            
                if msg_type == 'done':
                    break
                elif msg_type == 'error':
                    if exception_holder[0]:
                        raise exception_holder[0]
                    break
                elif msg_type == 'data':
                    yield data
                    
            except Exception as e:
                print(f"[stream] async_generator error: {e}")
                yield _sse({"type": "error", "message": str(e), "searchId": search_id or None})
                break

    return StreamingResponse(
        async_generator(),
        media_type="text/event-stream",
        headers={
            # Avoid proxies altering or buffering the stream
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # CORS safety valve if proxy is bypassed
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.post("/api/search/cancel")
async def cancel_stream(payload: Dict[str, Any] = Body(...)):
    """Set a cancellation flag for a running stream by searchId."""
    search_id = str(payload.get("searchId") or "")
    if not search_id:
        return {"ok": False, "error": "missing searchId"}
    CANCEL_FLAGS[search_id] = True
    print(f"[cancel] requested for searchId={search_id}")
    return {"ok": True, "searchId": search_id}
