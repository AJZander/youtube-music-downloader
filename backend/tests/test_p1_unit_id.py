"""Albums that keep who they are: a unit's identity (md_albums.unit_id) and
the user's decisions on it survive a move, a merge into another folder, an
artist merge and a split folder; issues are keyed on the unit, not its
folder; and a merged-away album sends its page on to the album it joined.

Bliss n Eso's Circus in the Sky was accepted as its release at 06:01 and
asked about again at 06:22, under its new folder's key (review 2026-11,
4.A item 2).

Run with: python -m unittest discover backend/tests"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.engine import tagio
from app.metadata import issues, units
from test_metadata import HAVE_FFMPEG, make_audio, release
from test_p0_oscillation import KEEP, ORIG, RELEASES, SONGS, LibraryTestCase, stamp

MJ = "Michael Jackson"
RIP = f"{MJ}/Thriller 25 rip"
BONUS = f"{MJ}/Bonus Disc"


def celebrity() -> dict:
    rel = release(tracks=[("Pop", 3.0), ("Celebrity", 4.0), ("The Game Is Over", 5.0)])
    rel["release-group"]["id"] = "rg-nsync"
    for t in rel["media"][0]["tracks"]:
        t["recording"]["id"] = "n" + t["recording"]["id"]
    return rel


class UnitTestCase(LibraryTestCase):
    """LibraryTestCase, plus files written to order."""

    def write(self, rel: str, *, seconds: float, **tags) -> None:
        path = self.lib / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        make_audio(path, seconds)
        tagio.write(path, {k: [v] for k, v in tags.items()})

    def thriller_file(self, folder: str, name: str, rec: str, n: int, *, album="Thriller 25"):
        self.write(f"{folder}/{name}", seconds=SONGS[rec][1], title=SONGS[rec][0], artist=MJ,
                   albumartist=MJ, album=album, tracknumber=str(n), mb_albumid="rel-25",
                   mb_releasegroupid="rg-1", mb_trackid=rec, mb_releasetrackid=f"rel-25-t{n}")

    async def record(self, key: str) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM md_albums WHERE key = ?", (key,))
        return dict(row) if row else None

    async def open_issue(self, kind: str, key: str) -> dict:
        row = await self.db.fetchone(
            "SELECT * FROM md_issues WHERE kind = ? AND album_key = ? "
            "AND state IN ('open', 'failed')", (kind, key))
        self.assertIsNotNone(row, f"no open {kind} for {key}")
        return dict(row)

    async def apply_kinds(self, key: str, kinds: tuple[str, ...]) -> None:
        """The album's open fixes of these kinds, through the file stage."""
        from app.metadata import apply
        members = [issues.issue_out(r) for r in await self.db.fetchall(
            f"SELECT * FROM md_issues WHERE album_key = ? AND state IN ('open', 'failed') "
            f"AND kind IN ({','.join('?' * len(kinds))})", (key, *kinds))]
        self.assertEqual(sorted(i["kind"] for i in members), sorted(kinds))
        _, _, state = await self.runner._start_album(key, members)
        await self.runner._apply_simple(state["files"], state["ctx"], apply.FILE_STAGE)

    async def rescan(self) -> dict:
        from app import library
        await library.scan(full=False)
        return await units.sync_registry()


