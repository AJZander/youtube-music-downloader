# backend/app/queue_service.py
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Set

from sqlalchemy import select, update

from app.config import settings
from app.database import AsyncSessionLocal
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
        return True

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

            download.status    = DownloadStatus.DOWNLOADING
            download.progress  = 0.0
            download.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(download)
            await self._broadcast(download)

            async def on_progress(pct: float) -> None:
                download.progress   = min(pct, 99.9)
                download.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(download)
                await self._broadcast(download)

            async def on_status(st: DownloadStatus, pct: float) -> None:
                download.status     = st
                download.progress   = pct
                download.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(download)
                await self._broadcast(download)

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
                await session.commit()
                await session.refresh(download)
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
                await session.commit()
                await session.refresh(download)
                await self._broadcast(download)


queue_service = QueueService(max_concurrent=settings.max_concurrent_downloads)