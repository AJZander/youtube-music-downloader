# backend/app/enrichment_service.py
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

import acoustid
import mutagen
import musicbrainzngs

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Download, DownloadStatus
from app.utils import clean_artist_for_folder

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {'.opus', '.m4a', '.mp3', '.flac', '.ogg', '.wav', '.aac', '.webm'}

musicbrainzngs.set_useragent("YTMDownloader", "1.0", "contact@local")
musicbrainzngs.set_rate_limit(1.0)


class EnrichmentService:

    async def enrich_download(self, download_id: int) -> None:
        """Main entry point — fetches download from DB, finds files, runs enrichment pipeline."""
        async with AsyncSessionLocal() as session:
            download = await session.get(Download, download_id)
            if not download or download.status != DownloadStatus.COMPLETED:
                return

            if not settings.acoustid_api_key:
                download.enrichment_status = "skipped"
                await session.commit()
                return

            download.enrichment_status = "enriching"
            await session.commit()
            await self._broadcast(download)

        try:
            files = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._find_audio_files(download_id)
            )
            if not files:
                logger.info("No audio files found for download %d — marking no_match", download_id)
                await self._set_status(download_id, "no_match")
                return

            logger.info("Enriching download %d: %d file(s) — %s",
                        download_id, len(files), [f.name for f in files])
            any_enriched = False
            for filepath in files:
                try:
                    ok = await self._enrich_file(filepath)
                    if ok:
                        any_enriched = True
                        logger.info("[enrich] OK: %s", filepath.name)
                    else:
                        logger.info("[enrich] no_match: %s", filepath.name)
                except Exception as exc:
                    logger.warning("[enrich] error on %s: %s", filepath.name, exc)

            await self._set_status(download_id, "enriched" if any_enriched else "no_match")

        except Exception as exc:
            logger.error("Enrichment failed for download %d: %s", download_id, exc)
            await self._set_status(download_id, "failed")

    # ── File discovery ────────────────────────────────────────────────────────

    def _find_audio_files(self, download_id: int) -> list[Path]:
        """Synchronous — find audio files on disk that belong to this download."""
        import asyncio as _asyncio
        # Run the async DB fetch synchronously via a new event loop call
        loop = _asyncio.new_event_loop()
        try:
            download = loop.run_until_complete(self._fetch_download(download_id))
        finally:
            loop.close()

        if not download:
            return []

        root = settings.download_dir
        artist_folder = clean_artist_for_folder(download.artist or "Unknown Artist")

        # Build candidate directories from most-specific to least
        candidates: list[Path] = []
        if download.album:
            album_clean = re.sub(r"[^\w\s\-]", "", download.album).strip()[:100]
            candidates.append(root / artist_folder / album_clean)
        candidates.append(root / artist_folder)

        logger.debug(
            "[find_audio] download %d: artist_folder=%r album=%r searching %d candidate dir(s)",
            download_id, artist_folder,
            download.album, len(candidates),
        )

        for folder in candidates:
            if not folder.exists():
                logger.debug("[find_audio] dir not found: %s", folder)
                continue
            found: list[Path] = []
            for ext in AUDIO_EXTENSIONS:
                found.extend(folder.rglob(f"*{ext}"))

            logger.debug("[find_audio] %s → %d file(s)", folder, len(found))

            if not found:
                continue

            # For single songs, narrow by title match
            if download.download_type == "song" and download.title:
                title_lower = download.title.lower().strip()
                narrowed = [f for f in found if title_lower in f.stem.lower()]
                if narrowed:
                    logger.debug("[find_audio] narrowed to title match: %s", narrowed[0].name)
                    return narrowed[:1]

            return sorted(set(found))

        logger.warning(
            "[find_audio] no audio files found for download %d (artist=%r album=%r searched=%s)",
            download_id, artist_folder, download.album,
            [str(c) for c in candidates],
        )
        return []

    async def _fetch_download(self, download_id: int) -> Optional[Download]:
        async with AsyncSessionLocal() as session:
            return await session.get(Download, download_id)

    # ── Enrichment pipeline for one file ─────────────────────────────────────

    async def _enrich_file(self, filepath: Path) -> bool:
        loop = asyncio.get_event_loop()

        # Step 1: fingerprint
        try:
            duration, fingerprint = await loop.run_in_executor(
                None, lambda: self._fpcalc(filepath)
            )
        except Exception as exc:
            logger.warning("fpcalc failed for %s: %s", filepath.name, exc)
            return False

        # Step 2: AcoustID lookup
        recording_ids = await loop.run_in_executor(
            None, lambda: self._acoustid_lookup(fingerprint, duration)
        )
        if not recording_ids:
            return False

        # Step 3: MusicBrainz metadata — try first few high-confidence matches
        for rec_id in recording_ids[:3]:
            metadata = await loop.run_in_executor(
                None, lambda r=rec_id: self._mb_lookup(r)
            )
            if metadata:
                try:
                    self._write_tags(filepath, metadata)
                    return True
                except Exception as exc:
                    logger.warning("Tag write failed for %s: %s", filepath.name, exc)

        return False

    # ── fpcalc (synchronous subprocess) ──────────────────────────────────────

    def _fpcalc(self, filepath: Path) -> tuple[float, str]:
        import subprocess
        result = subprocess.run(
            ["fpcalc", "-json", str(filepath)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"fpcalc error: {result.stderr[:200]}")
        data = json.loads(result.stdout)
        return float(data["duration"]), data["fingerprint"]

    # ── AcoustID lookup (synchronous) ─────────────────────────────────────────

    def _acoustid_lookup(self, fingerprint: str, duration: float) -> list[str]:
        try:
            results = acoustid.lookup(
                settings.acoustid_api_key,
                fingerprint,
                duration,
                meta=["recordings"],
            )
            ids: list[str] = []
            all_results = []
            for score, recording_id, title, artist in acoustid.parse_lookup_result(results):
                all_results.append((score, recording_id, title, artist))
                if score >= 0.7 and recording_id:
                    ids.append(recording_id)
            logger.debug(
                "[acoustid] duration=%.1fs → %d result(s), %d above 0.7 threshold: %s",
                duration, len(all_results), len(ids),
                [(f"{s:.2f}", t, a) for s, _, t, a in all_results[:5]],
            )
            return ids
        except Exception as exc:
            logger.warning("[acoustid] lookup failed: %s", exc)
            return []

    # ── MusicBrainz metadata fetch (synchronous) ──────────────────────────────

    def _mb_lookup(self, recording_id: str) -> Optional[dict]:
        try:
            result = musicbrainzngs.get_recording_by_id(
                recording_id,
                includes=["artists", "releases", "tags"],
            )
            rec = result["recording"]
            metadata: dict = {}

            # Artist credits
            credits = rec.get("artist-credit", [])
            names = [
                c["artist"]["name"]
                for c in credits
                if isinstance(c, dict) and "artist" in c
            ]
            if names:
                metadata["artist"] = ", ".join(names)

            if rec.get("title"):
                metadata["title"] = rec["title"]

            # Album and year from first release
            releases = rec.get("release-list", [])
            if releases:
                rel = releases[0]
                if rel.get("title"):
                    metadata["album"] = rel["title"]
                date = rel.get("date", "")
                if date:
                    metadata["year"] = date[:4]

            # Top genre tag
            tags = sorted(
                rec.get("tag-list", []),
                key=lambda t: int(t.get("count", 0)),
                reverse=True,
            )
            if tags:
                metadata["genre"] = tags[0].get("name", "").title()

            if metadata:
                logger.debug(
                    "[mb] recording=%s → title=%r artist=%r album=%r year=%r genre=%r",
                    recording_id,
                    metadata.get("title"), metadata.get("artist"),
                    metadata.get("album"), metadata.get("year"), metadata.get("genre"),
                )
            return metadata if metadata else None

        except musicbrainzngs.ResponseError as exc:
            logger.debug("[mb] no result for recording %s: %s", recording_id, exc)
            return None
        except Exception as exc:
            logger.warning("[mb] lookup error for recording %s: %s", recording_id, exc)
            return None

    # ── Tag writing via mutagen easy interface ────────────────────────────────

    def _write_tags(self, filepath: Path, metadata: dict) -> None:
        audio = mutagen.File(str(filepath), easy=True)
        if audio is None:
            raise RuntimeError(f"mutagen could not open {filepath.name}")

        field_map = {
            "title":  "title",
            "artist": "artist",
            "album":  "album",
            "year":   "date",
            "genre":  "genre",
        }
        written: dict = {}
        for src, tag in field_map.items():
            if metadata.get(src):
                try:
                    audio[tag] = [str(metadata[src])]
                    written[tag] = str(metadata[src])
                except (KeyError, mutagen.MutagenError) as exc:
                    logger.debug("[tags] could not write %r to %s: %s", tag, filepath.name, exc)

        # album_artist for Plex album grouping
        if metadata.get("artist"):
            try:
                audio["albumartist"] = [metadata["artist"]]
                written["albumartist"] = metadata["artist"]
            except (KeyError, mutagen.MutagenError):
                pass

        audio.save()
        logger.info("[tags] wrote to %s: %s", filepath.name, written)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _set_status(self, download_id: int, status: str) -> None:
        async with AsyncSessionLocal() as session:
            download = await session.get(Download, download_id)
            if download:
                download.enrichment_status = status
                await session.commit()
                await self._broadcast(download)

    async def _broadcast(self, download: Download) -> None:
        try:
            from app.queue_service import queue_service
            await queue_service._broadcast(download)
        except Exception:
            pass


enrichment_service = EnrichmentService()
