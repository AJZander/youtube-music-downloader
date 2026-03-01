# backend/app/main.py
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import (
    Body, Depends, FastAPI, HTTPException, Query, WebSocket,
    WebSocketDisconnect, status,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, get_session, init_db, retry_on_db_lock
from app.downloader import download_manager
from app.models import Download, DownloadStatus
from app.queue_service import batch_queue_service, queue_service
from app.schemas import (
    BatchStatusResponse, ChannelPlaylistsResponse, ChannelQueueRequest, ChannelQueueResponse,
    ChannelRequest, DownloadCreate, DownloadResponse,
    FormatListResponse, ErrorDetail, StatsResponse,
)

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up…")
    await init_db()
    await queue_service.start()
    yield
    logger.info("Shutting down…")
    await queue_service.stop()


app = FastAPI(
    title=settings.app_name,
    version="2.1.0",
    description="Production-grade YouTube Music downloader API with interactive format selection",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helper Functions ──────────────────────────────────────────────────────────

async def check_duplicate_download(session: AsyncSession, url: str) -> Optional[Download]:
    """
    Check if a URL already exists in the database with an active status.
    Returns the existing download if found, None otherwise.
    Active statuses: queued, downloading, processing, completed
    """
    result = await session.execute(
        select(Download)
        .where(Download.url == url)
        .where(Download.status.in_([
            DownloadStatus.QUEUED,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.PROCESSING,
            DownloadStatus.COMPLETED,
        ]))
        .order_by(desc(Download.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── API Routes ────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def root():
    return {"app": settings.app_name, "status": "ok", "version": "2.1.0"}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "ts": datetime.utcnow().isoformat()}


@app.post(
    "/formats",
    response_model=FormatListResponse,
    tags=["Downloads"],
)
async def get_available_formats(body: DownloadCreate):
    """
    Get available formats for a URL before downloading.
    Shows user what audio/video options are available.
    """
    try:
        formats_info = await download_manager.get_formats(body.url)
        return formats_info
    except Exception as exc:
        logger.error("get_formats failed for %s: %s", body.url, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not extract formats from URL: {exc}",
        )


@app.post(
    "/downloads",
    response_model=DownloadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Downloads"],
)
async def create_download(
    body: DownloadCreate,
    session: AsyncSession = Depends(get_session),
):
    """
    Create a new download job.
    If format_id is not provided, uses 'bestaudio/best' (auto-select).
    Skips if URL is already queued/downloading/processing/completed.
    """
    # Check for duplicates first
    existing = await check_duplicate_download(session, body.url)
    if existing:
        logger.info(
            "Skipping duplicate URL %s - already %s (ID: %d)",
            body.url, existing.status, existing.id
        )
        return existing.to_dict()
    
    try:
        info = await download_manager.get_info(body.url)
    except Exception as exc:
        logger.error("get_info failed for %s: %s", body.url, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not read metadata from URL: {exc}",
        )

    # Store the selected format
    format_id = body.format_id or "bestaudio/best"

    # Use retry wrapper for database operation
    return await _create_download_with_retry(session, body, info, format_id)


@retry_on_db_lock(max_retries=5, base_delay=0.2)
async def _create_download_with_retry(
    session: AsyncSession, body: DownloadCreate, 
    info: dict, format_id: str
) -> dict:
    """Helper to create download with retry logic."""
    dl = Download(
        url           = body.url,
        title         = info["title"],
        artist        = info["artist"],
        album         = info["album"],
        download_type = info["download_type"],
        total_tracks  = info.get("total_tracks"),
        status        = DownloadStatus.QUEUED,
        progress      = 0.0,
        format_id     = format_id,  # Store selected format
    )
    session.add(dl)
    await session.commit()
    await session.refresh(dl)

    await queue_service.enqueue(dl.id)
    logger.info("Queued download %d — %s (format: %s)", dl.id, body.url, format_id)
    return dl.to_dict()


@app.get(
    "/downloads",
    response_model=list[DownloadResponse],
    tags=["Downloads"],
)
async def list_downloads(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(500, le=2000),
    offset: int = 0,
    search: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """
    List downloads with optional status filter, search, and pagination.
    Returns up to 2000 rows (no hidden 50-item cap).
    """
    q = select(Download)
    if status_filter:
        q = q.where(Download.status == status_filter)
    if search:
        term = f"%{search}%"
        q = q.where(
            Download.title.ilike(term)
            | Download.artist.ilike(term)
            | Download.album.ilike(term)
        )
    q = q.order_by(desc(Download.created_at)).limit(limit).offset(offset)
    rows = await session.execute(q)
    return [d.to_dict() for d in rows.scalars()]


@app.get(
    "/downloads/stats",
    response_model=StatsResponse,
    tags=["Downloads"],
)
async def get_stats(session: AsyncSession = Depends(get_session)):
    """Return per-status counts for the dashboard header."""
    rows = await session.execute(
        select(Download.status, func.count(Download.id)).group_by(Download.status)
    )
    counts = {r[0]: r[1] for r in rows}
    return {
        "queued":      counts.get(DownloadStatus.QUEUED,      0),
        "downloading": counts.get(DownloadStatus.DOWNLOADING, 0),
        "processing":  counts.get(DownloadStatus.PROCESSING,  0),
        "completed":   counts.get(DownloadStatus.COMPLETED,   0),
        "failed":      counts.get(DownloadStatus.FAILED,      0),
        "cancelled":   counts.get(DownloadStatus.CANCELLED,   0),
        "total":       sum(counts.values()),
    }


@app.get(
    "/downloads/{download_id}",
    response_model=DownloadResponse,
    tags=["Downloads"],
)
async def get_download(download_id: int, session: AsyncSession = Depends(get_session)):
    dl = await session.get(Download, download_id)
    if not dl:
        raise HTTPException(status_code=404, detail="Not found")
    return dl.to_dict()


@app.delete(
    "/downloads/{download_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Downloads"],
)
async def cancel_download(download_id: int, session: AsyncSession = Depends(get_session)):
    dl = await session.get(Download, download_id)
    if not dl:
        raise HTTPException(status_code=404, detail="Not found")
    if dl.status in (DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING):
        await queue_service.cancel(download_id)


@app.post(
    "/downloads/{download_id}/retry",
    response_model=DownloadResponse,
    tags=["Downloads"],
)
async def retry_download(download_id: int, session: AsyncSession = Depends(get_session)):
    """Re-queue a failed or cancelled download."""
    dl = await session.get(Download, download_id)
    if not dl:
        raise HTTPException(status_code=404, detail="Not found")
    if dl.status not in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed or cancelled downloads can be retried",
        )
    
    return await _retry_download_with_retry(session, dl)


@retry_on_db_lock(max_retries=5, base_delay=0.2)
async def _retry_download_with_retry(session: AsyncSession, dl: Download) -> dict:
    """Helper to retry download with database retry logic."""
    dl.status        = DownloadStatus.QUEUED
    dl.progress      = 0.0
    dl.error_message = None
    dl.updated_at    = datetime.utcnow()
    await session.commit()
    await session.refresh(dl)
    await queue_service.enqueue(dl.id)
    logger.info("Retrying download %d", dl.id)
    return dl.to_dict()


@app.delete(
    "/downloads",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Downloads"],
)
async def bulk_delete(
    status_filter: str = Query(..., alias="status"),
    session: AsyncSession = Depends(get_session),
):
    """Delete all downloads with the given status (e.g. completed, cancelled, failed)."""
    safe = (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED)
    if status_filter not in safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bulk delete only allowed for: {', '.join(s.value for s in safe)}",
        )
    from sqlalchemy import delete as sql_delete
    await session.execute(
        sql_delete(Download).where(Download.status == status_filter)
    )
    await session.commit()
    logger.info("Bulk deleted all %s downloads", status_filter)


@app.get(
    "/downloads/status/active",
    response_model=list[DownloadResponse],
    tags=["Downloads"],
)
async def active_downloads(session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        select(Download)
        .where(Download.status.in_([DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING]))
        .order_by(Download.created_at)
    )
    return [d.to_dict() for d in rows.scalars()]


# ── Channel playlist-import endpoints ────────────────────────────────────────

@app.post(
    "/channel/playlists",
    response_model=ChannelPlaylistsResponse,
    tags=["Channel"],
)
async def get_channel_playlists(body: ChannelRequest):
    """
    Scan a YouTube channel and return all of its playlists (albums/singles).
    Pass this channel URL and the system will fetch every playlist it can find.
    """
    try:
        playlists = await download_manager.get_channel_playlists(body.url)
    except Exception as exc:
        logger.error("get_channel_playlists failed for %s: %s", body.url, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not extract playlists from channel: {exc}",
        )

    channel_name = playlists[0]["channel"] if playlists else None
    return {
        "playlists": playlists,
        "channel": channel_name,
        "total": len(playlists),
    }


@app.post(
    "/channel/queue-all",
    response_model=ChannelQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Channel"],
)
async def queue_channel_playlists(
    body: ChannelQueueRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Queue every playlist in the request body for download.
    Returns immediately with a batch ID. The playlists are queued in the background.
    Use GET /channel/batch/{batch_id} to check progress.
    All playlists are queued with 'bestaudio/best' (top quality audio).
    """
    # Convert Pydantic models to dicts for the background task
    playlists_data = [p.model_dump() for p in body.playlists]
    
    # Create batch and start background processing
    batch_id = await batch_queue_service.create_batch(playlists_data)
    
    logger.info(
        "Created batch %s for %d playlists (processing in background)",
        batch_id[:8], len(playlists_data)
    )
    
    return {
        "batch_id": batch_id,
        "total": len(playlists_data),
        "message": f"Batch queuing started for {len(playlists_data)} playlists. Processing in background.",
    }


@app.get(
    "/channel/batch/{batch_id}",
    response_model=BatchStatusResponse,
    tags=["Channel"],
)
async def get_batch_status(batch_id: str):
    """
    Get the current status of a background batch operation.
    Returns information about how many playlists have been queued, failed, etc.
    """
    batch_status = await batch_queue_service.get_batch_status(batch_id)
    if not batch_status:
        raise HTTPException(
            status_code=404,
            detail=f"Batch {batch_id} not found",
        )
    return batch_status


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    queue_service.register_ws(ws)
    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                select(Download).order_by(desc(Download.created_at)).limit(2000)
            )
            await ws.send_json({
                "type": "initial_data",
                "data": [d.to_dict() for d in rows.scalars()],
            })

        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket closed with error", exc_info=True)
    finally:
        queue_service.unregister_ws(ws)