"""Debot API - Depop measurement-based search backend."""

import sys
import asyncio
import datetime as dt
import json
import queue
import threading
import time
from typing import Dict, Any, Optional, Callable

from fastapi import FastAPI, Request, Body
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright

from parser import parser
from scraper import (
    build_seller_url,
    accept_cookies,
    check_page_for_rate_limit,
    dismiss_login_modal,
    remove_sold_sections,
    collect_listing_links,
    parse_listing,
    extract_seller_sold_count,
    create_browser_context,
    get_following_list,
    BROWSE_URL,
    RateLimitError,
    SearchCancelled,
    raise_if_cancelled,
    sleep_with_cancel,
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
DEFAULT_P2P_TOL = 0.5
DEFAULT_LENGTH_TOL = 1.25
RATE_LIMIT_RETRY_DELAYS = (2, 5)
BROWSE_ALL_STALLED_BATCHES = 3

# SSE helpers
SSE_PREAMBLE = (":" + (" " * 2048) + "\n").encode("utf-8")


def _sse(data: Dict[str, Any]) -> bytes:
    """Encode data as an SSE event."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _is_cancelled(search_id: str) -> bool:
    """Check if a search has been cancelled."""
    return bool(search_id and CANCEL_FLAGS.get(search_id))


def _cancel_check(search_id: str) -> Callable[[], bool]:
    """Build a callback that reflects the latest cancellation flag."""
    return lambda: _is_cancelled(search_id)


def _error_payload_for_exception(exc: Exception, search_id: str) -> Dict[str, Any]:
    """Normalize stream errors into a consistent SSE payload."""
    payload: Dict[str, Any] = {
        "type": "error",
        "message": str(exc) or "Stream error",
        "searchId": search_id or None,
    }
    if isinstance(exc, RateLimitError):
        payload["code"] = exc.code
    return payload


def _run_with_rate_limit_retries(
    action,
    should_cancel: Callable[[], bool],
    label: str,
):
    """Retry rare rate-limit failures with bounded, cancelable backoff."""
    for attempt in range(len(RATE_LIMIT_RETRY_DELAYS) + 1):
        raise_if_cancelled(should_cancel)
        try:
            return action()
        except SearchCancelled:
            raise
        except RateLimitError as exc:
            if attempt >= len(RATE_LIMIT_RETRY_DELAYS):
                raise RateLimitError(
                    f"{exc} Retried {len(RATE_LIMIT_RETRY_DELAYS)} times after the initial failure and still hit a limit.",
                    status=exc.status,
                ) from exc

            delay = RATE_LIMIT_RETRY_DELAYS[attempt]
            print(f"[stream] Rate limited during {label}; retrying in {delay}s")
            sleep_with_cancel(delay, should_cancel)


def _response_status(response) -> Optional[int]:
    """Best-effort response status extraction."""
    try:
        status = getattr(response, "status", None)
        return int(status) if status is not None else None
    except Exception:
        return None


def _load_page_with_retries(
    page,
    url: str,
    search_id: str,
    label: str,
    *,
    expect_product_links: bool = False,
    expect_listing: bool = False,
) -> None:
    """Navigate to a page with cancellation and rate-limit retries."""
    should_cancel = _cancel_check(search_id)

    def action():
        raise_if_cancelled(should_cancel)
        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        raise_if_cancelled(should_cancel)
        accept_cookies(page)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        raise_if_cancelled(should_cancel)
        dismiss_login_modal(page)
        check_page_for_rate_limit(
            page,
            response_status=_response_status(response),
            expect_product_links=expect_product_links,
            expect_listing=expect_listing,
        )

    _run_with_rate_limit_retries(action, should_cancel, label)


def _resolve_seller_sold_count(ctx, seller_cache: Dict[str, int], seller: str,
                               search_id: str = "",
                               groups: str = "tops", gender: str = "male") -> int:
    """Load and cache seller sold counts from seller pages."""
    seller_key = (seller or "").strip().lstrip("@")
    if not seller_key:
        return 0

    if seller_key in seller_cache:
        return seller_cache[seller_key]

    profile_page = ctx.new_page()
    try:
        _load_page_with_retries(
            profile_page,
            build_seller_url(seller_key, groups=groups, gender=gender),
            search_id,
            f"seller stats for @{seller_key}",
        )
        sold_count = extract_seller_sold_count(profile_page) or 0
    except SearchCancelled:
        raise
    except RateLimitError:
        raise
    except Exception as e:
        print(f"[seller-stats] Failed to load @{seller_key}: {e}")
        sold_count = 0
    finally:
        profile_page.close()

    seller_cache[seller_key] = sold_count
    return sold_count


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
    p2p_tol = float(payload.get("p2pTolerance") or DEFAULT_P2P_TOL)
    length_tol = float(payload.get("lengthTolerance") or DEFAULT_LENGTH_TOL)
    
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
                    try:
                        if seller:
                            yield from _search_seller(
                                ctx, page, seller, groups, gender,
                                target_p2p, target_length, p2p_tol, length_tol,
                                max_items, max_links, max_scrolls, search_id
                            )
                        else:
                            yield from _browse_all(
                                ctx, page, groups, gender,
                                target_p2p, target_length, p2p_tol, length_tol,
                                max_items, max_links, max_scrolls, search_id
                            )
                    except SearchCancelled:
                        yield _sse({"type": "cancelled", "searchId": search_id or None})
                finally:
                    ctx.close()
                    browser.close()
                    
        except RateLimitError as e:
            print(f"[stream] rate limited: {e}")
            yield _sse(_error_payload_for_exception(e, search_id))
        except SearchCancelled:
            yield _sse({"type": "cancelled", "searchId": search_id or None})
        except Exception as e:
            print(f"[stream] error: {e}")
            yield _sse(_error_payload_for_exception(e, search_id))
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
    should_cancel = _cancel_check(search_id)
    search_url = build_seller_url(seller, groups=groups, gender=gender)
    print(f"[stream] navigating: {search_url}")
    
    _load_page_with_retries(
        page,
        search_url,
        search_id,
        f"seller page for @{seller}",
        expect_product_links=True,
    )
    seller_sold_count = extract_seller_sold_count(page) or 0
    remove_sold_sections(page)
    
    yield _sse({"type": "progress", "phase": "landing", "processed": 0, "total": None, "matches": 0, "searchId": search_id or None})
    
    links = collect_listing_links(
        page,
        max_scrolls=max_scrolls,
        max_links=max_links,
        should_cancel=should_cancel,
    )
    total = len(links)
    print(f"[stream] collected {total} links")
    
    yield _sse({"type": "meta", "links": total, "seller": seller, "searchId": search_id or None})
    
    matches = 0
    processed = 0
    
    for url in links:
        raise_if_cancelled(should_cancel)
        item = _run_with_rate_limit_retries(
            lambda current_url=url: parse_listing(page, current_url, should_cancel=should_cancel),
            should_cancel,
            f"listing page {url}",
        )
        processed += 1
        
        if item:
            # Stop if item is over 45 days old (listings are sorted by newest)
            age_days = item.get("ageDays")
            if age_days is not None and age_days > 45:
                print(f"[stream] Item is {age_days:.1f} days old, stopping (max 45 days)")
                yield _sse({"type": "progress", "processed": processed, "total": total, "matches": matches, "searchId": search_id or None, "stopped": "age_limit"})
                break
            
            match = _process_item(item, target_p2p, target_length, p2p_tol, length_tol)
            if match:
                match["soldCount"] = seller_sold_count
                print(f"[stream] MATCH {processed}/{total} p2p={match['p2p']} len={match['length']}")
                yield _sse({"type": "match", "item": match, "searchId": search_id or None})
                matches += 1
                
                if matches >= max_items:
                    break
        
        yield _sse({"type": "progress", "processed": processed, "total": total, "matches": matches, "searchId": search_id or None})
    
    yield _sse({"type": "done", "searchId": search_id or None})


def _browse_all(ctx, page, groups, gender, target_p2p, target_length, p2p_tol, length_tol,
                max_items, max_links, max_scrolls, search_id):
    """Browse all listings on the category page."""
    should_cancel = _cancel_check(search_id)
    target_matches = max(max_items or 0, 1)
    max_parsed_links = max(max_links or 0, 1)
    print(f"[stream] browsing: {BROWSE_URL}")
    
    _load_page_with_retries(
        page,
        BROWSE_URL,
        search_id,
        "browse page",
        expect_product_links=True,
    )
    page.wait_for_timeout(500)
    
    yield _sse({"type": "progress", "phase": "browsing", "processed": 0, "total": 0, "matches": 0, "searchId": search_id or None})
    
    processed = 0
    matches = 0
    seen_urls = set()
    stalled_batches = 0
    seller_stats_cache: Dict[str, int] = {}
    item_page = ctx.new_page()
    try:
        while processed < max_parsed_links and matches < target_matches:
            raise_if_cancelled(should_cancel)

            remaining_capacity = max_parsed_links - len(seen_urls)
            if remaining_capacity <= 0:
                break

            links = collect_listing_links(
                page,
                max_scrolls=max_scrolls,
                per_scroll_wait_ms=1200,
                max_links=remaining_capacity,
                should_cancel=should_cancel,
                aggressive_end_scroll=True,
            )
            unique_new = [url for url in links if url not in seen_urls]

            if not unique_new:
                stalled_batches += 1
                if stalled_batches >= BROWSE_ALL_STALLED_BATCHES:
                    break
                page.wait_for_timeout(400)
                continue

            stalled_batches = 0
            seen_urls.update(unique_new)
            print(f"[stream] Collected {len(unique_new)} new browse links ({len(seen_urls)} total)")

            for url in unique_new:
                raise_if_cancelled(should_cancel)

                if processed >= max_parsed_links or matches >= target_matches:
                    break

                item = _run_with_rate_limit_retries(
                    lambda current_url=url: parse_listing(item_page, current_url, should_cancel=should_cancel),
                    should_cancel,
                    f"listing page {url}",
                )
                
                processed += 1
                
                if item:
                    match = _process_item(item, target_p2p, target_length, p2p_tol, length_tol)
                    if match:
                        seller_name = (match.get("seller") or "").strip()
                        sold_count = _resolve_seller_sold_count(
                            ctx,
                            seller_stats_cache,
                            seller_name,
                            search_id=search_id,
                            groups=groups,
                            gender=gender,
                        )
                        match["soldCount"] = sold_count

                        # Check seller reputation in browse mode
                        if sold_count > 50:
                            print(f"[stream] MATCH seller={seller_name} ({sold_count} sold)")
                            yield _sse({"type": "match", "item": match, "seller": seller_name, "searchId": search_id or None})
                            matches += 1
                            
                            if matches >= target_matches:
                                yield _sse({"type": "done", "searchId": search_id or None})
                                return
                
                yield _sse({"type": "progress", "processed": processed, "total": len(seen_urls), "matches": matches, "searchId": search_id or None})
    finally:
        item_page.close()
    
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


@app.post("/api/search/following/stream")
async def browse_following_stream(request: Request):
    """SSE streaming endpoint to browse all accounts a user is following."""
    payload = await request.json()
    print("[following-stream] payload:", payload)
    
    username = (payload.get("username") or "").strip().lstrip("@")
    if not username:
        async def empty_gen():
            yield SSE_PREAMBLE
            yield _sse({"type": "error", "message": "Username required"})
            yield _sse({"type": "done"})
        return StreamingResponse(empty_gen(), media_type="text/event-stream")
    
    ms = payload.get("measurements") or {}
    target_p2p = float(ms["first"]) if ms.get("first") is not None else None
    target_length = float(ms["second"]) if ms.get("second") is not None else None
    p2p_tol = float(payload.get("p2pTolerance") or DEFAULT_P2P_TOL)
    length_tol = float(payload.get("lengthTolerance") or DEFAULT_LENGTH_TOL)
    
    max_items_per_seller = int(payload.get("maxItemsPerSeller") or 10)
    max_links_per_seller = int(payload.get("maxLinksPerSeller") or 100)
    max_scrolls = int(payload.get("maxScrolls") or 4)
    max_threads = int(payload.get("maxThreads") or 5)  # Limit concurrent threads
    headless = bool(payload.get("headless", False))
    slowmo = int(payload.get("slowmo") or 0)
    gender = payload.get("gender") or "male"
    groups = payload.get("groups") or "tops"
    search_id = str(payload.get("searchId") or "")
    
    if search_id:
        CANCEL_FLAGS[search_id] = False

    def run_following_search():
        """Synchronous search generator for following accounts."""
        try:
            yield SSE_PREAMBLE
            yield _sse({"type": "hello", "searchId": search_id or None, "ts": dt.datetime.utcnow().isoformat()})
            
            with sync_playwright() as pw:
                browser, ctx = create_browser_context(pw, headless=headless, slowmo=slowmo)
                page = ctx.new_page()
                
                try:
                    # Get the following list first
                    yield _sse({"type": "progress", "phase": "getting_following", "message": f"Getting following list for @{username}", "searchId": search_id})
                    
                    following_list = get_following_list(page, username)
                    
                    if not following_list:
                        yield _sse({"type": "error", "message": f"Could not find any accounts that @{username} follows", "searchId": search_id})
                        yield _sse({"type": "done", "searchId": search_id})
                        return
                    
                    yield _sse({
                        "type": "following_list",
                        "usernames": following_list,
                        "count": len(following_list),
                        "searchId": search_id
                    })
                    
                    # Now browse each account using threading
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    import threading
                    
                    results_queue = queue.Queue()
                    processed_sellers = [0]
                    total_matches = [0]
                    lock = threading.Lock()
                    should_cancel = _cancel_check(search_id)
                    
                    def search_seller_thread(seller_name: str, thread_id: int):
                        """Search a single seller in a separate thread."""
                        try:
                            raise_if_cancelled(should_cancel)
                            # Create a new page for this thread
                            thread_page = ctx.new_page()
                            try:
                                search_url = build_seller_url(seller_name, groups=groups, gender=gender)
                                _load_page_with_retries(
                                    thread_page,
                                    search_url,
                                    search_id,
                                    f"following seller page for @{seller_name}",
                                    expect_product_links=True,
                                )
                                seller_sold_count = extract_seller_sold_count(thread_page) or 0
                                remove_sold_sections(thread_page)
                                
                                links = collect_listing_links(
                                    thread_page,
                                    max_scrolls=max_scrolls,
                                    max_links=max_links_per_seller,
                                    should_cancel=should_cancel,
                                )
                                
                                seller_matches = 0
                                for url in links:
                                    raise_if_cancelled(should_cancel)
                                    item = _run_with_rate_limit_retries(
                                        lambda current_url=url: parse_listing(thread_page, current_url, should_cancel=should_cancel),
                                        should_cancel,
                                        f"listing page {url}",
                                    )
                                    if item:
                                        # Stop if item is over 45 days old
                                        age_days = item.get("ageDays")
                                        if age_days is not None and age_days > 45:
                                            print(f"[following-thread] {seller_name}: Item is {age_days:.1f} days old, stopping")
                                            break
                                        
                                        match = _process_item(item, target_p2p, target_length, p2p_tol, length_tol)
                                        if match:
                                            match["soldCount"] = seller_sold_count
                                            results_queue.put({
                                                "type": "match",
                                                "item": match,
                                                "seller": seller_name,
                                                "searchId": search_id
                                            })
                                            seller_matches += 1
                                            with lock:
                                                total_matches[0] += 1
                                            
                                            if seller_matches >= max_items_per_seller:
                                                break
                                
                                with lock:
                                    processed_sellers[0] += 1
                                    results_queue.put({
                                        "type": "seller_done",
                                        "seller": seller_name,
                                        "matches": seller_matches,
                                        "processed": processed_sellers[0],
                                        "total": len(following_list),
                                        "searchId": search_id
                                    })
                                    
                            finally:
                                thread_page.close()
                                
                        except SearchCancelled:
                            return
                        except Exception as e:
                            print(f"[following-thread] Error searching {seller_name}: {e}")
                            with lock:
                                processed_sellers[0] += 1
                                results_queue.put({
                                    "type": "seller_error",
                                    "seller": seller_name,
                                    "error": str(e),
                                    "processed": processed_sellers[0],
                                    "total": len(following_list),
                                    "searchId": search_id
                                })
                    
                    # Start threading
                    with ThreadPoolExecutor(max_workers=max_threads) as executor:
                        futures = {
                            executor.submit(search_seller_thread, seller, i): seller
                            for i, seller in enumerate(following_list)
                        }
                        
                        # Yield results as they come in
                        completed_count = 0
                        while completed_count < len(following_list):
                            if _is_cancelled(search_id):
                                yield _sse({"type": "cancelled", "searchId": search_id})
                                return
                            
                            try:
                                while True:
                                    try:
                                        result = results_queue.get_nowait()
                                        yield _sse(result)
                                        if result["type"] in ["seller_done", "seller_error"]:
                                            completed_count = result["processed"]
                                    except queue.Empty:
                                        break
                            except Exception:
                                pass
                            
                            # Brief sleep to avoid busy-waiting
                            time.sleep(0.05)
                        
                        # Drain any remaining results
                        while not results_queue.empty():
                            try:
                                result = results_queue.get_nowait()
                                yield _sse(result)
                            except queue.Empty:
                                break
                    
                finally:
                    ctx.close()
                    browser.close()
                    
        except RateLimitError as e:
            print(f"[following-stream] rate limited: {e}")
            yield _sse(_error_payload_for_exception(e, search_id))
        except SearchCancelled:
            yield _sse({"type": "cancelled", "searchId": search_id or None})
        except Exception as e:
            print(f"[following-stream] error: {e}")
            yield _sse(_error_payload_for_exception(e, search_id))
        finally:
            yield _sse({"type": "done", "searchId": search_id or None})
            if search_id and search_id in CANCEL_FLAGS:
                del CANCEL_FLAGS[search_id]

    async def async_wrapper():
        """Run sync generator in thread and yield results async."""
        result_queue = queue.Queue()
        error_holder = [None]
        
        def worker():
            try:
                for chunk in run_following_search():
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
