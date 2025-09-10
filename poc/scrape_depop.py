import argparse
import csv
import json
import re
import time
from fractions import Fraction
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

# ---------- logging ----------
VERBOSE = True

def log(msg: str, *args) -> None:
    if VERBOSE:
        ts = time.strftime("%H:%M:%S")
        if args:
            msg = msg.format(*args)
        print(f"[{ts}] {msg}", flush=True)

# ---------- measurement parsing (tops) ----------
class MeasurementParser:
    """Encapsulate measurement pattern matching and helpers for tops."""
    # 19, 19.5, or 19 1/2
    NUM  = r'(?P<val>\d+(?:\.\d+)?(?:\s+\d\/\d)?)'
    # allow optional unit after the numeric value so we can detect 'cm' and convert
    UNIT = r'(?P<unit>\s*(?:cm|mm|in|inch|inches|["″”]))?'

    P2P_LABELS    = r'(?:p2p|pit\s*[- ]?to\s*[- ]?pit|pit[- ]?to[- ]?pit|pit\s*to\s*pit|chest|width|across\s*chest)'
    LENGTH_LABELS = r'(?:length|top\s*to\s*bottom|back\s*length|hps\s*to\s*hem)'

    RE_P2P    = re.compile(rf'\b{P2P_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)
    RE_LENGTH = re.compile(rf'\b{LENGTH_LABELS}\b[^0-9]{{0,10}}{NUM}{UNIT}', re.I)

    # 24x31 or 24×31, with optional unit after each number
    RE_PAIR_X = re.compile(
        r'\b'
        r'(?P<w>\d+(?:\.\d+)?)(?P<u1>\s*(?:cm|mm|in|inch|inches|["″”]))?'
        r'\s*[x×]\s*'
        r'(?P<l>\d+(?:\.\d+)?)(?P<u2>\s*(?:cm|mm|in|inch|inches|["″”]))?'
        r'\b',
        re.I
    )

    def to_inches(self, num_str: str, unit_str: str = "") -> float:
        """
        Handle '19', '19.5', '19 1/2' and convert cm -> inches if unit says cm.
        num_str: the captured numeric part only (no labels/prefixes!)
        unit_str: the captured unit suffix (may be None/empty)
        """
        s = (num_str or "").strip().replace("″", '"').replace("”", '"')
        # parse number including mixed fractions
        if " " in s and "/" in s:
            a, b = s.split(None, 1)
            value = float(a) + float(Fraction(b))
        elif "/" in s:
            value = float(Fraction(s))
        else:
            value = float(s)
        if (unit_str or "").lower().strip().startswith("cm"):
            return value / 2.54
        return value

    def extract_tops(self, text: str) -> Tuple[Optional[float], Optional[float]]:
        """Extract (p2p, length) in inches from free text."""
        t = (text or "").lower().replace("”", '"').replace("″", '"')

        # labeled capture: use named groups 'val' and 'unit'
        p2p_vals = [
            self.to_inches(m.group("val"), m.group("unit") or "")
            for m in self.RE_P2P.finditer(t)
        ]
        len_vals = [
            self.to_inches(m.group("val"), m.group("unit") or "")
            for m in self.RE_LENGTH.finditer(t)
        ]

        # handle 24x31 pairs (units may follow each number independently)
        for m in self.RE_PAIR_X.finditer(t):
            w = self.to_inches(m.group("w"), m.group("u1") or "")
            l = self.to_inches(m.group("l"), m.group("u2") or "")
            if l < w:  # bigger number is length
                w, l = l, w
            p2p_vals.append(w)
            len_vals.append(l)

        p2p = p2p_vals[0] if p2p_vals else None
        length = len_vals[0] if len_vals else None

        if VERBOSE:
            if p2p_vals or len_vals:
                log("Parsed candidates → p2p: {} | length: {}", p2p_vals, len_vals)
            else:
                log("No measurement candidates found in text snippet ({} chars)", len(t))

        # Line-based fallback: scan per-line with the same named captures.
        if p2p is None or length is None:
            simple_p2p_rx = re.compile(rf'\b{self.P2P_LABELS}\b.*?{self.NUM}{self.UNIT}', re.I)
            simple_len_rx = re.compile(rf'\b{self.LENGTH_LABELS}\b.*?{self.NUM}{self.UNIT}', re.I)
            for line in t.splitlines():
                if p2p is None:
                    m = simple_p2p_rx.search(line)
                    if m:
                        try:
                            p2p = self.to_inches(m.group("val"), m.group("unit") or "")
                            log("Line-fallback P2P hit: {}", line.strip())
                        except Exception:
                            pass
                if length is None:
                    m = simple_len_rx.search(line)
                    if m:
                        try:
                            length = self.to_inches(m.group("val"), m.group("unit") or "")
                            log("Line-fallback Length hit: {}", line.strip())
                        except Exception:
                            pass
                if p2p is not None and length is not None:
                    break
        return p2p, length

    def within(self, val: Optional[float], target: Optional[float], tol: float) -> bool:
        if target is None:
            return True
        if val is None:
            return False
        return abs(val - target) <= tol

# Module-level parser instance for convenience
parser = MeasurementParser()

# ---------- scraping ----------
def accept_cookies_if_any(page) -> None:
    # Best-effort click for cookie banners; ignore failures
    for text in ["Accept", "I agree", "Agree", "OK", "Got it"]:
        try:
            page.locator(f"button:has-text('{text}')").first.click(timeout=1500)
            log("Cookie banner accepted with button '{}'", text)
            return
        except Exception:
            continue
    log("No cookie banner handled")

def collect_listing_links(page, max_scrolls, per_scroll_wait_ms: int = 1200) -> List[str]:
    log("Collecting listing links (max_scrolls={}, wait={}ms)", max_scrolls, per_scroll_wait_ms)
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

    for scroll_idx in range(1, max_scrolls + 1):  
        page.wait_for_timeout(per_scroll_wait_ms)      
        before = len(seen)
        for sel in selectors:
            try:
                hrefs = page.eval_on_selector_all(sel, "els => els.map(e => e.getAttribute('href'))")
            except Exception as e:
                log("Selector eval failed: {} ({})", sel, e)
                hrefs = []
            new_here = 0
            for href in hrefs:
                if not href:
                    continue
                abs_url = urljoin(origin, href)
                if abs_url not in seen:
                    new_here += 1
                seen.add(abs_url)
            if hrefs:
                log("Selector yielded {:>4} hrefs (new {:>3}) → {}", len(hrefs), new_here, sel)
        after = len(seen)
        log("Scroll {}/{} → total unique links: {} (Δ{})", scroll_idx, max_scrolls, after, after - before)

        # Scroll to load more
        page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        page.wait_for_timeout(per_scroll_wait_ms)

        # If nothing new was added, try a stronger nudge once, then break
        if after == last_count:
            log("No growth since last pass; nudging page…")
            try:
                page.keyboard.press("End")
            except Exception:
                pass
            page.evaluate("window.scrollBy(0, 200)")
            page.wait_for_timeout(400)
            if len(seen) == last_count:
                log("Still no growth; stopping collection early.")
                break
        last_count = after
    return list(seen)

def parse_listing(page, url: str) -> Optional[Dict[str, Any]]:
    try:
        log("Visiting {}", url)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        # ----- title -----
        title = ""
        tloc = page.locator("h1, h2, [data-testid*='title']").first
        if tloc.count():
            try:
                title = (tloc.inner_text(timeout=1_000) or "").strip()
                log("Title: {}", title[:90])
            except Exception as e:
                log("Title extraction failed: {}", e)

        # ----- description -----
        desc = ""
        dloc = page.locator("p[class*='styles_textWrapper__']").first

        def get_text_from_p(p_locator):
            # Return the full textContent of the <p>, normalized (collapse whitespace)
            return p_locator.evaluate(
                """(el) => {
                    const txt = (el.textContent || "").replace(/\\s+/g, " ").trim();
                    return txt === "" ? null : txt;
                }"""
            )

        if dloc.count():
            try:
                desc = get_text_from_p(dloc) or ""
                log("Primary description wrapper found ({} chars)", len(desc))
            except Exception as e:
                log("Primary description read failed: {}", e)

        # Generic fallbacks if we still don't have a description
        if not desc:
            generic1 = page.locator("[data-testid*='description'], [itemprop='description']").first
            if generic1.count():
                try:
                    desc = (generic1.inner_text(timeout=1_200) or "").strip()
                    log("Fallback description via data-testid/itemprop ({} chars)", len(desc))
                except Exception as e:
                    log("Fallback 1 extraction failed: {}", e)

        if not desc:
            generic2 = page.locator("article, [class*='description']").first
            if generic2.count():
                try:
                    desc = (generic2.inner_text(timeout=1_200) or "").strip()
                    log("Fallback description via article/class*='description' ({} chars)", len(desc))
                except Exception as e:
                    log("Fallback 2 extraction failed: {}", e)

        if not desc:
            log("No description found for {}", url)

        return {"url": url, "title": title, "description": desc}

    except Exception as e:
        log("Error visiting {} → {}", url, e)
        return None

def scrape_depop(max_items: int, headless: bool=True, slowmo_ms: int=0) -> List[Dict[str, Any]]:
    """
    Returns up to max_items recent items with {url,title,description}
    """
    out: List[Dict[str, Any]] = []
    log("Launching Chromium (headless={}, slowmo={}ms)…", headless, slowmo_ms)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=slowmo_ms)
        ctx = browser.new_context(
            user_agent="Depop-Fit-Finder/0.1 (+contact)",
            viewport={"width": 1200, "height": 800},
        )
        page = ctx.new_page()

        # Go straight to search results page
        search_url = "https://www.depop.com/ca/category/mens/tops/tshirts/?sort=newlyListed"
        log("Navigating to search URL: {}", search_url)
        page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        accept_cookies_if_any(page)
        page.wait_for_load_state("networkidle", timeout=60000)
        log("Search page loaded (networkidle)")

        links = collect_listing_links(page, max_scrolls=6)
        log("Collected {} candidate links; visiting up to {}…", len(links), max_items)

        # Visit links and gather descriptions
        for idx, url in enumerate(links, 1):
            if len(out) >= max_items:
                break
            log("[{}/{}] Fetching listing…", idx, len(links))
            item = parse_listing(page, url)
            if item and item.get("description"):
                out.append(item)
                log("✓ Added ({} items total)", len(out))
            else:
                log("· Skipped (no description)")

        log("Closing browser context")
        ctx.close()
        browser.close()
    log("Scraping complete: {} items gathered", len(out))
    return out

