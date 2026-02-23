# backend/app/downloader.py
"""
Core download logic.

Design choices:
  - Info extraction is done BEFORE the actual download so we can:
      a) Compute a clean artist name for the output path
      b) Report accurate track counts to the caller
  - The output template uses the CLEANED artist name (host-computed) for
    folder naming, but the raw metadata flows through to ID3 tags.
  - Three progressive format fallback attempts are tried on failure.
  - All blocking yt-dlp work runs in a thread-pool executor so the async
    event loop is never blocked.
"""
import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Callable, Optional

import certifi
import yt_dlp

from app.config import settings
from app.cookies_manager import cookies_manager
from app.models import DownloadStatus
from app.utils import clean_artist_for_folder, sanitize_path_component

logger = logging.getLogger(__name__)

# Point Python's SSL stack at certifi's trusted CA bundle
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# ── Format selector ladder (tried in order on failure) ──────────────────────
# Keep selectors simple — overly specific ones ("ext=m4a") fail on tracks
# that only offer opus/webm, especially on authenticated sessions.
_FORMAT_LADDER = [
    "bestaudio/best",
    "bestaudio*",
    "best",
]


class DownloadManager:
    """Wraps yt-dlp into an async-friendly, production-grade downloader."""

    def __init__(self) -> None:
        self._root: Path = settings.download_dir
        self._root.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_info(self, url: str) -> dict:
        """
        Extract lightweight metadata (no download).
        Returns a normalised dict used to pre-populate the DB record.
        """
        opts = self._base_info_opts()
        loop = asyncio.get_event_loop()

        def _run():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        raw = await loop.run_in_executor(None, _run)
        if not raw:
            raise RuntimeError("yt-dlp returned no metadata for this URL")

        return self._normalise_info(url, raw)

    async def download(
        self,
        url: str,
        on_progress: Optional[Callable[[float], None]] = None,
        on_status: Optional[Callable[[DownloadStatus, float], None]] = None,
    ) -> dict:
        """
        Download audio.  Tries up to len(_FORMAT_LADDER) format selectors
        before giving up.  All callbacks are async callables.
        """
        last_err: Exception = RuntimeError("Unknown error")

        for attempt, fmt in enumerate(_FORMAT_LADDER, start=1):
            try:
                logger.info("Download attempt %d/%d — format: %s", attempt, len(_FORMAT_LADDER), fmt)
                return await self._attempt_download(url, fmt, on_progress, on_status)
            except Exception as exc:
                last_err = exc
                logger.warning("Attempt %d failed: %s", attempt, exc)

        # All attempts failed
        if on_status:
            await on_status(DownloadStatus.FAILED, 0.0)
        raise last_err

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _base_info_opts(self) -> dict:
        """Options used for metadata-only extraction."""
        opts = {
            "quiet":              True,
            "no_warnings":        True,
            # flat extraction so large playlists don't take ages
            "extract_flat":       "in_playlist",
            "ignoreerrors":       True,
            "nocheckcertificate": False,
            "geo_bypass":         True,
            "extractor_args": {
                "youtube": {
                    # web client is required when cookies are present;
                    # android clients ignore cookies and return restricted formats
                    "player_client": self._player_clients(),
                }
            },
        }
        self._inject_cookies(opts)
        return opts

    @staticmethod
    def _player_clients() -> list[str]:
        """
        Return the right player client list based on whether cookies are present.

        - With cookies   → web first (respects auth), android as fallback
        - Without cookies → android_music/android (no login needed, good format set)
        """
        if cookies_manager.has_cookies():
            return ["web", "android", "ios"]
        return ["android_music", "android", "ios"]

    async def _attempt_download(
        self,
        url: str,
        fmt_selector: str,
        on_progress: Optional[Callable],
        on_status: Optional[Callable],
    ) -> dict:
        """
        One download attempt with a specific format selector.

        Two-phase approach:
          Phase 1 — extract info (flat) to determine clean artist name
          Phase 2 — actual download using a outtmpl built with that clean name
        """
        loop = asyncio.get_event_loop()

        if on_status:
            await on_status(DownloadStatus.DOWNLOADING, 0.0)

        # ── Phase 1: info extraction ──────────────────────────────────────────
        info_opts = self._base_info_opts()
        # Remove flat extraction for real metadata
        info_opts.pop("extract_flat", None)
        info_opts["extract_flat"] = False
        info_opts["extractor_args"] = {
            "youtube": {"player_client": self._player_clients()}
        }

        def _get_info():
            with yt_dlp.YoutubeDL(info_opts) as ydl:
                return ydl.extract_info(url, download=False)

        raw_info = await loop.run_in_executor(None, _get_info)
        if not raw_info:
            raise RuntimeError("Failed to extract metadata")

        normalised = self._normalise_info(url, raw_info)
        clean_artist = normalised["folder_artist"]  # filesystem-safe

        # ── Phase 2: actual download ──────────────────────────────────────────
        # Build outtmpl: /downloads/<CleanArtist>/%(album)s/%(playlist_index|)s%(title)s.%(ext)s
        # %(playlist_index&{:02d} - |)s adds "01 - " prefix only for playlists/albums
        outtmpl = str(
            self._root
            / clean_artist
            / "%(album,Unknown Album)s"
            / "%(playlist_index&{:02d} - |)s%(title)s.%(ext)s"
        )

        dl_opts = self._build_download_opts(fmt_selector, outtmpl, on_progress, loop)

        def _do_download():
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                result = ydl.extract_info(url, download=True)
                if not result:
                    raise RuntimeError("yt-dlp returned no result after download")
                return result

        result_info = await loop.run_in_executor(None, _do_download)

        if on_status:
            await on_status(DownloadStatus.PROCESSING, 95.0)

        # Count successful entries
        if result_info.get("_type") == "playlist":
            entries = [e for e in (result_info.get("entries") or []) if e]
            done = len(entries)
            total = len(result_info.get("entries") or [])
        else:
            done, total = 1, 1

        if done == 0:
            raise RuntimeError(
                "No tracks were saved. "
                "This may be due to age restrictions, geo-blocks, or DRM. "
                "Try uploading your YouTube cookies."
            )

        if on_status:
            await on_status(DownloadStatus.COMPLETED, 100.0)

        return {
            **normalised,
            "done_tracks":  done,
            "total_tracks": total,
        }

    def _build_download_opts(
        self,
        fmt_selector: str,
        outtmpl: str,
        on_progress: Optional[Callable],
        loop: asyncio.AbstractEventLoop,
    ) -> dict:
        """Assemble the full yt-dlp options dict for an actual download."""

        def _progress_hook(d: dict) -> None:
            """Convert yt-dlp progress events to a 0-100 float."""
            if on_progress is None:
                return
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                idx   = d.get("info_dict", {}).get("playlist_index") or 1
                count = d.get("info_dict", {}).get("playlist_count") or d.get("info_dict", {}).get("n_entries") or 1
                file_pct = (downloaded / total) if total else 0
                overall  = ((idx - 1 + file_pct) / count) * 100
                asyncio.run_coroutine_threadsafe(on_progress(min(overall, 99.0)), loop)
            elif d["status"] == "finished":
                idx   = d.get("info_dict", {}).get("playlist_index") or 1
                count = d.get("info_dict", {}).get("playlist_count") or d.get("info_dict", {}).get("n_entries") or 1
                overall = (idx / count) * 100
                asyncio.run_coroutine_threadsafe(on_progress(min(overall, 99.0)), loop)

        opts: dict = {
            "format":  fmt_selector,
            "outtmpl": outtmpl,
            # ── Post-processing chain ─────────────────────────────────────────
            "postprocessors": [
                {
                    # Extract audio and re-encode to target format
                    "key":              "FFmpegExtractAudio",
                    "preferredcodec":   settings.audio_format,
                    # preferredquality=0 means "best" for lossy formats (VBR highest quality)
                    "preferredquality": "0",
                    "nopostoverwrites": False,
                },
                {
                    # Write metadata (title, artist, album, track#) into ID3/tags
                    "key":          "FFmpegMetadata",
                    "add_metadata": True,
                },
                {
                    # Embed album art into the audio file
                    "key":          "EmbedThumbnail",
                    "already_have_thumbnail": False,
                },
            ],
            "writethumbnail":   True,   # needed so EmbedThumbnail has something to use
            "ignoreerrors":     True,   # skip unavailable tracks in playlists
            "nocheckcertificate": False,
            "geo_bypass":       True,
            "quiet":            not settings.debug,
            "no_warnings":      not settings.debug,
            "retries":          5,
            "fragment_retries": 5,
            "skip_unavailable_fragments": True,
            "prefer_ffmpeg":    True,
            "keepvideo":        False,
            "extractor_args": {
                "youtube": {
                    # Mirror the same client logic used for info extraction
                    "player_client": self._player_clients(),
                }
            },
            "progress_hooks": [_progress_hook],
        }

        self._inject_cookies(opts)
        return opts

    @staticmethod
    def _inject_cookies(opts: dict) -> None:
        """Add cookiefile to opts if cookies exist — no-op otherwise."""
        path = cookies_manager.get_cookies_path()
        if path:
            opts["cookiefile"] = path
            logger.debug("Using cookies from %s", path)

    @staticmethod
    def _normalise_info(url: str, raw: dict) -> dict:
        """
        Turn raw yt-dlp info into a predictable structure.

        ``folder_artist`` is the cleaned name used for folder creation.
        ``artist`` retains the full original string for DB / ID3 use.
        """
        from app.utils import detect_download_type

        # For playlists, the album artist may be on the playlist root or on entries[0]
        raw_artist = (
            raw.get("artist")
            or raw.get("uploader")
            or (raw.get("entries") or [{}])[0].get("artist", "")  # type: ignore[index]
            or "Unknown Artist"
        )

        raw_album = (
            raw.get("album")
            or raw.get("title")
            or "Unknown Album"
        )

        return {
            "title":         raw.get("title", "Unknown"),
            "artist":        raw_artist,
            "folder_artist": clean_artist_for_folder(raw_artist),
            "album":         raw_album,
            "download_type": detect_download_type(url, raw),
            "total_tracks":  len(raw.get("entries") or []) or 1,
        }


# ── Module-level singleton ───────────────────────────────────────────────────
download_manager = DownloadManager()