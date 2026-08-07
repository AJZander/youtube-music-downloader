"""DB-backed download worker. The database is the source of truth; the
in-memory wakeup event is just a doorbell. One track downloads at a time."""
import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

from .config import settings
from .db import db, utcnow
from .engine import spotify, ytdlp_engine
from .engine.errors import EngineError, classify
from .engine.governor import governor, REQUEST_WEIGHT
from .engine.matcher import match, normalize, _NOISE_RE
from .engine.router import RoutedURL, route
from .engine import tagger
from .events import bus
from .importer import (ImportError_, ensure_sentinel, import_track,
                       library_online, quarantine, scan_debouncer)
from .library_index import add_entry, find_duplicate, in_legacy_archive
from .models import ErrorClass, JobStatus, TrackStatus, TERMINAL_TRACK_STATUSES
from .plex import plex
from .validator import ValidationError, validate_track

log = logging.getLogger("mdl.worker")

TRANSIENT_RETRY_DELAYS = [30, 120, 600]
PREMIUM_FORMATS = {"141", "774"}


class Worker:
    def __init__(self) -> None:
        self._wakeup = asyncio.Event()
        self._stopping = False
        self._tasks: list[asyncio.Task] = []
        self.current_track_id: str | None = None

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        ensure_sentinel()
        await self._crash_recovery()
        self._tasks = [
            asyncio.create_task(self._main_loop(), name="worker-main"),
            asyncio.create_task(self._maintenance_loop(), name="worker-maintenance"),
            asyncio.create_task(self._daily_loop(), name="worker-daily"),
        ]
        self.wake()

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        await scan_debouncer.flush()

    def wake(self) -> None:
        self._wakeup.set()

    # ------------------------------------------------------------ enqueue
    async def enqueue(self, url: str, force: bool = False) -> str:
        routed: RoutedURL = route(url)
        job_id = uuid.uuid4().hex[:12]
        await db.execute(
            """INSERT INTO jobs (id, source_url, provider, source_type, force,
                                 status, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (job_id, routed.canonical_url, routed.provider, routed.source_type,
             int(force), JobStatus.QUEUED, utcnow()))
        await self._publish_job(job_id)
        self.wake()
        return job_id

    # ------------------------------------------------------------ main loop
    async def _main_loop(self) -> None:
        while not self._stopping:
            try:
                did_work = await self._step()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker step crashed")
                did_work = False
                await asyncio.sleep(10)
            if not did_work:
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass

    async def _step(self) -> bool:
        job = await db.fetchone(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
            (JobStatus.QUEUED,))
        if job:
            await self._expand_job(job)
            return True
        track = await db.fetchone(
            """SELECT t.* FROM tracks t JOIN jobs j ON j.id = t.job_id
               WHERE t.status = ? AND j.status NOT IN (?, ?)
               ORDER BY j.created_at, t.position LIMIT 1""",
            (TrackStatus.PENDING, JobStatus.CANCELLED, JobStatus.FAILED))
        if track:
            await self._process_track(track)
            return True
        return False

    # ------------------------------------------------------------ expansion
    async def _expand_job(self, job) -> None:
        job_id = job["id"]
        await self._update_job(job_id, status=JobStatus.RESOLVING, started_at=utcnow())
        try:
            title, artist, tracks = await self._resolve(job)
        except EngineError as exc:
            await self._update_job(job_id, status=JobStatus.FAILED,
                                   error_code=exc.error_class, error_message=str(exc),
                                   finished_at=utcnow())
            return
        except Exception as exc:
            error_class, message = classify(exc)
            governor.report_error(error_class)
            log.exception("expansion failed for job %s", job_id)
            await self._update_job(job_id, status=JobStatus.FAILED,
                                   error_code=error_class, error_message=message[:500],
                                   finished_at=utcnow())
            return

        if not tracks:
            await self._update_job(job_id, status=JobStatus.FAILED,
                                   error_code="empty", error_message="No tracks found",
                                   finished_at=utcnow())
            return

        rows = []
        for position, t in enumerate(tracks, start=1):
            rows.append((
                uuid.uuid4().hex[:12], job_id, position,
                t.get("provider") or job["provider"],
                t.get("provider_track_id"), t.get("source_url"),
                t.get("artist"), t.get("album"), t.get("album_artist"), t.get("title"),
                t.get("track_number"), t.get("disc_number"), t.get("release_date"),
                t.get("isrc"), t.get("cover_url"), t.get("duration_sec"),
                TrackStatus.PENDING, utcnow()))
        await db.executemany(
            """INSERT INTO tracks (id, job_id, position, provider, provider_track_id,
                 source_url, artist, album, album_artist, title, track_number,
                 disc_number, release_date, isrc, cover_url, expected_duration_sec,
                 status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows)
        await self._update_job(job_id, status=JobStatus.DOWNLOADING, title=title,
                               artist=artist, total_tracks=len(tracks))

    async def _resolve(self, job) -> tuple[str | None, str | None, list[dict]]:
        provider, source_type = job["provider"], job["source_type"]
        url = job["source_url"]

        if provider == "spotify":
            entity_id = url.rstrip("/").split("/")[-1]
            if source_type == "track":
                t = await spotify.get_track(entity_id)
                return t["title"], t["artist"], [t]
            if source_type == "album":
                meta, tracks = await spotify.get_album_tracks(entity_id)
                return meta["title"], meta["artist"], tracks
            if source_type == "playlist":
                meta, tracks = await spotify.get_playlist_tracks(entity_id)
                return meta["title"], meta["artist"], tracks
            if source_type == "artist":
                meta, albums = await spotify.get_artist_albums(entity_id)
                tracks: list[dict] = []
                for album in albums:
                    _, album_tracks = await spotify.get_album_tracks(album["id"])
                    tracks.extend(album_tracks)
                return meta["title"], meta["artist"], tracks

        # YouTube / YouTube Music
        if source_type == "track":
            video_id = url.split("v=")[-1][:11]
            return None, None, [{
                "provider": provider, "provider_track_id": video_id,
                "source_url": f"https://music.youtube.com/watch?v={video_id}"}]

        if source_type in ("album", "playlist"):
            import re
            m = re.search(r"browse/(MPREb_[A-Za-z0-9_-]+)", url)
            if m:
                return await self._resolve_ytm_album(m.group(1))
            await governor.acquire(REQUEST_WEIGHT)
            info = await ytdlp_engine.extract_info(url, flat=True)
            entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
            tracks = [{
                "provider": provider, "provider_track_id": e["id"],
                "source_url": f"https://music.youtube.com/watch?v={e['id']}",
                "title": e.get("title"),
                "artist": e.get("uploader") or e.get("channel"),
            } for e in entries]
            return info.get("title"), info.get("uploader") or info.get("channel"), tracks

        if source_type == "artist":
            return await self._resolve_yt_artist(url)

        raise EngineError(ErrorClass.PERMANENT, f"unsupported job type {provider}/{source_type}")

    def _album_tracks(self, album: dict, album_artist: str | None) -> list[dict]:
        album_name = album.get("title")
        year = str(album.get("year") or "")
        out = []
        for i, t in enumerate(album.get("tracks") or [], start=1):
            if not t.get("videoId"):
                continue
            out.append({
                "provider": "youtube_music",
                "provider_track_id": t["videoId"],
                "source_url": f"https://music.youtube.com/watch?v={t['videoId']}",
                "title": t.get("title"),
                "artist": ", ".join(a.get("name", "") for a in (t.get("artists") or [])) or album_artist,
                "album": album_name, "album_artist": album_artist,
                "track_number": t.get("trackNumber") or i,
                "release_date": year,
                "duration_sec": t.get("duration_seconds"),
            })
        return out

    async def _resolve_ytm_album(self, browse_id: str) -> tuple[str | None, str | None, list[dict]]:
        from ytmusicapi import YTMusic

        loop = asyncio.get_running_loop()
        await governor.acquire(REQUEST_WEIGHT)
        album = await loop.run_in_executor(None, YTMusic().get_album, browse_id)
        album_artist = ", ".join(
            a.get("name", "") for a in (album.get("artists") or [])) or None
        tracks = self._album_tracks(album, album_artist)
        # get_album videoIds sometimes point at the music-video version (longer
        # than the album track). The album's audioPlaylistId serves the actual
        # album audio — override ids by position when the counts line up.
        playlist_id = album.get("audioPlaylistId")
        if playlist_id and tracks:
            try:
                await governor.acquire(REQUEST_WEIGHT)
                info = await ytdlp_engine.extract_info(
                    f"https://music.youtube.com/playlist?list={playlist_id}", flat=True)
                entries = [e for e in (info.get("entries") or []) if e and e.get("id")]
                if len(entries) == len(tracks):
                    for t, e in zip(tracks, entries):
                        t["provider_track_id"] = e["id"]
                        t["source_url"] = f"https://music.youtube.com/watch?v={e['id']}"
                else:
                    log.warning("audio playlist has %d entries vs %d album tracks — keeping album ids",
                                len(entries), len(tracks))
            except Exception as exc:
                log.warning("audioPlaylistId override failed: %s", exc)
        return album.get("title"), album_artist, tracks

    async def _resolve_yt_artist(self, url: str) -> tuple[str | None, str | None, list[dict]]:
        from ytmusicapi import YTMusic

        loop = asyncio.get_running_loop()
        channel_id = None
        for part in url.split("/"):
            if part.startswith("UC"):
                channel_id = part.split("?")[0]
                break
        if not channel_id:
            await governor.acquire(REQUEST_WEIGHT)
            info = await ytdlp_engine.extract_info(url, flat=True)
            channel_id = info.get("channel_id") or info.get("uploader_id")
        if not channel_id:
            raise EngineError(ErrorClass.PERMANENT, "could not resolve channel id")

        ytm = YTMusic()
        await governor.acquire(REQUEST_WEIGHT)
        artist = await loop.run_in_executor(None, ytm.get_artist, channel_id)
        artist_name = artist.get("name")
        tracks: list[dict] = []
        for section in ("albums", "singles"):
            sec = artist.get(section) or {}
            results = sec.get("results") or []
            # follow browse pages for the full list when available
            if sec.get("browseId"):
                try:
                    await governor.acquire(REQUEST_WEIGHT)
                    results = await loop.run_in_executor(
                        None, ytm.get_artist_albums, sec["browseId"], sec.get("params"))
                except Exception as exc:
                    log.warning("artist %s full album list failed: %s", section, exc)
            for release in results:
                browse_id = release.get("browseId")
                if not browse_id:
                    continue
                try:
                    await governor.acquire(REQUEST_WEIGHT)
                    album = await loop.run_in_executor(None, ytm.get_album, browse_id)
                except Exception as exc:
                    log.warning("get_album %s failed: %s", browse_id, exc)
                    continue
                tracks.extend(self._album_tracks(album, artist_name))
        return artist_name, artist_name, tracks

    # ------------------------------------------------------------ track pipeline
    async def _process_track(self, track) -> None:
        track_id, job_id = track["id"], track["job_id"]
        job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        force = bool(job["force"])
        self.current_track_id = track_id
        staging = settings.staging_jobs_dir / job_id / track_id
        try:
            await self._update_track(track_id, status=TrackStatus.DOWNLOADING,
                                     stage="preparing", started_at=utcnow())

            # -------- pre-download dedup
            if not force:
                dup = await find_duplicate(track["provider_track_id"],
                                           track["artist"], track["title"])
                if dup:
                    await self._update_track(
                        track_id, status=TrackStatus.SKIPPED_DUPLICATE,
                        stage=None, library_path=dup, finished_at=utcnow())
                    await self._recompute_job(job_id)
                    return
            if track["provider_track_id"] and await in_legacy_archive(
                    "youtube", track["provider_track_id"]):
                await bus.record("info", "legacy_hint",
                                 "was downloaded by the old system (advisory)",
                                 job_id, track_id)

            # -------- Spotify: match to a verified YT Music video
            source_url = track["source_url"]
            is_spotify = track["provider"] == "spotify"
            if is_spotify and "open.spotify.com" in (source_url or ""):
                await self._update_track(track_id, stage="matching")
                spotify_meta = self._track_row_to_meta(track)
                video_id, candidates = await match(spotify_meta)
                source_url = f"https://music.youtube.com/watch?v={video_id}"
                # persist the match so retries don't re-search
                await self._update_track(
                    track_id, source_url=source_url,
                    match_candidates=json.dumps(candidates[:8], default=str))

            # -------- download
            await self._update_track(track_id, stage="downloading")
            await governor.acquire(1.0)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            info = await ytdlp_engine.download(
                source_url, staging, progress_hook=self._make_progress_hook(track_id))
            governor.mark_download_end()
            governor.report_success()

            fmt_id = str(info.get("format_id") or "")
            below_target = ytdlp_engine.cookies.authenticated and fmt_id not in PREMIUM_FORMATS

            # -------- validate
            await self._update_track(track_id, status=TrackStatus.VALIDATING,
                                     stage="validating", progress_pct=100.0)
            validated = await validate_track(staging, track["expected_duration_sec"])

            # -------- tag (merge authoritative row metadata over file tags)
            await self._update_track(track_id, stage="tagging")
            row_meta = self._track_row_to_meta(track)
            final_meta = await self._write_final_tags(validated, row_meta, is_spotify)

            # -------- import
            await self._update_track(track_id, status=TrackStatus.IMPORTING, stage="importing")
            result = await import_track(validated["path"], validated, final_meta, force=force)
            meta = result["meta"]

            await add_entry(result["library_path"], meta["artist"], meta["title"],
                            meta["album"], validated["format"],
                            validated["duration_sec"], track["provider_track_id"])
            album_relpath = str(Path(result["library_path"]).parent)
            scan_debouncer.schedule(album_relpath)

            shutil.rmtree(staging, ignore_errors=True)
            await self._update_track(
                track_id, status=TrackStatus.COMPLETED, stage=None,
                library_path=result["library_path"],
                checksum_blake2b=result["checksum_blake2b"],
                format=validated["format"], bitrate_kbps=validated["bitrate_kbps"],
                sample_rate=validated["sample_rate"],
                duration_sec=validated["duration_sec"],
                filesize_bytes=validated["filesize_bytes"],
                below_target=int(below_target),
                ytdlp_version=ytdlp_engine.ytdlp_version(),
                artist=meta["artist"], album=meta["album"],
                album_artist=meta["album_artist"], title=meta["title"],
                error_code=None, error_message=None,
                finished_at=utcnow())
            asyncio.create_task(self._verify_in_plex(track_id, meta))
        except ValidationError as exc:
            await self._handle_validation_failure(track, staging, exc)
        except ImportError_ as exc:
            await self._handle_import_failure(track, exc)
        except EngineError as exc:
            await self._update_track(
                track_id, status=TrackStatus.FAILED, stage=None,
                error_code=exc.error_class, error_message=str(exc)[:500],
                match_candidates=json.dumps(exc.candidates[:8], default=str) if exc.candidates else track["match_candidates"],
                finished_at=utcnow())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._handle_engine_failure(track, staging, exc)
        finally:
            self.current_track_id = None
            await self._recompute_job(job_id)

    def _track_row_to_meta(self, track) -> dict:
        artist = track["artist"] or ""
        return {
            "provider_track_id": track["provider_track_id"],
            "title": track["title"] or "",
            "artist": artist,
            "primary_artist": artist.split(",")[0].strip(),
            "album": track["album"] or "",
            "album_artist": track["album_artist"] or artist.split(",")[0].strip(),
            "track_number": track["track_number"],
            "disc_number": track["disc_number"],
            "release_date": track["release_date"] or "",
            "duration_sec": track["expected_duration_sec"] or 0.0,
            "isrc": track["isrc"],
            "cover_url": track["cover_url"],
        }

    async def _write_final_tags(self, validated: dict, row_meta: dict,
                                is_spotify: bool) -> dict:
        """Merge authoritative metadata (Spotify / ytmusicapi expansion) over the
        file's embedded tags, verify completeness, write, and return the final
        meta used for both tags and path building."""
        path = validated["path"]
        tags = validated["tags"]

        def clean(s: str | None) -> str:
            return (s or "").removesuffix(" - Topic").strip()

        if is_spotify:
            meta = dict(row_meta)  # Spotify data is fully authoritative
        else:
            title = row_meta.get("title") or tags.get("title") or ""
            artist = clean(row_meta.get("artist") or tags.get("artist"))
            meta = {
                "title": _NOISE_RE.sub("", title).strip() or title,
                "artist": artist,
                "album": row_meta.get("album") or tags.get("album") or "",
                "album_artist": clean(row_meta.get("album_artist")
                                      or tags.get("album_artist")) or artist,
                "track_number": row_meta.get("track_number") or tags.get("track_number"),
                "disc_number": row_meta.get("disc_number") or tags.get("disc_number"),
                "release_date": row_meta.get("release_date") or tags.get("release_date"),
                "isrc": row_meta.get("isrc"),
            }
        for field in ("title", "artist", "album"):
            if not (meta.get(field) or "").strip():
                raise ValidationError(
                    "missing_tags",
                    f"{field!r} unknown from both source metadata and file tags")

        cover = None
        if is_spotify and row_meta.get("cover_url"):
            cover = await tagger.fetch_cover(row_meta["cover_url"])
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, tagger.write_tags, path, meta, cover)
        validated["tags"] = tagger.read_tags(path)
        return meta

    def _make_progress_hook(self, track_id: str):
        loop = asyncio.get_running_loop()
        state = {"last": 0.0}

        def hook(d: dict) -> None:  # runs on the yt-dlp thread
            if d.get("status") != "downloading":
                return
            now = time.time()
            if now - state["last"] < 2.0:
                return
            state["last"] = now
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if not total:
                return
            pct = round(100.0 * (d.get("downloaded_bytes") or 0) / total, 1)
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self._update_track(track_id, progress_pct=pct)))

        return hook

    # ------------------------------------------------------------ failure paths
    async def _handle_validation_failure(self, track, staging: Path,
                                         exc: ValidationError) -> None:
        track_id = track["id"]
        attempts = (track["attempts"] or 0) + 1
        if attempts <= 1:
            shutil.rmtree(staging, ignore_errors=True)
            await self._update_track(track_id, status=TrackStatus.PENDING,
                                     stage=None, attempts=attempts,
                                     error_code=exc.code, error_message=str(exc)[:500])
            return
        qpath = quarantine(track_id, staging,
                           {"code": exc.code, "message": str(exc),
                            "track": {k: track[k] for k in ("title", "artist", "album", "source_url")}})
        await self._update_track(track_id, status=TrackStatus.NEEDS_REVIEW, stage=None,
                                 attempts=attempts, staging_path=qpath,
                                 error_code=exc.code, error_message=str(exc)[:500],
                                 finished_at=utcnow())

    async def _handle_import_failure(self, track, exc: ImportError_) -> None:
        # staging is intact — auto-retried by maintenance when the mount is healthy
        await self._update_track(track["id"], status=TrackStatus.IMPORT_FAILED, stage=None,
                                 error_code=exc.code, error_message=str(exc)[:500])
        await bus.record("warning", exc.code, str(exc), track["job_id"], track["id"])

    async def _handle_engine_failure(self, track, staging: Path, exc: Exception) -> None:
        track_id = track["id"]
        error_class, message = classify(exc)
        governor.report_error(error_class)
        log.warning("track %s failed (%s): %s", track_id, error_class, message)
        if error_class in (ErrorClass.RATE_LIMITED, ErrorClass.BOT_FLAGGED,
                           ErrorClass.FORBIDDEN):
            # global condition, not this track's fault — back to the queue untouched
            shutil.rmtree(staging, ignore_errors=True)
            await self._update_track(track_id, status=TrackStatus.PENDING, stage=None,
                                     error_code=error_class, error_message=message[:500])
            return
        if error_class == ErrorClass.PERMANENT:
            shutil.rmtree(staging, ignore_errors=True)
            await self._update_track(track_id, status=TrackStatus.FAILED, stage=None,
                                     error_code=error_class, error_message=message[:500],
                                     finished_at=utcnow())
            return
        attempts = (track["attempts"] or 0) + 1
        shutil.rmtree(staging, ignore_errors=True)
        if attempts <= len(TRANSIENT_RETRY_DELAYS):
            delay = TRANSIENT_RETRY_DELAYS[attempts - 1]
            await self._update_track(track_id, status=TrackStatus.PENDING, stage=None,
                                     attempts=attempts,
                                     error_code=error_class, error_message=message[:500])
            await asyncio.sleep(delay)
        else:
            await self._update_track(track_id, status=TrackStatus.FAILED, stage=None,
                                     attempts=attempts,
                                     error_code=error_class, error_message=message[:500],
                                     finished_at=utcnow())

    # ------------------------------------------------------------ verify in plex
    async def _verify_in_plex(self, track_id: str, meta: dict) -> None:
        try:
            await asyncio.sleep(60)  # give the debounced scan time to run
            for _ in range(6):
                key = await plex.find_track(meta["title"], meta["album_artist"])
                if key:
                    await self._update_track(track_id, plex_verified=1, plex_rating_key=key)
                    return
                await asyncio.sleep(20)
        except Exception:
            log.debug("plex verify task failed", exc_info=True)

    # ------------------------------------------------------------ accounting
    async def _recompute_job(self, job_id: str) -> None:
        """Job status derived from track states. A skip is never a completion;
        any failed track means 'partial' at best."""
        rows = await db.fetchall(
            "SELECT status, COUNT(*) AS n FROM tracks WHERE job_id = ? GROUP BY status",
            (job_id,))
        counts = {r["status"]: r["n"] for r in rows}
        total = sum(counts.values())
        completed = counts.get(TrackStatus.COMPLETED, 0)
        skipped = counts.get(TrackStatus.SKIPPED_DUPLICATE, 0)
        failed = (counts.get(TrackStatus.FAILED, 0)
                  + counts.get(TrackStatus.NEEDS_REVIEW, 0)
                  + counts.get(TrackStatus.IMPORT_FAILED, 0))
        terminal = completed + skipped + failed + counts.get(TrackStatus.CANCELLED, 0)

        job = await db.fetchone("SELECT status FROM jobs WHERE id = ?", (job_id,))
        if job is None or job["status"] in (JobStatus.CANCELLED,):
            return
        fields: dict = {"completed_tracks": completed, "failed_tracks": failed,
                        "skipped_tracks": skipped, "total_tracks": total}
        if total and terminal == total:
            if failed == 0 and completed > 0:
                fields["status"] = JobStatus.COMPLETED
            elif failed == 0 and completed == 0 and skipped > 0:
                fields["status"] = JobStatus.COMPLETED  # everything already in library
            elif completed > 0 or skipped > 0:
                fields["status"] = JobStatus.PARTIAL
            else:
                fields["status"] = JobStatus.FAILED
            fields["finished_at"] = utcnow()
        await self._update_job(job_id, **fields)

    # ------------------------------------------------------------ db + events
    async def _update_job(self, job_id: str, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(f"UPDATE jobs SET {cols} WHERE id = ?",
                         (*fields.values(), job_id))
        await self._publish_job(job_id)

    async def _update_track(self, track_id: str, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        await db.execute(f"UPDATE tracks SET {cols} WHERE id = ?",
                         (*fields.values(), track_id))
        row = await db.fetchone("SELECT * FROM tracks WHERE id = ?", (track_id,))
        if row:
            from .models import row_to_track
            bus.publish("track.updated", row_to_track(row).model_dump())

    async def _publish_job(self, job_id: str) -> None:
        row = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row:
            from .models import row_to_job
            bus.publish("job.updated", row_to_job(row).model_dump(exclude={"tracks"}))

    # ------------------------------------------------------------ recovery
    async def _crash_recovery(self) -> None:
        in_flight = await db.fetchall(
            "SELECT * FROM tracks WHERE status IN (?,?,?,?)",
            (TrackStatus.DOWNLOADING, TrackStatus.VALIDATING,
             TrackStatus.DOWNLOADED, TrackStatus.IMPORTING))
        for track in in_flight:
            staging = settings.staging_jobs_dir / track["job_id"] / track["id"]
            shutil.rmtree(staging, ignore_errors=True)
            await db.execute(
                "UPDATE tracks SET status = ?, stage = NULL, progress_pct = NULL WHERE id = ?",
                (TrackStatus.PENDING, track["id"]))
        if in_flight:
            log.info("crash recovery: %d in-flight tracks re-queued", len(in_flight))
        stuck_jobs = await db.fetchall(
            "SELECT id FROM jobs WHERE status IN (?,?)",
            (JobStatus.RESOLVING, JobStatus.IMPORTING))
        for job in stuck_jobs:
            await db.execute("UPDATE jobs SET status = ? WHERE id = ?",
                             (JobStatus.QUEUED, job["id"]))
        for job in await db.fetchall(
                "SELECT id FROM jobs WHERE status IN (?,?)",
                (JobStatus.DOWNLOADING, JobStatus.QUEUED)):
            await self._recompute_job(job["id"])

    # ------------------------------------------------------------ maintenance
    async def _maintenance_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(300)
                ensure_sentinel()
                # retry imports parked on a broken mount
                if library_online():
                    parked = await db.fetchall(
                        "SELECT id FROM tracks WHERE status = ? LIMIT 50",
                        (TrackStatus.IMPORT_FAILED,))
                    for row in parked:
                        await db.execute(
                            "UPDATE tracks SET status = ? WHERE id = ?",
                            (TrackStatus.PENDING, row["id"]))
                    if parked:
                        log.info("re-queued %d import_failed tracks", len(parked))
                        self.wake()
                await self._sweep_staging()
                await db.execute(
                    "DELETE FROM events WHERE ts < datetime('now', '-90 days')")
                bus.publish("queue.stats", governor.snapshot())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("maintenance loop error")

    async def _sweep_staging(self) -> None:
        now = time.time()
        jobs_dir = settings.staging_jobs_dir
        if jobs_dir.exists():
            live = {r["id"] for r in await db.fetchall(
                "SELECT id FROM tracks WHERE status NOT IN (?,?,?,?)",
                (TrackStatus.COMPLETED, TrackStatus.FAILED,
                 TrackStatus.CANCELLED, TrackStatus.SKIPPED_DUPLICATE))}
            for job_dir in jobs_dir.iterdir():
                for track_dir in (d for d in job_dir.iterdir() if d.is_dir()):
                    if track_dir.name not in live and now - track_dir.stat().st_mtime > 48 * 3600:
                        shutil.rmtree(track_dir, ignore_errors=True)
                if not any(job_dir.iterdir()):
                    job_dir.rmdir()
        qdir = settings.quarantine_dir
        if qdir.exists():
            for item in qdir.iterdir():
                if now - item.stat().st_mtime > 14 * 86400:
                    shutil.rmtree(item, ignore_errors=True)

    async def _daily_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(24 * 3600)
                await db.backup()
                await self._self_update()
                if governor.snapshot()["needs_canary"]:
                    if await ytdlp_engine.canary_check():
                        governor.clear_canary()
                        self.wake()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("daily loop error")

    async def _self_update(self) -> None:
        """Upgrade yt-dlp in place; restart (via supervisor) if it changed."""
        before = ytdlp_engine.ytdlp_version()
        proc = await asyncio.create_subprocess_exec(
            "/opt/engine-venv/bin/pip", "install", "-q", "-U", "--pre",
            "yt-dlp[default]", "bgutil-ytdlp-pot-provider",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(proc.wait(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            return
        proc2 = await asyncio.create_subprocess_exec(
            "/opt/engine-venv/bin/python", "-c",
            "import yt_dlp; print(yt_dlp.version.__version__)",
            stdout=asyncio.subprocess.PIPE)
        out, _ = await proc2.communicate()
        after = out.decode().strip()
        if after and after != before:
            log.info("yt-dlp updated %s -> %s; restarting when idle", before, after)
            while self.current_track_id is not None:
                await asyncio.sleep(30)
            os._exit(0)  # docker restart policy brings us back on the new version


worker = Worker()
