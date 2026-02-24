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
from app.models import DownloadStatus
from app.utils import clean_artist_for_folder

logger = logging.getLogger(__name__)

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())


class RateLimitError(Exception):
    """Raised when YouTube rate limiting is detected."""
    pass


class DownloadManager:
    def __init__(self) -> None:
        self._root: Path = settings.download_dir
        self._root.mkdir(parents=True, exist_ok=True)
        self._last_download_time: float = 0.0

    async def get_formats(self, url: str) -> dict:
        """
        Extract all available formats for user selection.
        Returns format list with metadata about each option.
        """
        loop = asyncio.get_event_loop()

        # Step 1: Get basic info to determine if it's a playlist
        basic_opts = self._get_base_ydl_opts()
        basic_opts.update({
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
        })

        def _get_basic_info():
            with yt_dlp.YoutubeDL(basic_opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            basic_info = await loop.run_in_executor(None, _get_basic_info)
        except Exception as exc:
            logger.error("Failed to extract basic info: %s", exc)
            raise RuntimeError(f"Could not extract video information: {str(exc)}")

        if not basic_info:
            raise RuntimeError("Failed to extract metadata")

        metadata = self._normalise_info(url, basic_info)

        # Step 2: Get specific formats
        sample_url = url
            
            if basic_info.get("_type") == "playlist":
                entries = basic_info.get("entries", [])
                valid_entries = [e for e in entries if e and e.get("url")]
                
                if valid_entries:
                    first_entry = valid_entries[0]
                    sample_url = first_entry.get("url") or f"https://www.youtube.com/watch?v={first_entry.get('id')}"
                    logger.info("Using sample video for format extraction: %s", sample_url)

            # Extract formats from sample
            format_opts = self._get_base_ydl_opts()
            format_opts.update({
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "no_download": True,
                "simulate": True,
                "format": "bestaudio/best",
            })

            def _get_format_info():
                with yt_dlp.YoutubeDL(format_opts) as ydl:
                    return ydl.extract_info(sample_url, download=False)

        try:
            format_info = await loop.run_in_executor(None, _get_format_info)
            formats = self._build_format_options(format_info) if format_info else self._get_fallback_formats()
        except Exception as exc:
            logger.warning("Failed to extract detailed formats, using fallback: %s", exc)
            formats = self._get_fallback_formats()

        return {
            "formats": formats,
            "metadata": metadata,
            "has_video": False,
            "is_playlist": basic_info.get("_type") == "playlist",
        }

    def _get_fallback_formats(self) -> list[dict]:
        """Return safe fallback format options when extraction fails."""
        return [
            {
                "format_id": "bestaudio/best",
                "label": "🔄 Auto-select Best (Recommended)",
                "description": "Let yt-dlp automatically choose the best available audio format",
                "ext": "auto",
                "recommended": True,
                "has_video": False,
            },
            {
                "format_id": "bestaudio[ext=webm]",
                "label": "🎵 Opus/WebM (High Quality)",
                "description": "Opus codec in WebM container - excellent quality",
                "ext": "webm",
                "codec": "opus",
                "recommended": False,
                "has_video": False,
            },
            {
                "format_id": "bestaudio[ext=m4a]",
                "label": "🎵 AAC/M4A (Good Quality)",
                "description": "AAC codec in M4A container - good compatibility",
                "ext": "m4a",
                "codec": "aac",
                "recommended": False,
                "has_video": False,
            },
        ]

    def _build_format_options(self, info: dict) -> list[dict]:
        """Build user-friendly format selection options."""
        available_formats = info.get("formats", [])
        if not available_formats:
            return self._get_fallback_formats()

        # Group audio-only formats by quality
        audio_formats = {}

        for fmt in available_formats:
            format_id = fmt.get("format_id")
            ext = fmt.get("ext")
            acodec = fmt.get("acodec", "none")
            vcodec = fmt.get("vcodec", "none")
            abr = fmt.get("abr", 0)
            tbr = fmt.get("tbr", 0)
            filesize = fmt.get("filesize") or fmt.get("filesize_approx", 0)

            if acodec == "none" and vcodec == "none":
                continue

            # Audio-only formats
            if vcodec == "none" and acodec != "none":
                quality_key = f"{format_id}_{acodec}"
                audio_formats[quality_key] = {
                    "format_id": format_id,
                    "ext": ext,
                    "codec": acodec,
                    "bitrate": int(abr or tbr),
                    "filesize": filesize,
                    "quality": abr or tbr or 0,
                }

        recommendations = []

        # Best audio quality (recommended)
        if audio_formats:
            best_audio = max(audio_formats.values(), key=lambda x: x["quality"])
            recommendations.append({
                "format_id": best_audio["format_id"],
                "label": f"🎵 Best Quality ({best_audio['codec'].upper()}, ~{best_audio['bitrate']}kbps)",
                "description": "Highest quality audio available",
                "ext": best_audio["ext"],
                "codec": best_audio["codec"],
                "bitrate": best_audio["bitrate"],
                "filesize_mb": round(best_audio["filesize"] / 1024 / 1024, 1) if best_audio["filesize"] else None,
                "recommended": True,
                "has_video": False,
            })

            # Alternative audio formats (up to 3)
            alternatives = [
                fmt for fmt in sorted(audio_formats.values(), key=lambda x: -x["quality"])
                if fmt["format_id"] != best_audio["format_id"]
            ][:3]
            
            for fmt_data in alternatives:
                recommendations.append({
                    "format_id": fmt_data["format_id"],
                    "label": f"🎵 {fmt_data['codec'].upper()} (~{fmt_data['bitrate']}kbps)",
                    "description": f"Audio only - {fmt_data['codec']} codec",
                    "ext": fmt_data["ext"],
                    "codec": fmt_data["codec"],
                    "bitrate": fmt_data["bitrate"],
                    "filesize_mb": round(fmt_data["filesize"] / 1024 / 1024, 1) if fmt_data["filesize"] else None,
                    "recommended": False,
                    "has_video": False,
                })

        # Always add auto-select option at the end
        recommendations.append({
            "format_id": "bestaudio/best",
            "label": "🔄 Auto-select Best",
            "description": "Let yt-dlp automatically choose the best available format (safest option)",
            "ext": "auto",
            "recommended": len(recommendations) == 0,
            "has_video": False,
        })

        return recommendations

    async def get_info(self, url: str) -> dict:
        """Extract metadata without downloading."""
        opts = self._get_base_ydl_opts()
        opts.update({
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
        })

        loop = asyncio.get_event_loop()

        def _run():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            raw = await loop.run_in_executor(None, _run)
        except Exception as exc:
            logger.error("Failed to extract info: %s", exc)
            raise RuntimeError(f"Could not extract video information: {str(exc)}")

        if not raw:
            raise RuntimeError("Failed to extract metadata")

        return self._normalise_info(url, raw)

    async def download(
        self,
        url: str,
        format_id: str = "bestaudio/best",
        on_progress: Optional[Callable[[float], None]] = None,
        on_status: Optional[Callable[[DownloadStatus, float], None]] = None,
    ) -> dict:
        """Download audio from URL with user-selected format."""
        await self._enforce_rate_limit()

        rate_limit_retry_count = 0
        max_retries = settings.max_rate_limit_retries

        while True:
            try:
                logger.info("Starting download (format: %s)", format_id)
                result = await self._attempt_download(url, format_id, on_progress, on_status)
                self._last_download_time = time.time()
                return result

            except RateLimitError as exc:
                rate_limit_retry_count += 1
                if rate_limit_retry_count > max_retries:
                    logger.error("Max rate limit retries (%d) exceeded", max_retries)
                    if on_status:
                        await on_status(DownloadStatus.FAILED, 0.0)
                    raise RuntimeError(
                        f"YouTube rate limiting detected. Max retries ({max_retries}) exceeded. "
                        "Please wait 30-60 minutes before trying again."
                    )

                wait_time = settings.rate_limit_backoff_seconds * rate_limit_retry_count
                logger.warning(
                    "Rate limit detected (retry %d/%d). Waiting %ds...",
                    rate_limit_retry_count,
                    max_retries,
                    wait_time,
                )

                if on_status:
                    await on_status(DownloadStatus.QUEUED, 0.0)

                await asyncio.sleep(wait_time)
                continue

            except Exception as exc:
                logger.exception("Download failed")
                if on_status:
                    await on_status(DownloadStatus.FAILED, 0.0)
                raise

    async def _enforce_rate_limit(self) -> None:
        """Ensure minimum interval between download starts."""
        if self._last_download_time > 0:
            elapsed = time.time() - self._last_download_time
            if elapsed < settings.download_interval_seconds:
                wait = settings.download_interval_seconds - elapsed
                logger.info("Rate limit: waiting %.1fs before next download", wait)
                await asyncio.sleep(wait)

    async def _attempt_download(
        self,
        url: str,
        format_id: str,
        on_progress: Optional[Callable],
        on_status: Optional[Callable],
    ) -> dict:
        """Perform the actual download with selected format."""
        loop = asyncio.get_event_loop()

        if on_status:
            await on_status(DownloadStatus.DOWNLOADING, 0.0)

        # Get metadata first
        info = await self.get_info(url)
        normalised = info

        # Build output template
        clean_artist = normalised["folder_artist"]
        outtmpl = str(
            self._root
            / clean_artist
            / "%(album,playlist,Unknown Album)s"
            / "%(playlist_index&{:02d} - |)s%(title)s.%(ext)s"
        )

        # Build download options
        dl_opts = self._get_base_ydl_opts()
        dl_opts.update({
            # USER-SELECTED FORMAT
            "format": format_id,
            
            # Output template
            "outtmpl": outtmpl,
            
            # Thumbnail handling
            "writethumbnail": True,
            "embedthumbnail": True,
            
            # Progress hook
            "progress_hooks": [self._make_progress_hook(on_progress, loop)],
            
            # Quiet mode
            "quiet": not settings.debug,
            "no_warnings": not settings.debug,
            
            # Continue on errors in playlists
            "ignoreerrors": True,
            
            # Post-processing
            "postprocessors": self._get_postprocessors(),
        })

        def _do_download():
            try:
                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    result = ydl.extract_info(url, download=True)
                    if not result:
                        raise RuntimeError("Download returned no data")
                    return result
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e).lower()
                if any(keyword in error_msg for keyword in [
                    "429", "too many requests", "rate limit", "throttled"
                ]):
                    raise RateLimitError(str(e))
                raise

        try:
            result_info = await loop.run_in_executor(None, _do_download)
        except RateLimitError:
            raise
        except Exception as exc:
            logger.error("Download error: %s", exc, exc_info=True)
            raise RuntimeError(f"Download failed: {str(exc)}")

        if on_status:
            await on_status(DownloadStatus.PROCESSING, 95.0)

        # Count successful downloads
        if result_info.get("_type") == "playlist":
            entries = [e for e in (result_info.get("entries") or []) if e]
            done = len(entries)
            total = result_info.get("playlist_count") or len(result_info.get("entries") or [])
        else:
            done, total = 1, 1

        if done == 0:
            raise RuntimeError(
                "No tracks were downloaded successfully. Check logs for individual track errors."
            )

        if on_status:
            await on_status(DownloadStatus.COMPLETED, 100.0)

        return {
            **normalised,
            "done_tracks": done,
            "total_tracks": total,
        }

    def _get_base_ydl_opts(self) -> dict:
        """Get base yt-dlp options."""
        opts = {
            "nocheckcertificate": False,
            "user_agent": settings.user_agent,
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
        }

        return opts

    def _get_postprocessors(self) -> list:
        """Build postprocessor chain based on settings."""
        postprocessors = []

        # Audio extraction and conversion
        if settings.audio_format == "best":
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "best",
                "preferredquality": "0",
            })
        else:
            pp_config = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": settings.audio_format,
            }
            
            if settings.audio_format == "mp3":
                quality = settings.mp3_quality
                if quality.isdigit() and int(quality) <= 10:
                    pp_config["preferredquality"] = quality
                else:
                    pp_config["preferredquality"] = quality.replace("k", "")
            
            postprocessors.append(pp_config)

        postprocessors.append({
            "key": "FFmpegMetadata",
            "add_metadata": True,
        })

        postprocessors.append({
            "key": "EmbedThumbnail",
            "already_have_thumbnail": False,
        })

        return postprocessors

    def _make_progress_hook(
        self,
        on_progress: Optional[Callable],
        loop: asyncio.AbstractEventLoop,
    ) -> Callable:
        """Create progress hook function for yt-dlp."""
        def _progress_hook(d: dict) -> None:
            if on_progress is None:
                return

            status = d.get("status")
            
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                
                info_dict = d.get("info_dict", {})
                playlist_index = info_dict.get("playlist_index", 1)
                playlist_count = info_dict.get("playlist_count") or info_dict.get("n_entries", 1)
                
                if total > 0:
                    file_progress = (downloaded / total)
                else:
                    file_progress = 0
                
                overall_progress = ((playlist_index - 1 + file_progress) / playlist_count) * 100
                overall_progress = min(overall_progress, 99.0)
                
                asyncio.run_coroutine_threadsafe(
                    on_progress(overall_progress),
                    loop
                )
                
            elif status == "finished":
                info_dict = d.get("info_dict", {})
                playlist_index = info_dict.get("playlist_index", 1)
                playlist_count = info_dict.get("playlist_count") or info_dict.get("n_entries", 1)
                
                overall_progress = (playlist_index / playlist_count) * 100
                overall_progress = min(overall_progress, 99.0)
                
                asyncio.run_coroutine_threadsafe(
                    on_progress(overall_progress),
                    loop
                )

        return _progress_hook

    @staticmethod
    def _normalise_info(url: str, raw: dict) -> dict:
        """Normalize metadata from yt-dlp."""
        from app.utils import detect_download_type

        if raw.get("_type") == "playlist":
            entries = raw.get("entries", [])
            first_entry = next((e for e in entries if e), {})
            
            raw_artist = (
                raw.get("uploader")
                or raw.get("channel")
                or first_entry.get("artist")
                or first_entry.get("uploader")
                or "Unknown Artist"
            )
            
            raw_album = raw.get("title", "Unknown Album")
            total_tracks = len([e for e in entries if e])
        else:
            raw_artist = (
                raw.get("artist")
                or raw.get("uploader")
                or raw.get("channel")
                or "Unknown Artist"
            )
            
            raw_album = raw.get("album") or raw.get("title", "Unknown Album")
            total_tracks = 1

        return {
            "title": raw.get("title", "Unknown"),
            "artist": raw_artist,
            "folder_artist": clean_artist_for_folder(raw_artist),
            "album": raw_album,
            "download_type": detect_download_type(url, raw),
            "total_tracks": total_tracks,
        }


download_manager = DownloadManager()