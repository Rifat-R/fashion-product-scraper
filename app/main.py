from __future__ import annotations

import asyncio
import os
from typing import List, Optional, Tuple

import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.cache import ScanCache, decode_cursor, encode_cursor
from app.models import ScanRequest, ScanResponse, ScanStartResponse, ScanStatusResponse
from app.scrapers.base import run_scan, run_scan_all
from app.scrapers.sites import SITE_CONFIGS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Zain Scraper")
app.mount("/static", StaticFiles(directory="static"), name="static")

cache = ScanCache()


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


@app.post("/api/scan", response_model=ScanResponse)
async def scan(request: ScanRequest) -> ScanResponse:
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    results = await run_scan(query)
    scan_id = cache.create(query, len(SITE_CONFIGS))
    cache.add_results(scan_id, results)
    entry = cache.get(scan_id)
    if entry:
        entry.sites_done = entry.sites_total
    cache.mark_complete(scan_id)

    items, next_cursor = _paginate(results, scan_id, 0, request.page_size)
    return ScanResponse(items=items, next_cursor=next_cursor, total=len(results))


@app.post("/api/scan/start", response_model=ScanStartResponse)
async def start_scan(request: ScanRequest) -> ScanStartResponse:
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    scan_id = cache.create(query, len(SITE_CONFIGS))
    entry = cache.get(scan_id)
    if entry:
        entry.add_log("scan: started")
    logging.getLogger("scraper").info("scan %s: started", scan_id)
    asyncio.create_task(_run_scan_task(scan_id, query))
    return ScanStartResponse(scan_id=scan_id, sites_total=len(SITE_CONFIGS))


@app.post("/api/scan/start-all", response_model=ScanStartResponse)
async def start_scan_all() -> ScanStartResponse:
    scan_id = cache.create("*", len(SITE_CONFIGS))
    entry = cache.get(scan_id)
    if entry:
        entry.add_log("scan-all: started")
    logging.getLogger("scraper").info("scan-all %s: started", scan_id)
    asyncio.create_task(_run_scan_all_task(scan_id))
    return ScanStartResponse(scan_id=scan_id, sites_total=len(SITE_CONFIGS))


@app.get("/api/scan/status", response_model=ScanStatusResponse)
async def scan_status(
    scan_id: str, page_size: int = 20, cursor: Optional[str] = None
) -> ScanStatusResponse:
    entry = cache.get(scan_id)
    if entry is None:
        raise HTTPException(status_code=410, detail="Scan expired")

    if cursor:
        cursor_scan_id, offset = _decode_cursor(cursor)
        if cursor_scan_id != scan_id:
            raise HTTPException(status_code=400, detail="Cursor does not match scan")
    else:
        offset = 0

    items, next_cursor = _paginate(entry.results, scan_id, offset, page_size)
    return ScanStatusResponse(
        items=items,
        next_cursor=next_cursor,
        total=len(entry.results),
        sites_total=entry.sites_total,
        sites_done=entry.sites_done,
        status=entry.status,
        logs=entry.logs[-50:],
    )


@app.get("/api/scan/export")
async def export_scan(scan_id: str) -> FileResponse:
    entry = cache.get(scan_id)
    if entry is None:
        raise HTTPException(status_code=410, detail="Scan expired")

    export_path = cache.export_csv(scan_id, "exports")
    if not export_path:
        raise HTTPException(status_code=404, detail="Export not available")

    filename = os.path.basename(export_path)
    return FileResponse(export_path, media_type="text/csv", filename=filename)


async def _run_scan_task(scan_id: str, query: str) -> None:
    async def on_site_done(
        site_name: str,
        results: List[dict],
        error: Optional[Exception],
    ) -> None:
        if results:
            cache.add_results(scan_id, results)
        cache.mark_site_done(scan_id, site_name, error)

    async def on_log(message: str) -> None:
        cache.add_log(scan_id, message)

    await run_scan(query, on_site_done=on_site_done, on_log=on_log)
    cache.mark_complete(scan_id)
    logging.getLogger("scraper").info("scan %s: complete", scan_id)


async def _run_scan_all_task(scan_id: str) -> None:
    async def on_site_done(
        site_name: str,
        results: List[dict],
        error: Optional[Exception],
    ) -> None:
        if results:
            cache.add_results(scan_id, results)
        cache.mark_site_done(scan_id, site_name, error)

    async def on_log(message: str) -> None:
        cache.add_log(scan_id, message)

    await run_scan_all(on_site_done=on_site_done, on_log=on_log)
    cache.mark_complete(scan_id)
    logging.getLogger("scraper").info("scan-all %s: complete", scan_id)


def _decode_cursor(cursor: str) -> Tuple[str, int]:
    try:
        return decode_cursor(cursor)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


def _paginate(
    results: List[dict], scan_id: str, offset: int, page_size: int
) -> Tuple[List[dict], Optional[str]]:
    start = max(offset, 0)
    end = start + page_size
    page_items = results[start:end]
    next_cursor = encode_cursor(scan_id, end) if end < len(results) else None
    return page_items, next_cursor
