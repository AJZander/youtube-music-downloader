import asyncio
from typing import Dict, Set, Optional
from datetime import datetime
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Download, DownloadStatus
from app.downloader import download_manager
from app.database import async_session_maker

logger = logging.getLogger(__name__)


class QueueService:
    """Service for managing download queue and concurrent downloads."""
    
    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self.active_downloads: Dict[int, asyncio.Task] = {}
        self.download_queue: asyncio.Queue = asyncio.Queue()
        self.websocket_clients: Set = set()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        
    async def start(self):
        """Start the queue worker."""
        if not self._running:
            self._running = True
            self._worker_task = asyncio.create_task(self._process_queue())
            logger.info("Queue service started")
    
    async def stop(self):
        """Stop the queue worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        # Cancel all active downloads
        for task in self.active_downloads.values():
            task.cancel()
        
        self.active_downloads.clear()
        logger.info("Queue service stopped")
    
    def register_websocket(self, websocket):
        """Register a WebSocket client for updates."""
        self.websocket_clients.add(websocket)
        logger.debug(f"WebSocket client registered. Total clients: {len(self.websocket_clients)}")
    
    def unregister_websocket(self, websocket):
        """Unregister a WebSocket client."""
        self.websocket_clients.discard(websocket)
        logger.debug(f"WebSocket client unregistered. Total clients: {len(self.websocket_clients)}")
    
    async def broadcast_update(self, download: Download):
        """Broadcast download update to all connected WebSocket clients."""
        if not self.websocket_clients:
            return
        
        message = {
            "type": "download_update",
            "data": download.to_dict()
        }
        
        # Remove disconnected clients
        disconnected = set()
        
        for ws in self.websocket_clients:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to send to WebSocket client: {e}")
                disconnected.add(ws)
        
        # Clean up disconnected clients
        for ws in disconnected:
            self.websocket_clients.discard(ws)
    
    async def add_to_queue(self, download_id: int):
        """Add a download to the queue."""
        await self.download_queue.put(download_id)
        logger.info(f"Download {download_id} added to queue")
    
    async def _process_queue(self):
        """Process downloads from the queue."""
        logger.info("Queue worker started")
        
        while self._running:
            try:
                # Clean up completed tasks
                completed = [
                    download_id 
                    for download_id, task in self.active_downloads.items() 
                    if task.done()
                ]
                for download_id in completed:
                    del self.active_downloads[download_id]
                
                # Start new downloads if we have capacity
                while len(self.active_downloads) < self.max_concurrent:
                    try:
                        download_id = await asyncio.wait_for(
                            self.download_queue.get(), 
                            timeout=1.0
                        )
                        
                        # Start download task
                        task = asyncio.create_task(
                            self._process_download(download_id)
                        )
                        self.active_downloads[download_id] = task
                        logger.info(f"Started download {download_id}")
                        
                    except asyncio.TimeoutError:
                        break
                
                await asyncio.sleep(0.5)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in queue worker: {e}", exc_info=True)
                await asyncio.sleep(1)
        
        logger.info("Queue worker stopped")
    
    async def _process_download(self, download_id: int):
        """Process a single download."""
        async with async_session_maker() as session:
            try:
                # Get download from database
                result = await session.execute(
                    select(Download).where(Download.id == download_id)
                )
                download = result.scalar_one_or_none()
                
                if not download:
                    logger.error(f"Download {download_id} not found")
                    return
                
                # Update status to downloading
                download.status = DownloadStatus.DOWNLOADING
                download.progress = 0
                download.updated_at = datetime.utcnow()
                await session.commit()
                await session.refresh(download)
                await self.broadcast_update(download)
                
                # Progress callback
                async def update_progress(progress: float):
                    download.progress = min(progress, 99.9)
                    download.updated_at = datetime.utcnow()
                    await session.commit()
                    await session.refresh(download)
                    await self.broadcast_update(download)
                
                # Status callback
                async def update_status(status: DownloadStatus, progress: float):
                    download.status = status
                    download.progress = progress
                    download.updated_at = datetime.utcnow()
                    await session.commit()
                    await session.refresh(download)
                    await self.broadcast_update(download)
                
                # Perform download
                result = await download_manager.download(
                    download.url,
                    progress_callback=update_progress,
                    status_callback=update_status
                )
                
                # Update download with results
                if download.title == 'Pending...':
                    download.title = result['title']
                if download.artist == 'Unknown':
                    download.artist = result['artist']
                if download.album == 'Unknown':
                    download.album = result['album']
                
                download.status = DownloadStatus.COMPLETED
                download.progress = 100
                download.file_path = str(download_manager.download_dir)
                download.updated_at = datetime.utcnow()
                
                await session.commit()
                await session.refresh(download)
                await self.broadcast_update(download)
                
                logger.info(f"Download {download_id} completed successfully")
                
            except Exception as e:
                logger.error(f"Download {download_id} failed: {e}", exc_info=True)
                
                # Update download with error
                try:
                    download.status = DownloadStatus.FAILED
                    download.error_message = str(e)
                    download.updated_at = datetime.utcnow()
                    await session.commit()
                    await session.refresh(download)
                    await self.broadcast_update(download)
                except Exception as update_error:
                    logger.error(f"Failed to update download status: {update_error}")
    
    async def cancel_download(self, download_id: int) -> bool:
        """Cancel a download."""
        # Cancel active download task if exists
        if download_id in self.active_downloads:
            task = self.active_downloads[download_id]
            task.cancel()
            del self.active_downloads[download_id]
            
            async with async_session_maker() as session:
                await session.execute(
                    update(Download)
                    .where(Download.id == download_id)
                    .values(
                        status=DownloadStatus.CANCELLED,
                        updated_at=datetime.utcnow()
                    )
                )
                await session.commit()
                
                result = await session.execute(
                    select(Download).where(Download.id == download_id)
                )
                download = result.scalar_one_or_none()
                if download:
                    await self.broadcast_update(download)
            
            return True
        
        return False


# Global queue service instance
queue_service = QueueService(max_concurrent=3)
