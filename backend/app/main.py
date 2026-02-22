# backend/app/main.py
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import (
    Body, Depends, FastAPI, HTTPException, WebSocket,
    WebSocketDisconnect, status,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.cookies_manager import cookies_manager
from app.database import AsyncSessionLocal, get_session, init_db
from app.downloader import download_manager
from app.models import Download, DownloadStatus
from app.queue_service import queue_service
from app.schemas import CookieUpload, DownloadCreate, DownloadResponse, ErrorDetail

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up…")
    await init_db()
    await queue_service.start()
    yield
    logger.info("Shutting down…")
    await queue_service.stop()


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    description="Production-grade YouTube Music downloader API",
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


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def root():
    return {"app": settings.app_name, "status": "ok", "version": "2.0.0"}


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy", "ts": datetime.utcnow().isoformat()}


# ── Downloads ─────────────────────────────────────────────────────────────────

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
    Validate the URL, extract lightweight metadata, persist a Download
    record, and enqueue it for processing.
    """
    try:
        info = await download_manager.get_info(body.url)
    except Exception as exc:
        logger.error("get_info failed for %s: %s", body.url, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not read metadata from URL: {exc}",
        )

    dl = Download(
        url           = body.url,
        title         = info["title"],
        artist        = info["artist"],
        album         = info["album"],
        download_type = info["download_type"],
        total_tracks  = info.get("total_tracks"),
        status        = DownloadStatus.QUEUED,
        progress      = 0.0,
    )
    session.add(dl)
    await session.commit()
    await session.refresh(dl)

    await queue_service.enqueue(dl.id)
    logger.info("Queued download %d — %s", dl.id, body.url)
    return dl.to_dict()


@app.get(
    "/downloads",
    response_model=list[DownloadResponse],
    tags=["Downloads"],
)
async def list_downloads(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    rows = await session.execute(
        select(Download)
        .order_by(desc(Download.created_at))
        .limit(limit)
        .offset(offset)
    )
    return [d.to_dict() for d in rows.scalars()]


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


# ── Cookies / Authentication ──────────────────────────────────────────────────

@app.get("/cookies", tags=["Auth"])
async def cookies_info():
    """Returns whether cookies are configured and their last-modified date."""
    return cookies_manager.info()


@app.post("/cookies", tags=["Auth"])
async def upload_cookies(body: CookieUpload):
    """
    Accept Netscape-format cookie content and persist it to the data volume.
    Validation is done in the Pydantic schema (CookieUpload).
    """
    try:
        cookies_manager.save_cookies(body.cookies_content)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "message": "Cookies saved. Age-restricted downloads are now enabled."}


@app.delete("/cookies", tags=["Auth"])
async def delete_cookies():
    deleted = cookies_manager.delete_cookies()
    return {"ok": deleted, "message": "Deleted" if deleted else "No cookies to delete"}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    queue_service.register_ws(ws)
    try:
        # Send current snapshot on connect
        async with AsyncSessionLocal() as session:
            rows = await session.execute(
                select(Download).order_by(desc(Download.created_at)).limit(50)
            )
            await ws.send_json({
                "type": "initial_data",
                "data": [d.to_dict() for d in rows.scalars()],
            })

        # Keep alive — handle client pings
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