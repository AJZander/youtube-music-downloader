# backend/app/downloader.py
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

import certifi
import yt_dlp

from app.config import settings
from app.cookies_manager import cookies_manager
from app.models import DownloadStatus
from app.utils import clean_artist_for_folder

logger = logging.getLogger(__name__)

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

_RATE_LIMIT_ERRORS = [
    "HTTP Error 429",
    "Too Many Requests", 
    "rate limit",
    "throttled",
    "slow down",
]


class RateLimitError(Exception):
    """Raised when YouTube rate limiting is detected."""
    pass


class DownloadManager:
    def __init__(self) -> None:
        self._root: Path = settings.download_dir
        self._root.mkdir(parents=True, exist_ok=True)
        self._last_download_time: float = 0.0

    async def get_info(self, url: str) -> dict:
        """Extract metadata without downloading."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
        }
        
        # Add cookies if available
        if cookies_manager.has_cookies():
            opts["cookiefile"] = str(cookies_manager.get_cookies_path())
        
        loop = asyncio.get_event_loop()

        def _run():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        raw = await loop.run_in_executor(None, _run)
        if not raw:
            raise RuntimeError("Failed to extract metadata")

        return self._normalise_info(url, raw)

    async def download(
        self,
        url: str,
        on_progress: Optional[Callable[[float], None]] = None,
        on_status: Optional[Callable[[DownloadStatus, float], None]] = None,
    ) -> dict:
        """Download audio from URL with retry logic."""
        await self._enforce_rate_limit()
        
        rate_limit_retry_count = 0
        while True:
            try:
                has_cookies = cookies_manager.has_cookies()
                logger.info("Starting download (authenticated: %s)", "yes" if has_cookies else "no")
                result = await self._attempt_download(url, on_progress, on_status)
                self._last_download_time = time.time()
                return result
                
            except RateLimitError as exc:
                rate_limit_retry_count += 1
                if rate_limit_retry_count > settings.max_rate_limit_retries:
                    logger.error("Max rate limit retries exceeded")
                    if on_status:
                        await on_status(DownloadStatus.FAILED, 0.0)
                    raise exc
                
                wait_time = settings.rate_limit_backoff_seconds * rate_limit_retry_count
                logger.warning(
                    "Rate limit detected (retry %d/%d). Waiting %ds...",
                    rate_limit_retry_count,
                    settings.max_rate_limit_retries,
                    wait_time,
                )
                await asyncio.sleep(wait_time)
                continue
                
            except Exception as exc:
                if self._is_rate_limit_error(exc):
                    rate_limit_retry_count += 1
                    if rate_limit_retry_count <= settings.max_rate_limit_retries:
                        wait_time = settings.rate_limit_backoff_seconds * rate_limit_retry_count
                        logger.warning("Rate limit error detected. Waiting %ds...", wait_time)
                        await asyncio.sleep(wait_time)
                        continue
                
                if on_status:
                    await on_status(DownloadStatus.FAILED, 0.0)
                raise exc

    async def _enforce_rate_limit(self) -> None:
        """Ensure minimum interval between download starts."""
        if self._last_download_time > 0:
            elapsed = time.time() - self._last_download_time
            if elapsed < settings.download_interval_seconds:
                wait = settings.download_interval_seconds - elapsed
                logger.info("Rate limit: waiting %.1fs before next download", wait)
                await asyncio.sleep(wait)

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        """Check if exception message indicates rate limiting."""
        err_msg = str(exc).lower()
        return any(pattern.lower() in err_msg for pattern in _RATE_LIMIT_ERRORS)

    async def _attempt_download(
        self,
        url: str,
        on_progress: Optional[Callable],
        on_status: Optional[Callable],
    ) -> dict:
        """Perform the actual download."""
        loop = asyncio.get_event_loop()
        
        if on_status:
            await on_status(DownloadStatus.DOWNLOADING, 0.0)

        # Get metadata first
        info_opts = {
            "quiet": True,
            "extract_flat": False,
            "skip_download": True,
        }
        
        if cookies_manager.has_cookies():
            info_opts["cookiefile"] = str(cookies_manager.get_cookies_path())

        def _get_info():
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                return ydl.extract_info(url, download=False)

        raw_info = await loop.run_in_executor(None, _get_info)
        if not raw_info:
            raise RuntimeError("Failed to extract metadata")

        normalised = self._normalise_info(url, raw_info)
        
        # Build output path
        clean_artist = normalised["folder_artist"]
        outtmpl = str(
            self._root
            / clean_artist
            / "%(album,Unknown Album)s"
            / "%(playlist_index&{:02d} - |)s%(title)s.%(ext)s"
        )

        # Build download options - SIMPLIFIED AND WORKING
        dl_opts = {
            # NO FORMAT SPECIFIED - let yt-dlp handle it automatically
            "outtmpl": outtmpl,
            "writethumbnail": True,
            "quiet": not settings.debug,
            "no_warnings": not settings.debug,
            "ignoreerrors": True,
            "progress_hooks": [self._make_progress_hook(on_progress, loop)],
        }
        
        # Add cookies if available
        if cookies_manager.has_cookies():
            dl_opts["cookiefile"] = str(cookies_manager.get_cookies_path())
        
        # Add postprocessors based on desired format
        postprocessors = []
        
        if settings.audio_format == "best":
            # Just extract audio, keep best quality
            postprocessors.append({
                "key": "FFmpegExtractAudio",
            })
        else:
            # Convert to specific format
            pp = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": settings.audio_format,
            }
            
            # Add quality for MP3
            if settings.audio_format == "mp3":
                pp["preferredquality"] = settings.mp3_quality
            
            postprocessors.append(pp)
        
        # Add metadata
        postprocessors.append({
            "key": "FFmpegMetadata",
            "add_metadata": True,
        })
        
        # Add thumbnail
        postprocessors.append({
            "key": "EmbedThumbnail",
        })
        
        dl_opts["postprocessors"] = postprocessors

        def _do_download():
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                result = ydl.extract_info(url, download=True)
                if not result:
                    raise RuntimeError("Download failed")
                return result

        try:
            result_info = await loop.run_in_executor(None, _do_download)
        except Exception as exc:
            if self._is_rate_limit_error(exc):
                raise RateLimitError(str(exc))
            logger.error("Download error: %s", exc, exc_info=True)
            raise

        if on_status:
            await on_status(DownloadStatus.PROCESSING, 95.0)

        # Count results
        if result_info.get("_type") == "playlist":
            entries = [e for e in (result_info.get("entries") or []) if e]
            done = len(entries)
            total = len(result_info.get("entries") or [])
        else:
            done, total = 1, 1

        if done == 0:
            raise RuntimeError(
                "No tracks downloaded. Check logs for errors."
            )

        if on_status:
            await on_status(DownloadStatus.COMPLETED, 100.0)

        return {
            **normalised,
            "done_tracks": done,
            "total_tracks": total,
        }

    def _make_progress_hook(
        self,
        on_progress: Optional[Callable],
        loop: asyncio.AbstractEventLoop,
    ) -> Callable:
        """Create progress hook function."""
        def _progress_hook(d: dict) -> None:
            if on_progress is None:
                return
                
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                idx = d.get("info_dict", {}).get("playlist_index") or 1
                count = (
                    d.get("info_dict", {}).get("playlist_count")
                    or d.get("info_dict", {}).get("n_entries")
                    or 1
                )
                
                file_pct = (downloaded / total) if total else 0
                overall = ((idx - 1 + file_pct) / count) * 100
                asyncio.run_coroutine_threadsafe(on_progress(min(overall, 99.0)), loop)
                
            elif status == "finished":
                idx = d.get("info_dict", {}).get("playlist_index") or 1
                count = (
                    d.get("info_dict", {}).get("playlist_count")
                    or d.get("info_dict", {}).get("n_entries")
                    or 1
                )
                overall = (idx / count) * 100
                asyncio.run_coroutine_threadsafe(on_progress(min(overall, 99.0)), loop)
        
        return _progress_hook

    @staticmethod
    def _normalise_info(url: str, raw: dict) -> dict:
        """Normalize metadata."""
        from app.utils import detect_download_type

        raw_artist = (
            raw.get("artist")
            or raw.get("uploader")
            or (raw.get("entries") or [{}])[0].get("artist", "")
            or "Unknown Artist"
        )
        raw_album = raw.get("album") or raw.get("title") or "Unknown Album"

        return {
            "title": raw.get("title", "Unknown"),
            "artist": raw_artist,
            "folder_artist": clean_artist_for_folder(raw_artist),
            "album": raw_album,
            "download_type": detect_download_type(url, raw),
            "total_tracks": len(raw.get("entries") or []) or 1,
        }


download_manager = DownloadManager()