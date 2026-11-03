"""Honest issue states: a fix that doesn't stick is failed, not open again;
an album that moved, merged or went away supersedes its issues; Download
missing leftovers go to a Tried bucket; every state change is recorded;
and undoing a merge gives the album back what was decided about it.

Issues closed as `applied` and re-opened by the next check looped open →
queued → applied → open with auto-apply on (review 2026-11, 4.A).

Run with: python -m unittest discover backend/tests"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import utcnow
from app.engine import tagio
from app.metadata import units
from test_metadata import HAVE_FFMPEG, release
from test_p0_oscillation import KEEP, ORIG, SONGS, LibraryTestCase
from test_p1_unit_id import MJ, UnitTestCase, celebrity
import test_p0_download_missing as dl
import test_p0_oscillation as osc     # not its test cases by name: they'd run here too
import test_p1_relocate as rel

NSYNC = "NSYNC/(2001) Celebrity"
CELEBRITY = [("Pop", 3.0), ("Celebrity", 4.0), ("The Game Is Over", 5.0)]
RIP = f"{MJ}/Thriller 25 rip"
# §7: issues that came back, applied -> open within 24 h
CAME_BACK = (
    "SELECT COUNT(*) AS n FROM (SELECT issue_id, at, from_state, to_state, "
    "LAG(at) OVER (PARTITION BY issue_id ORDER BY id) AS prev_at FROM md_issue_events) "
    "WHERE from_state = 'applied' AND to_state = 'open' AND at > ? "
    "AND prev_at > strftime('%Y-%m-%dT%H:%M:%SZ', at, '-24 hours')")


def ago(hours: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours * 3600))


# ------------------------------------------------------------------ re-detection
class CelebrityTestCase(UnitTestCase):
    """NSYNC's Celebrity, identified, with a tags fix planned (its files lack
    the release's date, label and ids). The fix runs through the real file
    stage; another tagger then puts the old tags back."""

    releases = {"rel-1": celebrity()}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app import library
        for n, (title, seconds) in enumerate(CELEBRITY, start=1):
            self.write(f"{NSYNC}/{n:02d} - {title}.mp3", seconds=seconds, title=title,
                       artist="*NSYNC", albumartist="*NSYNC", album="Celebrity",
                       tracknumber=str(n), mb_albumid="rel-1", mb_releasetrackid=f"t{n}")
        await library.scan(full=True)
        await units.sync_registry()

    async def fix_tags(self) -> dict:
        """The tags fix, planned, queued and applied as auto-apply does it;
        then the files' old tags back, as another tagger leaves them."""
        from app import library
        result = await self.runner.check_unit(NSYNC)
        tags = await self.open_issue("tags", NSYNC)
        self.assertIn(tags["id"], result["auto_ids"])
        self.assertEqual(await self.runner.request_apply([tags["id"]]), 1)
        await self.runner._files_pass(self.runner._apply_queue.pop())
        applied = await self.issue(tags["id"])
        self.assertEqual(applied["state"], "applied", applied["error"])
        written = await self.db.fetchall("SELECT ref, old_value FROM md_changes "
                                         "WHERE target = 'file' AND field = 'tags'")
        self.assertTrue(written)
        later = time.time() + 10       # the scan counts a change within a second as none
        for r in written:
            tagio.restore(self.lib / r["ref"], json.loads(r["old_value"]))
            os.utime(self.lib / r["ref"], (later, later))
        await library.scan(full=False)
        return applied


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class ReDetectTestCase(CelebrityTestCase):

    async def test_a_fix_that_does_not_stick_fails_instead_of_reopening(self):
        with self.services():
            applied = await self.fix_tags()
            # the album's re-check after its apply (the Plex pass's)
            await self.runner._plex_pass()
            after = await self.issue(applied["id"])
        self.assertEqual(after["state"], "failed")
        self.assertIn("still there after the fix", after["error"] or "")
        self.assertEqual(after["attempts"], 1)
        events = [tuple(r) for r in await self.db.fetchall(
            "SELECT from_state, to_state FROM md_issue_events WHERE issue_id = ? ORDER BY id",
            (applied["id"],))]
        self.assertEqual(events[-1], ("applied", "failed"))

    async def test_a_failed_fix_is_not_applied_automatically(self):
        with self.services():
            applied = await self.fix_tags()
            await self.runner._plex_pass()
            result = await self.runner.check_unit(NSYNC)
            after = await self.issue(applied["id"])
        self.assertNotIn(applied["id"], result["auto_ids"])
        self.assertEqual(after["state"], "failed")

    async def test_a_problem_found_after_the_window_is_new(self):
        with self.services():
            applied = await self.fix_tags()
            await self.db.execute("UPDATE md_issues SET applied_at = ? WHERE id = ?",
                                  (ago(25), applied["id"]))
            await self.runner._plex_pass()
            after = await self.issue(applied["id"])
        self.assertEqual(after["state"], "open")
        self.assertIsNone(after["error"])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class NewProblemTestCase(UnitTestCase):
    """Celebrity's first two tracks, their tags fixed; within the hour
    Download missing brings the third with YouTube Music's tags (no ids)."""

    releases = {"rel-1": celebrity()}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app import library
        for n, (title, seconds) in enumerate(CELEBRITY[:2], start=1):
            self.write(f"{NSYNC}/{n:02d} - {title}.mp3", seconds=seconds, title=title,
                       artist="*NSYNC", albumartist="*NSYNC", album="Celebrity",
                       tracknumber=str(n), mb_albumid="rel-1", mb_releasetrackid=f"t{n}")
        await library.scan(full=True)
        await units.sync_registry()

    async def test_a_new_file_after_the_fix_is_a_new_problem(self):
        from app import library
        with self.services():
            await self.runner.check_unit(NSYNC)
            tags = await self.open_issue("tags", NSYNC)
            self.assertEqual(await self.runner.request_apply([tags["id"]]), 1)
            await self.runner._files_pass(self.runner._apply_queue.pop())
            await self.runner._plex_pass()
            self.assertEqual((await self.issue(tags["id"]))["state"], "applied")
            title, seconds = CELEBRITY[2]
            self.write(f"{NSYNC}/03 - {title}.mp3", seconds=seconds, title=title,
                       artist="*NSYNC", albumartist="*NSYNC", album="Celebrity", tracknumber="3")
            await library.scan(full=False)
            result = await self.runner.check_unit(NSYNC)
            after = await self.issue(tags["id"])
        # its fix is planned for the new file only: not the old fix failing
        self.assertNotEqual(after["detail"], tags["detail"])
        self.assertEqual((after["state"], after["error"]), ("open", None))
        self.assertIn(tags["id"], result["auto_ids"])


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class EventsTestCase(CelebrityTestCase):
    """Every state change of an issue is a row of md_issue_events."""

    async def test_every_state_change_is_recorded(self):
        with self.services():
            applied = await self.fix_tags()
            await self.runner._plex_pass()
            await self.runner.check_unit(NSYNC)     # failed stays failed: no event
        events = [tuple(r) for r in await self.db.fetchall(
            "SELECT from_state, to_state FROM md_issue_events WHERE issue_id = ? ORDER BY id",
            (applied["id"],))]
        self.assertEqual(events, [(None, "open"), ("open", "queued"), ("queued", "applied"),
                                  ("applied", "failed")])
        last = await self.db.fetchone("SELECT at, reason FROM md_issue_events WHERE issue_id = ? "
                                      "ORDER BY id DESC LIMIT 1", (applied["id"],))
        self.assertRegex(last["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertIn("still there after the fix", last["reason"])
        self.assertEqual((await self.db.fetchone(CAME_BACK, ("",)))["n"], 0)


# ------------------------------------------------------------------ superseded
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class SupersedeTestCase(UnitTestCase):

    async def test_a_merge_into_another_album_supersedes_and_the_keeper_keeps_the_dismissal(self):
        """Thriller 25's tracks 3-5, ripped into a folder of their own, move
        into the folder holding tracks 1-2: two albums of one release, one
        now. Each had a tags fix planned; the mover's was dismissed."""
        from app import library
        from app.metadata import routes
        for n, rec in enumerate(["r1", "r4"], start=1):
            self.thriller_file(KEEP, f"{n:02d} - {SONGS[rec][0]}.mp3", rec, n)
        for n, rec in ((3, "r2"), (4, "r3"), (5, "r5")):
            self.thriller_file(RIP, f"{n} {SONGS[rec][0]}.mp3", rec, n)
        await library.scan(full=True)
        with self.services():
            await self.check_all()
            theirs = await self.open_issue("tags", RIP)
            mine = await self.open_issue("tags", KEEP)
            move = await self.open_issue("move", RIP)
            self.assertEqual(json.loads(move["detail"])["to"], KEEP)
            await routes.dismiss_issues(routes.IdsIn(ids=[theirs["id"]]))
            # the real do_move: relocate -> rekey_unit -> merge_into
            await self.apply_kinds(RIP, ("move",))
            self.assertEqual((await self.issue(move["id"]))["state"], "applied")
            self.assertIsNone(await self.record(RIP))
            moved = await self.issue(theirs["id"])
            kept = await self.issue(mine["id"])
            await self.rescan()
            await self.runner.check_unit(KEEP)
            kept_after = await self.issue(mine["id"])
        self.assertEqual(moved["state"], "superseded")
        self.assertIn(KEEP, moved["error"])
        self.assertEqual(kept["state"], "dismissed")
        self.assertEqual(kept_after["state"], "dismissed")

    async def test_an_album_whose_files_are_gone_is_superseded_with_iso_time(self):
        await self.db.execute("INSERT INTO md_albums (key, dirpath, artist_dir) "
                              "VALUES ('A/gone', 'A/gone', 'A')")
        for key, state, album in (("tags:x", "open", "A/gone"), ("genres:y", "failed", "A/gone"),
                                  ("move:z", "queued", "A/elsewhere")):
            await self.db.execute(
                "INSERT INTO md_issues (key, kind, entity, ref, artist_dir, album_key, severity, "
                "summary, state, created_at, updated_at) VALUES (?, ?, 'album', ?, 'A', ?, 'info', "
                "'s', ?, 'x', 'x')", (key, key.split(":")[0], key.split(":")[1], album, state))
        await units.sync_registry()
        rows = {r["key"]: dict(r) for r in await self.db.fetchall("SELECT * FROM md_issues")}
        for key, row in rows.items():
            with self.subTest(key=key):
                self.assertEqual(row["state"], "superseded")
                self.assertTrue(row["error"])
                self.assertRegex(row["updated_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


# ------------------------------------------------------------------ undo a merge
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class UndoMergeTestCase(UnitTestCase):

    async def test_undoing_a_merge_gives_the_album_back_its_decisions(self):
        """Thriller (ORIG) is pinned to its release, has a dismissed tags fix
        and a genres override. Its copies merge into Thriller 25 (KEEP), its
        record folds into KEEP's, and the merge is undone from Activity."""
        from app.metadata import routes
        await self.make_thriller()
        with self.services():
            await self.check_all()
            orig = await self.record(ORIG)
            await self.db.execute("UPDATE md_albums SET manual = 1 WHERE key = ?", (ORIG,))
            dismissed = await self.open_issue("tags", ORIG)
            await routes.dismiss_issues(routes.IdsIn(ids=[dismissed["id"]]))
            await self.db.execute(
                "INSERT INTO md_overrides (entity, ref, field, value, created_at) "
                "VALUES ('album', ?, 'genres', '[\"Pop\"]', ?)", (orig["unit_id"], utcnow()))
            keep = await self.record(KEEP)
            keep_tags = await self.open_issue("tags", KEEP)
            merged = await self.merge()
            self.assertEqual(merged["state"], "applied", merged["error"])
            await self.rescan()
            self.assertIsNone(await self.record(ORIG))
            batch = (await self.db.fetchone(
                "SELECT batch_id FROM md_changes WHERE summary LIKE 'Merged %' LIMIT 1"))["batch_id"]
            result = await routes.revert_batch(batch)
            self.assertEqual(result["failures"], [])
            await self.rescan()
        back = await self.record(ORIG)
        self.assertIsNotNone(back)
        self.assertEqual(back["unit_id"], orig["unit_id"])
        self.assertEqual((back["manual"], back["release_mbid"]), (1, "rel-orig"))
        issue = await self.issue(dismissed["id"])
        self.assertEqual((issue["key"], issue["state"], issue["album_key"]),
                         (f"tags:{orig['unit_id']}", "dismissed", ORIG))
        override = await self.db.fetchone("SELECT value FROM md_overrides WHERE entity = 'album' "
                                          "AND ref = ? AND field = 'genres'", (orig["unit_id"],))
        self.assertEqual(json.loads(override["value"]), ["Pop"])
        after = await self.record(KEEP)
        fields = ("unit_id", "manual", "release_mbid", "no_release", "ignored")
        self.assertEqual({f: after[f] for f in fields}, {f: keep[f] for f in fields})
        self.assertEqual((await self.issue(keep_tags["id"]))["state"], keep_tags["state"])
        change = await self.db.fetchone("SELECT reverted_at FROM md_changes WHERE target = 'unit' "
                                        "AND ref = ?", (orig["unit_id"],))
        self.assertIsNotNone(change)
        self.assertIsNotNone(change["reverted_at"])
        self.assertIsNone(await self.db.fetchone("SELECT 1 FROM md_unit_aliases WHERE unit_id = ?",
                                                 (orig["unit_id"],)))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class ArtistMergeUndoTestCase(rel.RelocateTestCase):

    async def test_undoing_an_artist_merge_gives_the_folded_album_back(self):
        """Old's Celebrity folds into New's. The first of its files to move
        is no song (a booklet sorts first); the fold is still the artist
        merge's, and undone with it."""
        from app import library
        from app.metadata import apply
        self.album("Old/(2001) Celebrity", "Old", "Celebrity", ["Pop", "Celebrity"])
        self.album("New/(2001) Celebrity", "New", "Celebrity", ["Pop"])
        (self.lib / "Old/(2001) Celebrity/!booklet.pdf").write_bytes(b"pdf")
        await self.rescan()
        old = await self.record("Old/(2001) Celebrity")
        await self.db.execute("UPDATE md_albums SET manual = 1, release_mbid = 'rel-old' "
                              "WHERE unit_id = ?", (old["unit_id"],))
        result = await library.merge_artist_folders(["Old"], "New")
        self.assertEqual(result["failures"], [])
        fold = await self.db.fetchall("SELECT batch_id FROM md_changes WHERE target = 'unit' "
                                      "AND ref = ?", (old["unit_id"],))
        self.assertEqual([r["batch_id"] for r in fold], [result["batch_id"]])
        undone = await apply.revert(result["batch_id"])
        self.assertFalse(undone["failures"], undone)
        await self.rescan()
        back = await self.record("Old/(2001) Celebrity")
        self.assertIsNotNone(back)
        self.assertEqual((back["unit_id"], back["manual"], back["release_mbid"]),
                         (old["unit_id"], 1, "rel-old"))


# ------------------------------------------------------------------ tried
class TriedTestCase(dl.DbTestCase):
    """test_p0_download_missing's album: it holds tracks 1 and 2 of four."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app import catalog
        from app.metadata import missing
        from app.worker import worker
        self.desired = {"album": "Bee", "albumartist": "Ay", "original_year": "2001", "tracks": {
            f"{dl.ALBUM}/0{i} - {t}.m4a": {"matched": True, "releasetrack_mbid": f"t{i}"}
            for i, (t, _) in enumerate(dl.SONGS[:2], start=1)}}
        await self.db.execute(
            "INSERT INTO md_albums (key, dirpath, artist_dir, status, release_mbid, desired) "
            "VALUES (?, ?, 'Ay', 'resolved', 'rel-1', ?)",
            (dl.ALBUM, dl.ALBUM, json.dumps(self.desired)))
        fakes = [mock.patch.object(missing.mb, "release", mock.AsyncMock(
                     return_value=release(title="Bee", artist="Ay", tracks=dl.SONGS))),
                 mock.patch.object(missing, "_browse_from_history",
                                   mock.AsyncMock(return_value=dl.BROWSE)),
                 mock.patch.object(catalog, "album", mock.AsyncMock(return_value=dl.YTM_ALBUM)),
                 mock.patch.object(worker, "wake")]
        for p in fakes:
            p.start()
            self.addCleanup(p.stop)
        self.missing = missing
        await missing.refresh("rel-1")

    async def tried(self) -> dict:
        """A download of tracks 3 and 4 skipped both; asked again, nothing is queued."""
        first = await self.missing.queue_download("rel-1")
        self.assertEqual(first["queued"], 2)
        for n in (3, 4):
            await self.db.execute(
                "INSERT INTO tracks (id, job_id, position, provider_track_id, title, track_number, "
                "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'skipped_duplicate', '')",
                (f"j-{n}", first["job_id"], n - 2, f"audio{n}", dl.SONGS[n - 1][0], n))
        await self.db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?",
                              (first["job_id"],))
        again = await self.missing.queue_download("rel-1")
        self.assertEqual(again["message"], "already tried")
        return await self.issue()

    async def filled(self) -> dict:
        """A download of tracks 3 and 4 delivered both, and the next check
        matched them: the album is whole."""
        first = await self.missing.queue_download("rel-1")
        self.assertEqual(first["queued"], 2)
        await self.db.execute("UPDATE jobs SET status = 'completed' WHERE id = ?",
                              (first["job_id"],))
        for n in (3, 4):
            self.desired["tracks"][f"{dl.ALBUM}/0{n} - {dl.SONGS[n - 1][0]}.m4a"] = {
                "matched": True, "releasetrack_mbid": f"t{n}"}
        await self.set_desired()
        await self.missing.refresh("rel-1")
        return await self.issue()

    async def set_desired(self) -> None:
        await self.db.execute("UPDATE md_albums SET desired = ? WHERE key = ?",
                              (json.dumps(self.desired), dl.ALBUM))

    async def issue(self) -> dict:
        return dict(await self.db.fetchone("SELECT * FROM md_issues "
                                           "WHERE key = 'missing_tracks:rel-1'"))

    async def test_a_gap_filled_by_our_download_is_applied_and_timed(self):
        issue = await self.filled()
        self.assertEqual(issue["state"], "applied")
        self.assertIsNotNone(issue["applied_at"])
        self.assertEqual(issue["attempts"], 1)

    async def test_a_filled_gap_found_again_within_a_day_failed(self):
        await self.filled()
        # an hour later track 4's file is gone again
        del self.desired["tracks"][f"{dl.ALBUM}/04 - Four.m4a"]
        await self.set_desired()
        await self.missing.refresh("rel-1")
        issue = await self.issue()
        self.assertEqual((issue["state"], issue["error"]), ("failed", "still there after the fix"))
        self.assertEqual((await self.db.fetchone(CAME_BACK, ("",)))["n"], 0)

    async def test_a_new_gap_after_a_fill_is_a_new_problem(self):
        await self.filled()
        # track 2, which the download never fetched, is gone
        del self.desired["tracks"][f"{dl.ALBUM}/02 - Two.m4a"]
        await self.set_desired()
        await self.missing.refresh("rel-1")
        issue = await self.issue()
        self.assertEqual((issue["state"], issue["error"]), ("open", None))

    async def test_already_tried_goes_to_the_tried_bucket(self):
        from app.metadata import runner as runmod
        issue = await self.tried()
        self.assertEqual(issue["state"], "tried")
        self.assertTrue(issue["error"].startswith("already tried"))
        # asked for again while YouTube Music was busy, say: still not taken
        detail = json.loads(issue["detail"])
        detail["download"]["wanted"] = True
        await self.db.execute("UPDATE md_issues SET detail = ? WHERE id = ?",
                              (json.dumps(detail), issue["id"]))
        self.assertEqual(await self.missing.wanted_downloads(), [])
        runner = runmod.Runner()
        with mock.patch.object(runner, "publish", mock.AsyncMock()):
            self.assertEqual(await runner.request_apply([issue["id"]]), 0)
        self.assertEqual((await self.issue())["state"], "tried")

    async def test_retry_takes_it_out_of_the_bucket(self):
        self.assertEqual((await self.tried())["state"], "tried")
        again = await self.missing.queue_download("rel-1", force=True)
        self.assertEqual(again["queued"], 2)
        self.assertEqual((await self.issue())["state"], "open")

    async def test_a_new_gap_reopens_it(self):
        await self.tried()
        await self.missing.refresh("rel-1")
        self.assertEqual((await self.issue())["state"], "tried")
        # track 2's file went: something new is missing
        del self.desired["tracks"][f"{dl.ALBUM}/02 - Two.m4a"]
        await self.db.execute("UPDATE md_albums SET desired = ? WHERE key = ?",
                              (json.dumps(self.desired), dl.ALBUM))
        await self.missing.refresh("rel-1")
        issue = await self.issue()
        self.assertEqual(issue["state"], "open")
        self.assertEqual(len(json.loads(issue["detail"])["missing"]), 3)


# ------------------------------------------------------------------ the breaker's words
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class BreakerWordingTestCase(LibraryTestCase):
    OLD, HOME = osc.BreakerTestCase.OLD, osc.BreakerTestCase.HOME
    releases = osc.BreakerTestCase.releases
    make_nsync = osc.BreakerTestCase.make_nsync
    planned = osc.BreakerTestCase.planned

    async def test_the_refusal_says_an_apply_by_hand_is_refused_too(self):
        await self.make_nsync()
        with self.services():
            await self.runner._check_unit(self.OLD)
            move = await self.planned("move", self.OLD)
            await self.history(self.a, self.b, days_ago=2)
            # Apply, by hand
            self.assertEqual(await self.runner.request_apply([move["id"]]), 1)
            await self.runner._files_pass(self.runner._apply_queue.pop())
            after = await self.issue(move["id"])
        self.assertEqual(after["state"], "failed")
        self.assertIn("oscillating between", after["error"])
        self.assertIn("applied by hand", after["error"])
        self.assertTrue((self.lib / self.a).exists())


# ------------------------------------------------------------------ migration
class MigrationTestCase(unittest.IsolatedAsyncioTestCase):
    """v15 on a database left at v14, through the real connect()."""

    async def asyncSetUp(self):
        from app.config import settings
        from app.db import db
        self.tmp = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(settings, "data_dir", self.tmp / "data")
        self.patch.start()
        self.db = db

    async def asyncTearDown(self):
        await self.db.close()
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def at_v14(self) -> None:
        from app import db as dbmod
        with mock.patch.object(dbmod, "MIGRATIONS", dbmod.MIGRATIONS[:14]):
            await self.db.connect()
        await self.db.execute(
            "INSERT INTO md_issues (key, kind, entity, ref, album_key, severity, summary, state, "
            "created_at, updated_at) VALUES ('tags:u1', 'tags', 'album', 'u1', 'A/x', 'info', "
            "'s', 'applied', 'x', '2026-11-01 10:00:00')")
        await self.db.close()

    def schema(self) -> dict:
        conn = sqlite3.connect(self.tmp / "data" / "mdl.db")
        try:
            return {"version": conn.execute("SELECT MAX(version) FROM schema_migrations")
                    .fetchone()[0],
                    "columns": {r[1] for r in conn.execute("PRAGMA table_info(md_issues)")},
                    "tables": {r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'")},
                    "triggers": sorted(r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                        "AND tbl_name = 'md_issues'"))}
        finally:
            conn.close()

    async def test_v15_adds_the_columns_table_and_triggers_once(self):
        from app import db as dbmod
        await self.at_v14()
        await self.db.connect()
        old = await self.db.fetchone("SELECT * FROM md_issues WHERE key = 'tags:u1'")
        # nothing made up for what came before
        self.assertEqual((old["state"], old["attempts"], old["applied_at"]), ("applied", 0, None))
        self.assertEqual(old["updated_at"], "2026-11-01 10:00:00")
        self.assertIsNone(await self.db.fetchone("SELECT 1 FROM md_issue_events"))
        await self.db.execute(
            "INSERT INTO md_issues (key, kind, entity, ref, severity, summary, created_at, "
            "updated_at) VALUES ('tags:u2', 'tags', 'album', 'u2', 'info', 's', 'x', 'x')")
        await self.db.execute("UPDATE md_issues SET state = 'queued' WHERE key = 'tags:u2'")
        await self.db.execute("UPDATE md_issues SET state = 'queued', summary = 't' "
                              "WHERE key = 'tags:u2'")         # no change of state: no event
        await self.db.execute("UPDATE md_issues SET state = 'failed', error = 'boom' "
                              "WHERE key = 'tags:u2'")
        events = [tuple(r) for r in await self.db.fetchall(
            "SELECT from_state, to_state, reason FROM md_issue_events ORDER BY id")]
        self.assertEqual(events, [(None, "open", None), ("open", "queued", None),
                                  ("queued", "failed", "boom")])
        await self.db.close()
        first = self.schema()
        self.assertEqual(first["version"], len(dbmod.MIGRATIONS))
        self.assertLessEqual({"attempts", "applied_at"}, first["columns"])
        self.assertIn("md_issue_events", first["tables"])
        self.assertEqual(len(first["triggers"]), 2)
        await self.db.connect()     # a second start leaves it alone
        await self.db.close()
        self.assertEqual(self.schema(), first)

    async def test_a_cut_off_v15_leaves_nothing(self):
        from app import db as dbmod
        await self.at_v14()
        broken = dbmod.MIGRATIONS[:14] + [dbmod.MIGRATIONS[14] + "\nSELECT * FROM no_such_table;\n"]
        with mock.patch.object(dbmod, "MIGRATIONS", broken), \
                self.assertRaises(sqlite3.OperationalError):
            await self.db.connect()
        await self.db.close()
        cut = self.schema()
        self.assertEqual(cut["version"], 14)
        self.assertNotIn("attempts", cut["columns"])
        self.assertNotIn("applied_at", cut["columns"])
        self.assertNotIn("md_issue_events", cut["tables"])
        self.assertEqual(cut["triggers"], [])
        # the real one then runs
        await self.db.connect()
        row = await self.db.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        self.assertEqual(row["v"], len(dbmod.MIGRATIONS))


if __name__ == "__main__":
    unittest.main()
