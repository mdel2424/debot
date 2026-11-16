"""Bounded Playwright search probes; stop at the first blocked response."""

import argparse
from collections import Counter
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import main
import scraper


def benchmark(seller, *, max_links=72, max_matches=40, runs=2, timeout=60, category="tops"):
    reports = []
    with sync_playwright() as pw:
        browser, context = scraper.create_browser_context(pw, headless=True)
        try:
            for run in range(1, runs + 1):
                started = time.monotonic()
                statuses = Counter()
                paths = Counter()
                blocked = []
                listing_documents = 0
                product_responses = 0
                data_requests = 0
                first_match = None
                summary = None
                matches = 0

                search_id = f"benchmark-{seller}-{run}"

                def stop_search(search_id=search_id):
                    main.CANCEL_FLAGS[search_id] = True

                def request_seen(request):
                    nonlocal data_requests
                    host = urlparse(request.url).hostname or ''
                    if (host == 'depop.com' or host.endswith('.depop.com')) and request.resource_type in ('document', 'xhr', 'fetch'):
                        data_requests += 1

                def response_seen(response):
                    nonlocal listing_documents, product_responses
                    parsed = urlparse(response.url)
                    if not (parsed.hostname == "depop.com" or (parsed.hostname or "").endswith(".depop.com")):
                        return
                    if response.request.resource_type not in ("document", "xhr", "fetch"):
                        return
                    statuses[response.status] += 1
                    path = '/cdn-cgi/challenge-platform/' if parsed.path.startswith('/cdn-cgi/challenge-platform/') else parsed.path
                    paths[f"{parsed.hostname}{path}"] += 1
                    if response.request.resource_type == "document" and "/products/" in parsed.path:
                        listing_documents += 1
                    if scraper._is_shop_product_api_url(response.url):
                        product_responses += 1
                    if response.status in (403, 429):
                        blocked.append({
                            "status": response.status,
                            "path": parsed.path,
                            "retry_after_seconds": scraper.extract_retry_after_seconds(response),
                        })
                        stop_search()

                def consume_event(event):
                    nonlocal first_match, matches, summary
                    if event["type"] == "match":
                        matches += 1
                        if first_match is None:
                            first_match = time.monotonic() - started
                    if event["type"] == "done":
                        summary = event

                page = context.new_page()
                context.on("response", response_seen)
                context.on("request", request_seen)
                main.CANCEL_FLAGS[search_id] = False
                deadline = threading.Timer(timeout, stop_search)
                deadline.daemon = True
                deadline.start()
                outcome = "completed"
                error = None
                try:
                    with redirect_stdout(sys.stderr):
                        for chunk in main._search_seller(
                            context, page, seller, [category], "male", 21, 27, 0.75, 0.75,
                            max_matches, max_links, 1, search_id,
                            category=category, parse_workers=1,
                            emit_event=consume_event,
                        ):
                            consume_event(json.loads(chunk.decode().removeprefix("data: ")))
                except scraper.SearchCancelled:
                    outcome = "rate_limited" if blocked else "timeout"
                except scraper.RateLimitError as exc:
                    outcome = "rate_limited"
                    error = str(exc)
                except Exception as exc:
                    outcome = "error"
                    error = f"{type(exc).__name__}: {exc}"
                finally:
                    deadline.cancel()
                    main.CANCEL_FLAGS.pop(search_id, None)
                    context.remove_listener("response", response_seen)
                    context.remove_listener("request", request_seen)
                    page.close()
                report = {
                    "seller": seller, "run": run, "outcome": outcome,
                    "category": category, "max_links": max_links, "max_matches": max_matches,
                    "request_interval_seconds": scraper.REQUEST_INTERVAL_SECONDS,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "first_match_seconds": round(first_match, 3) if first_match is not None else None,
                    "matches": matches, "summary": summary,
                    "data_requests": data_requests,
                    "data_responses": sum(statuses.values()),
                    "product_api_responses": product_responses,
                    "individual_listing_documents": listing_documents,
                    "statuses": dict(statuses), "requests_by_path": dict(paths),
                    "blocked": blocked, "error": error,
                }
                reports.append(report)
                if outcome != "completed":
                    break
        finally:
            try:
                context.close()
            finally:
                browser.close()
    return reports


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seller")
    parser.add_argument("--max-links", type=int, default=72, choices=range(1, 241), metavar="1..240")
    parser.add_argument("--max-matches", type=int, default=40, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--runs", type=int, default=2, choices=range(1, 4))
    parser.add_argument("--timeout", type=int, default=60, choices=range(1, 121), metavar="1..120")
    parser.add_argument("--category", choices=("tops", "accessories"), default="tops")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    seller = main.validate_search_payload({"seller": args.seller})["seller"]
    if not seller:
        parser.error("seller must not be empty")
    reports = benchmark(
        seller, max_links=args.max_links, max_matches=args.max_matches,
        runs=args.runs, timeout=args.timeout, category=args.category,
    )
    serialized = json.dumps(reports, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0 if all(report["outcome"] == "completed" for report in reports) else 1


if __name__ == "__main__":
    sys.exit(cli())
