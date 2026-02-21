from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status, Body
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
from datetime import datetime
import logging

from app.config import settings
from app.database import init_db, get_session
from app.models import Download, DownloadStatus
from app.schemas import DownloadRequest, DownloadResponse, ErrorResponse
from app.downloader import download_manager
from app.queue_service import queue_service
from app.cookies_manager import cookies_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO if not settings.debug else logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting application...")
    await init_db()
    await queue_service.start()
    logger.info("Application started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down application...")
    await queue_service.stop()
    logger.info("Application shut down successfully")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="YouTube Music Downloader API",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Health"])
async def root():
    """Root endpoint."""
    return {
        "app": settings.app_name,
        "status": "running",
        "version": "1.0.0"
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post(
    "/downloads",
    response_model=DownloadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Downloads"]
)
async def create_download(
    request: DownloadRequest,
    session: AsyncSession = Depends(get_session)
):
    """Create a new download task."""
    try:
        # Extract info from URL
        logger.info(f"Extracting info from URL: {request.url}")
        info = await download_manager.get_info(request.url)
        
        # Create download record
        download = Download(
            url=request.url,
            title=info.get('title', 'Pending...'),
            artist=info.get('artist', 'Unknown'),
            album=info.get('album', 'Unknown'),
            type=info.get('type', 'song'),
            status=DownloadStatus.QUEUED,
            progress=0.0
        )
        
        session.add(download)
        await session.commit()
        await session.refresh(download)
        
        # Add to queue
        await queue_service.add_to_queue(download.id)
        
        logger.info(f"Created download {download.id} for URL: {request.url}")
        return download.to_dict()
        
    except Exception as e:
        logger.error(f"Failed to create download: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to process URL: {str(e)}"
        )


@app.get(
    "/downloads",
    response_model=list[DownloadResponse],
    tags=["Downloads"]
)
async def list_downloads(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session)
):
    """List all downloads."""
    try:
        result = await session.execute(
            select(Download)
            .order_by(desc(Download.created_at))
            .limit(limit)
            .offset(offset)
        )
        downloads = result.scalars().all()
        return [download.to_dict() for download in downloads]
    except Exception as e:
        logger.error(f"Failed to list downloads: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve downloads"
        )


@app.get(
    "/downloads/{download_id}",
    response_model=DownloadResponse,
    tags=["Downloads"]
)
async def get_download(
    download_id: int,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific download by ID."""
    try:
        result = await session.execute(
            select(Download).where(Download.id == download_id)
        )
        download = result.scalar_one_or_none()
        
        if not download:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Download {download_id} not found"
            )
        
        return download.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get download {download_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve download"
        )


@app.delete(
    "/downloads/{download_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Downloads"]
)
async def cancel_download(
    download_id: int,
    session: AsyncSession = Depends(get_session)
):
    """Cancel a download."""
    try:
        result = await session.execute(
            select(Download).where(Download.id == download_id)
        )
        download = result.scalar_one_or_none()
        
        if not download:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Download {download_id} not found"
            )
        
        # Only cancel if queued or downloading
        if download.status in [DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING]:
            await queue_service.cancel_download(download_id)
            logger.info(f"Cancelled download {download_id}")
        else:
            logger.warning(f"Cannot cancel download {download_id} with status {download.status}")
        
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel download {download_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel download"
        )


@app.get(
    "/downloads/status/active",
    response_model=list[DownloadResponse],
    tags=["Downloads"]
)
async def get_active_downloads(
    session: AsyncSession = Depends(get_session)
):
    """Get all active (queued or downloading) downloads."""
    try:
        result = await session.execute(
            select(Download)
            .where(Download.status.in_([DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING]))
            .order_by(Download.created_at)
        )
        downloads = result.scalars().all()
        return [download.to_dict() for download in downloads]
    except Exception as e:
        logger.error(f"Failed to get active downloads: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve active downloads"
        )


@app.get(
    "/cookies",
    tags=["Authentication"]
)
async def get_cookies_info():
    """Get information about stored YouTube cookies."""
    try:
        info = cookies_manager.get_cookies_info()
        return info
    except Exception as e:
        logger.error(f"Failed to get cookies info: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve cookies information"
        )


@app.post(
    "/cookies",
    tags=["Authentication"]
)
async def upload_cookies(cookies_content: str = Body(..., embed=True)):
    """Upload YouTube cookies for authentication.
    
    The cookies should be in Netscape format (cookies.txt).
    You can export cookies from your browser using extensions like:
    - "Get cookies.txt LOCALLY" for Chrome/Edge
    - "cookies.txt" for Firefox
    
    Only export cookies from youtube.com and music.youtube.com domains.
    """
    try:
        if not cookies_content or not cookies_content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cookies content is empty"
            )
        
        cookies_manager.save_cookies(cookies_content)
        logger.info("YouTube cookies uploaded successfully")
        
        return {
            "success": True,
            "message": "YouTube cookies saved successfully. Age-restricted content can now be downloaded."
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Failed to save cookies: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save cookies"
        )


@app.delete(
    "/cookies",
    tags=["Authentication"]
)
async def delete_cookies():
    """Delete stored YouTube cookies."""
    try:
        deleted = cookies_manager.delete_cookies()
        if deleted:
            return {
                "success": True,
                "message": "YouTube cookies deleted successfully"
            }
        else:
            return {
                "success": False,
                "message": "No cookies to delete"
            }
    except Exception as e:
        logger.error(f"Failed to delete cookies: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete cookies"
        )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await websocket.accept()
    queue_service.register_websocket(websocket)
    
    try:
        # Send initial data
        async with async_session_maker() as session:
            result = await session.execute(
                select(Download).order_by(desc(Download.created_at)).limit(50)
            )
            downloads = result.scalars().all()
            
            await websocket.send_json({
                "type": "initial_data",
                "data": [download.to_dict() for download in downloads]
            })
        
        # Keep connection alive
        while True:
            try:
                # Wait for any client messages (ping/pong)
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break
                
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
    finally:
        queue_service.unregister_websocket(websocket)


# Import async_session_maker for websocket
from app.database import async_session_maker
