"""Merges that stay merged: the copies merge tags what it moves in as the
album it joins, a file that went A→B→A is not moved a third time, and the
folder a merge just filled is not moved out of.

Michael Jackson's Thriller 25 was merged four times and moved back five
minutes later each time (review 2026-11, problem D).

Run with: python -m unittest discover backend/tests"""
import contextlib
import copy
import json
import os
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.engine import tagio
from app.metadata import issues, units
from test_attention import DbTestCase
from test_metadata import HAVE_FFMPEG, make_audio, release

ORIG = "Michael Jackson/(1982) Thriller"
KEEP = "Michael Jackson/(1982) Thriller 25 [2008 Reissue]"
SONGS = {"r1": ("Wanna Be Startin' Somethin'", 3.0), "r2": ("Baby Be Mine", 4.0),
         "r3": ("The Girl Is Mine", 5.0), "r4": ("Thriller", 6.0), "r5": ("For All Time", 7.0),
         "r9": ("The Lady in My Life", 8.0)}


def thriller(rid: str, title: str, date: str, recordings: list[str]) -> dict:
    return {
        "id": rid, "title": title, "date": date, "disambiguation": "", "status": "Official",
        "country": "US",
        "artist-credit": [{"name": "Michael Jackson", "joinphrase": "",
                           "artist": {"id": "mj", "name": "Michael Jackson",
                                      "sort-name": "Jackson, Michael"}}],
        "release-group": {"id": "rg-1", "primary-type": "Album", "secondary-types": [],
                          "first-release-date": "1982-11-30"},
        "label-info": [{"label": {"name": "Epic"}}],
        "media": [{"position": 1, "format": "CD", "track-count": len(recordings), "tracks": [
            {"id": f"{rid}-t{n}", "position": n, "number": str(n), "title": SONGS[rec][0],
             "length": int(SONGS[rec][1] * 1000),
             "recording": {"id": rec, "title": SONGS[rec][0], "isrcs": []}}
            for n, rec in enumerate(recordings, start=1)]}],
    }


# The original holds three of the reissue's tracks; the reissue numbers them
# differently ("The Girl Is Mine" is its 4th).
RELEASES = {"rel-orig": thriller("rel-orig", "Thriller", "1982-11-30", ["r1", "r2", "r3"]),
            "rel-25": thriller("rel-25", "Thriller 25", "2008-02-08", ["r1", "r4", "r2", "r3", "r5"])}
# folder, name, recording, release the file is tagged as, its track number there
FILES = [(ORIG, "01 - Wanna Be Startin' Somethin'.flac", "r1", "rel-orig", 1),   # beats the mp3
         (ORIG, "02 - Baby Be Mine.mp3", "r2", "rel-orig", 2),                   # a tie: trashed
         (ORIG, "03 - The Girl Is Mine.mp3", "r3", "rel-orig", 3),               # moves in
         (KEEP, "01 - Wanna Be Startin' Somethin'.mp3", "r1", "rel-25", 1),
         (KEEP, "02 - Thriller.mp3", "r4", "rel-25", 2),
         (KEEP, "03 - Baby Be Mine.mp3", "r2", "rel-25", 3)]
FLAC = f"{ORIG}/01 - Wanna Be Startin' Somethin'.flac"
GIRL = f"{ORIG}/03 - The Girl Is Mine.mp3"
BEATEN = f"{KEEP}/01 - Wanna Be Startin' Somethin'.mp3"


def track_of(rid: str, rec: str) -> int:
    return next(t["position"] for t in RELEASES[rid]["media"][0]["tracks"]
                if t["recording"]["id"] == rec)


def celebrity() -> dict:
    """NSYNC's album, in a release group and recordings of its own."""
    rel = release(tracks=[("Pop", 3.0), ("Celebrity", 4.0), ("The Game Is Over", 5.0)])
    rel["release-group"]["id"] = "rg-nsync"
    for t in rel["media"][0]["tracks"]:
        t["recording"]["id"] = "n" + t["recording"]["id"]
    return rel


