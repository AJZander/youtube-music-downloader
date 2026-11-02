"""Writers that take turns, and albums that settle while they download.

The importer, the metadata manager's fixes, Trash and the library scan all
change or read album folders, and nothing made them take turns: a move
emptied a folder an import was writing into (and the import made it again),
a scan read a merge's files half done and split their folder in two, and a
Download missing queued in the pass that moved its album went to the
folder the album had left. An album a download is going into is
"settling": it isn't moved, merged or re-tagged until the download ends
(review 2026-11, P1-F4).

Run with: python -m unittest discover backend/tests"""
import asyncio
import contextlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.engine import tagio
from app.metadata import issues, units
from test_attention import DbTestCase
from test_metadata import HAVE_FFMPEG, make_audio
from test_p0_oscillation import KEEP, ORIG, SONGS
from test_p1_unit_id import MJ, RIP, UnitTestCase

GIRL = f"{ORIG}/03 - The Girl Is Mine.mp3"
LANDS = "05 - For All Time.mp3"
# YouTube Music's Thriller 25, for a Download missing of its fifth track
YTM = {"title": "Thriller 25", "artists": [MJ], "tracks": [
    {"video_id": f"v{n}", "title": SONGS[rec][0], "track_number": n,
     "duration_sec": SONGS[rec][1], "available": True}
    for n, rec in enumerate(["r1", "r4", "r2", "r3", "r5"], start=1)]}