# ---------- filtering by measurements ----------
def filter_tops(items: List[Dict[str, Any]],
                p2p: Optional[float],
                length: Optional[float],
                tol: float) -> List[Dict[str, Any]]:
    log("Filtering items with targets: p2p={} in, length={} in, tol=±{} in", p2p, length, tol)
    results = []
    for i, it in enumerate(items, 1):
        text = (it.get("title") or "") + " " + (it.get("description") or "")
        p, L = parser.extract_tops(text)
        if parser.within(p, p2p, tol) and parser.within(L, length, tol):
            it2 = dict(it)
            it2.update({"p2p": p, "length": L})
            results.append(it2)
            log("  ✓ Match #{:>2}: p2p={:.2f} | length={:.2f} | {}", len(results), p or -1, L or -1, it.get("url"))
        else:
            log("  · No match #{:>2}: parsed p2p={} length={} | {}", i, p, L, it.get("url"))
    log("Filtering complete: {} / {} matched", len(results), len(items))
    return results

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Depop description-only scraper (POC)")
    ap.add_argument("--max-items", type=int, default=999)
    ap.add_argument("--headful", action="store_true", help="run browser headful (for debugging)")
    ap.add_argument("--slowmo", type=int, default=0, help="slow motion ms (debug)")
    ap.add_argument("--p2p", type=float, default=None, help="target P2P (inches)")
    ap.add_argument("--length", type=float, default=None, help="target Length (inches)")
    ap.add_argument("--tol", type=float, default=0.5, help="tolerance in inches")
    ap.add_argument("--json", default=None, help="save results to JSON file")
    ap.add_argument("--csv", default=None, help="save results to CSV file")
    ap.add_argument("--quiet", action="store_true", help="suppress progress logs")
    args = ap.parse_args()

    global VERBOSE
    VERBOSE = not args.quiet

    log("Starting scrape (max_items={}, headful={}, slowmo={}ms)…", args.max_items, args.headful, args.slowmo)

    items = scrape_depop(
        max_items=args.max_items,
        headless=not args.headful,
        slowmo_ms=args.slowmo
    )

    print(f"\nFetched {len(items)} recent items with descriptions.\n")

    filtered = filter_tops(items, args.p2p, args.length, args.tol)

    # Print a short preview
    print("\nPreview (up to 15):\n")
    for it in filtered[:15]:
        print(f"- {it.get('title')[:60]}")
        print(f"  {it.get('url')}")
        if it.get("p2p") is not None or it.get("length") is not None:
            print(f"  parsed → p2p={it.get('p2p')}  length={it.get('length')}")
        print()

    # Save
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(filtered, f, ensure_ascii=False, indent=2)
        print(f"Saved JSON → {args.json}")

    if args.csv:
        keys = ["url","title","description","p2p","length"]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for it in filtered:
                row = {k: it.get(k) for k in keys}
                w.writerow(row)
        print(f"Saved CSV  → {args.csv}")

if __name__ == "__main__":
    main()
