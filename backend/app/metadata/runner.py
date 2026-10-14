"""The metadata manager's background worker.

One task, one thing at a time, so nothing it does races anything else it
does: a cycle (library scan -> Plex sync -> duplicates -> check due albums ->
check their artists), and between albums, any fixes the user asked for.

  * Full run: every album, now. The first one is report-only by design.
  * Periodic: every METADATA_INTERVAL_MIN, only what is due — new albums,
    albums whose files or Plex entry changed, and old checks gone stale.
  * After a download: the new album is checked minutes after it lands.

Auto-apply is a switch the user turns on after reviewing the first report;
until then only explicit Apply actions change anything.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid

from .. import library
from ..config import settings
from ..db import db, utcnow
from ..events import bus
from . import apply, desired as desired_mod, dupes, issues, musicbrainz as mb, plexsync, units
from .art import youtube_thumbnail
from .resolver import Resolution, resolve

log = logging.getLogger("mdl.metadata")

UNRESOLVED_RETRY = 30 * 86400
PENDING_RETRY = 86400
IMPORT_SETTLE = 150      # seconds after the last import before checking
CHECK_CONCURRENCY = 3
# Albums whose file changes share one Plex rescan: a scan costs most of a
# minute however little it looks at, the file changes a second or two.
APPLY_CHUNK = 100
MOVE_KINDS = frozenset({"move"})
# Bumped when what an album *should* look like changes (naming, release
# choice, what is flagged), so the next pass re-plans everything.
RULES_VERSION = 4


class Cancelled(Exception):
    pass


class Runner:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self.paused = False
        self._cancel = False
        self.phase = "idle"
        self.current: str | None = None
        self.run: dict | None = None
        self._requested: dict | None = None       # {"kind", "report_only"}
        self._apply_queue: list[list[int]] = []
        self._imports: dict[str, float] = {}
        self._last_cycle = 0.0
        self._last_publish = 0.0
        self._t0 = 0.0
        self.next_run_at: float | None = None
        # Fixes run in their own task, so applying (which waits on Plex to
        # rescan) never holds up the checking. A check and an apply of the
        # same album must not interleave: one lock per album (or artist).
        self._apply_task: asyncio.Task | None = None
        self._apply_wake = asyncio.Event()
        self.applying: str | None = None
        self._key_locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if settings.metadata_enabled and self.task is None:
            self.task = asyncio.create_task(self._loop(), name="metadata-runner")
            self._apply_task = asyncio.create_task(self._apply_loop(), name="metadata-apply")

    async def stop(self) -> None:
        for task in (self.task, self._apply_task):
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.task = self._apply_task = None
        await mb.client.close()
        from . import lastfm
        await lastfm.client.close()

    def wake(self) -> None:
        self._wake.set()

    # ------------------------------------------------------------ controls
    async def request_run(self, full: bool, report_only: bool) -> None:
        run = self.run
        if run is not None and self.phase in ("scanning", "plex_sync", "duplicates") \
                and (run["kind"] == "incremental" or not full):
            # A pass is under way and hasn't picked its albums yet: make it the
            # run that was asked for rather than queueing a second one.
            if full:
                run["kind"] = "full"
            run["report_only"] = run["report_only"] or report_only
            self.paused = False
            self.wake()
            await self.publish(force=True)
            return
        self._requested = {"kind": "full" if full else "incremental",
                           "report_only": report_only}
        self.paused = False
        self.wake()

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False
        self.wake()

    def cancel(self) -> None:
        self._cancel = True
        self.paused = False
        self.wake()

    async def auto_apply(self) -> bool:
        row = await db.fetchone("SELECT value FROM md_state WHERE key = 'auto_apply'")
        return bool(row and row["value"] == "1")

    async def set_auto_apply(self, enabled: bool) -> None:
        await db.execute(
            "INSERT INTO md_state (key, value) VALUES ('auto_apply', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("1" if enabled else "0",))
        self.wake()

    async def request_apply(self, ids: list[int]) -> int:
        if not ids:
            return 0
        applyable = [k for k, v in issues.KINDS.items() if v["applyable"]]
        rows = await db.fetchall(
            f"SELECT id FROM md_issues WHERE id IN ({','.join('?' * len(ids))}) "
            f"AND state IN ('open', 'failed') AND kind IN ({','.join('?' * len(applyable))})",
            (*ids, *applyable))
        chosen = [r["id"] for r in rows]
        for i in range(0, len(chosen), 400):
            chunk = chosen[i:i + 400]
            await db.execute(
                f"UPDATE md_issues SET state = 'queued', updated_at = ? "
                f"WHERE id IN ({','.join('?' * len(chunk))})", (utcnow(), *chunk))
        if chosen:
            self._apply_queue.append(chosen)
            self._apply_wake.set()
            await self.publish(force=True)
        return len(chosen)

    async def note_import(self, library_path: str) -> None:
        folder = units.unit_dir(library_path.rsplit("/", 1)[0])
        self._imports[folder] = time.time()
        await db.execute("UPDATE md_albums SET next_check = 0, priority = 10 WHERE dirpath = ?",
                         (folder,))

    # ------------------------------------------------------------ status
    async def status(self) -> dict:
        last = await db.fetchone("SELECT * FROM md_runs WHERE status != 'running' "
                                 "ORDER BY started_at DESC LIMIT 1")
        queued = await db.fetchone("SELECT COUNT(*) AS n FROM md_issues WHERE state = 'queued'")
        due = await db.fetchone(
            "SELECT COUNT(*) AS n FROM md_albums WHERE ignored = 0 AND next_check <= ?",
            (time.time(),))
        from . import lastfm
        from ..plex import plex
        messages = []
        if not mb.enabled():
            messages.append("MUSICBRAINZ_CONTACT is not set in .env, so MusicBrainz lookups "
                            "are off and albums can't be identified.")
        if not lastfm.enabled():
            messages.append("LASTFM_API_KEY is not set: genres and moods fall back to "
                            "MusicBrainz and Plex only.")
        if not plex.configured:
            messages.append("Plex is not configured (PLEX_TOKEN).")
        run = dict(self.run) if self.run else None
        if run:
            elapsed = time.monotonic() - self._t0 if self._t0 else 0
            rate = run["done"] / elapsed if elapsed > 10 and run["done"] else None
            run["eta_sec"] = int((run["total"] - run["done"]) / rate) if rate else None
        phase = self.phase
        if phase == "idle" and self.applying:
            phase = "applying"
        return {
            "enabled": settings.metadata_enabled,
            "running": self.run is not None or phase not in ("idle", "waiting"),
            "paused": self.paused, "phase": phase, "current": self.current,
            "applying": self.applying,
            "run": run, "last_run": _run_out(last) if last else None,
            "auto_apply": await self.auto_apply(),
            "interval_min": settings.metadata_interval_min,
            "next_run_at": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.next_run_at))
                            if self.next_run_at else None),
            "apply_queue": queued["n"], "due": due["n"],
            "sources": {"musicbrainz": mb.enabled(), "lastfm": lastfm.enabled(),
                        "plex": plex.configured},
            "messages": messages,
        }

    async def publish(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_publish < 1.0:
            return
        self._last_publish = now
        try:
            bus.publish("metadata.status", await self.status())
        except Exception:
            log.debug("status publish failed", exc_info=True)

    async def publish_issues(self) -> None:
        rows = await db.fetchall("SELECT kind, COUNT(*) AS n FROM md_issues "
                                 "WHERE state IN ('open', 'failed', 'queued') GROUP BY kind")
        by_kind = {r["kind"]: r["n"] for r in rows}
        bus.publish("library.issues", {"open": sum(by_kind.values()), "by_kind": by_kind})

    # ------------------------------------------------------------ loop
    async def _loop(self) -> None:
        await asyncio.sleep(60)      # let startup, crash recovery and the worker go first
        await db.execute("UPDATE md_runs SET status = 'interrupted' WHERE status = 'running'")
        # the apply queue lives in memory: what a restart cut off is open again
        await db.execute("UPDATE md_issues SET state = 'open' WHERE state = 'queued'")
        await _rules_changed()
        interval = max(5, settings.metadata_interval_min) * 60
        self._last_cycle = time.time() - interval + 120
        while True:
            try:
                if not self.paused:
                    if self._requested:
                        req, self._requested = self._requested, None
                        await self.cycle(req["kind"], req["report_only"])
                    elif time.time() - self._last_cycle >= interval:
                        await self.cycle("incremental", False)
                    elif self._imports and time.time() - max(self._imports.values()) > IMPORT_SETTLE:
                        folders, self._imports = list(self._imports), {}
                        await self.cycle("single", False, folders=folders)
            except asyncio.CancelledError:
                raise
            except Cancelled:
                log.info("metadata run cancelled")
            except Exception:
                log.exception("metadata cycle failed")
                self.phase = "idle"
            self.next_run_at = self._last_cycle + interval
            self._wake.clear()
            if self.phase != "idle":
                self.phase = "idle"
                await self.publish(force=True)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    async def _checkpoint(self) -> None:
        """Between albums: honour pause and cancel."""
        if self._cancel:
            self._cancel = False
            raise Cancelled()
        while self.paused:
            await self.publish(force=True)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            if self._cancel:
                self._cancel = False
                raise Cancelled()

    # ------------------------------------------------------------ cycle
    async def cycle(self, kind: str, report_only: bool, folders: list[str] | None = None) -> None:
        run_id = uuid.uuid4().hex[:10]
        self._t0 = time.monotonic()
        self.run = {"id": run_id, "kind": kind, "report_only": report_only, "total": 0,
                    "done": 0, "started_at": utcnow(), "finished_at": None,
                    "counts": {"resolved": 0, "review": 0, "unresolved": 0, "pending": 0,
                               "applied": 0, "failed": 0}}
        await db.execute(
            "INSERT INTO md_runs (id, kind, report_only, status, started_at) VALUES (?,?,?,?,?)",
            (run_id, kind, int(report_only), "running", self.run["started_at"]))
        status, error = "finished", None
        try:
            await self._cycle(kind, report_only, folders)
        except Cancelled:
            status = "cancelled"
            raise
        except asyncio.CancelledError:
            status = "interrupted"      # shutting down
            raise
        except Exception as exc:
            status, error = "failed", str(exc)[:300]
            raise
        finally:
            self._last_cycle = time.time()
            self.run["finished_at"] = utcnow()
            await db.execute(
                "UPDATE md_runs SET kind = ?, report_only = ?, status = ?, total = ?, done = ?, "
                "counts = ?, finished_at = ?, error = ? WHERE id = ?",
                (self.run["kind"], int(self.run["report_only"]), status, self.run["total"],
                 self.run["done"], json.dumps(self.run["counts"]), self.run["finished_at"],
                 error, run_id))
            self.run = None
            self.phase, self.current = "idle", None
            await self.publish(force=True)
            await self.publish_issues()

    async def _save_progress(self) -> None:
        """So a pass cut short by a restart still shows how far it got."""
        run = self.run
        if run:
            await db.execute("UPDATE md_runs SET total = ?, done = ?, counts = ? WHERE id = ?",
                             (run["total"], run["done"], json.dumps(run["counts"]), run["id"]))

    async def _cycle(self, kind: str, report_only: bool, folders: list[str] | None) -> None:
        from ..plex import plex

        # 1. the library index, so the plan is made from what is on disk
        self.phase = "scanning"
        await self.publish(force=True)
        await _scan_library()

        # 2. Plex
        if plex.configured:
            self.phase = "plex_sync"
            self.current = None
            await self.publish(force=True)
            previous = await plexsync.last_sync()
            try:
                await plexsync.sync(full=(kind == "full"),
                                    progress=lambda what, n: self._progress_note(f"{what}: {n}"))
                await _due_after_plex_changes(previous)
            except Exception as exc:
                log.warning("plex sync failed: %s", exc)

        # 3. duplicates and Plex ghosts (library-wide)
        if kind != "single":
            self.phase = "duplicates"
            await self.publish(force=True)
            try:
                await dupes.sync()
                await issues.save(await issues.plan_ghosts(), scope_kinds=("plex_ghost",),
                                  all_scope=True)
            except Exception:
                log.exception("duplicate sync failed")
            await self.publish_issues()

        # 4. albums. The run may have been upgraded while scanning (a Full run
        # clicked during an automatic pass), so read what it is now.
        kind, report_only = self.run["kind"], self.run["report_only"]
        auto = (await self.auto_apply()) and not report_only
        await units.sync_registry()
        if mb.enabled():
            await db.execute("UPDATE md_albums SET next_check = 0 WHERE status = 'pending'")
        now = time.time()
        if kind == "full":
            rows = await db.fetchall(
                "SELECT m.key, m.artist_dir FROM md_albums m WHERE m.ignored = 0 "
                "ORDER BY m.priority DESC, (SELECT COUNT(*) FROM library_index l "
                "WHERE l.artist_dir = m.artist_dir) DESC, m.key")
        elif kind == "single":
            marks = ",".join("?" * len(folders or [""]))
            rows = await db.fetchall(
                f"SELECT key, artist_dir FROM md_albums WHERE ignored = 0 AND dirpath IN ({marks})",
                tuple(folders or [""]))
        else:
            rows = await db.fetchall(
                "SELECT key, artist_dir FROM md_albums WHERE ignored = 0 AND next_check <= ? "
                "ORDER BY priority DESC, next_check, key", (now,))
        keys = [(r["key"], r["artist_dir"]) for r in rows]
        self.run["total"] = len(keys)
        await self._save_progress()
        self.phase = "checking"
        self._t0 = time.monotonic()     # the ETA is about checking, not the scan before it
        artists: dict[str, None] = {}
        pending = list(keys)

        async def check_next() -> None:
            # A few albums in flight at once: MusicBrainz requests stay one
            # a second (the client paces them), but the Plex, Last.fm,
            # database and file work of one album overlaps another's wait.
            while pending:
                await self._checkpoint()
                key, artist_dir = pending.pop(0)
                self.current = key
                try:
                    result = await self.check_unit(key)
                except Exception as exc:
                    log.exception("check failed for %s", key)
                    await db.execute(
                        "UPDATE md_albums SET error = ?, next_check = ? WHERE key = ?",
                        (str(exc)[:300], time.time() + PENDING_RETRY, key))
                    result = {"status": "failed"}
                counts = self.run["counts"]
                status = result.get("status", "failed")
                counts[status] = counts.get(status, 0) + 1
                if artist_dir:
                    artists[artist_dir] = None
                if auto and result.get("auto_ids"):
                    await self.request_apply(result["auto_ids"])
                self.run["done"] += 1
                if self.run["done"] % 50 == 0:
                    await self._save_progress()
                await self.publish()

        tasks = [asyncio.create_task(check_next()) for _ in range(CHECK_CONCURRENCY)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.exception() is not None:
                    raise task.exception()     # Cancelled, or a real failure
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        # 5. artists
        if kind == "full":
            artists = {r["artist_dir"]: None for r in await db.fetchall(
                "SELECT DISTINCT artist_dir FROM md_albums WHERE artist_dir IS NOT NULL")}
        else:
            for r in await db.fetchall("SELECT artist_dir FROM md_artists WHERE next_check <= ? "
                                       "AND checked_at IS NOT NULL", (now,)):
                artists[r["artist_dir"]] = None
        self.phase = "checking"
        for artist_dir in artists:
            await self._checkpoint()
            self.current = artist_dir
            try:
                auto_ids = await self.check_artist(artist_dir)
            except Exception:
                log.exception("artist check failed for %s", artist_dir)
                continue
            if auto and auto_ids:
                await self.request_apply(auto_ids)
            await self.publish()
        if auto:
            ghosts = await db.fetchall("SELECT id FROM md_issues WHERE kind = 'plex_ghost' "
                                       "AND state = 'open' AND auto = 1")
            if ghosts:
                await self.request_apply([r["id"] for r in ghosts])

    def _progress_note(self, text: str) -> None:
        self.current = text
        asyncio.get_running_loop().create_task(self.publish())

    # ------------------------------------------------------------ checks
    def _lock(self, key: str) -> asyncio.Lock:
        return self._key_locks.setdefault(key, asyncio.Lock())

    async def check_unit(self, key: str, *, manual_release: str | None = None) -> dict:
        async with self._lock(key):
            return await self._check_unit(key, manual_release=manual_release)

    async def check_artist(self, artist_dir: str) -> list[int]:
        async with self._lock(f"artist:{artist_dir}"):
            return await self._check_artist(artist_dir)

    async def _check_unit(self, key: str, *, manual_release: str | None = None) -> dict:
        rows = await units.load_unit(key)
        if not rows:
            await db.execute("DELETE FROM md_albums WHERE key = ?", (key,))
            await issues.save([], scope_kinds=issues.UNIT_KINDS, album_key=key)
            return {"status": "gone"}
        artist_dir = rows[0].get("artist_dir")
        record = await db.fetchone("SELECT * FROM md_albums WHERE key = ?", (key,))
        if record is None:
            await db.execute(
                "INSERT INTO md_albums (key, dirpath, artist_dir, next_check) VALUES (?,?,?,0)",
                (key, units.unit_folder(key), artist_dir))
            record = await db.fetchone("SELECT * FROM md_albums WHERE key = ?", (key,))
        if record["ignored"]:
            await issues.save([], scope_kinds=issues.UNIT_KINDS, album_key=key)
            return {"status": "ignored"}
        plex_info = await plexsync.album_for_paths([r["path"] for r in rows])
        plex_album = (plex_info or {}).get("album")
        plex_artist = (plex_info or {}).get("artist")
        manual = manual_release or (record["release_mbid"] if record["manual"] else None)
        try:
            res = await resolve(rows, plex_mbid=(plex_album or {}).get("mbid"), manual=manual)
        except mb.MusicBrainzError as exc:
            res = Resolution(status="pending", error=str(exc))
        want = None
        if res.release is not None and res.status in ("resolved", "review"):
            overrides = await desired_mod.overrides_for("album", key)
            want = await desired_mod.build_album(rows, res, plex_album=plex_album,
                                                 plex_artist=plex_artist, overrides=overrides)
        plan = await issues.plan_unit(
            key, rows, want if res.status == "resolved" else None, res, plex_info,
            plex_synced=bool(await plexsync.last_sync()), ours_locked=apply.ours_locked)
        await issues.save(plan, scope_kinds=issues.UNIT_KINDS, album_key=key)
        now = time.time()
        if res.status == "resolved":
            next_check = now + settings.metadata_recheck_days * 86400
        elif res.status == "pending":
            next_check = now + PENDING_RETRY
        else:
            next_check = now + UNRESOLVED_RETRY
        await db.execute(
            "UPDATE md_albums SET release_mbid = ?, releasegroup_mbid = ?, artist_mbid = ?, "
            "confidence = ?, source = ?, status = ?, manual = ?, desired = ?, candidates = ?, "
            "plex_key = ?, file_sig = ?, plex_sig = ?, checked_at = ?, next_check = ?, "
            "priority = 0, error = ? WHERE key = ?",
            ((res.release or {}).get("id"),
             (want or {}).get("releasegroup_mbid"), (want or {}).get("albumartist_mbid"),
             res.score, res.source, res.status, int(bool(manual)),
             json.dumps(want) if want else None, json.dumps(res.candidates),
             (plex_album or {}).get("rating_key"), units.signature(rows),
             str((plex_album or {}).get("updated_at") or ""), now, next_check, res.error, key))
        # the release's missing tracks, across all of its folders (and the
        # release this album was identified as before, if that changed)
        from . import missing
        new_release = (res.release or {}).get("id") if res.status == "resolved" else None
        for release in {record["release_mbid"], new_release} - {None}:
            try:
                await missing.refresh(release)
            except Exception:
                log.debug("missing-tracks refresh failed for %s", release, exc_info=True)
        auto_ids = []
        if res.status == "resolved" or any(p["kind"] == "tags" for p in plan):
            rows_ = await db.fetchall(
                "SELECT id FROM md_issues WHERE album_key = ? AND state = 'open' AND auto = 1",
                (key,))
            auto_ids = [r["id"] for r in rows_]
        return {"status": res.status, "auto_ids": auto_ids, "issues": len(plan)}

    async def _check_artist(self, artist_dir: str) -> list[int]:
        albums = [json.loads(r["desired"]) for r in await db.fetchall(
            "SELECT desired FROM md_albums WHERE artist_dir = ? AND status = 'resolved' "
            "AND desired IS NOT NULL", (artist_dir,))]
        record = await db.fetchone("SELECT * FROM md_artists WHERE artist_dir = ?", (artist_dir,))
        plex_artist = None
        if record and record["plex_key"]:
            row = await db.fetchone("SELECT * FROM plex_items WHERE rating_key = ?",
                                    (record["plex_key"],))
            plex_artist = dict(row) if row else None
        overrides = await desired_mod.overrides_for("artist", artist_dir)
        want = await desired_mod.build_artist(artist_dir, albums, plex_artist, overrides)
        thumb = None
        if plex_artist and not plex_artist.get("thumb"):
            thumb = await youtube_thumbnail(artist_dir)
        plan = await issues.plan_artist(artist_dir, want, plex_artist, thumb,
                                        ours_locked=apply.ours_locked)
        await issues.save(plan, scope_kinds=issues.ARTIST_KINDS, artist_dir=artist_dir)
        now = time.time()
        await db.execute(
            "INSERT INTO md_artists (artist_dir, name, artist_mbid, status, desired, checked_at, "
            "next_check) VALUES (?,?,?,?,?,?,?) ON CONFLICT(artist_dir) DO UPDATE SET "
            "name = excluded.name, artist_mbid = excluded.artist_mbid, status = excluded.status, "
            "desired = excluded.desired, checked_at = excluded.checked_at, "
            "next_check = excluded.next_check",
            (artist_dir, want["name"], want.get("mbid"), want["status"], json.dumps(want), now,
             now + settings.metadata_recheck_days * 86400))
        rows = await db.fetchall(
            "SELECT id FROM md_issues WHERE artist_dir = ? AND entity = 'artist' "
            "AND state = 'open' AND auto = 1 AND kind IN (%s)"
            % ",".join("?" * len(issues.ARTIST_KINDS)), (artist_dir, *issues.ARTIST_KINDS))
        return [r["id"] for r in rows]

    # ------------------------------------------------------------ applying
    async def _apply_loop(self) -> None:
        """Run queued fixes as they come. Everything queued by the time a
        batch starts is taken together, so fixes clicked one at a time for the
        same album (tags, then move, then genres) run as one apply, in order,
        with one Plex rescan."""
        while True:
            try:
                if self._apply_queue:
                    ids = sorted({i for batch in self._apply_queue for i in batch})
                    self._apply_queue.clear()
                    outcome = await self.apply_ids(ids)
                    if self.run is not None:
                        self.run["counts"]["applied"] += outcome["applied"]
                        self.run["counts"]["failed"] += outcome["failed"]
                    continue
                self._apply_wake.clear()
                await self._apply_wake.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("apply batch failed")
            finally:
                if not self._apply_queue and self.applying:
                    self.applying = None
                    await self.publish(force=True)

    async def apply_ids(self, ids: list[int]) -> dict:
        """Apply issues, grouped by album (or artist), in dependency order."""
        if not ids:
            return {"applied": 0, "failed": 0}
        rows = [issues.issue_out(r) for r in await db.fetchall(
            f"SELECT * FROM md_issues WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))]
        groups: dict[tuple, list[dict]] = {}
        for issue in rows:
            if issue["kind"] == "plex_ghost":
                groups.setdefault(("ghost", issue["ref"]), []).append(issue)
            elif issue["entity"] == "artist":
                groups.setdefault(("artist", issue["artist_dir"]), []).append(issue)
            else:
                groups.setdefault(("album", issue["album_key"]), []).append(issue)
        applied = failed = 0
        albums = [(ref, members) for (scope, ref), members in groups.items() if scope == "album"]
        for i in range(0, len(albums), APPLY_CHUNK):
            a, f = await self._apply_albums(albums[i:i + APPLY_CHUNK])
            applied += a
            failed += f
        for (scope, ref), members in groups.items():
            if scope == "album":
                continue
            self.applying = ref
            await self.publish(force=True)
            if scope == "artist":
                a, f = await self._apply_artist(ref, members)
            else:
                a, f = await self._apply_simple(members, apply.Ctx(ref, [], None, None))
                await issues.save(await issues.plan_ghosts(), scope_kinds=("plex_ghost",),
                                  all_scope=True)
            applied += a
            failed += f
        await self.publish_issues()
        return {"applied": applied, "failed": failed}

    async def _mark(self, issue: dict, state: str, error: str | None = None) -> None:
        await db.execute("UPDATE md_issues SET state = ?, error = ?, updated_at = ? WHERE id = ?",
                         (state, error, utcnow(), issue["id"]))

    async def _apply_simple(self, members: list[dict], ctx: apply.Ctx,
                            executors: dict | None = None) -> tuple[int, int]:
        executors = executors or {**apply.PLEX_STAGE, **apply.FILE_STAGE, **apply.QUEUE_STAGE}
        applied = failed = 0
        for issue in sorted(members, key=lambda i: issues.APPLY_ORDER.index(i["kind"])
                            if i["kind"] in issues.APPLY_ORDER else 99):
            executor = executors.get(issue["kind"])
            if executor is None:
                await self._mark(issue, "open", "this kind can't be applied automatically")
                continue
            try:
                await executor(issue, ctx)
                await self._mark(issue, "applied")
                applied += 1
            except apply.KeepOpen as note:
                await db.execute("UPDATE md_issues SET state = 'open', updated_at = ? "
                                 "WHERE id = ? AND state = 'queued'", (utcnow(), issue["id"]))
                log.info("%s %s: %s", issue["kind"], issue["ref"], note)
                applied += 1
            except Exception as exc:
                log.info("apply %s %s failed: %s", issue["kind"], issue["ref"], exc)
                await self._mark(issue, "failed", str(exc)[:300])
                failed += 1
        return applied, failed

    async def _apply_albums(self, batch: list[tuple[str, list[dict]]]) -> tuple[int, int]:
        """Apply a batch of albums in rounds, one Plex rescan after each:
        every album's moves, then every album's tag writes, then every
        album's Plex changes. Plex follows a moved file to its new place,
        play history and all, only while the file is unchanged, so a file is
        never moved and re-tagged before Plex has seen the move."""
        applied = failed = 0
        async with contextlib.AsyncExitStack() as stack:
            for key in sorted({key for key, _ in batch}):
                await stack.enter_async_context(self._lock(key))
            started = []
            for key, members in batch:
                self.applying = key
                await self.publish(force=True)
                a, f, state = await self._start_album(key, members)
                applied += a
                failed += f
                if state:
                    started.append(state)
            for kinds in (MOVE_KINDS, None):
                for state in started:
                    todo = [i for i in state["files"]
                            if (i["kind"] in MOVE_KINDS) == (kinds is not None)]
                    if todo:
                        self.applying = state["ctx"].key
                        await self.publish(force=True)
                        a, f = await self._apply_simple(todo, state["ctx"], apply.FILE_STAGE)
                        applied += a
                        failed += f
                dirty = [state["ctx"] for state in started if state["ctx"].plex_dirty]
                if dirty:
                    try:
                        await apply.plex_catch_up(*dirty)
                    except Exception as exc:
                        log.info("plex catch-up after %d albums failed: %s", len(dirty), exc)
            for state in started:
                self.applying = state["ctx"].key
                await self.publish(force=True)
                a, f = await self._apply_plex(state)
                applied += a
                failed += f
        return applied, failed

    async def _start_album(self, key: str, members: list[dict]) -> tuple[int, int, dict | None]:
        """Load an album for applying, and queue its downloads."""
        record = await db.fetchone("SELECT * FROM md_albums WHERE key = ?", (key,))
        rows = await units.load_unit(key)
        if not rows or record is None:
            for issue in members:
                await self._mark(issue, "failed", "the album is no longer there")
            return 0, len(members), None
        want = (json.loads(record["desired"])
                if record["desired"] and record["status"] == "resolved" else None)
        ctx = apply.Ctx(key, rows, want, rows[0].get("artist_dir"))
        state = {"key": key, "ctx": ctx, "old_artist": ctx.artist_dir,
                 "files": [i for i in members if i["kind"] in apply.FILE_STAGE],
                 "plex_stage": [i for i in members if i["kind"] in apply.PLEX_STAGE]}
        queue_stage = [i for i in members if i["kind"] in apply.QUEUE_STAGE]
        applied, failed = await self._apply_simple(queue_stage, ctx, apply.QUEUE_STAGE)
        return applied, failed, state

    async def _apply_plex(self, state: dict) -> tuple[int, int]:
        key, ctx, plex_stage = state["key"], state["ctx"], state["plex_stage"]
        applied = failed = 0
        if plex_stage:
            # The plan may predate the file changes (a move gives Plex new
            # items): re-check, then apply the same kinds from the fresh plan.
            if ctx.key != key or ctx.batches:
                await self._check_unit(ctx.key)
            kinds = {i["kind"] for i in plex_stage}
            fresh = [issues.issue_out(r) for r in await db.fetchall(
                f"SELECT * FROM md_issues WHERE album_key = ? AND state IN ('open', 'queued', 'failed') "
                f"AND kind IN ({','.join('?' * len(kinds))})", (ctx.key, *kinds))]
            fresh_kinds = {i["kind"] for i in fresh}
            for issue in plex_stage:
                if issue["kind"] not in fresh_kinds and issue["album_key"] != ctx.key:
                    await self._mark(issue, "applied")
            applied, failed = await self._apply_simple(fresh, ctx, apply.PLEX_STAGE)
            for issue in plex_stage:
                if issue["album_key"] != ctx.key:
                    await self._mark(issue, "applied")    # planned again, and applied, under the new key
        await self._check_unit(ctx.key)
        bus.publish("library.changed", {
            "album_keys": sorted({key, ctx.key}),
            "artist_dirs": sorted({d for d in (state["old_artist"], ctx.artist_dir) if d}),
            # a move gives the album a new key; a page showing it follows
            "renamed": {key: {"key": ctx.key, "artist_dir": ctx.artist_dir}}
            if ctx.key != key else {}})
        return applied, failed

    async def _apply_artist(self, artist_dir: str, members: list[dict]) -> tuple[int, int]:
        async with self._lock(f"artist:{artist_dir}"):
            ctx = apply.Ctx(None, [], None, artist_dir)
            applied, failed = await self._apply_simple(members, ctx)
            await self._check_artist(artist_dir)
        bus.publish("library.changed", {"album_keys": [], "artist_dirs": [artist_dir]})
        return applied, failed


async def _due_after_plex_changes(since: float) -> None:
    """Albums whose Plex side changed since the last sync are checked again —
    an album rescanned after a move, a match fixed by hand in Plex — and so
    is any album still waiting for Plex to pick its files up."""
    if since:
        rows = await db.fetchall(
            "SELECT DISTINCT folder FROM plex_items WHERE type = 'track' AND folder IS NOT NULL "
            "AND (added_at >= ? OR updated_at >= ?)", (int(since) - 120, int(since) - 120))
        folders = sorted({units.unit_dir(r["folder"]) for r in rows})
        for i in range(0, len(folders), 400):
            chunk = folders[i:i + 400]
            await db.execute(
                f"UPDATE md_albums SET next_check = 0 WHERE dirpath IN ({','.join('?' * len(chunk))})",
                tuple(chunk))
    await db.execute(
        "UPDATE md_albums SET next_check = 0 WHERE key IN (SELECT album_key FROM md_issues "
        "WHERE kind = 'not_in_plex' AND state IN ('open', 'failed'))")


def _run_out(row) -> dict:
    d = dict(row)
    d["counts"] = json.loads(d["counts"]) if d.get("counts") else {}
    d["report_only"] = bool(d.get("report_only"))
    return d


async def _rules_changed() -> None:
    """When the house rules change, every album is planned again by the new
    ones on the next pass (MusicBrainz answers come from the cache)."""
    row = await db.fetchone("SELECT value FROM md_state WHERE key = 'rules_version'")
    if row and row["value"] == str(RULES_VERSION):
        return
    log.info("house rules changed: every album and artist is due a recheck")
    await db.execute("UPDATE md_albums SET next_check = 0")
    await db.execute("UPDATE md_artists SET next_check = 0")
    await db.execute("INSERT INTO md_state (key, value) VALUES ('rules_version', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (str(RULES_VERSION),))


async def _scan_library() -> None:
    """An incremental library scan (a stat per file, a read per changed one)."""
    while library.scan_state.running:
        await asyncio.sleep(5)
    last = 0.0

    def progress(done, total):
        nonlocal last
        now = time.monotonic()
        if now - last > 2:
            last = now
            bus.publish("library.scan", library.scan_state.snapshot())

    try:
        await library.scan(full=False, progress_cb=progress)
    except RuntimeError:
        while library.scan_state.running:
            await asyncio.sleep(5)
    bus.publish("library.scan", library.scan_state.snapshot())


runner = Runner()


async def note_import(library_path: str) -> None:
    await runner.note_import(library_path)