# ------------------------------------------------------------------ decisions
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class DecisionsTestCase(UnitTestCase):

    async def test_a_dismissal_survives_a_move(self):
        from app import library
        from app.metadata import routes
        for n, rec in enumerate(["r1", "r4", "r2", "r3"], start=1):
            self.thriller_file(RIP, f"{n} {SONGS[rec][0]}.mp3", rec, n)
        self.write(f"{RIP}/Bonus.mp3", seconds=9.0, title="Human Nature", artist=MJ,
                   albumartist=MJ, album="Thriller 25")
        await library.scan(full=True)
        with self.services():
            await self.check_all()
            extra = await self.open_issue("extra_tracks", RIP)
            move = await self.open_issue("move", RIP)
            self.assertEqual(json.loads(move["detail"])["to"], KEEP)
            await routes.dismiss_issues(routes.IdsIn(ids=[extra["id"]]))
            await self.apply_kinds(RIP, ("move",))
            self.assertEqual((await self.issue(move["id"]))["state"], "applied")
            self.assertTrue((self.lib / KEEP / "Bonus.mp3").exists())
            await self.rescan()
            await self.runner.check_unit(KEEP)
        after = await self.issue(extra["id"])
        self.assertEqual(after["state"], "dismissed")
        self.assertEqual(after["album_key"], KEEP)
        reopened = await self.db.fetchall(
            "SELECT key, album_key FROM md_issues WHERE kind = 'extra_tracks' "
            "AND state IN ('open', 'failed', 'queued')")
        self.assertEqual([tuple(r) for r in reopened], [])

    async def test_an_accepted_match_survives_a_move_into_an_existing_key(self):
        from app import library
        # the keeper's folder holds tracks 1-2; a second folder, named
        # nothing like the release, holds 3-5 and is only a likely match
        for n, rec in enumerate(["r1", "r4"], start=1):
            self.thriller_file(KEEP, f"{n:02d} - {SONGS[rec][0]}.mp3", rec, n)
        for n, rec in ((3, "r2"), (4, "r3"), (5, "r5")):
            self.thriller_file(BONUS, f"{n} {SONGS[rec][0]}.mp3", rec, n, album="Bonus Disc")
        await library.scan(full=True)
        with self.services():
            await self.check_all()
            self.assertEqual((await self.record(KEEP))["status"], "resolved")
            a = await self.record(BONUS)
            self.assertEqual(a["status"], "review")
            b = await self.record(KEEP)
            review = await self.open_issue("review_match", BONUS)
            # the user accepts it: through the runner's decisions
            self.assertEqual(await self.runner.request_decide([review["id"]], "accept_match"), 1)
            await self.runner._decide([self.runner._decisions.pop()])
            self.assertEqual((await self.record(BONUS))["manual"], 1)
            # its fixes take it into the keeper's folder
            await self.apply_kinds(BONUS, ("tags", "move"))
            self.assertFalse((self.lib / BONUS).exists())
            await self.check_all()
        keeper = await self.record(KEEP)
        self.assertEqual((keeper["manual"], keeper["release_mbid"]), (1, "rel-25"))
        self.assertEqual(keeper["unit_id"], b["unit_id"])
        self.assertIsNone(await self.record(BONUS))
        alias = await self.db.fetchone("SELECT successor, old_key FROM md_unit_aliases "
                                       "WHERE unit_id = ?", (a["unit_id"],))
        self.assertEqual(tuple(alias), (b["unit_id"], BONUS))
        again = await self.db.fetchall("SELECT key FROM md_issues WHERE kind = 'review_match' "
                                       "AND state IN ('open', 'failed', 'queued')")
        self.assertEqual([r["key"] for r in again], [])

    async def test_an_artist_merge_keeps_the_albums_decisions(self):
        from app import library
        from app.metadata import desired, routes
        old, new = "Old/(2001) Celebrity", "New/(2001) Celebrity"
        for n, title in enumerate(("Pop", "Celebrity"), start=1):
            self.write(f"{old}/{n:02d} - {title}.mp3", seconds=3.0 + n, title=title,
                       artist="Old", albumartist="Old", album="Celebrity", tracknumber=str(n))
        self.write("New/(1999) Other/01 - Other.mp3", seconds=3.0, title="Other", artist="New",
                   albumartist="New", album="Other", tracknumber="1")
        await library.scan(full=True)
        with self.services():
            await self.check_all()
            unit = (await self.record(old))["unit_id"]
            unresolved = await self.open_issue("unresolved", old)
            await routes.dismiss_issues(routes.IdsIn(ids=[unresolved["id"]]))
            await routes.put_override(routes.OverrideIn(entity="album", ref=unit, field="genres",
                                                        value=["Pop"]))
            await routes.album_ignore(routes.IgnoreIn(key=unit, ignored=True))
            self.assertEqual((await self.record(old))["ignored"], 1)
            result = await library.merge_artist_folders(["Old"], "New")
            self.assertEqual(result["failures"], [])
            await self.rescan()
        after = await self.record(new)
        self.assertIsNotNone(after)
        self.assertEqual(after["unit_id"], unit)
        self.assertEqual(after["ignored"], 1)
        self.assertIsNone(await self.record(old))
        issue = await self.issue(unresolved["id"])
        self.assertEqual((issue["state"], issue["album_key"]), ("dismissed", new))
        self.assertEqual(await desired.overrides_for("album", unit), {"genres": ["Pop"]})
        stamped = await self.db.fetchall("SELECT path, unit_id FROM library_index "
                                         "WHERE dirpath = ?", (new,))
        self.assertEqual({r["unit_id"] for r in stamped}, {unit})

    async def test_a_split_folder_keeps_its_unit_and_a_rejoin_merges_it(self):
        from app.metadata import desired
        folder = "Hilltop Hoods/(2003) The Calling"
        names = [f"{n:02d} - Track {n}.mp3" for n in range(1, 5)]
        for n, name in enumerate(names, start=1):
            self.write(f"{folder}/{name}", seconds=2.0 + n, title=f"Track {n}",
                       artist="Hilltop Hoods", albumartist="Hilltop Hoods",
                       album="The Calling", tracknumber=str(n))
        await self.rescan()
        unit = (await self.record(folder))["unit_id"]

        bumps = iter((10, 100))

        async def retag(name: str, album: str) -> None:
            tagio.write(self.lib / folder / name, {"album": [album]})
            later = time.time() + next(bumps)   # the scan counts a change within a second as none
            os.utime(self.lib / folder / name, (later, later))

        # one file retagged to another album: the folder splits by album tag
        await retag(names[3], "Drinking from the Sun")
        counts = await self.rescan()
        keys = sorted(r["key"] for r in await self.db.fetchall("SELECT key FROM md_albums"))
        self.assertEqual(len(keys), 2, keys)
        sizes = {k: len(await units.load_unit(k)) for k in keys}
        big = max(keys, key=sizes.get)
        small = min(keys, key=sizes.get)
        self.assertEqual((sizes[big], sizes[small]), (3, 1))
        self.assertEqual((await self.record(big))["unit_id"], unit)
        piece = (await self.record(small))["unit_id"]
        self.assertNotEqual(piece, unit)
        self.assertEqual(counts["successors"], 1)
        # a decision on each piece
        await self.db.execute("UPDATE md_albums SET ignored = 1 WHERE key = ?", (small,))
        for ref, field, value in ((unit, "moods", ["Gritty"]), (piece, "genres", ["Hip Hop"]),
                                  (unit, "genres", ["Rap"])):
            await self.db.execute(
                "INSERT INTO md_overrides (entity, ref, field, value, created_at) "
                "VALUES ('album', ?, ?, ?, ?)", (ref, field, json.dumps(value), stamp(0)))

        # the tag put back: one album again, under the folder's unit
        await retag(names[3], "The Calling")
        counts = await self.rescan()
        self.assertEqual([r["key"] for r in await self.db.fetchall("SELECT key FROM md_albums")],
                         [folder])
        record = await self.record(folder)
        self.assertEqual(record["unit_id"], unit)
        self.assertEqual(record["ignored"], 1)
        self.assertEqual(counts["merged"], 1)
        # the survivor's own override wins a field both had
        self.assertEqual(await desired.overrides_for("album", unit),
                         {"moods": ["Gritty"], "genres": ["Rap"]})
        self.assertEqual(await desired.overrides_for("album", piece), {})
        alias = await self.db.fetchone("SELECT successor, old_key FROM md_unit_aliases "
                                       "WHERE unit_id = ?", (piece,))
        self.assertEqual(tuple(alias), (unit, small))

    async def test_a_pin_never_crosses_release_groups(self):
        for key, manual, rel, group in (("A/V", 1, "rel-1", "g1"), ("A/K", 0, None, "g2"),
                                        ("A/W", 1, "rel-2", "g3"), ("A/L", 0, None, "g3"),
                                        ("A/X", 1, "rel-3", "g4"), ("A/M", 0, None, None)):
            await self.db.execute(
                "INSERT INTO md_albums (key, dirpath, artist_dir, status, manual, release_mbid, "
                "releasegroup_mbid) VALUES (?, ?, 'A', ?, ?, ?, ?)",
                (key, key, "resolved" if manual else "review", manual, rel, group))
        uid = {r["key"]: r["unit_id"] for r in await self.db.fetchall(
            "SELECT key, unit_id FROM md_albums")}
        await units.merge_into(uid["A/V"], uid["A/K"], reason="test")
        await units.merge_into(uid["A/W"], uid["A/L"], reason="test")
        await units.merge_into(uid["A/X"], uid["A/M"], reason="test")
        got = {r["key"]: (r["manual"], r["release_mbid"]) for r in await self.db.fetchall(
            "SELECT key, manual, release_mbid FROM md_albums")}
        # another release group's pin stays behind; one of the same group, or
        # an album not known to be any group, takes it
        self.assertEqual(got, {"A/K": (0, None), "A/L": (1, "rel-2"), "A/M": (1, "rel-3")})

    async def test_a_merged_away_album_redirects_to_its_keeper(self):
        import httpx
        from fastapi import FastAPI
        from app.metadata import routes
        await self.make_thriller()
        with self.services():
            await self.check_all()
            copy_unit = (await self.record(ORIG))["unit_id"]
            keeper_unit = (await self.record(KEEP))["unit_id"]
            merged = await self.merge()
            self.assertEqual(merged["state"], "applied", merged["error"])
            await self.rescan()
            # the merge folded the copy's record into the keeper's (relocate)
            self.assertIsNone(await self.record(ORIG))
            app = FastAPI()
            app.include_router(routes.router)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://mdl") as client:
                answers = {ref: (await client.get("/api/v1/library/album",
                                                  params={"key": ref})).json()
                           for ref in (copy_unit, ORIG, keeper_unit, KEEP)}
        for ref in (copy_unit, ORIG):
            with self.subTest(ref=ref):
                self.assertEqual(answers[ref], {"redirect": {"unit_id": keeper_unit, "key": KEEP,
                                                             "artist_dir": MJ}})
        for ref in (keeper_unit, KEEP):
            with self.subTest(ref=ref):
                self.assertEqual((answers[ref]["key"], answers[ref]["unit_id"]),
                                 (KEEP, keeper_unit))

    async def test_an_auto_pause_follows_the_album(self):
        from app import library
        old, home = "NSYNC/old", "NSYNC/(2001) Celebrity"
        self.releases = {"rel-1": celebrity(), **RELEASES}
        self.write(f"{old}/01 Pop.flac", seconds=3.0, title="Pop", artist="*NSYNC",
                   albumartist="*NSYNC", album="Celebrity", mb_albumid="rel-1",
                   mb_releasetrackid="t1")
        await library.scan(full=True)
        a, b = f"{old}/01 Pop.flac", f"{home}/01 - Pop.flac"
        with self.services():
            await self.check_all()
            unit = (await self.record(old))["unit_id"]
            move = await self.open_issue("move", old)
            await self.history(a, b, days_ago=2)
            await self.apply_kinds(old, ("move",))
            self.assertIn("oscillating between", (await self.issue(move["id"]))["error"])
            paused = await self.db.fetchone("SELECT value FROM md_state WHERE key = ?",
                                            (f"auto_paused:{unit}",))
            self.assertIsNotNone(paused)
            # the user moves it anyway (an Apply they ask for runs)
            await self.db.execute("DELETE FROM md_changes WHERE target = 'move'")
            await self.apply_kinds(old, ("move",))
            self.assertTrue((self.lib / b).exists())
            await self.rescan()
            self.assertEqual((await self.record(home))["unit_id"], unit)
            self.assertEqual((await self.runner._check_unit(home))["auto_ids"], [])
            # it is the pause that holds the album's fixes back
            await self.db.execute("DELETE FROM md_state WHERE key = ?", (f"auto_paused:{unit}",))
            self.assertNotEqual((await self.runner._check_unit(home))["auto_ids"], [])

    # -------------------------------------------------- moved outside the app
    def some_album(self, folder: str, artist: str = "B") -> None:
        for n in (1, 2):
            self.write(f"{folder}/0{n} - T{n}.mp3", seconds=2.0 + n, title=f"T{n}",
                       artist=artist, albumartist=artist, album=folder.rsplit("/", 1)[-1],
                       tracknumber=str(n))

    async def test_a_folder_renamed_outside_the_app_keeps_its_decisions(self):
        """Renamed in Explorer, over SMB: the scan sees the files go and the
        same files come at new paths, and the album goes with them."""
        from app.metadata import desired, routes
        one, other = "B/Some Album", "B/Other"
        self.some_album(one)
        self.some_album(other)
        with self.services():
            await self.check_all()
            unit = (await self.record(one))["unit_id"]
            ignored = (await self.record(other))["unit_id"]
            unresolved = await self.open_issue("unresolved", one)
            await routes.dismiss_issues(routes.IdsIn(ids=[unresolved["id"]]))
            await routes.put_override(routes.OverrideIn(entity="album", ref=unit, field="genres",
                                                        value=["Rock"]))
            await routes.album_ignore(routes.IgnoreIn(key=ignored, ignored=True))
            os.rename(self.lib / one, self.lib / "B/(2001) Some Album")
            os.rename(self.lib / other, self.lib / "B/(1999) Other")
            await self.check_all()
        after = await self.record("B/(2001) Some Album")
        self.assertEqual(after["unit_id"], unit)
        issue = await self.issue(unresolved["id"])
        self.assertEqual((issue["state"], issue["album_key"]), ("dismissed", "B/(2001) Some Album"))
        self.assertEqual(await desired.overrides_for("album", unit), {"genres": ["Rock"]})
        moved = await self.record("B/(1999) Other")
        self.assertEqual((moved["unit_id"], moved["ignored"]), (ignored, 1))
        self.assertEqual(sorted(r["key"] for r in await self.db.fetchall(
            "SELECT key FROM md_albums")), ["B/(1999) Other", "B/(2001) Some Album"])
        reopened = await self.db.fetchall(
            "SELECT key FROM md_issues WHERE kind = 'unresolved' "
            "AND state IN ('open', 'failed', 'queued')")
        self.assertEqual([r["key"] for r in reopened], [])

    async def test_files_that_cannot_be_told_apart_start_afresh(self):
        """Two copies of an album's folder, the original gone: which one is
        the album isn't known, so neither takes it. Other files where an
        album was are another album."""
        old, swapped = "B/Some Album", "B/Swapped"
        self.some_album(old)
        self.some_album(swapped)
        await self.rescan()
        units_before = {r["key"]: r["unit_id"] for r in await self.db.fetchall(
            "SELECT key, unit_id FROM md_albums")}
        await self.db.execute("UPDATE md_albums SET ignored = 1")
        shutil.copytree(self.lib / old, self.lib / "B/Copy One")
        shutil.copytree(self.lib / old, self.lib / "B/Copy Two")
        shutil.rmtree(self.lib / old)
        shutil.rmtree(self.lib / swapped)
        self.some_album("B/Swapped Again", artist="C")     # not its files: other tags
        counts = await self.rescan()
        self.assertEqual((counts["successors"], counts["merged"]), (0, 0))
        after = {r["key"]: (r["unit_id"], r["ignored"]) for r in await self.db.fetchall(
            "SELECT key, unit_id, ignored FROM md_albums")}
        self.assertEqual(sorted(after), ["B/Copy One", "B/Copy Two", "B/Swapped Again"])
        for key, (uid, ignored) in after.items():
            with self.subTest(key=key):
                self.assertNotIn(uid, units_before.values())
                self.assertEqual(ignored, 0)

    # ------------------------------------------------ back where it merged from
    async def test_an_album_back_at_a_merged_away_key_opens_as_itself(self):
        """Thriller's copies are merged, the registry folds ORIG into KEEP,
        and the merge is undone: ORIG's files are back, and a link to ORIG
        opens ORIG (and its actions run), not KEEP."""
        from app.metadata import routes, views
        await self.make_thriller()
        with self.services():
            await self.check_all()
            keeper_unit = (await self.record(KEEP))["unit_id"]
            merged = await self.merge()
            self.assertEqual(merged["state"], "applied", merged["error"])
            await self.rescan()
            self.assertIsNone(await self.record(ORIG))
            self.assertIn("redirect", await views.album(ORIG))
            batch = await self.db.fetchone(
                "SELECT batch_id FROM md_changes WHERE summary LIKE 'Merged %' LIMIT 1")
            self.assertEqual((await routes.revert_batch(batch["batch_id"]))["failures"], [])
            self.assertTrue(await units.load_unit(ORIG))
            page = await views.album(ORIG)
            self.assertNotIn("redirect", page)
            self.assertEqual((page["key"], page["unit_id"]), (ORIG, None))
            checked = await routes.album_recheck(routes.KeyIn(key=ORIG))
        record = await self.record(ORIG)
        self.assertIsNotNone(record)
        self.assertEqual((checked["key"], checked["unit_id"]), (ORIG, record["unit_id"]))
        self.assertNotEqual(record["unit_id"], keeper_unit)
        self.assertEqual((await self.record(KEEP))["unit_id"], keeper_unit)

    # ------------------------------------------------- pins and release groups
    def album_files(self, folder: str, artist: str, numbers, group: str) -> list[str]:
        names = []
        for n in numbers:
            names.append(f"{folder}/{n:02d} - T{n}.mp3")
            self.write(names[-1], seconds=2.0 + n, title=f"T{n}", artist=artist,
                       albumartist=artist, album="Album", tracknumber=str(n),
                       mb_releasegroupid=group)
        return names

    async def test_a_pin_never_crosses_release_groups_on_an_artist_merge(self):
        """The mover's files are in the album's folder by the time the two
        records merge: they don't count as what that album is."""
        from app import library
        old, new = "Old/(2001) Album", "New/(2001) Album"
        self.album_files(new, "New", (1, 2), "rg-K")
        self.album_files(old, "Old", (3, 4, 5), "rg-V")
        await self.rescan()
        await self.db.execute("UPDATE md_albums SET status = 'resolved', manual = 1, "
                              "release_mbid = 'rel-V', releasegroup_mbid = 'rg-V' WHERE key = ?",
                              (old,))
        await self.db.execute("UPDATE md_albums SET status = 'unresolved' WHERE key = ?", (new,))
        with self.services():
            result = await library.merge_artist_folders(["Old"], "New")
            self.assertEqual(result["failures"], [])
            await self.rescan()
        # the merge folded Old's record into New's (relocate)
        self.assertIsNone(await self.record(old))
        self.assertEqual(len(await units.load_unit(new)), 5)
        keeper = await self.record(new)
        self.assertEqual((keeper["manual"], keeper["release_mbid"]), (0, None))

    async def test_a_pin_never_crosses_release_groups_on_a_move(self):
        """The same through an apply's move (apply.rekey_unit), whose moved
        files may not have been stamped with their album yet."""
        from app import library
        from app.metadata import apply
        folder = "A/(2001) Album"
        self.album_files(folder, "A", (1, 2), "rg-K")
        await self.rescan()
        moved = self.album_files(folder, "A", (3, 4, 5), "rg-V")    # just moved in
        await library.scan(full=False)
        await self.db.execute("INSERT INTO md_albums (key, dirpath, artist_dir, status, manual, "
                              "release_mbid, releasegroup_mbid) VALUES ('A/v', 'A/v', 'A', "
                              "'resolved', 1, 'rel-V', 'rg-V')")
        keeper = (await self.record(folder))["unit_id"]
        self.assertEqual(await apply.rekey_unit("A/v", folder, "A", moved), keeper)
        got = await self.record(folder)
        self.assertEqual((got["manual"], got["release_mbid"]), (0, None))
        stamped = await self.db.fetchall("SELECT DISTINCT unit_id FROM library_index")
        self.assertEqual([r["unit_id"] for r in stamped], [keeper])

    async def test_one_file_of_another_group_keeps_a_pin_behind(self):
        """Nothing says which files are whose (none stamped): an album whose
        files name another group as well as the pin's doesn't take the pin;
        one whose files name only the pin's group does."""
        from app import library
        mixed, same = "A/(2001) Album", "A/(2002) Album"
        self.album_files(mixed, "A", (1, 2), "rg-K")
        self.album_files(mixed, "A", (3, 4, 5), "rg-V")
        self.album_files(same, "A", (1, 2), "rg-V")
        await library.scan(full=True)
        for key in (mixed, same, "A/v", "A/w"):
            pinned = key in ("A/v", "A/w")
            await self.db.execute(
                "INSERT INTO md_albums (key, dirpath, artist_dir, status, manual, release_mbid, "
                "releasegroup_mbid) VALUES (?, ?, 'A', ?, ?, ?, ?)",
                (key, key, "resolved" if pinned else "unresolved", int(pinned),
                 "rel-V" if pinned else None, "rg-V" if pinned else None))
        uid = {r["key"]: r["unit_id"] for r in await self.db.fetchall(
            "SELECT key, unit_id FROM md_albums")}
        await units.merge_into(uid["A/v"], uid[mixed], reason="test")
        await units.merge_into(uid["A/w"], uid[same], reason="test")
        got = {r["key"]: (r["manual"], r["release_mbid"]) for r in await self.db.fetchall(
            "SELECT key, manual, release_mbid FROM md_albums")}
        self.assertEqual(got, {mixed: (0, None), same: (1, "rel-V")})


