"""The duplicates manager's actions update Needs attention at once: Trash,
"not duplicates", Restore and an artist merge check again the albums and
artists they touched, and the duplicate findings, before they return. The
issues used to stay as they were until the next cycle came round, so a
duplicate just trashed was still listed (review 2026-11, P1-A3). So does
undoing "not duplicates". An album trashed whole keeps its record, and what
was decided about it, for a Restore.

And an artist merge files an album folder the target already has, in
another case, under the target's spelling: the share ignores case, so the
files went into the target's folder while the index named the source's. A
source that is the target folder itself, spelled another way, is left
alone: its albums were each "already in the target", and went to Trash.

Run with: python -m unittest discover backend/tests"""
import contextlib
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.metadata import dupes, issues, units
from test_metadata import HAVE_FFMPEG
from test_p0_oscillation import BEATEN, KEEP
from test_p1_relocate import dangling
from test_p1_unit_id import UnitTestCase

# Not a state name: the states an issue closes with are being renamed (P1-A1)
OPEN = ("open", "queued", "failed")
PK = "Paul Kelly"
GOSSIP = f"{PK}/(1987) Gossip"
COPY = f"{GOSSIP}/02 - Darling It Hurts (1).mp3"
TITLES = ["Last Train to Heaven", "Darling It Hurts", "To Her Door"]


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class DupActionsTestCase(UnitTestCase):
    """Real files, scan, Trash, merge and handlers; the app's runner is this
    test's, and MusicBrainz is faked (no release matches these files)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app import library
        # the analysis is kept by a fingerprint of the index, which another
        # test's library can share
        library._analysis["token"] = None

    @contextlib.contextmanager
    def services(self):
        from app.metadata import musicbrainz as mb
        # the re-checks check artists too, and an artist no album names an
        # MBID for is looked up by name
        with super().services(), \
                mock.patch.object(mb, "search_artists", mock.AsyncMock(return_value=[])):
            yield

    @contextlib.contextmanager
    def handlers(self):
        from app.metadata import runner as runmod
        with self.services(), mock.patch.object(runmod, "runner", self.runner), \
                mock.patch.object(self.runner, "request_apply", mock.AsyncMock()):
            yield

    def album(self, folder: str, artist: str, album: str, titles: list[str]) -> None:
        for n, title in enumerate(titles, start=1):
            self.write(f"{folder}/{n:02d} - {title}.mp3", seconds=2.0 + n, title=title,
                       artist=artist, albumartist=artist, album=album, tracknumber=str(n))

    def gossip(self) -> None:
        """Gossip, its second track twice."""
        self.album(GOSSIP, PK, "Gossip", TITLES)
        self.write(COPY, seconds=4.0, title="Darling It Hurts", artist=PK, albumartist=PK,
                   album="Gossip", tracknumber="2")

    async def checked(self) -> None:
        """A cycle's pass: every album checked, then the duplicates."""
        await self.check_all()
        await dupes.sync()

    async def open_one(self, kind: str) -> dict:
        rows = await self.db.fetchall(
            f"SELECT * FROM md_issues WHERE kind = ? AND state IN {OPEN}", (kind,))
        self.assertEqual(len(rows), 1, [dict(r) for r in rows])
        return issues.issue_out(rows[0])

    async def state(self, issue_id: int) -> str:
        return (await self.issue(issue_id))["state"]

    async def unit_issues(self, key: str) -> list[dict]:
        marks = ",".join("?" * len(issues.UNIT_KINDS))
        return [dict(r) for r in await self.db.fetchall(
            f"SELECT * FROM md_issues WHERE album_key = ? AND state IN {OPEN} "
            f"AND kind IN ({marks})", (key, *issues.UNIT_KINDS))]

    async def assert_planned(self, key: str) -> None:
        """The album was checked as its files are now."""
        record = await self.record(key)
        self.assertIsNotNone(record, key)
        self.assertEqual(record["file_sig"], units.signature(await units.load_unit(key)))

    # ------------------------------------------------------------ Trash
    async def test_trashing_a_duplicate_closes_its_issue_at_once(self):
        from app import api
        self.gossip()
        with self.handlers():
            await self.checked()
            dup = await self.open_one("dup_track")
            self.assertIn(COPY, dup["detail"]["paths"])
            result = await api.library_trash(api.TrashIn(paths=[COPY]))
        self.assertEqual(result["failures"], [])
        self.assertNotIn(await self.state(dup["id"]), OPEN)
        await self.assert_planned(GOSSIP)
        self.assertEqual(await dangling(self.db), [])

    async def test_a_recheck_applies_nothing_and_leaves_auto_fixes_to_the_cycle(self):
        """Its fixes are the cycle's to queue, as after a check from the
        album page: an album with any stays due, so the next pass queues
        them as it would have queued them before (its files changed)."""
        from app import api
        await self.make_thriller()
        with self.handlers():
            self.assertTrue(await self.check_all())
            journal = (await self.db.fetchone("SELECT COUNT(*) AS n FROM md_changes"))["n"]
            await api.library_trash(api.TrashIn(paths=[BEATEN]))
            self.runner.request_apply.assert_not_called()
        self.assertEqual((await self.db.fetchone("SELECT COUNT(*) AS n FROM md_changes"))["n"],
                         journal)
        await self.assert_planned(KEEP)
        auto = await self.db.fetchall(
            "SELECT id FROM md_issues WHERE album_key = ? AND state = 'open' AND auto = 1",
            (KEEP,))
        self.assertTrue(auto)
        self.assertEqual((await self.record(KEEP))["next_check"], 0)

    # ------------------------------------------------------------ not duplicates
    async def test_not_duplicates_closes_the_group_at_once(self):
        from app import api
        other = f"{PK}/Gossip"
        self.album(GOSSIP, PK, "Gossip", TITLES)
        self.album(other, PK, "Gossip", TITLES)
        with self.handlers():
            await self.checked()
            dup = await self.open_one("dup_album")
            self.assertEqual(sorted(dup["detail"]["dirpaths"]), sorted([GOSSIP, other]))
            result = await api.library_add_exclusion(
                api.ExclusionIn(scope="album", keys=[GOSSIP, other], reason="test"))
        self.assertEqual(result["pairs"], 1)
        self.assertNotIn(await self.state(dup["id"]), OPEN)

    async def test_undoing_not_duplicates_reports_the_group_again_at_once(self):
        from app import api
        other = f"{PK}/Gossip"
        self.album(GOSSIP, PK, "Gossip", TITLES)
        self.album(other, PK, "Gossip", TITLES)
        with self.handlers():
            await self.checked()
            dup = await self.open_one("dup_album")
            await api.library_add_exclusion(
                api.ExclusionIn(scope="album", keys=[GOSSIP, other], reason="test"))
            self.assertNotIn(await self.state(dup["id"]), OPEN)
            ids = [i for item in (await api.library_list_exclusions())["items"]
                   for i in item["ids"]]
            result = await api.library_delete_exclusions(api.ExclusionDeleteIn(ids=ids))
        self.assertEqual(result["removed"], 1)
        again = await self.open_one("dup_album")
        self.assertEqual(sorted(again["detail"]["dirpaths"]), sorted([GOSSIP, other]))

    # ------------------------------------------------------------ Restore
    async def test_a_restore_reopens_what_the_trash_closed(self):
        from app import api
        self.gossip()
        with self.handlers():
            await self.checked()
            dup = await self.open_one("dup_track")
            self.assertTrue(await self.unit_issues(GOSSIP))
            unit_id = (await self.record(GOSSIP))["unit_id"]
            trashed = await api.library_trash(api.TrashIn(paths=[GOSSIP]))
            self.assertEqual(trashed["failures"], [])
            with self.subTest("the Trash closed them"):
                self.assertEqual(await self.unit_issues(GOSSIP), [])
                self.assertNotIn(await self.state(dup["id"]), OPEN)
            with self.subTest("and kept the album's record for a Restore"):
                self.assertEqual((await self.record(GOSSIP) or {}).get("unit_id"), unit_id)
            restored = await api.library_trash_restore(trashed["batch_id"])
        self.assertEqual(restored["failures"], [])
        self.assertIn(await self.state(dup["id"]), OPEN)
        self.assertTrue(await self.unit_issues(GOSSIP))
        await self.assert_planned(GOSSIP)
        self.assertEqual((await self.record(GOSSIP))["unit_id"], unit_id)
        self.assertEqual(await dangling(self.db), [])

    async def test_a_restore_keeps_what_was_decided_about_the_album(self):
        """Trashed whole and restored before the next cycle: the same album,
        ignored and pinned as it was (its overrides hang off its unit id)."""
        from app import api
        from app.metadata import desired, routes
        self.album(GOSSIP, PK, "Gossip", TITLES)
        with self.handlers():
            await self.check_all()
            unit_id = (await self.record(GOSSIP))["unit_id"]
            await routes.put_override(routes.OverrideIn(entity="album", ref=unit_id,
                                                        field="genres", value=["Pop"]))
            await routes.album_ignore(routes.IgnoreIn(key=unit_id, ignored=True))
            trashed = await api.library_trash(api.TrashIn(paths=[GOSSIP]))
            self.assertEqual(trashed["failures"], [])
            restored = await api.library_trash_restore(trashed["batch_id"])
            self.assertEqual(restored["failures"], [])
            await self.check_all()      # the next cycle
        record = await self.record(GOSSIP)
        self.assertEqual((record["unit_id"], record["ignored"]), (unit_id, 1))
        self.assertEqual(await desired.overrides_for("album", unit_id), {"genres": ["Pop"]})
        self.assertEqual(await dangling(self.db), [])

    async def test_a_restore_keeps_a_dismissal(self):
        from app import api
        from app.metadata import routes
        self.album(GOSSIP, PK, "Gossip", TITLES)
        with self.handlers():
            await self.check_all()
            unresolved = await self.open_issue("unresolved", GOSSIP)
            await routes.dismiss_issues(routes.IdsIn(ids=[unresolved["id"]]))
            trashed = await api.library_trash(api.TrashIn(paths=[GOSSIP]))
            await api.library_trash_restore(trashed["batch_id"])
            await self.check_all()      # the next cycle
        self.assertEqual(await self.unit_issues(GOSSIP), [])
        self.assertEqual((await self.issue(unresolved["id"]))["state"], "dismissed")

    # ------------------------------------------------------------ artist merge
    async def test_an_artist_merge_closes_the_split_and_moves_the_albums_issues(self):
        from app import api
        old, new = "Sam_Smith/(2017) The Thrill of It All", "Sam Smith/(2017) The Thrill of It All"
        self.album("Sam Smith/(2014) In the Lonely Hour", "Sam Smith", "In the Lonely Hour",
                   ["Money on My Mind", "Stay With Me"])
        self.album(old, "Sam Smith", "The Thrill of It All", ["Too Good at Goodbyes"])
        with self.handlers():
            await self.checked()
            split = await self.open_one("split_artist")
            self.assertTrue(await self.unit_issues(old))
            # the tags say Sam Smith already: none is rewritten, so nothing
            # the analysis used to notice changes (rows, read times, Trash)
            result = await api.library_merge_artists(
                api.MergeArtistsIn(sources=["Sam_Smith"], target="Sam Smith"))
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["retagged"], 0)
        self.assertNotIn(await self.state(split["id"]), OPEN)
        moved = await self.unit_issues(new)
        self.assertIn("unresolved", {i["kind"] for i in moved})
        self.assertEqual(await self.unit_issues(old), [])
        await self.assert_planned(new)
        self.assertEqual(await self.db.fetchall(
            f"SELECT id FROM md_issues WHERE artist_dir = 'Sam_Smith' AND state IN {OPEN}"), [])
        # no issue names a key without files
        self.assertEqual(await dangling(self.db), [])

    async def test_an_artist_merge_files_into_the_targets_spelling(self):
        from app import api
        target = "Sam Smith/(2014) In the Lonely Hour"
        source = "Sam_Smith/(2014) In The Lonely Hour"
        self.album(target, "Sam Smith", "In the Lonely Hour", ["Money on My Mind"])
        self.write(f"{source}/02 - Stay With Me.mp3", seconds=4.0, title="Stay With Me",
                   artist="Sam Smith", albumartist="Sam Smith", album="In the Lonely Hour",
                   tracknumber="2")
        with self.handlers():
            await self.check_all()
            result = await api.library_merge_artists(
                api.MergeArtistsIn(sources=["Sam_Smith"], target="Sam Smith"))
        self.assertEqual(result["failures"], [])
        moved = f"{target}/02 - Stay With Me.mp3"
        self.assertTrue((self.lib / moved).is_file())
        # one folder, spelled the target's way, on disk and in the index
        self.assertEqual(sorted(p.name for p in (self.lib / "Sam Smith").iterdir()),
                         ["(2014) In the Lonely Hour"])
        self.assertEqual({r["path"] for r in await self.db.fetchall(
            "SELECT path FROM library_index WHERE artist_dir = 'Sam Smith'")},
            {f"{target}/01 - Money on My Mind.mp3", moved})
        self.assertEqual([r["key"] for r in await self.db.fetchall(
            "SELECT key FROM md_albums WHERE artist_dir = 'Sam Smith'")], [target])
        self.assertEqual(await dangling(self.db), [])

    async def test_an_artist_merge_of_another_spelling_moves_its_files(self):
        """A disk that minds case (the test's): `SAM SMITH` is a folder of
        its own, and its album goes into the target's, never to Trash as
        "already there" (the index's first row under the name was the
        source's, so the target was spelled as the source)."""
        from app import api
        self.write("SAM SMITH/(2014) X/01 - A.mp3", seconds=3.0, title="A", artist="Sam Smith",
                   albumartist="Sam Smith", album="X", tracknumber="1")
        self.write("Sam Smith/(2014) X/02 - B.mp3", seconds=4.0, title="B", artist="Sam Smith",
                   albumartist="Sam Smith", album="X", tracknumber="2")
        with self.handlers():
            await self.check_all()
            result = await api.library_merge_artists(
                api.MergeArtistsIn(sources=["SAM SMITH"], target="Sam Smith"))
        self.assertEqual((result["moved"], result["trashed"], result["failures"]), (1, 0, []))
        self.assertTrue((self.lib / "Sam Smith/(2014) X/01 - A.mp3").is_file())
        self.assertFalse((self.lib / "SAM SMITH").exists())
        self.assertEqual(await dangling(self.db), [])

    async def test_an_artist_merge_leaves_the_target_itself_alone(self):
        """On the share `SAM SMITH` is `Sam Smith`: each album of the
        "source" is its own destination, every file "already there", and
        the lot went to Trash. A link stands in for the share's other
        spelling here."""
        from app import api
        self.album("Sam Smith/(2014) In the Lonely Hour", "Sam Smith", "In the Lonely Hour",
                   ["Money on My Mind", "Stay With Me"])
        with self.handlers():
            await self.check_all()
            (self.lib / "SAM SMITH").symlink_to("Sam Smith")
            result = await api.library_merge_artists(
                api.MergeArtistsIn(sources=["SAM SMITH"], target="Sam Smith"))
        self.assertEqual((result["moved"], result["trashed"]), (0, 0))
        self.assertEqual([f["path"] for f in result["failures"]], ["SAM SMITH"])
        self.assertEqual((await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM library_trash"))["n"], 0)
        self.assertEqual(sorted(p.name for p in (self.lib / "Sam Smith").iterdir()),
                         ["(2014) In the Lonely Hour"])
        self.assertEqual(len(list((self.lib / "Sam Smith").rglob("*.mp3"))), 2)

    # ------------------------------------------------------------ the cap
    async def test_albums_past_the_cap_are_only_marked_due(self):
        from app.metadata import recheck
        for year, name in ((1985, "Post"), (1986, "Gossip"), (1987, "Under the Sun")):
            self.album(f"{PK}/({year}) {name}", PK, name, [f"{name} Song"])
        with self.handlers():
            await self.check_all()
            await self.db.execute("UPDATE md_albums SET next_check = 9e9")
            out = await recheck.after_library_change([PK], artist_dirs=[PK], cap=1)
        self.assertEqual((out["checked"], out["due"]), (1, 2))
        rows = await self.db.fetchall("SELECT next_check, priority FROM md_albums")
        self.assertEqual(len(rows), 3)
        # one checked (due again when an unidentified album is), two first in line
        self.assertEqual(sorted((r["next_check"] == 0, r["priority"]) for r in rows),
                         [(False, 0), (True, 10), (True, 10)])
