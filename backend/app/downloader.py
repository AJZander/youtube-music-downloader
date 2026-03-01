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

    async def get_playlist_full_metadata(self, playlist_url: str) -> dict:
        """
        Extract FULL metadata for a single playlist/album to get accurate track count
        and other detailed information.
        
        Returns dict with complete metadata including:
        - track_count: accurate number of tracks
        - duration: total duration if available
        - release_date/release_year: release information if available
        - description: playlist description
        """
        opts = self._get_base_ydl_opts()
        opts.update({
            "quiet": False if settings.debug else True,
            "no_warnings": False if settings.debug else True,
            "extract_flat": False,  # Full extraction for accurate track counts
            "skip_download": True,
            "playlistend": 100,  # Limit to first 100 tracks for performance
            "ignoreerrors": True,  # Continue even if some videos are unavailable
        })

        loop = asyncio.get_event_loop()

        def _run():
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(playlist_url, download=False)

        try:
            raw = await loop.run_in_executor(None, _run)
        except Exception as exc:
            logger.warning("Could not extract full metadata for %s: %s", playlist_url, exc)
            return {}

        if not raw:
            return {}

        # Extract all available metadata
        entries = raw.get("entries") or []
        # Filter out None entries (unavailable videos) and ensure valid entries
        valid_entries = [e for e in entries if e and e.get("id")]
        
        metadata = {
            "track_count": len(valid_entries),
            "playlist_count": raw.get("playlist_count"),
            "release_date": raw.get("release_date"),
            "release_year": raw.get("release_year"),
            "description": raw.get("description"),
            "availability": raw.get("availability"),
            "modified_date": raw.get("modified_date"),
            "view_count": raw.get("view_count"),
            "like_count": raw.get("like_count"),
        }
        
        # Calculate total duration if available
        total_duration = 0
        for entry in valid_entries:
            duration = entry.get("duration")
            if duration:
                total_duration += duration
        
        if total_duration > 0:
            metadata["total_duration"] = total_duration
            
        logger.debug(
            "Extracted full metadata for %s: %d valid tracks (from %d total), duration: %ds",
            playlist_url, len(valid_entries), len(entries), total_duration
        )
        
        return metadata

    async def get_channel_playlists(self, url: str) -> list[dict]:
        """
        Extract all albums AND playlists from a YouTube channel.
        Accepts any channel URL (/@Artist, /c/Name, /channel/UCxxx).

        YouTube Music artist channels keep albums/singles under the /releases tab
        and user-created playlists under /playlists.  We scan BOTH tabs and merge
        the results so nothing is missed.

        Returns a list of playlist dicts with id, title, url, thumbnail, track_count,
        and additional metadata for proper album/EP/single classification.
        """
        import re as _re

        # Strip any existing tab suffix to get the bare channel root
        clean = url.rstrip("/")
        clean = _re.sub(
            r"/(videos|shorts|streams|about|community|featured|store|playlists|releases)$",
            "",
            clean,
        )

        # Tabs to scan – 'releases' is where YouTube Music albums/singles live;
        # 'playlists' holds user-created collections.
        tabs_to_scan = [
            ("releases", clean + "/releases"),
        ]

        opts = self._get_base_ydl_opts()
        opts.update({
            "quiet": False if settings.debug else True,
            "no_warnings": False if settings.debug else True,
            "extract_flat": True,  # Extract all playlists without going into each one
            "skip_download": True,
            "playlistend": None,  # Get all items, not just first N
            "ignoreerrors": True,  # Continue on errors
        })

        loop = asyncio.get_event_loop()

        def _run(tab_url: str):
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(tab_url, download=False)

        channel_name: str | None = None
        channel_url_out: str | None = None
        seen_ids: set[str] = set()
        playlists: list[dict] = []

        for tab_name, tab_url in tabs_to_scan:
            try:
                raw = await loop.run_in_executor(None, _run, tab_url)
            except Exception as exc:
                logger.warning(
                    "Could not scan %s tab (%s): %s — skipping", tab_name, tab_url, exc
                )
                continue

            if not raw:
                continue

            # Capture channel identity from whichever tab responds first
            if not channel_name:
                channel_name = raw.get("channel") or raw.get("uploader") or raw.get("title")
                channel_url_out = raw.get("channel_url") or raw.get("uploader_url")

            entries = raw.get("entries") or []
            tab_count = 0
            
            logger.info(
                "Tab '%s': found %d entries to process",
                tab_name, len(entries)
            )

            for entry in entries:
                if not entry:
                    continue

                playlist_id = entry.get("id") or entry.get("playlist_id")
                entry_url = entry.get("url") or entry.get("webpage_url")
                if not entry_url and playlist_id:
                    entry_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                if not entry_url:
                    continue

                # Deduplicate across tabs
                dedup_key = playlist_id or entry_url
                if dedup_key in seen_ids:
                    continue
                seen_ids.add(dedup_key)

                # Log first entry to see what data we have available
                if tab_count == 0:
                    logger.info(
                        "First entry fields: %s",
                        {k: v for k, v in entry.items() if k not in ("entries", "thumbnails")}
                    )

                # Best thumbnail
                thumb = None
                thumbnails = entry.get("thumbnails")
                if thumbnails and isinstance(thumbnails, list):
                    thumb = thumbnails[-1].get("url")
                if not thumb:
                    thumb = entry.get("thumbnail")

                # Try multiple fields to get track count
                track_count = (
                    entry.get("playlist_count") or
                    entry.get("n_entries") or
                    entry.get("entry_count") or
                    entry.get("video_count") or
                    entry.get("track_count")
                )
                
                # If still no track count and entries list is available, use its length
                if track_count is None:
                    entry_entries = entry.get("entries")
                    if entry_entries and isinstance(entry_entries, list):
                        track_count = len(entry_entries)
                
                title = entry.get("title") or "Unknown Playlist"
                
                # Skip live albums and live performances
                if self._is_live_album(title):
                    logger.debug("Skipping live album: %s", title)
                    continue
                
                # Extract full metadata for accurate track count and additional info
                # Only do this for releases tab to get accurate album/single/EP classification
                full_metadata = {}
                if tab_name == "releases" and entry_url:
                    try:
                        logger.info("Extracting full metadata for: %s", title)
                        full_metadata = await self.get_playlist_full_metadata(entry_url)
                        # Use accurate track count from full extraction
                        if full_metadata.get("track_count") is not None:
                            track_count = full_metadata["track_count"]
                            logger.info(
                                "✓ '%s': %d tracks",
                                title, track_count
                            )
                    except Exception as exc:
                        logger.warning(
                            "✗ Failed to get full metadata for '%s': %s",
                            title, exc
                        )
                        # Continue processing even if full metadata fails
                        # Use whatever track count we got from flat extraction
                
                release_type = self._classify_release_type(
                    entry, tab_name, title, track_count
                )

                # Filter out music videos
                if release_type != "music_video":
                    playlist_data = {
                        "id": playlist_id,
                        "title": title,
                        "url": entry_url,
                        "thumbnail": thumb,
                        "track_count": track_count,
                        "channel": channel_name,
                        "channel_url": channel_url_out,
                        "source_tab": tab_name,
                        "release_type": release_type,
                    }
                    
                    # Add additional metadata if available from full extraction
                    if full_metadata:
                        for key in ["release_date", "release_year", "description", 
                                    "total_duration", "view_count", "like_count"]:
                            if full_metadata.get(key) is not None:
                                playlist_data[key] = full_metadata[key]
                    
                    playlists.append(playlist_data)
                    tab_count += 1
                else:
                    logger.debug("Filtered out music video: %s", title)

            logger.info(
                "Tab '%s': processed %d releases, kept %d after filtering (total: %d)",
                tab_name, len(entries), tab_count, len(playlists),
            )

        if channel_name is None and not playlists:
            raise RuntimeError(
                "Could not access any channel tabs. "
                "Check that the URL is a valid YouTube channel."
            )

        logger.info(
            "✓ Channel extraction complete: %d releases from %s",
            len(playlists), channel_name or url,
        )
        
        # Log summary by release type
        type_counts = {}
        for p in playlists:
            rt = p.get("release_type", "unknown")
            type_counts[rt] = type_counts.get(rt, 0) + 1
        logger.info("Release breakdown: %s", type_counts)
        
        return playlists

    @staticmethod
    def _classify_release_type(
        entry: dict,
        tab_name: str,
        title: str,
        track_count: int | None,
    ) -> str:
        """
        Determine whether a release is an album, EP, single, or music video.

        Priority:
        1. Filter out music videos first
        2. Explicit field from yt-dlp  (newer builds populate release_type)
        3. YouTube Music title suffixes (" - Single", " - EP", " - Album")
        4. Track-count heuristic:
           - 1 track → single
           - 2-4 tracks → single (could be single + remixes)
           - 5-7 tracks → EP
           - 8+ tracks → album
        5. Conservative default (releases tab → album, other → playlist)
        """
        import re as _re

        # Non-release tabs are just playlists
        if tab_name != "releases":
            return "playlist"

        # Filter out music videos first
        if DownloadManager._is_music_video(entry, title, track_count):
            return "music_video"

        # 1. Explicit field (e.g. "Album", "EP", "Single")
        explicit = (
            entry.get("release_type")
            or entry.get("album_type")
            or entry.get("playlist_type")
        )
        if explicit:
            norm = str(explicit).lower().strip()
            if norm == "ep":
                return "ep"
            if norm == "album":
                return "album"
            if norm == "single":
                return "single"

        # 2. Title suffix patterns used by YouTube Music
        lower = title.lower()
        if _re.search(r"[-–]\s*single\s*$", lower):
            return "single"
        if _re.search(r"[-–]\s*ep\s*$", lower):
            return "ep"
        if _re.search(r"[-–]\s*album\s*$", lower):
            return "album"

        # 3. Track-count heuristic with improved classification
        if track_count is not None:
            if track_count <= 4:
                return "single"
            elif track_count <= 7:
                return "ep"
            else:  # 8 or more tracks
                return "album"

        # 4. Default for releases tab
        return "album"

    @staticmethod
    def _is_live_album(title: str) -> bool:
        """
        Detect if a release is a live album or live performance.
        
        Live albums typically have:
        - "Live" in the title
        - "Live at" or "Live from"
        - "In Concert"
        - References to venues or dates
        """
        import re as _re
        
        lower_title = title.lower()
        
        # Common patterns for live albums
        live_patterns = [
            r"\blive\b",  # Word "live" as standalone word
            r"\(live\)",  # (Live)
            r"\blive at\b",  # Live at [venue]
            r"\blive from\b",  # Live from [venue]
            r"\blive in\b",  # Live in [city]
            r"in concert",
            r"concert at",
            r"recorded live",
            r"live recording",
            r"live session",
            r"live performance",
            # Venue indicators
            r"at.*(?:arena|stadium|hall|center|theatre|theater|opera house|club)",
        ]
        
        for pattern in live_patterns:
            if _re.search(pattern, lower_title):
                return True
        
        return False
    
    @staticmethod
    def _is_music_video(entry: dict, title: str, track_count: int | None) -> bool:
        """
        Identify music videos to filter them out.
        
        Music videos typically have:
        - Track count of 1
        - Contains "(Official Video)", "(Music Video)", or similar
        - Duration suggests single track rather than album
        - Contains video-specific keywords
        """
        import re as _re
        
        # Music videos almost always have exactly 1 track
        if track_count is not None and track_count != 1:
            return False
            
        lower_title = title.lower()
        
        # Check for video-specific patterns in title
        video_patterns = [
            r"\(official\s+video\)",
            r"\(music\s+video\)",
            r"\(official\s+music\s+video\)",
            r"\(lyric\s+video\)",
            r"\(official\s+lyric\s+video\)",
            r"\(live\s+video\)",
            r"\(performance\s+video\)",
            r"\(visuali[sz]er\)",
            r"\(official\s+visuali[sz]er\)",
            r"\bmv\b",  # Common abbreviation for music video
            r"official\s+mv\b",
        ]
        
        for pattern in video_patterns:
            if _re.search(pattern, lower_title):
                return True
                
        # Check if entry has video-specific metadata
        entry_type = entry.get("_type", "")
        if "video" in entry_type.lower():
            # Additional check: if it's explicitly marked as a video and has 1 track
            if track_count == 1:
                return True
                
        # Check duration if available - music videos are typically 2-8 minutes
        duration = entry.get("duration")
        if duration is not None and track_count == 1:
            # If single track and duration is typical for a music video (2-8 min)
            if 120 <= duration <= 480:  # 2-8 minutes in seconds
                # Additional heuristic: check if title doesn't indicate album/EP
                if not _re.search(r"(album|ep)\b", lower_title):
                    return True
        
        return False

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