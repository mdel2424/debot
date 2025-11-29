"""Debot API - Depop measurement-based search backend."""

import sys
import asyncio
import datetime as dt
import json
import queue
import threading
import time
from typing import Dict, Any

from fastapi import FastAPI, Request, Body
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright

from parser import parser
from scraper import (
    build_seller_url,
    accept_cookies,
    remove_sold_sections,
    collect_listing_links,
    parse_listing,
    create_browser_context,
    BROWSE_URL,
)

# Windows event loop policy fix
if sys.platform.startswith("win"):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

# In-memory cancellation flags
CANCEL_FLAGS: Dict[str, bool] = {}

# SSE helpers
SSE_PREAMBLE = (":" + (" " * 2048) + "\n").encode("utf-8")


def _sse(data: Dict[str, Any]) -> bytes:
    """Encode data as an SSE event."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _is_cancelled(search_id: str) -> bool:
    """Check if a search has been cancelled."""
    return bool(search_id and CANCEL_FLAGS.get(search_id))


def _process_item(item: Dict[str, Any], target_p2p: float, target_length: float,
                  p2p_tol: float, length_tol: float) -> Dict[str, Any] | None:
    """Check if item matches measurement criteria and return formatted result."""
    text = item.get("description", "")
    w, L = parser.extract_tops(text)
    
    if parser.within(w, target_p2p, p2p_tol) and parser.within(L, target_length, length_tol):
        return {
            "url": item.get("url"),
            "image": item.get("image"),
            "price": item.get("price"),
            "p2p": w,
            "length": L,
            "ageDays": item.get("ageDays"),
            "listedAt": item.get("listedAt"),
            "seller": item.get("seller"),
            "soldCount": item.get("soldCount"),
        }
    return None


@app.post("/api/search/stream")
async def search_stream(request: Request):
    """SSE streaming search endpoint."""
    payload = await request.json()
    print("[stream] payload:", payload)
    
    # Parse request
    category = (payload.get("category") or "tops").lower()
    if category != "tops":
        async def empty_gen():
            yield SSE_PREAMBLE
            yield _sse({"type": "done"})
        return StreamingResponse(empty_gen(), media_type="text/event-stream")
    
    ms = payload.get("measurements") or {}
    target_p2p = float(ms["first"]) if ms.get("first") is not None else None
    target_length = float(ms["second"]) if ms.get("second") is not None else None
    p2p_tol = float(payload.get("p2pTolerance") or 1)
    length_tol = float(payload.get("lengthTolerance") or 0.5)
    
    seller = (payload.get("seller") or "").strip()
    max_items = int(payload.get("maxItems") or 40)
    max_links = int(payload.get("maxLinks") or 1000)
    max_scrolls = int(payload.get("maxScrolls") or 8)
    headless = bool(payload.get("headless", True))
    slowmo = int(payload.get("slowmo") or 0)
    gender = payload.get("gender") or "male"
    groups = payload.get("groups") or "tops"
    search_id = str(payload.get("searchId") or "")
    
    if search_id:
        CANCEL_FLAGS[search_id] = False

    def run_search():
        """Synchronous search generator."""
        try:
            yield SSE_PREAMBLE
            yield _sse({"type": "hello", "searchId": search_id or None, "ts": dt.datetime.utcnow().isoformat()})
            
            with sync_playwright() as pw:
                browser, ctx = create_browser_context(pw, headless=headless, slowmo=slowmo)
                page = ctx.new_page()
                
                try:
                    if seller:
                        yield from _search_seller(
                            ctx, page, seller, groups, gender,
                            target_p2p, target_length, p2p_tol, length_tol,
                            max_items, max_links, max_scrolls, search_id
                        )
                    else:
                        yield from _browse_all(
                            ctx, page,
                            target_p2p, target_length, p2p_tol, length_tol,
                            max_items, max_links, search_id
                        )
                finally:
                    ctx.close()
                    browser.close()
                    
        except Exception as e:
            print(f"[stream] error: {e}")
            yield _sse({"type": "error", "message": str(e), "searchId": search_id or None})
        finally:
            if search_id and search_id in CANCEL_FLAGS:
                del CANCEL_FLAGS[search_id]

    async def async_wrapper():
        """Run sync generator in thread and yield results async."""
        result_queue = queue.Queue()
        error_holder = [None]
        
        def worker():
            try:
                for chunk in run_search():
                    result_queue.put(("data", chunk))
                result_queue.put(("done", None))
            except Exception as e:
                error_holder[0] = e
                result_queue.put(("error", None))
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        
        while True:
            try:
                if await request.is_disconnected():
                    CANCEL_FLAGS[search_id] = True
                    break
            except Exception:
                pass
            
            start = time.time()
            while True:
                try:
                    msg_type, data = result_queue.get_nowait()
                    break
                except queue.Empty:
                    if time.time() - start > 0.1:
                        if not thread.is_alive():
                            if error_holder[0]:
                                raise error_holder[0]
                            try:
                                msg_type, data = result_queue.get_nowait()
                                break
                            except queue.Empty:
                                return
                        await asyncio.sleep(0.01)
                        start = time.time()
                    else:
                        await asyncio.sleep(0.001)
            
            if msg_type == "done":
                break
            elif msg_type == "error" and error_holder[0]:
                raise error_holder[0]
            elif msg_type == "data":
                yield data

    return StreamingResponse(
        async_wrapper(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


def _search_seller(ctx, page, seller, groups, gender,
                   target_p2p, target_length, p2p_tol, length_tol,
                   max_items, max_links, max_scrolls, search_id):
    """Search a specific seller's listings."""
    search_url = build_seller_url(seller, groups=groups, gender=gender)
    print(f"[stream] navigating: {search_url}")
    
    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
    accept_cookies(page)
    page.wait_for_load_state("networkidle", timeout=60000)
    remove_sold_sections(page)
    
    yield _sse({"type": "progress", "phase": "landing", "processed": 0, "total": None, "matches": 0, "searchId": search_id or None})
    
    links = collect_listing_links(page, max_scrolls=max_scrolls, max_links=max_links)
    total = len(links)
    print(f"[stream] collected {total} links")
    
    yield _sse({"type": "meta", "links": total, "seller": seller, "searchId": search_id or None})
    
    matches = 0
    processed = 0
    
    for url in links:
        if _is_cancelled(search_id):
            yield _sse({"type": "cancelled", "searchId": search_id})
            return
        
        item = parse_listing(page, url)
        processed += 1
        
        if item:
            match = _process_item(item, target_p2p, target_length, p2p_tol, length_tol)
            if match:
                print(f"[stream] MATCH {processed}/{total} p2p={match['p2p']} len={match['length']}")
                yield _sse({"type": "match", "item": match, "searchId": search_id or None})
                matches += 1
                
                if matches >= max_items:
                    break
        
        yield _sse({"type": "progress", "processed": processed, "total": total, "matches": matches, "searchId": search_id or None})
    
    yield _sse({"type": "done", "searchId": search_id or None})


