"""yt-dlp integration. Embedded as a library (typed exceptions, no stderr
parsing); every call runs in a single-thread executor with a hard timeout —
on a true hang we exit the process and let Docker restart us into crash
recovery rather than leak a stuck thread forever."""
import asyncio
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import settings
from ..events import bus

log = logging.getLogger("mdl.engine")

# Serialize all yt-dlp work through one thread.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ytdlp")

CANARY_VIDEO_ID = "dQw4w9WgXcQ"  # stable public upload, used for health probes
PREMIUM_FORMAT_IDS = {"141", "774"}


class CookieState:
    """Master cookie file is mounted read-only; yt-dlp only ever sees a
    working copy, so its cookie-jar rewrites can't corrupt the export."""

    def __init__(self) -> None:
        self.authenticated = False
        self._master_mtime = 0.0

    def refresh(self) -> None:
        master = settings.cookies_master
        working = settings.cookies_working
        if not master.exists():
            self.authenticated = False
            return
        mtime = master.stat().st_mtime
        if mtime != self._master_mtime or not working.exists():
            working.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(master, working)
            os.chmod(working, 0o600)
            self._master_mtime = mtime
            log.info("cookie working copy refreshed from master")
        content = master.read_text(errors="ignore")
        has_auth = ("SAPISID" in content or "__Secure-3PSID" in content) and ".youtube.com" in content
        if has_auth != self.authenticated:
            self.authenticated = has_auth
            log.warning("cookie state: authenticated=%s", has_auth)

    @property
    def cookiefile(self) -> str | None:
        return str(settings.cookies_working) if settings.cookies_working.exists() else None


cookies = CookieState()


def _common_opts() -> dict:
    opts = {
        "js_runtimes": {"deno": {}},
        "extractor_args": {
            "youtube": {
                "player_client": ["web_music", "default"],
                "lang": ["en"],
            },
            "youtubepot-bgutilhttp": {
                "base_url": [settings.pot_provider_url],
            },
        },
        "sleep_interval_requests": settings.ytdlp_sleep_requests,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "noplaylist": True,
        "color": {"stdout": "no_color", "stderr": "no_color"},
        "quiet": True,
        "no_warnings": False,
        "logger": _YdlLogger(),
    }
    cookies.refresh()
    if cookies.cookiefile:
        opts["cookiefile"] = cookies.cookiefile
    return opts


class _YdlLogger:
    def debug(self, msg):
        if msg.startswith("[debug]"):
            return
        log.debug("%s", msg)

    def info(self, msg):
        log.info("%s", msg)

    def warning(self, msg):
        log.warning("%s", msg)

    def error(self, msg):
        log.error("%s", msg)


def probe_opts() -> dict:
    return {**_common_opts(), "skip_download": True}


def flat_playlist_opts() -> dict:
    return {**_common_opts(), "skip_download": True, "extract_flat": "in_playlist", "noplaylist": False}


# Premium AAC 256 -> Premium Opus 256 -> free Opus -> free AAC -> any audio-only
FORMAT_SELECTOR = "141/774/251/140/bestaudio[vcodec=none]/bestaudio"


def download_opts(track_dir: Path, progress_hook=None) -> dict:
    opts = {
        **_common_opts(),
        # Flat, id-keyed output inside the per-track staging dir. Human paths
        # are built by the importer from verified tags AFTER download+tagging,
        # so a literal-template folder name is structurally impossible.
        "paths": {"home": str(track_dir), "temp": str(track_dir / ".tmp")},
        "outtmpl": {"default": "%(id)s.%(ext)s"},
        "format": FORMAT_SELECTOR,
        "sleep_interval": settings.ytdlp_sleep_min,
        "max_sleep_interval": settings.ytdlp_sleep_max,
        "ratelimit": settings.ratelimit_bytes,
        "writethumbnail": True,
        "postprocessors": [
            # preferredcodec=best => stream copy into native container
            # (141/140 -> .m4a, 774/251 -> .opus). Never transcode.
            {"key": "FFmpegExtractAudio", "preferredcodec": "best"},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            {"key": "EmbedThumbnail", "already_have_thumbnail": False},
        ],
    }
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    return opts


async def _run(func, timeout: int | None = None):
    """Run a yt-dlp callable on the engine thread with a hard timeout."""
    loop = asyncio.get_running_loop()
    timeout = timeout or settings.download_timeout_sec
    try:
        return await asyncio.wait_for(loop.run_in_executor(_executor, func), timeout=timeout)
    except asyncio.TimeoutError:
        # The worker thread is unrecoverable — exit; Docker restarts us and
        # startup crash-recovery re-queues whatever was in flight.
        log.critical("yt-dlp call exceeded %ss — exiting for supervisor restart", timeout)
        bus.publish("log", {"level": "critical", "code": "engine_hang",
                            "message": f"yt-dlp hung >{timeout}s; restarting worker"})
        await asyncio.sleep(0.5)
        os._exit(70)


async def extract_info(url: str, flat: bool = False, timeout: int = 300) -> dict:
    import yt_dlp

    opts = flat_playlist_opts() if flat else probe_opts()

    def _do():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.sanitize_info(ydl.extract_info(url, download=False))

    return await _run(_do, timeout=timeout)


async def download(url: str, track_dir: Path, progress_hook=None) -> dict:
    """Download one track into track_dir. Returns the sanitized info dict."""
    import yt_dlp

    track_dir.mkdir(parents=True, exist_ok=True)
    opts = download_opts(track_dir, progress_hook)

    def _do():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.sanitize_info(ydl.extract_info(url, download=True))

    return await _run(_do)


def find_audio_file(track_dir: Path) -> Path | None:
    audio = [p for p in track_dir.iterdir()
             if p.is_file() and p.suffix.lower() in (".m4a", ".opus", ".mp3", ".ogg", ".flac", ".webm")]
    return audio[0] if audio else None


async def canary_check() -> bool:
    """Cheap end-to-end extractor health probe against a stable public video."""
    try:
        info = await extract_info(f"https://www.youtube.com/watch?v={CANARY_VIDEO_ID}", timeout=120)
        ok = bool(info and info.get("formats"))
        log.info("canary probe: %s", "ok" if ok else "no formats")
        return ok
    except Exception as exc:
        log.warning("canary probe failed: %s", exc)
        return False


async def credential_check(sample_music_video_id: str = "Yxf10Vdu0_M") -> dict:
    """Verify Premium formats are visible with current cookies."""
    result = {"authenticated_cookies": False, "premium_formats": False}
    cookies.refresh()
    result["authenticated_cookies"] = cookies.authenticated
    if not cookies.authenticated:
        return result
    try:
        info = await extract_info(f"https://music.youtube.com/watch?v={sample_music_video_id}", timeout=120)
        ids = {f.get("format_id") for f in (info.get("formats") or [])}
        result["premium_formats"] = bool(ids & PREMIUM_FORMAT_IDS)
    except Exception as exc:
        log.warning("credential check failed: %s", exc)
    return result


def ytdlp_version() -> str:
    import yt_dlp

    return yt_dlp.version.__version__


def assert_version_floor() -> None:
    v = ytdlp_version()
    def key(s: str):
        return tuple(int(p) for p in s.split(".")[:3] if p.isdigit())
    if key(v) < key(settings.ytdlp_min_version):
        raise RuntimeError(
            f"yt-dlp {v} is below the floor {settings.ytdlp_min_version}; "
            "run the entrypoint update or rebuild the image"
        )
