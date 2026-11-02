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

from .. import library, locks
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
SETTLING_RECHECK = 3600  # an album a download is going into is checked again within this
# the fixes that move an album's files to another folder
MOVE_KINDS = ("move", "move_conflict", "album_copies")
CHECK_CONCURRENCY = 3
# Bumped when what an album *should* look like changes (naming, release
# choice, what is flagged), so the next pass re-plans everything.
# 9: damaged files are their own issue, a box set's albums stay in the set,
# another album of the same name gets its own folder, matches are triaged
# 10: a single beside its album takes "[Single]" whichever came first, and
# one album under two release groups is a copy, not "[12 Tracks]"
RULES_VERSION = 10


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
        # the user's calls on albums ("accept the match", "not on
        # MusicBrainz"), made between fixes: [(issue ids, decision)]
        self._decisions: list[tuple[list[int], str]] = []
        # follow-up fixes queued straight after an apply, by issue: two fixes
        # that undo each other would otherwise take turns forever
        self._followed: dict[int, int] = {}
        # Download missing fixes held back once for a move of their album
        self._held_back: set[int] = set()
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
        self.apply_note: str | None = None
        # albums whose files are done and whose Plex side waits for the rescan
        self._plex_due: dict[str, dict] = {}
        self._plex_later: list[tuple[tuple, list[dict]]] = []     # artists, ghosts
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._started_at = utcnow()

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._started_at = utcnow()
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

    DECISIONS = {"accept_match": ("review_match",),
                 "no_release": ("review_match", "unresolved")}

    async def request_decide(self, ids: list[int], decision: str) -> int:
        """Queue a call the user made on these issues' albums: accept the
        best match, or "MusicBrainz has no release for this". Each album is
        then checked again, and its fixes follow like any other check's."""
        kinds = self.DECISIONS[decision]
        if not ids:
            return 0
        chosen = []
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            chosen += [r["id"] for r in await db.fetchall(
                f"SELECT id FROM md_issues WHERE id IN ({','.join('?' * len(chunk))}) "
                f"AND state IN ('open', 'failed') AND kind IN ({','.join('?' * len(kinds))})",
                (*chunk, *kinds))]
        for i in range(0, len(chosen), 400):
            chunk = chosen[i:i + 400]
            await db.execute(
                f"UPDATE md_issues SET state = 'queued', updated_at = ? "
                f"WHERE id IN ({','.join('?' * len(chunk))})", (utcnow(), *chunk))
        if chosen:
            self._decisions.append((chosen, decision))
            self._apply_wake.set()
            await self.publish(force=True)
        return len(chosen)

    async def note_import(self, library_path: str) -> None:
        folder = units.unit_dir(library_path.rsplit("/", 1)[0])
        self._imports[folder] = time.time()
        await db.execute("UPDATE md_albums SET next_check = 0, priority = 10 WHERE dirpath = ?",
                         (folder,))

    async def job_ended(self, job_id: str) -> None:
        """A download ended. The albums it went into were settling (nothing
        moved, merged or re-tagged them meanwhile): each is checked again
        first, once its files have settled in Plex, as after an import."""
        folders = sorted(await locks.settling(job_id))
        now = time.time()
        for i in range(0, len(folders), 400):
            chunk = tuple(folders[i:i + 400])
            marks = ",".join("?" * len(chunk))
            for r in await db.fetchall(f"SELECT DISTINCT dirpath FROM md_albums "
                                       f"WHERE path_key(dirpath) IN ({marks})", chunk):
                self._imports[r["dirpath"]] = now
            await db.execute(f"UPDATE md_albums SET next_check = 0, priority = 10 "
                             f"WHERE path_key(dirpath) IN ({marks})", chunk)
        if folders:
            self.wake()

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
        if phase == "idle" and (self.applying or self.apply_note):
            phase = "applying"
        return {
            "enabled": settings.metadata_enabled,
            "running": self.run is not None or phase not in ("idle", "waiting"),
            "paused": self.paused, "phase": phase, "current": self.current,
            "applying": self.applying, "apply_note": self.apply_note,
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
    async def _resume_queue(self) -> None:
        """The apply queue lives in memory: after a restart, carry on with the
        fixes it held, or, with auto-apply now off, leave them open. Only
        what was queued before this start: anything asked for since is in
        the queue already."""
        before = self._started_at
        # a decision queued before the restart is asked for again
        await db.execute("UPDATE md_issues SET state = 'open' WHERE state = 'queued' "
                         "AND updated_at < ? AND kind IN ('review_match', 'unresolved')", (before,))
        left = [r["id"] for r in await db.fetchall(
            "SELECT id FROM md_issues WHERE state = 'queued' AND updated_at < ?", (before,))]
        if left and await self.auto_apply():
            log.info("carrying on with %d fixes queued before the restart", len(left))
            self._apply_queue.append(left)
            self._apply_wake.set()
        elif left:
            await db.execute("UPDATE md_issues SET state = 'open' WHERE state = 'queued' "
                             "AND updated_at < ?", (before,))

    async def _loop(self) -> None:
        # what a restart cut off, first: before anything new is queued
        try:
            back = await apply.restore_hidden()
            if back:
                log.info("put back %d files a Plex re-add had hidden before the restart", back)
        except Exception:
            log.exception("putting back hidden files failed")
        await self._resume_queue()
        await asyncio.sleep(60)      # let startup, crash recovery and the worker go first
        await db.execute("UPDATE md_runs SET status = 'interrupted' WHERE status = 'running'")
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
        # 6. albums held in more than one folder (library-wide, from the plans),
        # and missing tracks asked for while YouTube Music couldn't be asked
        if kind != "single":
            from . import missing
            try:
                wanted = await missing.wanted_downloads()
                if wanted:
                    log.info("asking YouTube Music again for %d albums' missing tracks",
                             len(wanted))
                    await self.request_apply(wanted)
            except Exception:
                log.exception("retrying wanted downloads failed")
            try:
                await issues.save(await issues.plan_album_copies(), scope_kinds=("album_copies",),
                                  all_scope=True)
            except Exception:
                log.exception("album copies check failed")
            await self.publish_issues()
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
            # its files went elsewhere (a move, a merge): the album, and what
            # was decided about it, goes with them; else it is gone
            moved = await units.follow(key)
            if moved:
                return {"status": "gone", "moved_to": moved}
            await db.execute("DELETE FROM md_albums WHERE key = ?", (key,))
            await issues.save([], scope_kinds=issues.UNIT_KINDS, album_key=key,
                              close_as="superseded", reason="the album is gone")
            return {"status": "gone"}
        artist_dir = rows[0].get("artist_dir")
        record = await db.fetchone("SELECT * FROM md_albums WHERE key = ?", (key,))
        if record is None:
            await db.execute(
                "INSERT INTO md_albums (key, unit_id, dirpath, artist_dir, next_check) "
                "VALUES (?,?,?,?,0)",
                (key, units.new_unit_id(), units.unit_folder(key), artist_dir))
            record = await db.fetchone("SELECT * FROM md_albums WHERE key = ?", (key,))
        if record["ignored"]:
            await issues.save([], scope_kinds=issues.UNIT_KINDS, album_key=key,
                              reason="the album is ignored")
            return {"status": "ignored"}
        plex_info = await plexsync.album_for_paths([r["path"] for r in rows])
        plex_album = (plex_info or {}).get("album")
        plex_artist = (plex_info or {}).get("artist")
        # an album Plex shares with other albums' tracks carries their match
        # and genres: no hints from it
        hint_album, hint_artist = ((None, None) if plexsync.shares_album(plex_info, rows)
                                   else (plex_album, plex_artist))
        manual = manual_release or (record["release_mbid"] if record["manual"] else None)
        no_release = bool(record["no_release"]) and not manual_release
        if no_release:
            # the user says MusicBrainz has none: not looked up again
            res = Resolution(status="unresolved", error="Not on MusicBrainz (your call)")
        else:
            try:
                res = await resolve(rows, plex_mbid=(hint_album or {}).get("mbid"), manual=manual)
            except mb.MusicBrainzError as exc:
                res = Resolution(status="pending", error=str(exc))
        want = None
        if res.release is not None and res.status in ("resolved", "review"):
            overrides = await desired_mod.overrides_for("album", record["unit_id"])
            want = await desired_mod.build_album(rows, res, plex_album=hint_album,
                                                 plex_artist=hint_artist, overrides=overrides)
        # A download is going into it (locks.settling): its files are left
        # as they are, and so are the issues about them, until the download
        # ends and it is checked again (job_ended). Plex's side goes ahead.
        settling = await locks.settling_into(units.unit_folder(key),
                                             (want or {}).get("folder")) is not None
        plan = await issues.plan_unit(
            key, rows, want if res.status == "resolved" else None, res, plex_info,
            plex_synced=bool(await plexsync.last_sync()), ours_locked=apply.ours_locked,
            no_release=no_release, merge_keeper=await issues.merged_into(units.unit_folder(key)),
            unit_id=record["unit_id"], settling=settling)
        await issues.save(plan, album_key=key, scope_kinds=tuple(
            k for k in issues.UNIT_KINDS if not (settling and k in issues.SETTLING_KINDS)))
        now = time.time()
        if res.status == "resolved":
            next_check = now + settings.metadata_recheck_days * 86400
        elif res.status == "pending":
            next_check = now + PENDING_RETRY
        else:
            next_check = now + UNRESOLVED_RETRY
        if settling:
            # and soon, however the download ends (one cancelled says nothing)
            next_check = min(next_check, now + SETTLING_RECHECK)
        await db.execute(
            "UPDATE md_albums SET release_mbid = ?, releasegroup_mbid = ?, artist_mbid = ?, "
            "confidence = ?, source = ?, status = ?, manual = ?, desired = ?, candidates = ?, "
            "plex_key = ?, file_sig = ?, plex_sig = ?, checked_at = ?, next_check = ?, "
            "priority = 0, error = ?, no_release = ? WHERE key = ?",
            ((res.release or {}).get("id"),
             (want or {}).get("releasegroup_mbid"), (want or {}).get("albumartist_mbid"),
             res.score, res.source, res.status, int(bool(manual)),
             json.dumps(want) if want else None, json.dumps(res.candidates),
             (plex_album or {}).get("rating_key"), units.signature(rows),
             str((plex_album or {}).get("updated_at") or ""), now, next_check, res.error,
             int(no_release), key))
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
        # an album whose files were caught going back and forth waits for the
        # user (apply.refuse_oscillation), with all of its fixes
        if (res.status == "resolved" or any(p["kind"] == "tags" for p in plan)) \
                and not await apply.auto_paused(record["unit_id"]):
            rows_ = await db.fetchall(
                "SELECT id, kind FROM md_issues WHERE album_key = ? AND state = 'open' "
                "AND auto = 1", (key,))
            auto_ids = [r["id"] for r in rows_
                        if not (settling and r["kind"] in issues.SETTLING_KINDS)]
        return {"status": res.status, "auto_ids": auto_ids, "issues": len(plan),
                "planned": [p["key"] for p in plan]}

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
        """Run queued fixes in three passes, however many there are:

        1. every album's file changes (tags, numbers, moves), as they are
           queued; fixes queued meanwhile join this pass;
        2. once no file work is left, one Plex rescan of everything that
           changed, waited out, and one refresh of the Plex mirror;
        3. every album's Plex changes, from a plan made after the rescan.

        Plex is never waited for album by album: a rescan costs most of a
        minute however little it looks at, a file change a second or two."""
        while True:
            try:
                if self._decisions:
                    batch, self._decisions = self._decisions, []
                    await self._decide(batch)
                    continue
                if self._apply_queue:
                    ids = sorted({i for batch in self._apply_queue for i in batch})
                    self._apply_queue.clear()
                    self._count(*await self._files_pass(ids))
                    continue
                if self._plex_due or self._plex_later:
                    self._count(*await self._plex_pass())
                    continue
                self._apply_wake.clear()
                await self._apply_wake.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("apply pass failed")
            finally:
                if not self._apply_queue and not self._plex_due and not self._plex_later \
                        and not self._decisions and (self.applying or self.apply_note):
                    self.applying = self.apply_note = None
                    await self.publish(force=True)

    async def _decide(self, batch: list[tuple[list[int], str]]) -> None:
        """The user's calls, album by album: set what they decided, check the
        album again, and queue the fixes the new plan has (auto-apply on)."""
        auto = await self.auto_apply()
        todo = [(i, decision) for ids, decision in batch for i in ids]
        follow: list[int] = []
        for n, (issue_id, decision) in enumerate(todo, start=1):
            row = await db.fetchone("SELECT * FROM md_issues WHERE id = ?", (issue_id,))
            if row is None or row["state"] != "queued":
                continue
            issue = issues.issue_out(row)
            self.applying = issue["album_key"] or issue["artist_dir"]
            self.apply_note = f"{'accepting matches' if decision == 'accept_match' else 'marking not on MusicBrainz'} ({n:,} of {len(todo):,})"
            await self.publish()
            try:
                ids, again = await self._decide_one(issue, decision)
                follow += ids
                # the album's check planned the same call again: it didn't settle it
                state, error = ("failed", "still there after the decision") if again \
                    else ("applied", None)
                await self._mark(issue, state, error, ran=True)
            except Exception as exc:
                log.info("%s for %s failed: %s", decision, issue["key"], exc)
                await self._mark(issue, "failed", str(exc)[:300], ran=True)
            if auto and len(follow) >= 200:
                await self.request_apply(follow)
                follow = []
        if auto and follow:
            await self.request_apply(follow)
        await self.publish_issues()

    async def _decide_one(self, issue: dict, decision: str) -> tuple[list[int], bool]:
        """(the album's fixes its check planned, whether it planned this
        issue again)."""
        key = issue["album_key"]
        if decision == "accept_match":
            best = next((c for c in issue["detail"].get("candidates") or [] if c.get("selected")),
                        None) or (issue["detail"].get("candidates") or [None])[0]
            if not best or not best.get("release_mbid"):
                raise apply.ApplyError("no candidate release to accept")
            await db.execute("UPDATE md_albums SET manual = 1, no_release = 0, release_mbid = ? "
                             "WHERE key = ?", (best["release_mbid"], key))
            result = await self.check_unit(key, manual_release=best["release_mbid"])
        else:
            await db.execute("UPDATE md_albums SET no_release = 1, manual = 0 WHERE key = ?",
                             (key,))
            result = await self.check_unit(key)
        uid = await units.unit_id_of(key)
        bus.publish("library.changed", {"album_keys": [key], "unit_ids": [uid] if uid else [],
                                        "artist_dirs": [issue["artist_dir"]]
                                        if issue["artist_dir"] else []})
        return result.get("auto_ids") or [], issue["key"] in (result.get("planned") or ())

    def _count(self, applied: int, failed: int) -> None:
        if self.run is not None:
            self.run["counts"]["applied"] += applied
            self.run["counts"]["failed"] += failed

    async def _files_pass(self, ids: list[int]) -> tuple[int, int]:
        """Pass 1: the file changes of these fixes, album by album. Each
        album's Plex side is kept for pass 3."""
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
        rescan: set[str] = set()       # artist folders albums moved out of or into
        current = None
        # artist by artist, so each artist's folder is rescanned once its albums are done
        for (scope, ref), members in sorted(groups.items(), key=lambda g: (g[0][0], g[0][1] or "")):
            if scope != "album":
                self._plex_later.append(((scope, ref), members))
                continue
            artist = ref.split("/", 1)[0]
            if rescan and artist != current:
                await self._rescan(rescan)
            current = artist
            self.applying = ref
            self.apply_note = (f"changing files ({len(self._plex_due):,} albums done): {ref}")
            await self.publish()
            # merging copies changes their folders too: hold them all
            keys = {ref}
            for i in members:
                if i["kind"] == "album_copies":
                    keys |= set(await apply.copy_keys(i["detail"]))
            keys = sorted(keys)
            # A Download missing asked for with a move of its album goes
            # after the move, from the album's re-check (_apply_plex): queued
            # first, it would go to the folder the album is leaving. Once.
            queue_now = not any(i["kind"] in MOVE_KINDS for i in members) or any(
                i["id"] in self._held_back for i in members if i["kind"] in apply.QUEUE_STAGE)
            try:
                async with contextlib.AsyncExitStack() as held:
                    for k in keys:
                        await held.enter_async_context(self._lock(k))
                    # and their folders, where the files are and go: an
                    # import into them, or Trash, waits; a scan leaves them
                    await held.enter_async_context(locks.hold(*await _folders(keys)))
                    a, f, state = await self._start_album(ref, members, queue=queue_now)
                    if state:
                        b, g = await self._apply_simple(state["files"], state["ctx"],
                                                        apply.FILE_STAGE)
                        a, f = a + b, f + g
            except Exception:
                log.exception("applying %s failed", ref)
                continue
            applied += a
            failed += f
            if state:
                self._keep_for_plex(state)
                if state["ctx"].moved:
                    rescan |= {p.split("/", 1)[0] for p in state["ctx"].moved}
                    rescan.add(state["ctx"].artist_dir or "")
        if rescan:
            await self._rescan(rescan)
        return applied, failed

    async def _rescan(self, folders: set[str]) -> None:
        try:
            await apply.scan_artists(folders)
        except Exception as exc:
            log.info("plex rescan of %s failed: %s", sorted(folders), exc)
        folders.clear()

    def _keep_for_plex(self, state: dict) -> None:
        """Remember an album for pass 3 (one entry per album, however many
        times its fixes were queued)."""
        key = state["ctx"].key
        held = self._plex_due.get(key)
        if held is None:
            self._plex_due[key] = state
            return
        held["plex_stage"] += state["plex_stage"]
        held["later"] = (held.get("later") or []) + (state.get("later") or [])
        held["ctx"].plex_dirty |= state["ctx"].plex_dirty
        held["ctx"].touched_folders |= state["ctx"].touched_folders
        held["ctx"].new_folders |= state["ctx"].new_folders
        held["ctx"].moved.update(state["ctx"].moved)
        held["ctx"].batches += state["ctx"].batches

    async def _plex_pass(self) -> tuple[int, int]:
        """Passes 2 and 3: one Plex rescan for everything pass 1 changed,
        then each album's Plex changes, then artists'."""
        due, self._plex_due = list(self._plex_due.values()), {}
        later, self._plex_later = self._plex_later, []
        dirty = [state["ctx"] for state in due if state["ctx"].plex_dirty]
        if dirty:
            self.applying = None
            self.apply_note = f"waiting for Plex to rescan {len(dirty):,} changed albums"
            await self.publish(force=True)
            try:
                await apply.plex_catch_up(*dirty)
            except Exception as exc:
                log.info("plex catch-up after %d albums failed: %s", len(dirty), exc)
        applied = failed = 0
        for n, state in enumerate(due, start=1):
            self.applying = state["ctx"].key
            self.apply_note = f"updating Plex ({n:,} of {len(due):,} albums): {state['ctx'].key}"
            await self.publish()
            try:
                async with self._lock(state["ctx"].key):
                    a, f = await self._apply_plex(state)
            except Exception:
                log.exception("plex changes for %s failed", state["ctx"].key)
                continue
            finally:
                await self._queue_later(state)
            applied += a
            failed += f
        ghosts = [issue for (scope, _ref), members in later if scope == "ghost" for issue in members]
        for (scope, ref), members in later:
            if scope != "artist":
                continue
            self.applying = ref
            self.apply_note = None
            await self.publish()
            try:
                a, f = await self._apply_artist(ref, members)
            except Exception:
                log.exception("applying %s failed", ref)
                continue
            applied += a
            failed += f
        if ghosts:
            # old Plex entries, in one go: one rescan, one emptying of the
            # trash. Plex often drops them itself first (when a long scan of
            # its own finishes), so look again: only what is left costs a scan.
            self.applying = None
            self.apply_note = "checking which old Plex entries are still there"
            await self.publish(force=True)
            try:
                await plexsync.sync()
                still = {i["key"]: i for i in await issues.plan_ghosts()}
                left = [still[g["key"]] for g in ghosts if g["key"] in still]
                log.info("old Plex entries: %d of %d folders still have them",
                         len(left), len(ghosts))
                if left:
                    self.apply_note = f"clearing old Plex entries from {len(left):,} folders"
                    await self.publish(force=True)
                    await apply.clean_ghosts(left)
                for issue in ghosts:
                    await self._mark(issue, "applied", ran=True)
                applied += len(ghosts)
            except Exception as exc:
                log.info("clearing old Plex entries failed: %s", exc)
                for issue in ghosts:
                    await self._mark(issue, "failed", str(exc)[:300], ran=True)
                failed += len(ghosts)
            await issues.save(await issues.plan_ghosts(), scope_kinds=("plex_ghost",),
                              all_scope=True)
        await self.publish_issues()
        return applied, failed

    async def _start_album(self, key: str, members: list[dict], *,
                           queue: bool = True) -> tuple[int, int, dict | None]:
        """Load an album for applying, and queue its downloads (`queue`), or
        keep them for after its re-check (state["later"])."""
        record = await db.fetchone("SELECT * FROM md_albums WHERE key = ?", (key,))
        rows = await units.load_unit(key)
        if not rows or record is None:
            # moved or merged away (a singles-folder track filed into its
            # album): nothing left to fix here, so the fix is superseded, not
            # failed (the album where its files went plans its own)
            for issue in members:
                await self._mark(issue, "superseded", "the album is no longer there")
            log.info("%s: the album is no longer there; closed %d fixes", key, len(members))
            return 0, 0, None
        want = (json.loads(record["desired"])
                if record["desired"] and record["status"] == "resolved" else None)
        ctx = apply.Ctx(key, rows, want, rows[0].get("artist_dir"), unit_id=record["unit_id"])
        state = {"key": key, "unit_id": record["unit_id"], "ctx": ctx, "old_artist": ctx.artist_dir,
                 "files": [i for i in members if i["kind"] in apply.FILE_STAGE],
                 "plex_stage": [i for i in members if i["kind"] in apply.PLEX_STAGE],
                 "later": []}
        queue_stage = [i for i in members if i["kind"] in apply.QUEUE_STAGE]
        if not queue:
            state["later"] = queue_stage
            self._held_back |= {i["id"] for i in queue_stage}
            return 0, 0, state
        self._held_back -= {i["id"] for i in queue_stage}
        applied, failed = await self._apply_simple(queue_stage, ctx, apply.QUEUE_STAGE)
        return applied, failed, state

    async def _queue_later(self, state: dict) -> None:
        """The downloads held back for a move (_files_pass), once the album
        was checked again where it is now: still queued, into the next pass."""
        ids = [i["id"] for i in state.get("later") or []]
        if not ids:
            return
        still = [r["id"] for r in await db.fetchall(
            f"SELECT id FROM md_issues WHERE id IN ({','.join('?' * len(ids))}) "
            f"AND state = 'queued'", tuple(ids))]
        if still:
            self._apply_queue.append(still)
            self._apply_wake.set()

    async def _mark(self, issue: dict, state: str, error: str | None = None, *,
                    ran: bool = False) -> None:
        """`ran`: a fix of ours (or the user's decision) ran on it, whatever
        came of it. It is counted and timed: found again by the check right
        after, the issue is failed rather than open (issues._save)."""
        now = utcnow()
        if ran:
            await db.execute("UPDATE md_issues SET state = ?, error = ?, updated_at = ?, "
                             "applied_at = ?, attempts = attempts + 1 WHERE id = ?",
                             (state, error, now, now, issue["id"]))
            return
        await db.execute("UPDATE md_issues SET state = ?, error = ?, updated_at = ? WHERE id = ?",
                         (state, error, now, issue["id"]))

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
                await self._mark(issue, "applied", ran=True)
                applied += 1
            except apply.KeepOpen as note:
                await db.execute("UPDATE md_issues SET state = 'open', updated_at = ? "
                                 "WHERE id = ? AND state = 'queued'", (utcnow(), issue["id"]))
                if isinstance(note, apply.Settling):
                    # it says why nothing happened (the check after the
                    # download plans it again, which clears this)
                    await db.execute("UPDATE md_issues SET error = ? WHERE id = ?",
                                     (str(note)[:300], issue["id"]))
                log.info("%s %s: %s", issue["kind"], issue["ref"], note)
                applied += 1
            except Exception as exc:
                log.info("apply %s %s failed: %s", issue["kind"], issue["ref"], exc)
                await self._mark(issue, "failed", str(exc)[:300], ran=True)
                failed += 1
        return applied, failed

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
            # A fix planned again is the same issue (it is keyed by the
            # album, not its folder), and is applied as one of `fresh`; only
            # one left behind under the old key is closed here.
            fresh_ids = {i["id"] for i in fresh}
            for issue in plex_stage:
                if issue["id"] not in fresh_ids and issue["kind"] not in fresh_kinds \
                        and issue["album_key"] != ctx.key:
                    await self._mark(issue, "superseded",
                                     f"not planned again after the album moved to {ctx.key}")
            applied, failed = await self._apply_simple(fresh, ctx, apply.PLEX_STAGE)
            for issue in plex_stage:
                if issue["id"] not in fresh_ids and issue["kind"] in fresh_kinds \
                        and issue["album_key"] != ctx.key:
                    # planned again, and applied, under the new key
                    await self._mark(issue, "superseded", "planned again under the album's new key")
        result = await self._check_unit(ctx.key)
        # What the files' changes made due (a moved album's genres, tags of
        # tracks just filed in) follows now rather than at the next check —
        # but never a fix this pass just ran, or one that won't stay fixed
        # would run round and round.
        ran = {i["id"] for i in state["files"] + state["plex_stage"]}
        follow = [i for i in result.get("auto_ids") or []
                  if i not in ran and self._followed.get(i, 0) < 2]
        if follow and await self.auto_apply():
            for i in follow:
                self._followed[i] = self._followed.get(i, 0) + 1
            await self.request_apply(follow)
        uid = await units.unit_id_of(ctx.key) or ctx.unit_id
        renamed = {}
        if ctx.key != key or uid != state["unit_id"]:
            # a move gives the album a new key (merged into another, a new
            # unit too): a page showing it, by either, follows
            to = {"key": ctx.key, "unit_id": uid, "artist_dir": ctx.artist_dir}
            renamed = {ref: to for ref in (key, state["unit_id"]) if ref}
        bus.publish("library.changed", {
            "album_keys": sorted({key, ctx.key}),
            "unit_ids": sorted({u for u in (state["unit_id"], uid) if u}),
            "artist_dirs": sorted({d for d in (state["old_artist"], ctx.artist_dir) if d}),
            "renamed": renamed})
        return applied, failed

    async def _apply_artist(self, artist_dir: str, members: list[dict]) -> tuple[int, int]:
        async with self._lock(f"artist:{artist_dir}"):
            ctx = apply.Ctx(None, [], None, artist_dir)
            applied, failed = await self._apply_simple(members, ctx)
            await self._check_artist(artist_dir)
        bus.publish("library.changed", {"album_keys": [], "artist_dirs": [artist_dir]})
        return applied, failed


async def _folders(keys: list[str]) -> list[str]:
    """The folders the fixes of these albums change: where their files are,
    and where the layout puts them."""
    out = [units.unit_folder(k) for k in keys]
    for r in await db.fetchall(f"SELECT desired FROM md_albums WHERE key IN "
                               f"({','.join('?' * len(keys))}) AND desired IS NOT NULL",
                               tuple(keys)):
        folder = (json.loads(r["desired"]) or {}).get("folder")
        if folder:
            out.append(folder)
    return out


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
    """An incremental library scan (a stat per file, a read per changed one).
    Not while a Plex re-add has files hidden: the scan would forget them."""
    async with apply.SCAN_HOLD:
        await _scan_library_now()


async def _scan_library_now() -> None:
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


async def job_ended(job_id: str) -> None:
    await runner.job_ended(job_id)