def _browse_all(ctx, page, target_p2p, target_length, p2p_tol, length_tol,
                max_items, max_links, search_id):
    """Browse all listings on the category page."""
    print(f"[stream] browsing: {BROWSE_URL}")
    
    page.goto(BROWSE_URL, wait_until="domcontentloaded", timeout=60000)
    accept_cookies(page)
    page.wait_for_load_state("networkidle", timeout=60000)
    
    yield _sse({"type": "progress", "phase": "browsing", "processed": 0, "matches": 0, "searchId": search_id or None})
    
    seen_urls = set()
    processed = 0
    matches = 0
    
    while processed < max_links:
        if _is_cancelled(search_id):
            yield _sse({"type": "cancelled", "searchId": search_id})
            return
        
        found_links = collect_listing_links(page, max_scrolls=1, per_scroll_wait_ms=1000)
        unique_new = [u for u in found_links if u not in seen_urls]
        
        if not unique_new:
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
            found_links = collect_listing_links(page, max_scrolls=1, per_scroll_wait_ms=500)
            unique_new = [u for u in found_links if u not in seen_urls]
            if not unique_new:
                print("[stream] No new links found, stopping.")
                break
        
        for url in unique_new:
            if _is_cancelled(search_id):
                yield _sse({"type": "cancelled", "searchId": search_id})
                return
            
            if processed >= max_links:
                break
            
            seen_urls.add(url)
            
            # Use separate page to preserve scroll state
            item_page = ctx.new_page()
            try:
                item = parse_listing(item_page, url)
            finally:
                item_page.close()
            
            processed += 1
            
            if item:
                match = _process_item(item, target_p2p, target_length, p2p_tol, length_tol)
                if match:
                    # Check seller reputation in browse mode
                    sold_count = match.get("soldCount") or 0
                    if sold_count > 50:
                        seller_name = match.get("seller", "")
                        print(f"[stream] MATCH seller={seller_name} ({sold_count} sold)")
                        yield _sse({"type": "match", "item": match, "seller": seller_name, "searchId": search_id or None})
                        matches += 1
                        
                        if matches >= max_items:
                            yield _sse({"type": "done", "searchId": search_id or None})
                            return
            
            yield _sse({"type": "progress", "processed": processed, "matches": matches, "searchId": search_id or None})
    
    yield _sse({"type": "done", "searchId": search_id or None})


@app.post("/api/search/cancel")
async def cancel_stream(payload: Dict[str, Any] = Body(...)):
    """Cancel a running search stream."""
    search_id = str(payload.get("searchId") or "")
    if not search_id:
        return {"ok": False, "error": "missing searchId"}
    CANCEL_FLAGS[search_id] = True
    print(f"[cancel] requested for searchId={search_id}")
    return {"ok": True, "searchId": search_id}
