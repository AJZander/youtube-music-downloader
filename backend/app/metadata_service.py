# backend/app/metadata_service.py
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
from app.models import MetadataProcessingJob, MetadataProcessingStatus, MetadataPlaylistItem

logger = logging.getLogger(__name__)


class MetadataProcessingService:
    def __init__(self) -> None:
        self._active_jobs: Dict[str, asyncio.Task] = {}
        self._ws_clients: Set[Any] = set()
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info("Metadata processing service started")

    async def stop(self) -> None:
        self._running = False
        
        # Cancel all active jobs
        for job_id, task in list(self._active_jobs.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._active_jobs.clear()
        logger.info("Metadata processing service stopped")

    def register_ws(self, ws: Any) -> None:
        self._ws_clients.add(ws)

    def unregister_ws(self, ws: Any) -> None:
        self._ws_clients.discard(ws)

    async def _broadcast_job_update(self, job: MetadataProcessingJob) -> None:
        """Broadcast job status update to connected WebSocket clients."""
        dead: Set[Any] = set()
        payload = {"type": "metadata_job_update", "data": job.to_dict()}
        for ws in self._ws_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    async def start_metadata_extraction(self, channel_url: str) -> str:
        """Start background metadata extraction for a channel."""
        job_id = str(uuid.uuid4())
        
        # Create job record
        async with AsyncSessionLocal() as session:
            job = MetadataProcessingJob(
                id=job_id,
                channel_url=channel_url,
                status=MetadataProcessingStatus.PENDING
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)

        # Start background task
        task = asyncio.create_task(
            self._process_metadata_job(job_id),
            name=f"metadata-job-{job_id[:8]}"
        )
        self._active_jobs[job_id] = task
        
        logger.info("Started metadata processing job %s for %s", job_id[:8], channel_url)
        return job_id

    async def _process_metadata_job(self, job_id: str) -> None:
        """Background task to process channel metadata."""
        async with AsyncSessionLocal() as session:
            # Get job
            job = await session.get(MetadataProcessingJob, job_id)
            if not job:
                logger.error("Metadata job %s not found", job_id)
                return

            try:
                # Update status to processing
                job.status = MetadataProcessingStatus.PROCESSING
                job.progress = 0.0
                job.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(job)
                await self._broadcast_job_update(job)

                logger.info("Starting metadata extraction for job %s: %s", job_id[:8], job.channel_url)
                
                # Phase 1: Discover all releases (10% of progress)
                logger.info("Phase 1: Discovering releases...")
                job.progress = 5.0
                await session.commit()
                await session.refresh(job)
                await self._broadcast_job_update(job)
                
                # Get list of playlists WITHOUT full metadata first (faster)
                from app.downloader import download_manager
                import re as _re
                
                # Use a simplified version to just get the list first
                clean = job.channel_url.rstrip("/")
                clean = _re.sub(
                    r"/(videos|shorts|streams|about|community|featured|store|playlists|releases)$",
                    "",
                    clean,
                )
                
                from app.config import settings
                from yt_dlp import YoutubeDL
                import asyncio
                loop = asyncio.get_event_loop()
                
                opts = download_manager._get_base_ydl_opts()
                opts.update({
                    "quiet": False if settings.debug else True,
                    "no_warnings": False if settings.debug else True,
                    "extract_flat": True,
                    "skip_download": True,
                    "playlistend": None,
                    "ignoreerrors": True,
                })
                
                def _get_releases_list():
                    with YoutubeDL(opts) as ydl:
                        return ydl.extract_info(clean + "/releases", download=False)
                
                raw = await loop.run_in_executor(None, _get_releases_list)
                
                if not raw:
                    raise RuntimeError("No releases found on channel. The channel may be empty or private.")
                
                entries = raw.get("entries") or []
                channel_name = raw.get("channel") or raw.get("uploader") or raw.get("title")
                channel_url_out = raw.get("channel_url") or raw.get("uploader_url")
                
                # Filter out live albums early
                filtered_entries = []
                for entry in entries:
                    if not entry:
                        continue
                    title = entry.get("title", "")
                    if download_manager._is_live_album(title):
                        logger.debug("Skipping live album: %s", title)
                        continue
                    filtered_entries.append(entry)
                
                logger.info("Discovered %d releases (filtered from %d)", len(filtered_entries), len(entries))
                
                job.channel_name = channel_name
                job.total_items = len(filtered_entries)
                job.processed_items = 0
                job.progress = 10.0
                await session.commit()
                await session.refresh(job)
                await self._broadcast_job_update(job)
                
                # Phase 2: Extract full metadata for each release (10% to 90%)
                logger.info("Phase 2: Extracting full metadata for %d releases...", len(filtered_entries))
                playlists = []
                
                for idx, entry in enumerate(filtered_entries, 1):
                    playlist_id = entry.get("id") or entry.get("playlist_id")
                    entry_url = entry.get("url") or entry.get("webpage_url")
                    if not entry_url and playlist_id:
                        entry_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                    if not entry_url:
                        continue
                    
                    title = entry.get("title") or "Unknown Playlist"
                    
                    # Best thumbnail
                    thumb = None
                    thumbnails = entry.get("thumbnails")
                    if thumbnails and isinstance(thumbnails, list):
                        thumb = thumbnails[-1].get("url")
                    if not thumb:
                        thumb = entry.get("thumbnail")
                    
                    # Try to get track count from flat extraction
                    track_count = (
                        entry.get("playlist_count") or
                        entry.get("n_entries") or
                        entry.get("entry_count") or
                        entry.get("video_count") or
                        entry.get("track_count")
                    )
                    
                    # Get full metadata
                    try:
                        logger.info("[%d/%d] Extracting metadata for: %s", idx, len(filtered_entries), title)
                        full_metadata = await download_manager.get_playlist_full_metadata(entry_url)
                        
                        # Use accurate track count from full extraction
                        if full_metadata.get("track_count") is not None:
                            track_count = full_metadata["track_count"]
                            logger.info("  ✓ %d tracks", track_count)
                    except Exception as exc:
                        logger.warning("  ✗ Failed to get full metadata: %s", exc)
                        full_metadata = {}
                    
                    # Classify release type
                    release_type = download_manager._classify_release_type(
                        entry, "releases", title, track_count
                    )
                    
                    # Skip music videos
                    if release_type == "music_video":
                        logger.debug("Skipping music video: %s", title)
                        continue
                    
                    # Build playlist data
                    playlist_data = {
                        "id": playlist_id,
                        "title": title,
                        "url": entry_url,
                        "thumbnail": thumb,
                        "track_count": track_count,
                        "channel": channel_name,
                        "channel_url": channel_url_out,
                        "source_tab": "releases",
                        "release_type": release_type,
                    }
                    
                    # Add additional metadata if available
                    if full_metadata:
                        for key in ["release_date", "release_year", "description", 
                                    "total_duration", "view_count", "like_count"]:
                            if full_metadata.get(key) is not None:
                                playlist_data[key] = full_metadata[key]
                    
                    playlists.append(playlist_data)
                    
                    # Update progress (10% to 90% range)
                    job.processed_items = idx
                    job.progress = 10.0 + (idx / len(filtered_entries)) * 80.0
                    await session.commit()
                    await session.refresh(job)
                    await self._broadcast_job_update(job)
                
                # Phase 3: Save to database (90% to 100%)
                logger.info("Phase 3: Saving %d releases to database...", len(playlists))
                job.progress = 90.0
                await session.commit()
                await session.refresh(job)
                await self._broadcast_job_update(job)
                
                if not playlists:
                    raise RuntimeError("No valid releases found after filtering.")
                
                # Update job with final results
                job.total_items = len(playlists)
                job.processed_items = len(playlists)
                job.metadata_results = playlists
                
                # Create individual playlist item records
                for playlist in playlists:
                    item = MetadataPlaylistItem(
                        processing_job_id=job_id,
                        playlist_id=playlist.get("id"),
                        title=playlist["title"],
                        url=playlist["url"],
                        thumbnail=playlist.get("thumbnail"),
                        track_count=playlist.get("track_count"),
                        channel=playlist.get("channel"),
                        channel_url=playlist.get("channel_url"),
                        source_tab=playlist.get("source_tab"),
                        release_type=playlist.get("release_type"),
                        selected_for_download=False,
                        # Additional metadata fields
                        release_date=playlist.get("release_date"),
                        release_year=playlist.get("release_year"),
                        description=playlist.get("description"),
                        total_duration=playlist.get("total_duration"),
                        view_count=playlist.get("view_count"),
                        like_count=playlist.get("like_count"),
                    )
                    session.add(item)

                await session.commit()
                
                # Mark as complete
                job.progress = 100.0
                job.status = MetadataProcessingStatus.COMPLETED
                job.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(job)
                
                # Log summary by release type
                type_counts = {}
                for p in playlists:
                    rt = p.get("release_type", "unknown")
                    type_counts[rt] = type_counts.get(rt, 0) + 1
                
                logger.info(
                    "✓ Metadata extraction complete for job %s: %d items (%s)",
                    job_id[:8], len(playlists), 
                    ", ".join(f"{count} {rtype}" for rtype, count in type_counts.items())
                )

            except Exception as exc:
                logger.error("Metadata processing job %s failed: %s", job_id[:8], exc)
                job.status = MetadataProcessingStatus.FAILED
                job.error_message = str(exc)
                job.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(job)

            finally:
                # Broadcast final status
                await self._broadcast_job_update(job)
                
                # Remove from active jobs
                self._active_jobs.pop(job_id, None)

    async def get_job_status(self, job_id: str) -> Optional[Dict]:
        """Get the status of a metadata processing job."""
        async with AsyncSessionLocal() as session:
            job = await session.get(MetadataProcessingJob, job_id)
            if not job:
                return None
            return job.to_dict()

    async def list_jobs(self, limit: int = 50, offset: int = 0) -> list[Dict]:
        """List metadata processing jobs."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MetadataProcessingJob)
                .order_by(MetadataProcessingJob.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            jobs = result.scalars().all()
            return [job.to_dict() for job in jobs]

    async def get_job_items(self, job_id: str, limit: int = 1000, offset: int = 0) -> list[Dict]:
        """Get the playlist items for a metadata processing job."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(MetadataPlaylistItem)
                .where(MetadataPlaylistItem.processing_job_id == job_id)
                .order_by(MetadataPlaylistItem.created_at)
                .limit(limit)
                .offset(offset)
            )
            items = result.scalars().all()
            return [item.to_dict() for item in items]

    async def update_item_selection(self, item_ids: list[int], selected: bool) -> int:
        """Update the selection status of metadata items. Returns number of updated items."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(MetadataPlaylistItem)
                .where(MetadataPlaylistItem.id.in_(item_ids))
                .values(selected_for_download=selected)
            )
            await session.commit()
            return result.rowcount

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a metadata processing job."""
        # Cancel active task if running
        task = self._active_jobs.get(job_id)
        if task and not task.done():
            task.cancel()
            self._active_jobs.pop(job_id, None)

        # Update database status
        async with AsyncSessionLocal() as session:
            job = await session.get(MetadataProcessingJob, job_id)
            if not job:
                return False
            
            if job.status in (MetadataProcessingStatus.PENDING, MetadataProcessingStatus.PROCESSING):
                job.status = MetadataProcessingStatus.CANCELLED
                job.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(job)
                await self._broadcast_job_update(job)
                return True
        
        return False


# Global instance
metadata_service = MetadataProcessingService()