# ------------------------------------------------------------------ migration
class MigrationTestCase(unittest.IsolatedAsyncioTestCase):
    """v12 on a database left at v11, through the real connect()."""

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

    async def at_v11(self) -> None:
        from app import db as dbmod
        with mock.patch.object(dbmod, "MIGRATIONS", dbmod.MIGRATIONS[:11]):
            await self.db.connect()
        db = self.db
        for key in ("A/(2001) One", "A/Singles|b side", "A/Singles|a side"):
            await db.execute("INSERT INTO md_albums (key, dirpath, artist_dir) VALUES (?, ?, 'A')",
                             (key, units.unit_folder(key)))
        rows = [("extra_tracks", "A/(2001) One", "dismissed"),
                ("review_match", "A/(2001) One", "open"),
                ("tags", "A/Singles|b side", "applied"),
                ("unresolved", "A/Gone", "dismissed")]          # history: a vanished key
        for kind, key, state in rows:
            await db.execute(
                "INSERT INTO md_issues (key, kind, entity, ref, artist_dir, album_key, severity, "
                "summary, state, created_at, updated_at) VALUES (?, ?, 'album', ?, 'A', ?, "
                "'info', 's', ?, 'x', 'x')", (f"{kind}:{key}", kind, key, key, state))
        await db.execute(
            "INSERT INTO md_issues (key, kind, entity, ref, artist_dir, album_key, severity, "
            "summary, state, created_at, updated_at) VALUES ('missing_tracks:rel-9', "
            "'missing_tracks', 'album', 'rel-9', 'A', 'A/(2001) One', 'warning', 's', 'open', "
            "'x', 'x')")
        for ref in ("A/(2001) One", "A/Gone"):
            await db.execute("INSERT INTO md_overrides (entity, ref, field, value, created_at) "
                             "VALUES ('album', ?, 'genres', '[\"Pop\"]', 'x')", (ref,))
        await db.execute("INSERT INTO md_overrides (entity, ref, field, value, created_at) "
                         "VALUES ('artist', 'A', 'genres', '[\"Pop\"]', 'x')")
        for key in ("auto_paused:A/Singles|a side", "merge_keeper:a/(2001) one", "auto_apply"):
            await db.execute("INSERT INTO md_state (key, value) VALUES (?, '1')", (key,))
        await db.close()

    async def dump(self) -> dict:
        out = {}
        for table, order in (("md_albums", "key"), ("md_issues", "id"),
                             ("md_overrides", "entity, ref, field"), ("md_state", "key"),
                             ("schema_migrations", "version")):
            out[table] = [tuple(r) for r in await self.db.fetchall(
                f"SELECT * FROM {table} ORDER BY {order}")]
        return out

    async def test_existing_rows_get_unit_ids_and_their_issue_keys_follow(self):
        await self.at_v11()
        await self.db.connect()
        uid = {r["key"]: r["unit_id"] for r in await self.db.fetchall(
            "SELECT key, unit_id FROM md_albums")}
        self.assertEqual(len(set(uid.values())), 3)
        for value in uid.values():
            self.assertRegex(value, r"^[0-9a-f]{16}$")
        got = {r["kind"]: (r["key"], r["ref"], r["album_key"], r["state"])
               for r in await self.db.fetchall("SELECT * FROM md_issues")}
        one, b = uid["A/(2001) One"], uid["A/Singles|b side"]
        self.assertEqual(got, {
            "extra_tracks": (f"extra_tracks:{one}", one, "A/(2001) One", "dismissed"),
            "review_match": (f"review_match:{one}", one, "A/(2001) One", "open"),
            "tags": (f"tags:{b}", b, "A/Singles|b side", "applied"),
            "unresolved": ("unresolved:A/Gone", "A/Gone", "A/Gone", "dismissed"),
            "missing_tracks": ("missing_tracks:rel-9", "rel-9", "A/(2001) One", "open")})
        overrides = sorted((r["entity"], r["ref"]) for r in await self.db.fetchall(
            "SELECT entity, ref FROM md_overrides"))
        self.assertEqual(overrides, sorted([("album", one), ("album", "A/Gone"), ("artist", "A")]))
        state = sorted(r["key"] for r in await self.db.fetchall("SELECT key FROM md_state"))
        self.assertEqual(state, sorted([f"auto_paused:{uid['A/Singles|a side']}",
                                        "merge_keeper:a/(2001) one", "auto_apply"]))
        # a unit added later gets an id too, however it is inserted
        await self.db.execute("INSERT INTO md_albums (key, dirpath) VALUES ('B/x', 'B/x')")
        self.assertRegex((await self.db.fetchone(
            "SELECT unit_id FROM md_albums WHERE key = 'B/x'"))["unit_id"], r"^[0-9a-f]{16}$")
        before = await self.dump()
        await self.db.close()
        await self.db.connect()
        self.assertEqual(await self.dump(), before)

    async def test_an_interrupted_migration_leaves_nothing_half_done(self):
        from app import db as dbmod
        import sqlite3
        await self.at_v11()
        broken = dbmod.MIGRATIONS[:11] + [dbmod.MIGRATIONS[11] + "\nSELECT * FROM no_such_table;\n"]
        with mock.patch.object(dbmod, "MIGRATIONS", broken), \
                self.assertRaises(sqlite3.OperationalError):
            await self.db.connect()
        await self.db.close()
        conn = sqlite3.connect(self.tmp / "data" / "mdl.db")
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            columns = {r[1] for r in conn.execute("PRAGMA table_info(md_albums)")}
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            keys = {r[0] for r in conn.execute("SELECT key FROM md_issues")}
        finally:
            conn.close()
        self.assertEqual(version, 11)
        self.assertNotIn("unit_id", columns)
        self.assertNotIn("md_unit_aliases", tables)
        self.assertIn("extra_tracks:A/(2001) One", keys)
        # the real one then runs
        await self.db.connect()
        row = await self.db.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        self.assertEqual(row["v"], len(dbmod.MIGRATIONS))
        self.assertIsNone(await self.db.fetchone("SELECT 1 FROM md_albums WHERE unit_id IS NULL"))

    async def test_a_python_step_runs_in_one_transaction_with_its_version(self):
        from app import db as dbmod
        import sqlite3

        async def step(conn, fail=False):
            await conn.execute("CREATE TABLE later (x INTEGER)")
            await conn.execute("INSERT INTO later (x) VALUES (1)")
            if fail:
                raise RuntimeError("cut off")

        async def failing(conn):
            await step(conn, fail=True)

        with mock.patch.object(dbmod, "MIGRATIONS", dbmod.MIGRATIONS + [failing]), \
                self.assertRaises(RuntimeError):
            await self.db.connect()
        await self.db.close()
        conn = sqlite3.connect(self.tmp / "data" / "mdl.db")
        try:
            self.assertEqual(conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                             len(dbmod.MIGRATIONS))
            self.assertNotIn("later", {r[0] for r in conn.execute("SELECT name FROM sqlite_master")})
        finally:
            conn.close()
        with mock.patch.object(dbmod, "MIGRATIONS", dbmod.MIGRATIONS + [step]):
            await self.db.connect()
            await self.db.close()
            await self.db.connect()     # once only
        row = await self.db.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        self.assertEqual(row["v"], len(dbmod.MIGRATIONS) + 1)
        self.assertEqual((await self.db.fetchone("SELECT COUNT(*) AS n FROM later"))["n"], 1)


if __name__ == "__main__":
    unittest.main()
