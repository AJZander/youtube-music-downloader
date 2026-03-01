# backend/app/queue_service.py
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Set

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, retry_on_db_lock
from app.downloader import download_manager
from app.models import Download, DownloadStatus

logger = logging.getLogger(__name__)


class QueueService:
    def __init__(self, max_concurrent: int = 1) -> None:
        self._max   = max_concurrent
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._active: Dict[int, asyncio.Task] = {}
        self._ws_clients: Set[Any] = set()
        self._worker: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker  = asyncio.create_task(self._loop(), name="queue-worker")
        logger.info("Queue service started (max_concurrent=%d, rate_limit=%ds)",
                   self._max, settings.download_interval_seconds)
        await self._resume_interrupted()

    async def _resume_interrupted(self) -> None:
        """
        On startup, find any rows that were left in QUEUED or DOWNLOADING state
        from a previous run and re-enqueue them so they are not silently lost.
        Rows stuck as DOWNLOADING are reset to QUEUED first (the previous worker
        died mid-transfer, so progress is unknown).
        """
        async with AsyncSessionLocal() as session:
            # Reset stuck DOWNLOADING rows back to QUEUED
            await session.execute(
                update(Download)
                .where(Download.status == DownloadStatus.DOWNLOADING)
                .values(status=DownloadStatus.QUEUED, progress=0.0,
                        updated_at=datetime.utcnow())
            )
            await session.commit()

            # Load all QUEUED rows ordered oldest-first
            result = await session.execute(
                select(Download)
                .where(Download.status == DownloadStatus.QUEUED)
                .order_by(Download.created_at)
            )
            pending = result.scalars().all()

        if pending:
            logger.info("Resuming %d pending download(s) from previous session", len(pending))
            for dl in pending:
                await self._queue.put(dl.id)
        else:
            logger.info("No pending downloads to resume")

    async def stop(self) -> None:
        self._running = False
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

        for task in list(self._active.values()):
            task.cancel()
        self._active.clear()
        logger.info("Queue service stopped")

    def register_ws(self, ws: Any) -> None:
        self._ws_clients.add(ws)

    def unregister_ws(self, ws: Any) -> None:
        self._ws_clients.discard(ws)

    async def _broadcast(self, download: Download) -> None:
        dead: Set[Any] = set()
        payload = {"type": "download_update", "data": download.to_dict()}
        for ws in self._ws_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    async def enqueue(self, download_id: int) -> None:
        await self._queue.put(download_id)
        queue_size = self._queue.qsize()
        logger.info("Enqueued download %d (queue size: %d)", download_id, queue_size)

    async def cancel(self, download_id: int) -> bool:
        task = self._active.pop(download_id, None)
        if task:
            task.cancel()

        await self._cancel_in_db(download_id)
        return True

    @retry_on_db_lock(max_retries=5, base_delay=0.2)
    async def _cancel_in_db(self, download_id: int) -> None:
        """Cancel download in database with retry logic."""
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Download)
                .where(Download.id == download_id)
                .values(status=DownloadStatus.CANCELLED, updated_at=datetime.utcnow())
            )
            await session.commit()
            dl = await session.get(Download, download_id)
            if dl:
                await self._broadcast(dl)

    async def _loop(self) -> None:
        logger.debug("Queue worker loop started")
        while self._running:
            try:
                # Clean up completed tasks
                done_ids = [did for did, t in self._active.items() if t.done()]
                for did in done_ids:
                    self._active.pop(did, None)
                
                if done_ids:
                    logger.debug("Cleaned up %d completed task(s)", len(done_ids))

                # Start new downloads up to concurrency limit
                while len(self._active) < self._max:
                    try:
                        did = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                        queue_remaining = self._queue.qsize()
                        logger.info("Starting download %d (%d remaining in queue)", 
                                  did, queue_remaining)
                        
                        task = asyncio.create_task(
                            self._run_download(did), 
                            name=f"download-{did}"
                        )
                        self._active[did] = task
                        
                    except asyncio.TimeoutError:
                        break

                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                logger.info("Queue worker cancelled")
                break
            except Exception:
                logger.exception("Unexpected error in queue loop")
                await asyncio.sleep(2)

    async def _run_download(self, download_id: int) -> None:
        async with AsyncSessionLocal() as session:
            download = await session.get(Download, download_id)
            if not download:
                logger.error("Download %d not found in DB", download_id)
                return

            # Item may have been cancelled while sitting in the in-memory queue
            if download.status == DownloadStatus.CANCELLED:
                logger.info("Download %d was cancelled before it started — skipping", download_id)
                return

            # Update to DOWNLOADING status with retry
            try:
                await self._update_download_status(
                    session, download, DownloadStatus.DOWNLOADING, 0.0
                )
            except Exception as e:
                logger.error("Failed to update download %d status: %s", download_id, e)
                return

            async def on_progress(pct: float) -> None:
                try:
                    # Use a fresh session for each progress update to avoid transaction conflicts
                    async with AsyncSessionLocal() as progress_session:
                        progress_dl = await progress_session.get(Download, download_id)
                        if progress_dl:
                            progress_dl.progress   = min(pct, 99.9)
                            progress_dl.updated_at = datetime.utcnow()
                            await self._commit_with_retry(progress_session, progress_dl)
                            await self._broadcast(progress_dl)
                except Exception as e:
                    logger.warning("Failed to update progress for download %d: %s", download_id, e)

            async def on_status(st: DownloadStatus, pct: float) -> None:
                try:
                    # Use a fresh session for each status update to avoid transaction conflicts
                    async with AsyncSessionLocal() as status_session:
                        status_dl = await status_session.get(Download, download_id)
                        if status_dl:
                            status_dl.status     = st
                            status_dl.progress   = pct
                            status_dl.updated_at = datetime.utcnow()
                            await self._commit_with_retry(status_session, status_dl)
                            await self._broadcast(status_dl)
                except Exception as e:
                    logger.warning("Failed to update status for download %d: %s", download_id, e)

            try:
                # Use the stored format_id
                format_id = download.format_id or "bestaudio/best"
                
                result = await download_manager.download(
                    download.url,
                    format_id=format_id,
                    on_progress=on_progress,
                    on_status=on_status,
                )

                download.title        = result.get("title", download.title)
                download.artist       = result.get("artist", download.artist)
                download.album        = result.get("album", download.album)
                download.total_tracks = result.get("total_tracks")
                download.done_tracks  = result.get("done_tracks")
                download.status       = DownloadStatus.COMPLETED
                download.progress     = 100.0
                download.file_path    = str(download_manager._root)
                download.updated_at   = datetime.utcnow()
                await self._commit_with_retry(session, download)
                await self._broadcast(download)
                logger.info("Download %d completed (%s track(s))", 
                          download_id, result.get("done_tracks"))

            except asyncio.CancelledError:
                logger.info("Download %d was cancelled", download_id)

            except Exception as exc:
                logger.exception("Download %d failed", download_id)
                download.status        = DownloadStatus.FAILED
                download.error_message = str(exc)[:2048]
                download.updated_at    = datetime.utcnow()
                try:
                    await self._commit_with_retry(session, download)
                    await self._broadcast(download)
                except Exception as e:
                    logger.error("Failed to save error state for download %d: %s", download_id, e)

    @retry_on_db_lock(max_retries=5, base_delay=0.1)
    async def _update_download_status(
        self, session: AsyncSession, download: Download, 
        status: DownloadStatus, progress: float
    ) -> None:
        """Update download status with retry on lock."""
        download.status = status
        download.progress = progress
        download.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(download)
        await self._broadcast(download)

    @retry_on_db_lock(max_retries=3, base_delay=0.05)
    async def _commit_with_retry(self, session: AsyncSession, download: Download) -> None:
        """Commit session changes with retry on lock."""
        await session.commit()
        await session.refresh(download)


