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
from app.models import Download, DownloadStatus, MetadataProcessingJob, MetadataPlaylistItem
from app.queue_service import batch_queue_service, queue_service
from app.metadata_service import metadata_service
from app.schemas import (
    BatchStatusResponse, ChannelPlaylistsResponse, ChannelQueueRequest, ChannelQueueResponse,
    ChannelRequest, DownloadCreate, DownloadResponse, PaginatedDownloadsResponse,
    FormatListResponse, ErrorDetail, StatsResponse,
    MetadataProcessingRequest, MetadataProcessingResponse, MetadataProcessingJob as MetadataProcessingJobSchema,
    MetadataPlaylistItem as MetadataPlaylistItemSchema, MetadataItemSelectionRequest, MetadataQueueSelectedRequest
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
    await metadata_service.start()
    yield
    logger.info("Shutting down…")
    await queue_service.stop()
    await metadata_service.stop()


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
    response_model=PaginatedDownloadsResponse,
    tags=["Downloads"],
)
async def list_downloads(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(500, le=1000),
    offset: int = 0,
    search: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """
    List downloads with optional status filter, search, and pagination.
    Returns downloads with pagination info and total count.
    """
    # Build query
    q = select(Download)
    count_q = select(func.count(Download.id))
    
    if status_filter:
        q = q.where(Download.status == status_filter)
        count_q = count_q.where(Download.status == status_filter)
    if search:
        term = f"%{search}%"
        search_condition = (
            Download.title.ilike(term)
            | Download.artist.ilike(term)
            | Download.album.ilike(term)
        )
        q = q.where(search_condition)
        count_q = count_q.where(search_condition)

    # Get total count
    total_result = await session.execute(count_q)
    total_count = total_result.scalar()

    # Get paginated results
    q = q.order_by(desc(Download.created_at)).limit(limit).offset(offset)
    rows = await session.execute(q)
    downloads = [d.to_dict() for d in rows.scalars()]

    return {
        "downloads": downloads,
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(downloads) < total_count
    }


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


# ── Metadata Processing Endpoints ────────────────────────────────────────────

@app.post(
    "/metadata/process",
    response_model=MetadataProcessingResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Metadata"],
)
async def start_metadata_processing(body: MetadataProcessingRequest):
    """
    Start background metadata processing for a YouTube channel.
    This will extract all albums/singles metadata including track counts and names.
    Returns immediately with a job ID to track progress.
    """
    try:
        job_id = await metadata_service.start_metadata_extraction(body.url)
        return {
            "job_id": job_id,
            "message": "Metadata processing started. Check /metadata/jobs/{job_id} for progress."
        }
    except Exception as exc:
        logger.error("Failed to start metadata processing for %s: %s", body.url, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not start metadata processing: {exc}",
        )


@app.get(
    "/metadata/jobs/{job_id}",
    response_model=MetadataProcessingJobSchema,
    tags=["Metadata"],
)
async def get_metadata_job(job_id: str):
    """Get the status and details of a metadata processing job."""
    job_status = await metadata_service.get_job_status(job_id)
    if not job_status:
        raise HTTPException(
            status_code=404,
            detail=f"Metadata job {job_id} not found",
        )
    return job_status


@app.get(
    "/metadata/jobs",
    response_model=list[MetadataProcessingJobSchema],
    tags=["Metadata"],
)
async def list_metadata_jobs(
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    """List metadata processing jobs."""
    return await metadata_service.list_jobs(limit, offset)


@app.get(
    "/metadata/jobs/{job_id}/items",
    response_model=list[MetadataPlaylistItemSchema],
    tags=["Metadata"],
)
async def get_metadata_job_items(
    job_id: str,
    limit: int = Query(1000, le=5000),
    offset: int = 0,
):
    """Get the discovered playlist items for a metadata processing job."""
    return await metadata_service.get_job_items(job_id, limit, offset)


@app.post(
    "/metadata/jobs/{job_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Metadata"],
)
async def cancel_metadata_job(job_id: str):
    """Cancel a metadata processing job."""
    success = await metadata_service.cancel_job(job_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Metadata job {job_id} not found or already completed",
        )


@app.post(
    "/metadata/items/select",
    tags=["Metadata"],
)
async def update_metadata_item_selection(body: MetadataItemSelectionRequest):
    """Update the selection status of metadata items."""
    updated_count = await metadata_service.update_item_selection(body.item_ids, body.selected)
    return {"updated_count": updated_count}


@app.post(
    "/metadata/jobs/{job_id}/queue-selected",
    response_model=ChannelQueueResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Metadata"],
)
async def queue_selected_metadata_items(
    job_id: str,
    body: MetadataQueueSelectedRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Queue selected metadata items for download.
    Only items marked as selected_for_download=True will be queued.
    """
    # Get selected items
    result = await session.execute(
        select(MetadataPlaylistItem)
        .where(MetadataPlaylistItem.processing_job_id == job_id)
        .where(MetadataPlaylistItem.selected_for_download == True)
    )
    selected_items = result.scalars().all()

    if not selected_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No items selected for download"
        )

    # Convert to playlist format for batch queue
    playlists_data = []
    for item in selected_items:
        playlist_data = {
            "id": item.playlist_id,
            "title": item.title,
            "url": item.url,
            "thumbnail": item.thumbnail,
            "track_count": item.track_count,
            "channel": item.channel,
            "channel_url": item.channel_url,
            "source_tab": item.source_tab,
            "release_type": item.release_type,
        }
        playlists_data.append(playlist_data)

    # Create batch using existing batch queue service
    batch_id = await batch_queue_service.create_batch(playlists_data, body.format_id)
    
    logger.info(
        "Created batch %s for %d selected metadata items from job %s",
        batch_id[:8], len(playlists_data), job_id[:8]
    )
    
    return {
        "batch_id": batch_id,
        "total": len(playlists_data),
        "message": f"Batch queuing started for {len(playlists_data)} selected items. Processing in background.",
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    queue_service.register_ws(ws)
    metadata_service.register_ws(ws)
    try:
        async with AsyncSessionLocal() as session:
            # Send initial data with pagination info
            result = await session.execute(
                select(func.count(Download.id))
            )
            total_count = result.scalar()

            rows = await session.execute(
                select(Download).order_by(desc(Download.created_at)).limit(500)
            )
            downloads = [d.to_dict() for d in rows.scalars()]

            await ws.send_json({
                "type": "initial_data",
                "data": {
                    "downloads": downloads,
                    "total": total_count,
                    "limit": 500,
                    "offset": 0,
                    "has_more": len(downloads) < total_count
                }
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
        metadata_service.unregister_ws(ws)