def stamp(days_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * 86400))


class LibraryTestCase(DbTestCase):
    """Real files, the real scan, registry, checks and executors; only
    MusicBrainz and Last.fm are faked, and Plex is not configured."""

    releases = RELEASES

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.metadata import runner as runmod
        self.runner = runmod.Runner()

    @contextlib.contextmanager
    def services(self):
        from app import library
        from app.metadata import lastfm, musicbrainz as mb
        rels = self.releases
        with mock.patch.object(mb, "enabled", return_value=True), \
                mock.patch.object(mb, "release", mock.AsyncMock(
                    side_effect=lambda m: copy.deepcopy(rels.get(m)))), \
                mock.patch.object(mb, "release_group", mock.AsyncMock(return_value=None)), \
                mock.patch.object(mb, "search_releases", mock.AsyncMock(return_value=[])), \
                mock.patch.object(mb, "isrc_releases", mock.AsyncMock(return_value=[])), \
                mock.patch.object(mb, "artist", mock.AsyncMock(return_value={})), \
                mock.patch.object(mb, "artist_aliases", mock.AsyncMock(return_value=[])), \
                mock.patch.object(lastfm, "album_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(lastfm, "artist_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(library, "_refresh_plex", mock.AsyncMock()), \
                mock.patch.object(self.runner, "publish", mock.AsyncMock()), \
                mock.patch.object(self.runner, "publish_issues", mock.AsyncMock()):
            yield

    async def make_thriller(self, extra=()):
        from app import library
        for folder, name, rec, rid, n in [*FILES, *extra]:
            path = self.lib / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            make_audio(path, SONGS[rec][1])
            tagio.write(path, {
                "title": [SONGS[rec][0]], "artist": ["Michael Jackson"],
                "albumartist": ["Michael Jackson"], "album": [RELEASES[rid]["title"]],
                "tracknumber": [str(n)], "mb_albumid": [rid], "mb_releasegroupid": ["rg-1"],
                "mb_trackid": [rec], "mb_releasetrackid": [f"{rid}-t{n}"]})
        await library.scan(full=True)

    async def check_all(self) -> list[int]:
        """A pass of the cycle: scan, registry, every album checked."""
        from app import library
        await library.scan(full=False)
        await units.sync_registry()
        auto = []
        for r in await self.db.fetchall("SELECT key FROM md_albums ORDER BY key"):
            auto += (await self.runner.check_unit(r["key"])).get("auto_ids") or []
        return auto

    async def plan_copies(self) -> list[dict]:
        await issues.save(await issues.plan_album_copies(), scope_kinds=("album_copies",),
                          all_scope=True)
        return [issues.issue_out(r) for r in await self.db.fetchall(
            "SELECT * FROM md_issues WHERE kind = 'album_copies' AND state IN ('open', 'failed')")]

    async def merge(self) -> dict:
        """Apply Thriller's merge, as the user does."""
        planned = [i for i in await self.plan_copies() if "rg-1" in i["key"]]
        self.assertEqual(len(planned), 1, planned)
        self.assertEqual(planned[0]["detail"]["keep"], KEEP)
        await self.runner._files_pass([planned[0]["id"]])
        return await self.issue(planned[0]["id"])

    async def issue(self, issue_id: int) -> dict:
        return dict(await self.db.fetchone("SELECT * FROM md_issues WHERE id = ?", (issue_id,)))

    async def apply(self, issue: dict) -> None:
        """One fix through the runner's file stage."""
        from app.metadata import apply
        _, _, state = await self.runner._start_album(issue["album_key"], [issues.issue_out(issue)])
        await self.runner._apply_simple(state["files"], state["ctx"], apply.FILE_STAGE)

    async def history(self, path: str, there: str, days_ago: float) -> None:
        """Journal path -> there, then there -> path a day later."""
        for src, dst, age in ((path, there, days_ago + 1), (there, path, days_ago)):
            await self.db.execute(
                "INSERT INTO md_changes (batch_id, target, ref, artist_dir, album_key, field, "
                "old_value, new_value, summary, applied_at) VALUES (?, 'move', ?, ?, ?, 'file', "
                "?, ?, ?, ?)",
                (uuid.uuid4().hex[:12], src, src.split("/", 1)[0], src.rsplit("/", 1)[0],
                 json.dumps(src), json.dumps(dst), f"Moved {Path(src).name}", stamp(age)))

    async def open_moves(self) -> list[tuple]:
        return [tuple(r) for r in await self.db.fetchall(
            "SELECT key, state FROM md_issues WHERE kind IN ('move', 'move_conflict') "
            "AND state IN ('open', 'queued', 'failed')")]

    async def split_keys(self) -> list[str]:
        return [r["key"] for r in await self.db.fetchall(
            "SELECT key FROM md_albums WHERE key LIKE '%|%'")]

    async def trashed(self) -> set[str]:
        return {r["original_path"] for r in await self.db.fetchall(
            "SELECT original_path FROM library_trash WHERE restored_at IS NULL")}

    def tags(self, rel: str) -> dict:
        return tagio.read(self.lib / rel)["fields"]


# ------------------------------------------------------------------ the root fix
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class MergeStaysMergedTestCase(LibraryTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.make_thriller()

    async def test_merge_then_check_then_auto_apply_plans_no_move(self):
        from app.metadata import apply
        with self.services():
            await self.check_all()
            merged = await self.merge()
            self.assertEqual(merged["state"], "applied", merged["error"])
            # the next check finds one album in the keeper's folder, and
            # nothing that would take the files moved in back out
            auto = await self.check_all()
            self.assertEqual(await self.split_keys(), [])
            self.assertEqual(await self.open_moves(), [])
            if auto:
                await self.runner._files_pass(auto)
            await self.check_all()
            self.assertEqual(await self.split_keys(), [])
            self.assertEqual(await self.open_moves(), [])

            # the files moved in are tagged as the keeper's tracks
            moved = [json.loads(r["new_value"]) for r in await self.db.fetchall(
                "SELECT new_value FROM md_changes WHERE target = 'move' AND field = 'file' "
                "AND summary LIKE 'Merged %'")]
            self.assertEqual(sorted(moved), [f"{KEEP}/01 - Wanna Be Startin' Somethin'.flac",
                                             f"{KEEP}/04 - The Girl Is Mine.mp3"])
            for path in moved:
                rec = "r1" if path.endswith(".flac") else "r3"
                n = track_of("rel-25", rec)
                got = self.tags(path)
                with self.subTest(path=path):
                    self.assertEqual(got.get("album"), ["Thriller 25"])
                    self.assertEqual(got.get("albumartist"), ["Michael Jackson"])
                    self.assertEqual(got.get("mb_albumid"), ["rel-25"])
                    self.assertEqual(got.get("mb_releasegroupid"), ["rg-1"])
                    self.assertEqual(got.get("mb_albumartistid"), ["mj"])
                    self.assertEqual(got.get("mb_trackid"), [rec])
                    self.assertEqual(got.get("mb_releasetrackid"), [f"rel-25-t{n}"])
                    self.assertEqual(got.get("tracknumber"), [str(n)])
                    self.assertEqual(got.get("discnumber"), ["1"])

            # the keeper is still pinned to its release
            row = await self.db.fetchone("SELECT manual, release_mbid FROM md_albums WHERE key = ?",
                                         (KEEP,))
            self.assertEqual(tuple(row), (1, "rel-25"))

            # nothing is left to merge, now or on the next pass
            rows = await units.load_unit(KEEP)
            want = json.loads((await self.db.fetchone(
                "SELECT desired FROM md_albums WHERE key = ?", (KEEP,)))["desired"])
            for issue in [merged] + [dict(i) for i in await self.plan_copies()]:
                detail = issue["detail"] if isinstance(issue["detail"], dict) \
                    else json.loads(issue["detail"])
                plan = await apply.copies_plan({"detail": detail}, apply.Ctx(KEEP, rows, want,
                                                                             "Michael Jackson"))
                self.assertEqual((plan["moves"], plan["trash"]), ([], []))
            for issue in await self.plan_copies():
                await self.runner._files_pass([issue["id"]])

        # the review's oscillation check (§7), over all time
        again = await self.db.fetchall(
            "SELECT album_key, COUNT(*) FROM (SELECT batch_id, album_key FROM md_changes "
            "WHERE summary LIKE 'Merged %copies into%' GROUP BY batch_id) "
            "GROUP BY album_key HAVING COUNT(*) > 1")
        self.assertEqual([tuple(r) for r in again], [])

    async def test_a_failed_tag_write_neither_moves_nor_trashes_that_track(self):
        real_write = tagio.write

        def write(path, fields, **kwargs):
            if str(path).endswith(FLAC):
                raise tagio.TagError("the share said no")
            return real_write(path, fields, **kwargs)

        with self.services():
            await self.check_all()
            with mock.patch.object(tagio, "write", write):
                await self.merge()
        # the flac stays, untouched, and the copy it would have replaced too
        self.assertTrue((self.lib / FLAC).exists())
        self.assertEqual(self.tags(FLAC).get("album"), ["Thriller"])
        self.assertTrue((self.lib / BEATEN).exists())
        self.assertNotIn(BEATEN, await self.trashed())
        # the rest of the merge went ahead
        self.assertTrue((self.lib / KEEP / "04 - The Girl Is Mine.mp3").exists())
        self.assertFalse((self.lib / GIRL).exists())
        self.assertEqual(await self.trashed(), {f"{ORIG}/02 - Baby Be Mine.mp3"})

    async def test_undo_puts_files_tags_and_trash_back(self):
        from app.metadata import apply
        before = {f"{folder}/{name}": self.tags(f"{folder}/{name}")
                  for folder, name, *_ in FILES}
        with self.services():
            await self.check_all()
            merged = await self.merge()
            self.assertEqual(merged["state"], "applied", merged["error"])
            self.assertEqual(self.tags(f"{KEEP}/04 - The Girl Is Mine.mp3").get("album"),
                             ["Thriller 25"])
            batch = (await self.db.fetchone(
                "SELECT batch_id FROM md_changes WHERE summary LIKE 'Merged %' LIMIT 1"))["batch_id"]
            result = await apply.revert(batch)
        self.assertFalse(result["failures"], result)
        for path, tags in before.items():
            with self.subTest(path=path):
                self.assertTrue((self.lib / path).exists())
                self.assertEqual(self.tags(path), tags)
        self.assertFalse((self.lib / KEEP / "04 - The Girl Is Mine.mp3").exists())
        self.assertEqual(await self.trashed(), set())
        # and the folder is no longer held as the merge's
        self.assertFalse(await issues.merged_into(KEEP))


# ------------------------------------------------------------------ the breaker
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class BreakerTestCase(LibraryTestCase):
    OLD = "NSYNC/old"
    HOME = "NSYNC/(2001) Celebrity"
    releases = {"rel-1": celebrity(), **RELEASES}

    async def make_nsync(self, *, copy_at_home=False):
        from app import library
        files = [(self.lib / self.OLD / "01 Pop.flac", "t1")]
        if copy_at_home:
            files.append((self.lib / self.HOME / "01 - Pop.mp3", "t1"))
        for path, track_id in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            make_audio(path, 3.0)
            tagio.write(path, {"title": ["Pop"], "artist": ["*NSYNC"], "albumartist": ["*NSYNC"],
                               "album": ["Celebrity"], "mb_albumid": ["rel-1"],
                               "mb_releasetrackid": [track_id]})
        await library.scan(full=True)
        self.a, self.b = f"{self.OLD}/01 Pop.flac", f"{self.HOME}/01 - Pop.flac"

    async def planned(self, kind: str, key: str) -> dict:
        row = await self.db.fetchone("SELECT * FROM md_issues WHERE kind = ? AND album_key = ?",
                                     (kind, key))
        self.assertIsNotNone(row, f"no {kind} planned for {key}")
        return dict(row)

    async def test_a_file_that_went_a_b_a_is_not_moved_again(self):
        await self.make_nsync()
        with self.services():
            first = await self.runner._check_unit(self.OLD)
            move = await self.planned("move", self.OLD)
            self.assertIn(move["id"], first["auto_ids"])
            await self.history(self.a, self.b, days_ago=2)
            await self.apply(move)
            after = await self.issue(move["id"])
            self.assertEqual(after["state"], "failed")
            self.assertIn("oscillating between", after["error"])
            self.assertTrue((self.lib / self.a).exists())
            self.assertFalse((self.lib / self.b).exists())
            paused = await self.db.fetchone("SELECT value FROM md_state WHERE key = ?",
                                            (f"auto_paused:{self.OLD}",))
            self.assertIsNotNone(paused)
            self.assertEqual((await self.runner._check_unit(self.OLD))["auto_ids"], [])
            # it is the pause that holds the album's other fixes back
            await self.db.execute("DELETE FROM md_state WHERE key = ?", (f"auto_paused:{self.OLD}",))
            self.assertNotEqual((await self.runner._check_unit(self.OLD))["auto_ids"], [])

    async def test_moves_older_than_the_window_are_allowed(self):
        await self.make_nsync()
        with self.services():
            await self.runner._check_unit(self.OLD)
            move = await self.planned("move", self.OLD)
            await self.history(self.a, self.b, days_ago=15)
            await self.apply(move)
            after = await self.issue(move["id"])
        self.assertEqual(after["state"], "applied", after["error"])
        self.assertTrue((self.lib / self.b).exists())

    async def test_merges_are_refused_before_anything_is_trashed(self):
        from app.metadata import apply
        # a blocked move's merge
        await self.make_nsync(copy_at_home=True)
        with self.services():
            await self.runner._check_unit(self.OLD)
            conflict = await self.planned("move_conflict", self.OLD)
            await self.history(self.a, self.b, days_ago=2)
            await self.apply(conflict)
            after = await self.issue(conflict["id"])
        self.assertEqual(after["state"], "failed")
        self.assertIn("oscillating between", after["error"])
        self.assertTrue((self.lib / self.a).exists())
        self.assertTrue((self.lib / self.HOME / "01 - Pop.mp3").exists())
        self.assertEqual(await self.trashed(), set())

        # a merge of copies
        await self.make_thriller()
        before = {f"{folder}/{name}": self.tags(f"{folder}/{name}") for folder, name, *_ in FILES}
        with self.services():
            await self.check_all()
            await self.history(GIRL, f"{KEEP}/04 - The Girl Is Mine.mp3", days_ago=1)
            merged = await self.merge()
        self.assertEqual(merged["state"], "failed")
        self.assertIn("oscillating between", merged["error"])
        self.assertEqual(await self.trashed(), set())
        for path, tags in before.items():
            with self.subTest(path=path):
                self.assertTrue((self.lib / path).exists())
                self.assertEqual(self.tags(path), tags)
        written = await self.db.fetchone("SELECT COUNT(*) AS n FROM md_changes WHERE target = 'file'")
        self.assertEqual(written["n"], 0)
        self.assertIsNotNone(await apply.auto_paused(KEEP))


# ------------------------------------------------------------------ the layout guard
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class LayoutGuardTestCase(LibraryTestCase):
    async def planned_moves(self) -> list[dict]:
        return [issues.issue_out(r) for r in await self.db.fetchall(
            "SELECT * FROM md_issues WHERE kind IN ('move', 'move_conflict') "
            "AND state IN ('open', 'queued', 'failed') ORDER BY id")]

    async def test_no_move_out_of_a_folder_just_merged_into(self):
        await self.make_thriller()
        with self.services():
            await self.check_all()
            merged = await self.merge()
            self.assertEqual(merged["state"], "applied", merged["error"])
            # a misnamed file in the keeper's folder is still renamed in it
            (self.lib / KEEP / "03 - Baby Be Mine.mp3").rename(self.lib / KEEP / "3 Baby.mp3")
            await self.check_all()
            moves = await self.planned_moves()
            self.assertEqual([(m["kind"], m["detail"]["files"]) for m in moves], [("move", [
                {"from": f"{KEEP}/3 Baby.mp3", "to": f"{KEEP}/03 - Baby Be Mine.mp3"}])])
            # a file there that still says it is the original (moved in by an
            # older merge, or a crash between the tag write and the move) is
            # an album of its own that belongs in the original's folder
            moved_in = f"{KEEP}/01 - Wanna Be Startin' Somethin'.flac"
            tagio.write(self.lib / moved_in, {"album": ["Thriller"], "mb_albumid": ["rel-orig"],
                                              "mb_releasetrackid": ["rel-orig-t1"]})
            later = time.time() + 10       # the scan counts a change within a second as none
            os.utime(self.lib / moved_in, (later, later))
            await self.check_all()
            split =[k for k in await self.split_keys() if k.endswith("|thriller")]
            self.assertEqual(len(split), 1, await self.split_keys())
            row = await self.db.fetchone("SELECT status, desired FROM md_albums WHERE key = ?",
                                         (split[0],))
            self.assertEqual(row["status"], "resolved")
            self.assertEqual(json.loads(row["desired"])["folder"], ORIG)
            moves = await self.planned_moves()
        # but nothing is planned to leave the folder
        self.assertEqual([m for m in moves if m["album_key"] == split[0]], [])
        self.assertEqual([m for m in moves if m["detail"]["to"].casefold() != KEEP.casefold()], [])


# ------------------------------------------------------------------ while a merge runs
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class MergeMeanwhileTestCase(LibraryTestCase):
    """What a cycle running beside a merge sees, and what a tag write that
    goes wrong leaves behind."""
    # the original has a track of its own, which stays in its folder, and
    # the reissue one more, so it is still the keeper
    releases = {"rel-orig": thriller("rel-orig", "Thriller", "1982-11-30", ["r1", "r2", "r3", "r9"]),
                "rel-25": RELEASES["rel-25"]}
    EXTRA = [(ORIG, "04 - The Lady in My Life.mp3", "r9", "rel-orig", 4),
             (KEEP, "05 - For All Time.mp3", "r5", "rel-25", 5)]

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.make_thriller(self.EXTRA)

    async def test_a_cycle_meanwhile_sees_no_folder_split_and_keeps_the_pin(self):
        from app import library
        from app.metadata import apply
        real_trash, real_journal = library.trash_paths, apply.journal
        seen = []

        async def a_cycle_meanwhile(where: str) -> None:
            # the cycle's registry step, landing at one of the merge's awaits
            await units.sync_registry()
            seen.append((where, sorted(r["key"] for r in await self.db.fetchall(
                "SELECT key FROM md_albums WHERE artist_dir = 'Michael Jackson'"))))

        async def trash(paths, reason=""):
            await a_cycle_meanwhile("before the trash")
            return await real_trash(paths, reason=reason)

        async def journal(batch, target, ref, *args, **kwargs):
            if target == "move":
                await a_cycle_meanwhile(f"after moving {Path(ref).name}")
            return await real_journal(batch, target, ref, *args, **kwargs)

        with self.services():
            await self.check_all()
            # the user accepted the original's match
            await self.db.execute("UPDATE md_albums SET manual = 1 WHERE key = ?", (ORIG,))
            with mock.patch.object(library, "trash_paths", trash), \
                    mock.patch.object(apply, "journal", journal):
                merged = await self.merge()
            self.assertEqual(merged["state"], "applied", merged["error"])
            await units.sync_registry()         # the next cycle
            after = await self.db.fetchone("SELECT manual, release_mbid FROM md_albums "
                                           "WHERE key = ?", (ORIG,))
        self.assertGreaterEqual(len(seen), 3, seen)
        for where, keys in seen:
            with self.subTest(where=where):
                self.assertEqual(keys, [ORIG, KEEP])
        self.assertIsNotNone(after)
        self.assertEqual(tuple(after), (1, "rel-orig"))
        self.assertTrue((self.lib / ORIG / "04 - The Lady in My Life.mp3").exists())

    def shrink_after_writing(self, path: str):
        """tagio, reading the audio of `path` 0.6 s shorter right after a
        tag write (an undo's write aside)."""
        real_write, real_read = tagio.write, tagio.read
        due = set()

        def write(p, fields, **kwargs):
            result = real_write(p, fields, **kwargs)
            if str(p).endswith(path) and kwargs.get("strip_junk", True):
                due.add(str(p))
            return result

        def read(p):
            out = real_read(p)
            if str(p) in due:
                due.discard(str(p))
                out["info"]["length"] -= 0.6
            return out

        return mock.patch.object(tagio, "write", write), mock.patch.object(tagio, "read", read)

    async def test_a_write_that_changes_the_length_is_put_back(self):
        before = self.tags(FLAC)
        write, read = self.shrink_after_writing(FLAC)
        with self.services():
            await self.check_all()
            with write, read:
                merged = await self.merge()
        self.assertEqual(merged["state"], "applied", merged["error"])
        # the flac is left as it was, and so is the copy it would have replaced
        self.assertTrue((self.lib / FLAC).exists())
        self.assertEqual(self.tags(FLAC), before)
        self.assertTrue((self.lib / BEATEN).exists())
        self.assertNotIn(BEATEN, await self.trashed())
        written = await self.db.fetchone("SELECT COUNT(*) AS n FROM md_changes "
                                         "WHERE target = 'file' AND ref = ?", (FLAC,))
        self.assertEqual(written["n"], 0)
        # the rest of the merge went ahead
        self.assertTrue((self.lib / KEEP / "04 - The Girl Is Mine.mp3").exists())

    async def test_a_write_that_cannot_be_put_back_is_journalled(self):
        from app.metadata import apply
        before = self.tags(FLAC)
        write, read = self.shrink_after_writing(FLAC)
        with self.services():
            await self.check_all()
            with write, read, mock.patch.object(tagio, "restore",
                                                side_effect=tagio.TagError("the share said no")):
                merged = await self.merge()
            self.assertEqual(merged["state"], "applied", merged["error"])
            self.assertEqual(self.tags(FLAC).get("album"), ["Thriller 25"])
            change = await self.db.fetchone("SELECT batch_id, old_value FROM md_changes "
                                            "WHERE target = 'file' AND ref = ?", (FLAC,))
            self.assertIsNotNone(change, "the write that stayed is not in the journal")
            self.assertNotIn(BEATEN, await self.trashed())
            result = await apply.revert(change["batch_id"])
        self.assertFalse(result["failures"], result)
        self.assertEqual(self.tags(FLAC), before)

    async def test_a_tags_fix_that_changes_the_length_is_put_back_too(self):
        from app.metadata import apply
        before = self.tags(GIRL)
        want = {"tracks": {GIRL: {"tags": {"album": ["Thriller (Special Edition)"]}}}}
        ctx = apply.Ctx(ORIG, await units.load_unit(ORIG), want, "Michael Jackson")
        write, read = self.shrink_after_writing(GIRL)
        with write, read, self.assertRaisesRegex(apply.ApplyError, "audio length changed"):
            await apply.do_tags({"kind": "tags", "detail": {}}, ctx)
        self.assertEqual(self.tags(GIRL), before)
        # and one that can't be put back is in the journal, to be undone
        write, read = self.shrink_after_writing(GIRL)
        with write, read, mock.patch.object(tagio, "restore", side_effect=OSError("busy")), \
                self.assertRaises(apply.ApplyError):
            await apply.do_tags({"kind": "tags", "detail": {}}, ctx)
        self.assertEqual(self.tags(GIRL).get("album"), ["Thriller (Special Edition)"])
        with self.services():
            result = await apply.revert(ctx.batches[-1])
        self.assertFalse(result["failures"], result)
        self.assertEqual(self.tags(GIRL), before)


if __name__ == "__main__":
    unittest.main()