queue_service = QueueService(max_concurrent=settings.max_concurrent_downloads)


# ── Background Batch Queue Service ──────────────────────────────────────────

class BatchQueueService:
    """
    Manages background batch processing of multiple playlists.
    Supports concurrent batch operations safely with asyncio locks.
    """
    def __init__(self) -> None:
        self._active_batches: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        logger.info("BatchQueueService initialized")

    async def create_batch(self, playlists: list[Dict[str, Any]]) -> str:
        """
        Create a new batch processing task and return its ID immediately.
        The batch will process in the background.
        """
        batch_id = str(uuid.uuid4())
        
        async with self._lock:
            self._active_batches[batch_id] = {
                "id": batch_id,
                "status": "processing",
                "total": len(playlists),
                "queued": 0,
                "skipped": 0,
                "failed": 0,
                "download_ids": [],
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
        
        # Start background task
        asyncio.create_task(
            self._process_batch(batch_id, playlists),
            name=f"batch-{batch_id}"
        )
        
        logger.info("Created batch %s with %d playlists", batch_id, len(playlists))
        return batch_id

    async def get_batch_status(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a batch operation."""
        async with self._lock:
            return self._active_batches.get(batch_id)

    async def _process_batch(self, batch_id: str, playlists: list[Dict[str, Any]]) -> None:
        """
        Background task that processes all playlists in a batch.
        This runs asynchronously without blocking the HTTP response.
        """
        logger.info("Starting batch processing for %s (%d playlists)", batch_id, len(playlists))
        
        for idx, playlist in enumerate(playlists, 1):
            try:
                # Get playlist info
                info = await download_manager.get_info(playlist["url"])
                
                # Create download record with retry logic (includes duplicate check)
                skipped = await self._create_and_queue_download(
                    batch_id, idx, len(playlists), playlist, info
                )
                
                # Update skipped counter if duplicate was found
                if skipped:
                    async with self._lock:
                        if batch_id in self._active_batches:
                            self._active_batches[batch_id]["skipped"] += 1
                            self._active_batches[batch_id]["updated_at"] = datetime.utcnow()
                
            except Exception as exc:
                logger.warning(
                    "Batch %s: Failed to queue playlist %s (%s): %s",
                    batch_id[:8], playlist.get("title", "Unknown"), playlist["url"], exc
                )
                async with self._lock:
                    if batch_id in self._active_batches:
                        self._active_batches[batch_id]["failed"] += 1
                        self._active_batches[batch_id]["updated_at"] = datetime.utcnow()
        
        # Mark batch as completed
        async with self._lock:
            if batch_id in self._active_batches:
                self._active_batches[batch_id]["status"] = "completed"
                self._active_batches[batch_id]["updated_at"] = datetime.utcnow()
                queued = self._active_batches[batch_id]["queued"]
                skipped = self._active_batches[batch_id]["skipped"]
                failed = self._active_batches[batch_id]["failed"]
        
        logger.info(
            "Batch %s completed: %d queued, %d skipped, %d failed",
            batch_id[:8], queued, skipped, failed
        )

    @retry_on_db_lock(max_retries=5, base_delay=0.2)
    async def _create_and_queue_download(
        self, batch_id: str, idx: int, total: int, 
        playlist: Dict[str, Any], info: Dict[str, Any]
    ) -> bool:
        """
        Helper method to create and queue a download with retry logic for database locks.
        Returns True if skipped (duplicate), False if queued.
        """
        async with AsyncSessionLocal() as session:
            # Check for duplicates first
            result = await session.execute(
                select(Download)
                .where(Download.url == playlist["url"])
                .where(Download.status.in_([
                    DownloadStatus.QUEUED,
                    DownloadStatus.DOWNLOADING,
                    DownloadStatus.PROCESSING,
                    DownloadStatus.COMPLETED,
                ]))
                .limit(1)
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                logger.info(
                    "Batch %s: Skipped %d/%d - %s (already %s, ID: %d)",
                    batch_id[:8], idx, total, playlist["title"], 
                    existing.status, existing.id
                )
                return True  # Skipped
            
            # Create new download
            dl = Download(
                url           = playlist["url"],
                title         = info["title"],
                artist        = info["artist"],
                album         = info["album"],
                download_type = info["download_type"],
                total_tracks  = info.get("total_tracks"),
                status        = DownloadStatus.QUEUED,
                progress      = 0.0,
                format_id     = "bestaudio/best",
            )
            session.add(dl)
            await session.commit()
            await session.refresh(dl)
            
            # Enqueue for download
            await queue_service.enqueue(dl.id)
            
            # Update batch status
            async with self._lock:
                if batch_id in self._active_batches:
                    self._active_batches[batch_id]["queued"] += 1
                    self._active_batches[batch_id]["download_ids"].append(dl.id)
                    self._active_batches[batch_id]["updated_at"] = datetime.utcnow()
            
            logger.info(
                "Batch %s: Queued %d/%d - %s (ID: %d)",
                batch_id[:8], idx, total, playlist["title"], dl.id
            )
            return False  # Not skipped


batch_queue_service = BatchQueueService()