class SettlingTestCase(UnitTestCase):
    """UnitTestCase, plus downloads: jobs, and the importer as the worker
    calls it."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.staging = self.tmp / "staging"
        self.staging.mkdir()
        self.serial = 0

    def thriller_rip(self) -> None:
        """Four of Thriller 25's five tracks, in a folder of their own: a
        move to its layout folder (KEEP), and one track missing."""
        for n, rec in enumerate(["r1", "r4", "r2", "r3"], start=1):
            self.thriller_file(RIP, f"{n} {SONGS[rec][0]}.mp3", rec, n)

    async def job(self, *, status: str = "queued", target_dir: str | None = None) -> str:
        self.serial += 1
        job_id = f"j{self.serial}"
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, target_dir, "
            "created_at) VALUES (?, 'https://music.youtube.com/browse/MPREb_t25', "
            "'youtube_music', 'album', ?, ?, '2026-11-02T00:00:00Z')",
            (job_id, status, target_dir))
        return job_id

    async def target_dir(self, job_id: str) -> str | None:
        return (await self.db.fetchone("SELECT target_dir FROM jobs WHERE id = ?",
                                       (job_id,)))["target_dir"]

    async def import_track(self, job_id: str, target_dir: str | None, *, rec: str = "r5",
                           number: int = 5) -> dict:
        """One track of the job through the importer, as the worker runs it:
        `target_dir` is the job's folder when the track started, and the
        index entry is made before the folder is let go."""
        from app import importer, library_index
        title = SONGS[rec][0]
        source = self.staging / f"{rec}.mp3"
        make_audio(source, SONGS[rec][1])
        tagio.write(source, {"title": [title], "artist": [MJ], "albumartist": [MJ],
                             "album": ["Thriller 25"], "tracknumber": [str(number)],
                             "date": ["2008"]})
        validated = {"tags": {"title": title, "artist": MJ, "album": "Thriller 25",
                              "album_artist": MJ, "track_number": number,
                              "release_date": "2008"},
                     "duration_sec": SONGS[rec][1], "format": "mp3", "path": source}

        async def landed(result):
            meta = result["meta"]
            await library_index.add_entry(result["library_path"], meta["artist"], meta["title"],
                                          meta["album"], "mp3", SONGS[rec][1], f"vid-{rec}")

        with mock.patch.object(importer.lyrics_mod, "fetch_synced",
                               mock.AsyncMock(return_value=None)):
            result = await importer.import_track(source, validated, {}, target_dir=target_dir,
                                                 job_id=job_id, landed=landed)
        self.serial += 1
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, position, provider_track_id, status, library_path, "
            "created_at) VALUES (?, ?, ?, ?, 'completed', ?, '2026-11-02T00:00:00Z')",
            (f"t{self.serial}", job_id, number, f"vid-{rec}", result["library_path"]))
        return result

    @contextlib.contextmanager
    def paused_at_first_move(self):
        """apply.journal, waiting (once) after the first file a move or a
        merge has moved: (`reached`, `go`)."""
        from app.metadata import apply
        real_journal = apply.journal
        reached, go = asyncio.Event(), asyncio.Event()

        async def journal(batch, target, ref, *args, **kwargs):
            out = await real_journal(batch, target, ref, *args, **kwargs)
            if target == "move" and not reached.is_set():
                reached.set()
                await go.wait()
            return out

        with mock.patch.object(apply, "journal", journal):
            yield reached, go

    async def settle(self) -> None:
        """Let other tasks run as far as they can."""
        for _ in range(5):
            await asyncio.sleep(0.05)


# ------------------------------------------------------------------ the triage's acceptance
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class ImportDuringMoveTestCase(SettlingTestCase):

    async def asyncSetUp(self):
        from app import library
        await super().asyncSetUp()
        self.thriller_rip()
        await library.scan(full=True)

    async def test_an_import_during_a_planned_move_lands_in_the_current_folder(self):
        from app.worker import worker
        with self.services():
            await self.check_all()
            move = await self.open_issue("move", RIP)
            self.assertEqual(json.loads(move["detail"])["to"], KEEP)
            # a Download missing into the album is queued
            job = await self.job(target_dir=RIP)
            result = await self.runner.check_unit(RIP)
            self.assertNotIn(move["id"], result["auto_ids"])
            self.assertEqual((await self.issue(move["id"]))["state"], "open")
            # the move planned before the download waits for it
            await self.apply_kinds(RIP, ("move",))
            stale = await self.issue(move["id"])
            self.assertEqual(stale["state"], "open")
            self.assertIn("waits for the download", stale["error"] or "")
            self.assertTrue((self.lib / RIP).is_dir())
            self.assertFalse((self.lib / KEEP).exists())
            # the download lands where the album is
            landed = await self.import_track(job, await self.target_dir(job))
            self.assertEqual(landed["library_path"], f"{RIP}/{LANDS}")
            self.assertTrue((self.lib / RIP / LANDS).exists())
            # the job ends: the album is due at once, and its move takes the new file too
            await self.db.execute("UPDATE jobs SET status = 'downloading' WHERE id = ?", (job,))
            await worker._recompute_job(job)
            record = await self.record(RIP)
            self.assertEqual((record["next_check"], record["priority"]), (0, 10))
            await self.rescan()
            await self.runner.check_unit(RIP)
            move = await self.open_issue("move", RIP)
            await self.apply_kinds(RIP, ("move",))
            self.assertEqual((await self.issue(move["id"]))["state"], "applied")
        self.assertFalse((self.lib / RIP).exists())
        self.assertTrue((self.lib / KEEP / LANDS).exists())
        self.assertEqual(len(list((self.lib / KEEP).glob("*.mp3"))), 5)

    async def test_an_import_waits_for_a_running_move_and_follows_it(self):
        with self.services():
            await self.check_all()
            move = await self.open_issue("move", RIP)
            with self.paused_at_first_move() as (reached, go):
                moving = asyncio.create_task(self.runner._files_pass([move["id"]]))
                await asyncio.wait_for(reached.wait(), 10)
                # queued while the move runs: the track starts with the old folder
                job = await self.job(status="downloading", target_dir=RIP)
                importing = asyncio.create_task(self.import_track(job, await self.target_dir(job)))
                await self.settle()
                self.assertFalse(importing.done())
                self.assertFalse((self.lib / RIP / LANDS).exists())
                go.set()
                await asyncio.wait_for(moving, 10)
                landed = await asyncio.wait_for(importing, 10)
        self.assertEqual((await self.issue(move["id"]))["state"], "applied")
        self.assertEqual(await self.target_dir(job), KEEP)
        self.assertEqual(landed["library_path"], f"{KEEP}/{LANDS}")
        self.assertTrue((self.lib / KEEP / LANDS).exists())
        self.assertFalse((self.lib / RIP).exists())

    async def test_a_scan_during_a_move_neither_reads_nor_prunes_its_folders(self):
        from app import library
        first = f"{RIP}/1 {SONGS['r1'][0]}.mp3"
        await self.db.execute("UPDATE library_index SET provider_track_id = 'vid-r1' "
                              "WHERE path = ?", (first,))
        with self.services():
            await self.check_all()
            uid = (await self.record(RIP))["unit_id"]
            move = await self.open_issue("move", RIP)
            with self.paused_at_first_move() as (reached, go):
                moving = asyncio.create_task(self.runner._files_pass([move["id"]]))
                await asyncio.wait_for(reached.wait(), 10)
                # one file is in its new folder, its index row not yet
                await library.scan(full=False)
                go.set()
                await asyncio.wait_for(moving, 10)
        self.assertEqual((await self.issue(move["id"]))["state"], "applied")
        rows = [dict(r) for r in await self.db.fetchall(
            "SELECT path, provider_track_id, unit_id FROM library_index WHERE dirpath = ?",
            (KEEP,))]
        self.assertEqual(len(rows), 4, rows)
        moved = next(r for r in rows if r["path"].endswith(SONGS["r1"][0] + ".mp3"))
        self.assertEqual((moved["provider_track_id"], moved["unit_id"]), ("vid-r1", uid))


# ------------------------------------------------------------------ merges of copies
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class MergeTestCase(SettlingTestCase):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.make_thriller()

    async def test_a_scan_during_a_merge_does_not_index_the_movers_at_the_source(self):
        from app import library
        real_trash = library.trash_paths
        seen = {}

        async def trash(paths, reason=""):
            # a library scan (a full one reads every file) and the registry,
            # after the movers were tagged as the keeper's and before they move
            await library.scan(full=True)
            await units.sync_registry()
            seen["girl"] = dict(await self.db.fetchone(
                "SELECT album, mb_albumid FROM library_index WHERE path = ?", (GIRL,)))
            seen["keys"] = await self.split_keys()
            return await real_trash(paths, reason=reason)

        with self.services():
            await self.check_all()
            with mock.patch.object(library, "trash_paths", trash):
                merged = await self.merge()
        self.assertEqual(merged["state"], "applied", merged["error"])
        self.assertEqual(seen["keys"], [])
        self.assertEqual(seen["girl"], {"album": "Thriller", "mb_albumid": "rel-orig"})
        self.assertEqual(await self.split_keys(), [])
        self.assertTrue((self.lib / KEEP / "04 - The Girl Is Mine.mp3").exists())

    async def test_merges_and_the_layout_guard_wait_while_a_copy_is_settling(self):
        with self.services():
            await self.check_all()
            planned = [i for i in await self.plan_copies() if "rg-1" in i["key"]]
            self.assertEqual(len(planned), 1)
            before = await self.issue(planned[0]["id"])
            # a download into the copy that would be merged away
            await self.job(target_dir=ORIG)
            fresh = await issues.plan_album_copies()
            self.assertEqual([i for i in fresh if not i.get("unchanged")], [])
            await issues.save(fresh, scope_kinds=("album_copies",), all_scope=True)
            kept = await self.issue(planned[0]["id"])
            self.assertEqual((kept["state"], kept["detail"]), ("open", before["detail"]))
            # the user applies it anyway: nothing is tagged, moved or trashed
            girl = self.tags(GIRL)
            await self.runner._files_pass([planned[0]["id"]])
            after = await self.issue(planned[0]["id"])
        self.assertEqual(after["state"], "open")
        self.assertIn(f"waits for the download into {ORIG}", after["error"] or "")
        self.assertEqual(self.tags(GIRL), girl)
        self.assertEqual(await self.trashed(), set())
        self.assertEqual(len(list((self.lib / ORIG).iterdir())), 3)

    async def test_trash_waits_for_a_folder_held_by_an_apply(self):
        from app import library
        theirs = f"{KEEP}/02 - Thriller.mp3"
        with self.services():
            await self.check_all()
            planned = [i for i in await self.plan_copies() if "rg-1" in i["key"]]
            with self.paused_at_first_move() as (reached, go):
                merging = asyncio.create_task(self.runner._files_pass([planned[0]["id"]]))
                await asyncio.wait_for(reached.wait(), 10)
                trashing = asyncio.create_task(library.trash_paths([theirs], reason="test"))
                await self.settle()
                self.assertFalse(trashing.done())
                self.assertTrue((self.lib / theirs).exists())
                go.set()
                await asyncio.wait_for(merging, 10)
                result = await asyncio.wait_for(trashing, 10)
        self.assertEqual((await self.issue(planned[0]["id"]))["state"], "applied")
        self.assertEqual((result["trashed"], result["failures"]), (1, []))
        self.assertFalse((self.lib / theirs).exists())


# ------------------------------------------------------------------ Download missing
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class QueueAfterMoveTestCase(SettlingTestCase):

    async def asyncSetUp(self):
        from app import library
        await super().asyncSetUp()
        self.thriller_rip()
        await library.scan(full=True)

    async def test_download_missing_is_not_queued_in_the_pass_that_moves_the_album(self):
        from app.metadata import missing
        from app.worker import worker
        calls = []
        real_enqueue = worker.enqueue

        async def enqueue(url, force=False, **kwargs):
            calls.append(kwargs.get("target_dir"))
            return await real_enqueue(url, force, **kwargs)

        with self.services(), \
                mock.patch.object(missing, "find_on_youtube",
                                  mock.AsyncMock(return_value=("MPREb_t25", YTM))), \
                mock.patch.object(worker, "enqueue", enqueue), \
                mock.patch.object(worker, "wake"):
            await self.check_all()
            move = await self.open_issue("move", RIP)
            gap = await self.db.fetchone("SELECT * FROM md_issues WHERE kind = 'missing_tracks'")
            self.assertEqual(json.loads(gap["detail"])["missing"][0]["title"], SONGS["r5"][0])
            await self.runner.request_apply([move["id"], gap["id"]])
            ids = sorted({i for batch in self.runner._apply_queue for i in batch})
            self.runner._apply_queue.clear()
            await self.runner._files_pass(ids)
            # the move ran; the download waits for the album's re-check
            self.assertEqual(calls, [])
            self.assertEqual((await self.issue(move["id"]))["state"], "applied")
            self.assertFalse((self.lib / RIP).exists())
            self.assertEqual((await self.issue(gap["id"]))["state"], "queued")
            await self.runner._plex_pass()
            again = sorted({i for batch in self.runner._apply_queue for i in batch})
            self.assertEqual(again, [gap["id"]])
            self.runner._apply_queue.clear()
            await self.runner._files_pass(again)
        self.assertEqual(calls, [KEEP])
        jobs = [tuple(r) for r in await self.db.fetchall("SELECT target_dir, track_filter FROM jobs")]
        self.assertEqual(jobs, [(KEEP, '["v5"]')])


class FilingDuringTrashTestCase(DbTestCase):
    """The apply holds the album's folder, and a Download missing then
    files the tracks an earlier download left in a folder of their own
    (_file_in_downloads), taking that folder too, while a Trash of files in
    both has that folder and waits for the album's."""

    async def test_the_download_gives_way_to_a_trash_of_both_folders(self):
        from app import library, locks
        from app.metadata import apply, missing
        album, source = "ABBA/(1980) Super Trouper [2001 Remaster]", "ABBA/(1980) Super Trouper"
        self.assertLess(locks.key(source), locks.key(album))
        ours, stray = f"{album}/01 - Super Trouper.flac", f"{source}/09 - Lay All Your Love On Me.m4a"
        for path in (ours, stray):
            (self.lib / path).parent.mkdir(parents=True, exist_ok=True)
            (self.lib / path).write_bytes(b"a")
            await self.db.execute(
                "INSERT INTO library_index (norm_artist, norm_title, path, added_at, dirpath, "
                "artist_dir) VALUES ('a', 't', ?, '', ?, 'ABBA')", (path, str(Path(path).parent)))
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, created_at) "
            "VALUES ('j1', 'u', 'youtube', 'album', 'completed', '')")
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, status, library_path, provider_track_id, created_at) "
            "VALUES ('t1', 'j1', 'completed', ?, 'vid9', '')", (stray,))
        issue = {"kind": "missing_tracks", "ref": "rel-1",
                 "detail": {"download": {"job_id": "j1"}}}
        ctx = apply.Ctx(album, [{"path": ours, "dirpath": album, "artist_dir": "ABBA"}],
                        None, "ABBA")
        holding, go = asyncio.Event(), asyncio.Event()

        async def apply_pass():
            async with locks.hold(album):                   # runner._files_pass
                holding.set()
                await go.wait()
                return await apply.do_download_missing(issue, ctx)

        async def user_trash():
            await holding.wait()
            return await library.trash_paths([stray, ours], "user")

        queue = mock.AsyncMock(return_value={"queued": 1})
        with mock.patch.object(library, "_refresh_plex", mock.AsyncMock()), \
                mock.patch.object(missing, "queue_download", queue):
            applying = asyncio.create_task(apply_pass())
            trashing = asyncio.create_task(user_trash())
            await asyncio.wait_for(holding.wait(), 5)
            for _ in range(50):         # the Trash has the source, and waits for the album
                if locks.busy(source):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(locks.busy(source))
            go.set()
            done, pending = await asyncio.wait({applying, trashing}, timeout=5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.assertEqual(pending, set(), "the apply and the Trash waited on each other")
        with self.assertRaises(locks.Busy) as raised:
            applying.result()
        self.assertIn(source, str(raised.exception))
        queue.assert_not_awaited()
        self.assertEqual((trashing.result()["trashed"], trashing.result()["failures"]), (2, []))
        self.assertFalse((self.lib / album / Path(stray).name).exists())
        self.assertEqual(len(await self.db.fetchall("SELECT id FROM library_trash")), 2)


# ------------------------------------------------------------------ the locks
class LocksTestCase(unittest.IsolatedAsyncioTestCase):

    async def test_a_task_holding_a_folder_holds_it_again_at_once(self):
        from app import locks
        async with locks.hold("A/X", "A/Y"):
            async with locks.hold("a/x"):
                self.assertFalse(locks.busy("A/X"))
            self.assertFalse(locks.busy("A/X"))
            seen = await asyncio.create_task(self._busy("A/X"))
            self.assertTrue(seen)
        self.assertFalse(await asyncio.create_task(self._busy("A/X")))

    async def _busy(self, folder: str) -> bool:
        from app import locks
        return locks.busy(folder)

    async def test_a_disc_folder_is_its_album_folder(self):
        from app import locks
        async with locks.hold("A/X/CD 2"):
            self.assertTrue(await asyncio.create_task(self._busy("A/X")))

    async def test_two_tasks_taking_folders_in_either_order_both_finish(self):
        from app import locks
        order = []

        async def take(*folders):
            async with locks.hold(*folders):
                await asyncio.sleep(0.01)
                order.append(folders)

        await asyncio.wait_for(asyncio.gather(take("A/X", "A/Y"), take("A/Y", "A/X")), 5)
        self.assertEqual(len(order), 2)

    async def test_a_task_holding_a_folder_takes_one_before_it_only_if_free(self):
        """Holding Y, a task wanting X (before Y) takes it at once if no one
        has it; if a task has it and waits for Y, it gives way (Busy, with
        nothing taken) rather than wait on a task waiting on it."""
        from app import locks
        async with locks.hold("A/Y"):
            async with locks.hold("A/Y", "A/X"):
                self.assertTrue(await asyncio.create_task(self._busy("A/X")))
        holding, go = asyncio.Event(), asyncio.Event()

        async def apply_pass():
            async with locks.hold("A/Y"):
                holding.set()
                await go.wait()
                async with locks.hold("A/Y", "A/X"):
                    return "filed"

        async def trash():
            await holding.wait()
            async with locks.hold("A/X", "A/Y"):
                return "trashed"

        applying, trashing = asyncio.create_task(apply_pass()), asyncio.create_task(trash())
        await asyncio.wait_for(holding.wait(), 5)
        await asyncio.sleep(0.01)
        self.assertTrue(locks.busy("A/X"))
        go.set()
        with self.assertRaises(locks.Busy):
            await asyncio.wait_for(applying, 5)
        self.assertEqual(await asyncio.wait_for(trashing, 5), "trashed")
        self.assertFalse(locks.busy("A/X") or locks.busy("A/Y"))

    async def test_a_task_holding_a_folder_waits_for_one_after_it(self):
        from app import locks
        order, held = [], asyncio.Event()

        async def other():
            async with locks.hold("A/Z"):
                held.set()
                await asyncio.sleep(0.05)
                order.append("other")

        task = asyncio.create_task(other())
        await asyncio.wait_for(held.wait(), 5)
        async with locks.hold("A/A"):
            async with locks.hold("A/A", "A/Z"):
                order.append("nested")
        await task
        self.assertEqual(order, ["other", "nested"])

    async def test_a_folder_let_go_since_counts_as_touched(self):
        from app import locks
        since = locks.tick()
        self.assertFalse(locks.touched_since("A/Z", since))
        async with locks.hold("A/Z"):
            self.assertTrue(await asyncio.create_task(self._touched("A/Z", since)))
        self.assertTrue(locks.touched_since("A/Z", since))
        self.assertFalse(locks.touched_since("A/Z", locks.tick()))

    async def _touched(self, folder: str, since: int) -> bool:
        from app import locks
        return locks.touched_since(folder, since)

    def test_every_file_and_queue_executor_waits_for_downloads(self):
        from app.metadata import apply
        for kind, executor in {**apply.FILE_STAGE, **apply.QUEUE_STAGE}.items():
            with self.subTest(kind=kind):
                self.assertTrue(getattr(executor, "settles", False))
        self.assertFalse(getattr(apply.PLEX_STAGE["track_order"], "settles", False))


if __name__ == "__main__":
    unittest.main()
