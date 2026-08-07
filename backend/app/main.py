import logging
import logging.handlers
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router
from .config import settings
from .db import db
from .engine import ytdlp_engine
from .worker import worker


def _setup_logging() -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    rotating = logging.handlers.RotatingFileHandler(
        settings.logs_dir / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5)
    rotating.setFormatter(fmt)
    root.handlers = [stream, rotating]
    # httpx logs full request URLs at INFO — would leak the Plex token
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    log = logging.getLogger("mdl.main")
    await db.connect()
    settings.staging_jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.quarantine_dir.mkdir(parents=True, exist_ok=True)

    ytdlp_engine.assert_version_floor()
    log.info("yt-dlp %s", ytdlp_engine.ytdlp_version())
    ytdlp_engine.cookies.refresh()
    if not ytdlp_engine.cookies.authenticated:
        log.warning("no authenticated YouTube cookies — running anonymous "
                    "(no Premium formats, higher bot-check risk). "
                    "Export cookies to %s", settings.cookies_master)

    await worker.start()
    yield
    await worker.stop()
    await db.close()


app = FastAPI(title="Music Downloader v2", version="2.0.0", lifespan=lifespan)
app.include_router(router)
