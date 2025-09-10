import sys
import asyncio
import math
import re
import datetime as dt
from fractions import Fraction
from typing import Optional, Tuple, List, Dict, Any
from urllib.parse import urljoin, urlparse

from fastapi import FastAPI, Request, Body
from playwright.sync_api import sync_playwright

if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

app = FastAPI()

RELTIME_RX = re.compile(r"\b(\d+)\s*(minute|hour|day|week|month)s?\s*ago\b", re.I)

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
        text = f"{it.get('title','')} {it.get('description','')}"
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

def collect_listing_links(page, max_scrolls: int = 2, per_scroll_wait_ms: int = 1200) -> List[str]:
    seen: set[str] = set()
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
                hrefs = page.eval_on_selector_all(sel, "els => els.map(e => e.getAttribute('href'))")
            except Exception:
                hrefs = []
            for href in hrefs:
                if not href: continue
                seen.add(urljoin(origin, href))
        page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        page.wait_for_timeout(per_scroll_wait_ms)
        if len(seen) == last_count:
            try: page.keyboard.press("End")
            except Exception: pass
            page.evaluate("window.scrollBy(0, 200)")
            page.wait_for_timeout(400)
            if len(seen) == last_count: break
        last_count = len(seen)
    return list(seen)

def parse_listing(page, url: str) -> Optional[Dict[str, Any]]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        title = ""
        tloc = page.locator("h1, h2, [data-testid*='title']").first
        if tloc.count():
            try: title = (tloc.inner_text(timeout=1_000) or "").strip()
            except Exception: pass
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
        return {"url": url, "title": title, "description": desc}
    except Exception:
        return None

def scrape_descriptions_sync(query: str, max_items: int = 30) -> List[Dict[str, Any]]:
    # Placeholder: ignoring query for now, using category page for new listings
    return scrape_depop(max_items=max_items, headless=True)

def scrape_depop(max_items: int, headless: bool = True, slowmo_ms: int = 0, max_scrolls: int = 2) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=slowmo_ms)
        ctx = browser.new_context(user_agent="Depop-Fit-Finder/0.1 (+contact)", viewport={"width": 1200, "height": 800})
        page = ctx.new_page()
        search_url = "https://www.depop.com/ca/category/mens/tops/tshirts/?sort=newlyListed"
        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        accept_cookies_if_any(page)
        page.wait_for_load_state("networkidle", timeout=60000)
        links = collect_listing_links(page, max_scrolls=max_scrolls)
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

    max_items = int(payload.get("maxItems") or 40)
    headless = bool(payload.get("headless", True))
    slowmo = int(payload.get("slowmo") or 0)
    max_scrolls = int(payload.get("maxScrolls") or 2)

    def _job():
        raw = scrape_depop(max_items=max_items, headless=headless, slowmo_ms=slowmo, max_scrolls=max_scrolls)
        return filter_tops(raw, target_p2p, target_length, tol)

    filtered = await asyncio.to_thread(_job)
    return {"count": len(filtered), "items": filtered}
