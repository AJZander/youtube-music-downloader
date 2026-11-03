"""Clearing Needs attention: genre fixes that stay fixed, merging a blocked
move, damaged files, Plex re-adds, the bulk calls on matches, and filing
missing tracks into their album.

Run with: python -m unittest discover backend/tests"""
import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.engine import tagio
from app.metadata import desired, issues, resolver, units
from test_metadata import HAVE_FFMPEG, as_spelled, file_row, make_audio, release


class DbTestCase(unittest.IsolatedAsyncioTestCase):
    """A scratch database (and library folder) per test."""

    async def asyncSetUp(self):
        from app.config import settings
        from app.db import db
        self.tmp = Path(tempfile.mkdtemp())
        self.lib = self.tmp / "library"
        self.lib.mkdir()
        (self.lib / ".mdl-library-online").touch()
        self.patches = [mock.patch.object(settings, "data_dir", self.tmp / "data"),
                        mock.patch.object(settings, "library_dir", self.lib),
                        mock.patch.object(settings, "plex_token", "")]
        for p in self.patches:
            p.start()
        await db.connect()
        self.db = db
        desired._artist_dirs["at"] = 0.0

    async def asyncTearDown(self):
        await self.db.close()
        for p in self.patches:
            p.stop()
        desired._artist_dirs["at"] = 0.0
        shutil.rmtree(self.tmp, ignore_errors=True)


def mb_quiet():
    """build_album without MusicBrainz or Last.fm."""
    return [mock.patch.object(desired.mb, "release_group", mock.AsyncMock(return_value=None)),
            mock.patch.object(desired.lastfm, "album_tags", mock.AsyncMock(return_value=[])),
            mock.patch.object(desired.lastfm, "artist_tags", mock.AsyncMock(return_value=[])),
            mock.patch.object(desired.mb, "artist", mock.AsyncMock(return_value={}))]


# ------------------------------------------------------------------ genres
class GenresStayFixedTestCase(DbTestCase):
    """The fix set an album's and its tracks' genres in Plex, but only the
    album was read back: the next check saw the tracks' old genres and
    planned the same fix again, every half hour."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # what Plex really holds; the mirror starts out behind it
        self.server = {"50": ["Pop"], "9": ["Pop", "Teen Pop"], "10": ["Pop"]}
        for key, kind in (("50", "album"), ("9", "track"), ("10", "track")):
            await self.db.execute(
                "INSERT INTO plex_items (rating_key, type, genres, seen_at) VALUES (?, ?, ?, 0)",
                (key, kind, json.dumps(["Pop"])))

    def fakes(self, takes_tracks=True):
        from app.metadata import apply
        calls = []

        async def set_tags(type_num, keys, tag, values, current=None, lock=True):
            calls.append((type_num, list(keys), list(current or [])))
            for k in keys:
                if takes_tracks or type_num != apply.TRACK:
                    self.server[k] = list(values)

        async def refresh_items(keys):
            for k in keys:
                await self.db.execute("UPDATE plex_items SET genres = ? WHERE rating_key = ?",
                                      (json.dumps(self.server[k]), k))

        fake = mock.MagicMock(set_tags=set_tags)
        return calls, [mock.patch.object(apply, "plex", fake),
                       mock.patch.object(apply.plexsync, "refresh_items", refresh_items),
                       mock.patch.object(apply, "_shared", mock.AsyncMock(return_value=False))]

    async def run_fix(self, takes_tracks=True):
        from app.metadata import apply
        calls, patches = self.fakes(takes_tracks)
        for p in patches:
            p.start()
        try:
            ctx = apply.Ctx("A/B", [], {"genres": ["Pop", "Dance-Pop"]}, "A")
            note = await apply.do_genres({"detail": {"rating_key": "50", "tracks": ["9", "10"],
                                                     "to": ["Pop", "Dance-Pop"]}}, ctx)
        finally:
            for p in patches:
                p.stop()
        return calls, note

    async def test_the_tracks_are_read_back_after_the_edit(self):
        await self.run_fix()
        for key in ("50", "9", "10"):
            row = await self.db.fetchone("SELECT genres FROM plex_items WHERE rating_key = ?", (key,))
            self.assertEqual(json.loads(row["genres"]), ["Pop", "Dance-Pop"], key)

    async def test_what_plex_has_is_removed_not_what_the_mirror_saw(self):
        calls, _ = await self.run_fix()
        from app.metadata import apply
        track_calls = [c for c in calls if c[0] == apply.TRACK]
        # "Teen Pop" was only in Plex, never in the mirror: still removed
        self.assertTrue(any("Teen Pop" in current for _, _, current in track_calls), track_calls)

    async def test_a_track_edit_plex_did_not_take_fails_the_fix(self):
        from app.metadata import apply
        with self.assertRaises(apply.ApplyError) as cm:
            await self.run_fix(takes_tracks=False)
        self.assertIn("2 of 3 items", str(cm.exception))


# ------------------------------------------------------------------ planning
class PlanTestCase(unittest.TestCase):
    def plan(self, rows, *, status="resolved", plex=None, no_release=False, conflict=None,
             box=False):
        rel = release()
        score, mapping = resolver.score_release([r for r in rows if not r.get("unreadable")], rel)
        res = resolver.Resolution(status=status, release=rel, score=score, track_map=mapping,
                                  candidates=[{"release_mbid": "rel-1", "selected": True}])
        with contextlib_all(*mb_quiet(),
                            mock.patch.object(issues, "conflict_of",
                                              mock.AsyncMock(return_value=conflict)),
                            mock.patch.object(issues, "in_box_set",
                                              mock.AsyncMock(return_value=box)),
                            mock.patch.object(issues, "other_album",
                                              mock.AsyncMock(return_value=False)),
                            mock.patch.object(issues, "match_flipflop",
                                              mock.AsyncMock(return_value=[])),
                            as_spelled()):
            want = asyncio.run(desired.build_album(rows, res, plex_album=None, plex_artist=None,
                                                   overrides={}))
            return {p["kind"]: p for p in asyncio.run(issues.plan_unit(
                "A/B", rows, want if status == "resolved" else None, res, plex,
                plex_synced=plex is not None, ours_locked=mock.AsyncMock(return_value=False),
                no_release=no_release))}

    def test_a_damaged_file_is_its_own_issue(self):
        rows = [file_row("A/B/1.flac", "Pop", track=1),
                file_row("A/B/2.flac", "", track=None, unreadable=1, duration=None)]
        plan = self.plan(rows, plex={"album": None, "tracks": {}})
        self.assertEqual(plan["bad_file"]["detail"]["paths"], ["A/B/2.flac"])
        # the rest of the album is planned without it
        self.assertNotIn("A/B/2.flac", plan["not_in_plex"]["detail"]["paths"])
        self.assertNotIn("extra_tracks", plan)
        self.assertTrue(plan["tags"]["summary"].startswith("1 of 1 files"))

    def test_an_album_plex_holds_by_our_release_id_is_not_unmatched(self):
        rows = [file_row("A/B/1.flac", "Pop", track=1)]
        album = {"rating_key": "50", "title": "Celebrity", "guid": "mbid://rel-1",
                 "mbid": "another-release", "genres": "[]", "moods": "[]", "locked": None,
                 "thumb": "x"}
        plex = {"album": album, "tracks": {"A/B/1.flac": {"rating_key": "9", "parent_key": "50",
                                                          "idx": 1, "parent_idx": 1}}}
        with mock.patch.object(issues.settings, "metadata_write_plex", True):
            self.assertNotIn("unmatched", self.plan(rows, plex=plex))
            album["guid"] = "local://50"
            self.assertIn("unmatched", self.plan(rows, plex=plex))

    def test_a_check_match_says_whether_the_files_settle_it(self):
        rows = [file_row("A/B/1.flac", "Pop", track=1),
                file_row("A/B/2.flac", "Celebrity", track=2, duration=196.0)]
        detail = self.plan(rows, status="review")["review_match"]["detail"]
        self.assertEqual((detail["files"], detail["matched"], detail["artist"]), (2, 2, 1.0))

    def test_not_on_musicbrainz_asks_nothing_more(self):
        rows = [file_row("A/B/1.flac", "Pop", track=1, album_artist="Someone Else")]
        self.assertIn("review_match", self.plan(rows, status="review"))
        plan = self.plan(rows, status="review", no_release=True)
        for kind in ("review_match", "unresolved", "folder_artist"):
            self.assertNotIn(kind, plan)

    def test_a_box_sets_album_is_not_moved_out(self):
        rows = [file_row("A/B/1.flac", "Pop", track=1)]
        self.assertIn("move", self.plan(rows))
        self.assertNotIn("move", self.plan(rows, box=True))

    def test_a_move_blocked_by_a_copy_can_be_merged(self):
        rows = [file_row("A/B/1.flac", "Pop", track=1)]
        plan = self.plan(rows, conflict={"message": "X already holds a copy of these tracks",
                                         "kind": "copy", "occupants": []})
        self.assertTrue(issues.KINDS["move_conflict"]["applyable"])
        self.assertEqual(plan["move_conflict"]["detail"]["conflict_kind"], "copy")
        self.assertNotIn("move", plan)


class contextlib_all:
    """Enter several context managers (a with-statement can't take a list)."""

    def __init__(self, *managers):
        self.managers = managers

    def __enter__(self):
        for m in self.managers:
            m.__enter__()
        return self

    def __exit__(self, *exc):
        for m in reversed(self.managers):
            m.__exit__(*exc)


class StayingNameTestCase(DbTestCase):
    async def test_a_file_staying_put_blocks_a_rename_onto_its_name(self):
        rows = [file_row("A/B/02 - Song (feat. X).m4a", "Song"), file_row("A/B/02 - Song.m4a", "Song")]
        want = {"folder": "A/B", "release_mbid": "rel-1", "tracks": {
            r["path"]: {"target": "A/B/02 - Song.m4a"} for r in rows}}
        moves = issues.planned_moves(rows, want)
        self.assertEqual(len(moves), 1)
        found = await issues.conflict_of("A/B", rows, want, moves)
        self.assertEqual(found["kind"], "names")


class OtherAlbumTestCase(DbTestCase):
    def releases(self, **by_id):
        return mock.patch.object(issues.mb, "release", mock.AsyncMock(
            side_effect=lambda mbid: by_id.get(mbid)))

    @staticmethod
    def typed(rel_id, group, kind, **kw):
        rel = release(**kw)
        rel["id"] = rel_id
        rel["release-group"] = {"id": group, "primary-type": kind}
        return rel

    def found(self, folder, rel, group):
        return {"kind": "other", "occupants": [
            {"path": f"{folder}/01.opus", "mb_albumid": rel, "mb_releasegroupid": group}]}

    async def test_a_single_named_like_the_album_gets_its_own_folder(self):
        want = {"folder": "Flo Rida/(2012) Wild Ones", "release_mbid": "rel-1",
                "releasegroup_mbid": "rg-single", "releasetype": ["single"], "tracks": {}}
        found = self.found(want["folder"], "rel-album", "rg-album")
        with self.releases(**{"rel-1": self.typed("rel-1", "rg-single", "Single"),
                              "rel-album": self.typed("rel-album", "rg-album", "Album")}), \
                mock.patch.object(desired, "on_disk", mock.AsyncMock(side_effect=lambda f, *_: f)):
            self.assertTrue(await issues.other_album(want, found))
            self.assertEqual(await issues.own_folder(want, found),
                             "Flo Rida/(2012) Wild Ones [Single]")

    async def test_an_album_beside_its_single_waits_for_the_single_to_move(self):
        await self.db.execute("INSERT INTO md_albums (key, dirpath, artist_dir, next_check) "
                              "VALUES ('Q/(1991) Innuendo', 'Q/(1991) Innuendo', 'Q', 999)")
        want = {"folder": "Q/(1991) Innuendo", "release_mbid": "rel-1",
                "releasegroup_mbid": "rg-album", "releasetype": ["album"], "tracks": {}}
        found = self.found(want["folder"], "rel-single", "rg-single")
        with self.releases(**{"rel-1": self.typed("rel-1", "rg-album", "Album"),
                              "rel-single": self.typed("rel-single", "rg-single", "Single")}):
            self.assertTrue(await issues.other_album(want, found))
            self.assertIsNone(await issues.own_folder(want, found))      # no "[12 Tracks]"
        row = await self.db.fetchone("SELECT next_check FROM md_albums WHERE key = 'Q/(1991) Innuendo'")
        self.assertEqual(row["next_check"], 0)

    async def test_one_album_under_two_release_groups_is_a_copy(self):
        want = {"folder": "L/(2018) Tha Carter V", "release_mbid": "rel-1",
                "releasegroup_mbid": "rg-1", "releasetype": ["album"], "tracks": {}}
        found = self.found(want["folder"], "rel-2", "rg-2")
        with self.releases(**{"rel-1": self.typed("rel-1", "rg-1", "Album"),
                              "rel-2": self.typed("rel-2", "rg-2", "Album")}):
            self.assertFalse(await issues.other_album(want, found))

    async def test_the_single_gives_the_album_its_name_back(self):
        await self.db.execute(
            "INSERT INTO md_albums (key, dirpath, artist_dir, status, releasegroup_mbid, desired) "
            "VALUES ('Q/ORIGINAL CD/1991 - Innuendo', 'Q/ORIGINAL CD/1991 - Innuendo', 'Q', "
            "'resolved', 'rg-album', ?)",
            (json.dumps({"folder": "Q/(1991) Innuendo", "releasetype": ["album"]}),))
        single = {"folder": "Q/(1991) Innuendo", "release_mbid": "rel-s",
                  "releasegroup_mbid": "rg-single", "releasetype": ["single"], "tracks": {}}
        with mock.patch.object(desired, "on_disk", mock.AsyncMock(side_effect=lambda f, *_: f)):
            self.assertEqual(await issues.single_beside_album("Q/(1991) Innuendo", single),
                             "Q/(1991) Innuendo [Single]")
            album = {**single, "releasetype": ["album"]}
            self.assertIsNone(await issues.single_beside_album("Q/x", album))

    async def test_another_pressing_of_the_same_edition_is_a_copy(self):
        ours, theirs = release(), release(disambiguation="2009 remaster")
        theirs["id"] = "rel-2"
        want = {"folder": "A/(2001) Celebrity", "release_mbid": "rel-1",
                "releasegroup_mbid": "rg-1", "tracks": {}}
        found = {"kind": "other", "occupants": [{"path": "A/(2001) Celebrity/1.flac",
                                                 "mb_albumid": "rel-2", "mb_releasegroupid": "rg-1"}]}
        fetch = mock.AsyncMock(side_effect=lambda mbid: ours if mbid == "rel-1" else theirs)
        with mock.patch.object(issues.mb, "release", fetch):
            self.assertFalse(await issues.other_album(want, found))
            theirs["disambiguation"] = "deluxe edition"
            self.assertTrue(await issues.other_album(want, found))

    async def test_untagged_files_at_the_destination_count_as_a_copy(self):
        want = {"folder": "A/(2001) Celebrity", "release_mbid": "rel-1",
                "releasegroup_mbid": "rg-1", "tracks": {}}
        found = {"kind": "other", "occupants": [{"path": "A/(2001) Celebrity/1.flac",
                                                 "mb_albumid": None, "mb_releasegroupid": None}]}
        self.assertFalse(await issues.other_album(want, found))


class RekeyTestCase(DbTestCase):
    async def test_a_decision_the_new_name_already_has_is_kept_once(self):
        from app.metadata import apply
        for a, b in (("A/old", "A/x"), ("A/new", "A/x")):
            await self.db.execute(
                "INSERT INTO library_exclusions (scope, key_a, key_b, reason, created_at) "
                "VALUES ('album', ?, ?, '', '')", (a, b))
        await apply.rekey_folder("A/old", "A/new")
        rows = await self.db.fetchall("SELECT key_a, key_b FROM library_exclusions")
        self.assertEqual([tuple(r) for r in rows], [("A/new", "A/x")])


class BoxSetTestCase(DbTestCase):
    async def index(self, path, album):
        await self.db.execute(
            "INSERT INTO library_index (norm_artist, norm_title, path, added_at, dirpath, "
            "artist_dir, album, album_artist) VALUES ('a', 't', ?, '2026-01-01', ?, ?, ?, 'Queen')",
            (path, str(Path(path).parent), path.split("/", 1)[0], album))

    async def test_an_album_split_out_of_a_box_sets_discs(self):
        rows = [file_row("Queen/Box, 20CD/CD 06 - News/01.mp3", "We Will Rock You",
                         album="News of the World")]
        self.assertTrue(await units.in_box_set("Queen/Box, 20CD|newsoftheworld", rows))
        loose = [file_row("Queen/Singles/01.mp3", "Under Pressure", album="Hot Space")]
        self.assertFalse(await units.in_box_set("Queen/Singles|hotspace", loose))

    async def test_a_dated_collection_holds_other_albums(self):
        await self.index("Queen/1995 - Collection/Jazz/01.mp3", "Jazz")
        await self.index("Queen/1995 - Collection/The Game/01.mp3", "The Game")
        rows = [file_row("Queen/1995 - Collection/Jazz/01.mp3", "Mustapha", album="Jazz")]
        self.assertTrue(await units.in_box_set("Queen/1995 - Collection/Jazz", rows))

    async def test_a_dated_folder_of_one_albums_discs_is_that_album(self):
        await self.index("DM/2011 Remixes (3CD)/2011 Remixes (CD2)/01.mp3", "Remixes 2: 81-11")
        await self.index("DM/2011 Remixes (3CD)/2011 Remixes (CD3)/01.mp3", "Remixes 2: 81-11")
        rows = [file_row("DM/2011 Remixes (3CD)/2011 Remixes (CD3)/01.mp3", "Personal Jesus",
                         album="Remixes 2: 81-11")]
        self.assertFalse(await units.in_box_set("DM/2011 Remixes (3CD)/2011 Remixes (CD3)", rows))


# ------------------------------------------------------------------ merging
@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class MergeTestCase(DbTestCase):
    """A second copy of an album, blocked from moving onto the first: made
    one album, the better file of each track kept."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app import library
        dest, old = self.lib / "NSYNC" / "(2001) Celebrity", self.lib / "NSYNC" / "old"
        dest.mkdir(parents=True)
        old.mkdir(parents=True)
        files = [(dest / "01 - Pop.mp3", "Pop", 3.0), (dest / "02 - Celebrity.flac", "Celebrity", 4.0),
                 (old / "01 Pop.flac", "Pop", 3.0), (old / "02 Celebrity.mp3", "Celebrity", 4.0),
                 (old / "03 The Game Is Over.flac", "The Game Is Over", 5.0)]
        for path, title, secs in files:
            make_audio(path, secs)
            tagio.write(path, {"title": [title], "artist": ["NSYNC"], "album": ["Celebrity"]})
        (dest / "folder.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 10)
        await library.scan(full=True)

    async def plan_and_merge(self):
        from app import library
        from app.metadata import apply
        rows = await units.load_unit("NSYNC/old")
        rel = release(tracks=[("Pop", 3.0), ("Celebrity", 4.0), ("The Game Is Over", 5.0)])
        score, mapping = resolver.score_release(rows, rel)
        res = resolver.Resolution(status="resolved", release=rel, score=score, track_map=mapping)
        with contextlib_all(*mb_quiet()):
            want = await desired.build_album(rows, res, plex_album=None, plex_artist=None,
                                             overrides={})
        plan = {p["kind"]: p for p in await issues.plan_unit(
            "NSYNC/old", rows, want, res, None, plex_synced=False, ours_locked=apply.ours_locked)}
        self.assertIn("move_conflict", plan)
        self.assertNotIn("move", plan)
        ctx = apply.Ctx("NSYNC/old", rows, want, "NSYNC")
        with mock.patch.object(library, "_refresh_plex", mock.AsyncMock()):
            note = await apply.do_merge(issue_of(plan["move_conflict"]), ctx)
        return ctx, note

    async def test_the_better_copy_of_each_track_stays(self):
        ctx, note = await self.plan_and_merge()
        dest = self.lib / "NSYNC" / "(2001) Celebrity"
        self.assertEqual(sorted(p.name for p in dest.iterdir() if p.suffix != ".jpg"),
                         ["01 - Pop.flac", "02 - Celebrity.flac", "03 - The Game Is Over.flac"])
        self.assertFalse((self.lib / "NSYNC" / "old").exists())
        trashed = {r["original_path"] for r in await self.db.fetchall(
            "SELECT original_path FROM library_trash WHERE kind = 'audio'")}
        self.assertEqual(trashed, {"NSYNC/(2001) Celebrity/01 - Pop.mp3", "NSYNC/old/02 Celebrity.mp3"})
        self.assertIn("2 worse copies trashed", note)
        self.assertEqual(ctx.key, "NSYNC/(2001) Celebrity")

    async def test_undo_puts_both_copies_back(self):
        from app.metadata import apply
        ctx, _ = await self.plan_and_merge()
        result = await apply.revert(ctx.batches[0])
        self.assertFalse(result["failures"], result)
        old = self.lib / "NSYNC" / "old"
        self.assertEqual(sorted(p.name for p in old.iterdir()),
                         ["01 Pop.flac", "02 Celebrity.mp3", "03 The Game Is Over.flac"])
        self.assertTrue((self.lib / "NSYNC" / "(2001) Celebrity" / "01 - Pop.mp3").exists())

    async def test_a_file_it_cant_pair_is_never_trashed(self):
        from app import library
        from app.metadata import apply
        # a different recording that happens to take the same name
        stray = self.lib / "NSYNC" / "(2001) Celebrity" / "03 - The Game Is Over.flac"
        make_audio(stray, 9.0)
        tagio.write(stray, {"title": ["Something Else"], "artist": ["NSYNC"]})
        await library.scan(full=False)
        ctx, note = await self.plan_and_merge()
        self.assertTrue(stray.exists())
        self.assertTrue((self.lib / "NSYNC" / "old" / "03 The Game Is Over.flac").exists())
        trashed = {r["original_path"] for r in await self.db.fetchall(
            "SELECT original_path FROM library_trash")}
        self.assertNotIn("NSYNC/(2001) Celebrity/03 - The Game Is Over.flac", trashed)


class MergePlanTestCase(DbTestCase):
    async def test_two_unmatched_files_of_one_name_are_both_kept(self):
        """Pantera's deluxe: "Disc 1/09. Demo.flac" and "Disc 3/09. Demo.flac"
        aren't on the release, so both keep their names, which collide. Neither
        is proven a copy of the other: one moves, the other stays put."""
        from app.metadata import apply
        rows = [{"path": f"P/old/Disc {d}/09. Demo.flac", "dirpath": f"P/old/Disc {d}",
                 "artist_dir": "P", "title": "Demo", "duration_sec": 100.0 + d, "format": "flac"}
                for d in (1, 3)]
        want = {"folder": "P/(1990) X", "release_mbid": "rel-1", "tracks": {
            r["path"]: {"matched": False, "target": "P/(1990) X/09. Demo.flac"} for r in rows}}
        plan = await apply.merge_plan(apply.Ctx("P/old", rows, want, "P"))
        self.assertEqual(plan["conflict"], "names")
        # a box set's album is never merged out, whatever an old plan says
        box = [{**r, "dirpath": "Q/Box, 20CD/CD 06 - News"} for r in rows]
        with self.assertRaises(apply.ApplyError):
            await apply.merge_plan(apply.Ctx("Q/Box, 20CD|news", box, want, "Q"))
        self.assertEqual(plan["trash"], [])
        self.assertEqual(len(plan["moves"]), 1)
        self.assertEqual(len(plan["left"]), 1)
        # one of them already at the name stays, and nothing moves onto it
        rows[0]["path"] = "P/(1990) X/09. Demo.flac"
        want["tracks"] = {r["path"]: {"matched": i == 1, "target": "P/(1990) X/09. Demo.flac"}
                          for i, r in enumerate(rows)}
        plan = await apply.merge_plan(apply.Ctx("P/old", rows, want, "P"))
        self.assertEqual((plan["moves"], plan["trash"], plan["left"]), ([], [], [rows[1]["path"]]))


class AlbumCopiesTestCase(DbTestCase):
    """One album held in several folders (Queen's CD, vinyl rip and reissue):
    one album, the best file of each recording."""

    REL = {"id": "rel-std", "title": "Jazz", "release-group": {"id": "rg-jazz", "primary-type": "Album"},
           "media": [{"position": 1, "tracks": [
               {"id": f"t{i}", "position": i, "number": str(i), "title": t,
                "recording": {"id": f"r{i}", "title": t}}
               for i, t in enumerate(["Mustapha", "Fat Bottomed Girls", "Jealousy"], start=1)]}]}

    async def unit(self, key, files, *, release="rel-std", group="rg-jazz", box_dir=None):
        """files: (name, recording or None, format, bitrate)"""
        tracks = {}
        for name, rec, fmt, kbps in files:
            path = f"{box_dir or key}/{name}"
            await self.db.execute(
                "INSERT INTO library_index (norm_artist, norm_title, path, added_at, dirpath, "
                "artist_dir, format, bitrate_kbps, duration_sec, title, album) "
                "VALUES ('q', 't', ?, '', ?, 'Queen', ?, ?, 180, ?, 'Jazz')",
                (path, box_dir or key, fmt, kbps, name))
            tracks[path] = {"matched": rec is not None, "recording_mbid": rec,
                            "target": f"Queen/(1978) Jazz/{name}"}
        want = {"folder": "Queen/(1978) Jazz", "release_mbid": release, "releasegroup_mbid": group,
                "album": "Jazz", "albumartist": "Queen", "tracks": tracks}
        await self.db.execute(
            "INSERT INTO md_albums (key, dirpath, artist_dir, status, release_mbid, releasegroup_mbid, "
            "desired) VALUES (?, ?, 'Queen', 'resolved', ?, ?, ?)",
            (key, key.split("|")[0], release, group, json.dumps(want)))
        return want

    def mb(self, **extra):
        rels = {"rel-std": self.REL, **extra}
        return mock.patch.object(issues.mb, "release", mock.AsyncMock(side_effect=lambda m: rels.get(m)))

    async def test_copies_sharing_a_recording_are_one_issue_and_a_remix_is_not(self):
        await self.unit("Queen/(1978) Jazz", [("01 - Mustapha.mp3", "r1", "mp3", 320),
                                              ("02 - Fat Bottomed Girls.mp3", "r2", "mp3", 320)])
        await self.unit("Queen/VINYL-RIP/1978 - Jazz", [("01. Mustapha.flac", "r1", "flac", 900)])
        await self.unit("Queen/(1978) Jazz (2026 Mix)", [("01 - Mustapha.flac", "r91", "flac", 900)])
        await self.unit("Queen/Box, 20CD|jazz", [("01. Mustapha.mp3", "r1", "mp3", 320)],
                        box_dir="Queen/Box, 20CD/CD 07 - Jazz")
        with self.mb():
            planned_issues = await issues.plan_album_copies()
        self.assertEqual(len(planned_issues), 1)
        d = planned_issues[0]["detail"]
        self.assertEqual(d["keep"], "Queen/(1978) Jazz")          # it holds most of the album
        self.assertEqual(d["copies"], ["Queen/VINYL-RIP/1978 - Jazz"])   # not the remix, not the box

    async def test_the_best_file_of_each_recording_stays(self):
        from app.metadata import apply
        keep = await self.unit("Queen/(1978) Jazz", [("01 - Mustapha.mp3", "r1", "mp3", 320),
                                                     ("02 - Fat Bottomed Girls.flac", "r2", "flac", 900)])
        await self.unit("Queen/VINYL-RIP/1978 - Jazz", [
            ("01. Mustapha.flac", "r1", "flac", 900),            # better: replaces the mp3
            ("02. Fat Bottomed Girls.mp3", "r2", "mp3", 320),     # worse: trashed
            ("03. Jealousy.flac", "r3", "flac", 900),             # missing there: moves in
            ("04. Bonus Demo.flac", None, "flac", 900)])          # not identified: stays
        rows = await units.load_unit("Queen/(1978) Jazz")
        ctx = apply.Ctx("Queen/(1978) Jazz", rows, keep, "Queen")
        with self.mb():
            plan = await apply.copies_plan({"detail": {"copies": ["Queen/VINYL-RIP/1978 - Jazz"]}}, ctx)
        self.assertEqual(sorted(plan["trash"]), ["Queen/(1978) Jazz/01 - Mustapha.mp3",
                                                 "Queen/VINYL-RIP/1978 - Jazz/02. Fat Bottomed Girls.mp3"])
        self.assertEqual(sorted((m["from"], m["to"]) for m in plan["moves"]), [
            ("Queen/VINYL-RIP/1978 - Jazz/01. Mustapha.flac", "Queen/(1978) Jazz/01 - Mustapha.flac"),
            ("Queen/VINYL-RIP/1978 - Jazz/03. Jealousy.flac", "Queen/(1978) Jazz/03 - Jealousy.flac")])
        self.assertEqual(plan["left"], ["Queen/VINYL-RIP/1978 - Jazz/04. Bonus Demo.flac"])

    async def test_a_recording_known_in_several_mixes_is_never_called_a_copy(self):
        """Sgt. Pepper: a stereo remaster once identified as the mono vinyl
        (same tracklist) shares the mono disc's recordings by our guess only."""
        import copy
        mono = copy.deepcopy(self.REL)
        for t in mono["media"][0]["tracks"]:
            t["recording"]["disambiguation"] = "original mono studio mix"
        await self.unit("Queen/(1978) Jazz", [("01 - Mustapha.flac", "r1", "flac", 900)])
        await self.unit("Queen/(1978) Jazz [1979 Reissue]", [("01 - Mustapha.flac", "r1", "flac", 900)])
        with mock.patch.object(issues.mb, "release", mock.AsyncMock(return_value=mono)):
            self.assertEqual(await issues.plan_album_copies(), [])

    async def test_two_lengths_are_not_one_recording(self):
        from app.metadata import apply
        keep = await self.unit("Queen/(1978) Jazz", [("01 - Mustapha.mp3", "r1", "mp3", 128)])
        await self.unit("Queen/VINYL-RIP/1978 - Jazz", [("01. Mustapha.flac", "r1", "flac", 900)])
        await self.db.execute("UPDATE library_index SET duration_sec = 240 WHERE path LIKE '%VINYL%'")
        rows = await units.load_unit("Queen/(1978) Jazz")
        with self.mb():
            plan = await apply.copies_plan({"detail": {"copies": ["Queen/VINYL-RIP/1978 - Jazz"]}},
                                           apply.Ctx("Queen/(1978) Jazz", rows, keep, "Queen"))
        self.assertEqual((plan["trash"], plan["moves"]), ([], []))

    async def test_the_most_complete_edition_keeps_the_album(self):
        deluxe = {**self.REL, "id": "rel-dlx", "media": self.REL["media"] + [
            {"position": 2, "tracks": [{"id": "t9", "position": 1, "number": "1", "title": "Demo",
                                        "recording": {"id": "r9", "title": "Demo"}}]}]}
        await self.unit("Queen/(1978) Jazz", [("01 - Mustapha.mp3", "r1", "mp3", 320),
                                              ("02 - Fat Bottomed Girls.mp3", "r2", "mp3", 320)])
        await self.unit("Queen/DELUXE/1978 - Jazz (2CD)", [("1-01 Mustapha.mp3", "r1", "mp3", 320),
                                                           ("2-01 Demo.mp3", "r9", "mp3", 320)],
                        release="rel-dlx")
        with self.mb(**{"rel-dlx": deluxe}):
            planned_issues = await issues.plan_album_copies()
        self.assertEqual(planned_issues[0]["detail"]["keep"], "Queen/DELUXE/1978 - Jazz (2CD)")


def issue_of(planned_issue: dict) -> dict:
    return {**planned_issue, "id": 1, "created_at": "2026-01-01T00:00:00Z"}


# ------------------------------------------------------------------ damaged files
class DamagedFileTestCase(DbTestCase):
    async def test_a_file_that_reads_now_is_not_trashed(self):
        from app import library
        from app.metadata import apply
        (self.lib / "A" / "B").mkdir(parents=True)
        zeroed = self.lib / "A" / "B" / "1.flac"
        zeroed.write_bytes(b"\0" * 4096)
        rows = [{"path": "A/B/1.flac", "unreadable": 1, "dirpath": "A/B", "artist_dir": "A"}]
        ctx = apply.Ctx("A/B", rows, None, "A")
        fine = mock.patch.object(apply.tagio, "read",
                                 mock.MagicMock(return_value={"info": {"length": 3.0}}))
        with fine, mock.patch.object(apply, "reindex", mock.AsyncMock()) as reindex:
            note = await apply.do_bad_file({"detail": {}}, ctx)
        self.assertIn("read fine", note)
        reindex.assert_awaited_once_with(["A/B/1.flac"])
        self.assertTrue(zeroed.exists())
        with mock.patch.object(library, "_refresh_plex", mock.AsyncMock()):
            note = await apply.do_bad_file({"detail": {}}, ctx)
        self.assertIn("1 damaged files trashed", note)
        self.assertFalse(zeroed.exists())


# ------------------------------------------------------------------ plex re-add
class ReaddTestCase(DbTestCase):
    async def test_files_are_hidden_for_one_scan_and_come_back(self):
        from app.metadata import apply
        (self.lib / "A" / "B").mkdir(parents=True)
        track = self.lib / "A" / "B" / "01 - Pop.m4a"
        track.write_bytes(b"audio")
        seen = []

        async def refresh_path(folder):
            seen.append((folder, track.exists(), (track.parent / ".mdl-readd-01 - Pop.m4a").exists()))
            return True
        fake = mock.MagicMock(refresh_path=refresh_path, wait_for_scan=mock.AsyncMock(),
                              empty_trash=mock.AsyncMock())
        with mock.patch.object(apply, "plex", fake):
            self.assertEqual(await apply.readd(["A/B/01 - Pop.m4a"]), 1)
        self.assertEqual(seen, [("A/B", False, True), ("A/B", True, False)])
        fake.empty_trash.assert_awaited_once()
        self.assertIsNone(await self.db.fetchone(
            "SELECT 1 FROM md_state WHERE key = ?", (apply.READD_PENDING,)))

    async def test_a_restart_puts_hidden_files_back(self):
        from app.metadata import apply
        (self.lib / "A" / "B").mkdir(parents=True)
        hidden = self.lib / "A" / "B" / ".mdl-readd-01 - Pop.m4a"
        hidden.write_bytes(b"audio")
        await self.db.execute("INSERT INTO md_state (key, value) VALUES (?, ?)", (
            apply.READD_PENDING, json.dumps([["A/B/01 - Pop.m4a", "A/B/.mdl-readd-01 - Pop.m4a"]])))
        self.assertEqual(await apply.restore_hidden(), 1)
        self.assertTrue((self.lib / "A" / "B" / "01 - Pop.m4a").exists())

    async def test_only_an_issue_that_outlived_its_scans_is_re_added(self):
        from app.metadata import apply
        rows = [{"path": "A/B/1.m4a", "dirpath": "A/B", "artist_dir": "A"}]
        ctx = apply.Ctx("A/B", rows, None, "A")
        # Plex itself doesn't list the file either (see test_p0_plex_mirror)
        fake = mock.MagicMock(configured=True, folder_files=mock.AsyncMock(return_value={}))
        with mock.patch.object(apply, "plex", fake), \
                mock.patch.object(apply.plexsync, "album_for_paths",
                                  mock.AsyncMock(return_value={"tracks": {}})), \
                mock.patch.object(apply, "readd", mock.AsyncMock(return_value=1)) as readd:
            await apply.do_scan({"detail": {}, "created_at": apply.utcnow()}, ctx)
            readd.assert_not_awaited()
            await apply.do_scan({"detail": {}, "created_at": "2026-01-01T00:00:00Z"}, ctx)
            readd.assert_awaited_once_with(["A/B/1.m4a"])
            # a file Plex won't take even so is not re-added every half hour
            with self.assertRaises(apply.ApplyError):
                await apply.do_scan({"detail": {}, "created_at": "2026-01-01T00:00:00Z"}, ctx)
            readd.assert_awaited_once()


# ------------------------------------------------------------------ decisions
class DecisionsTestCase(DbTestCase):
    async def add_issue(self, kind, key, detail, state="open"):
        await self.db.execute(
            "INSERT INTO md_issues (key, kind, entity, ref, artist_dir, album_key, severity, "
            "summary, detail, auto, state, created_at, updated_at) "
            "VALUES (?, ?, 'album', ?, 'A', ?, 'warning', '', ?, 0, ?, '', '')",
            (f"{kind}:{key}", kind, key, key, json.dumps(detail), state))
        await self.db.execute("INSERT OR IGNORE INTO md_albums (key, dirpath, artist_dir) "
                              "VALUES (?, ?, 'A')", (key, key))
        row = await self.db.fetchone("SELECT id FROM md_issues WHERE key = ?", (f"{kind}:{key}",))
        return row["id"]

    async def test_the_triage_filters(self):
        from app.metadata import views
        sure = await self.add_issue("review_match", "A/sure", {"artist": 1.0, "files": 3, "matched": 3})
        namesake = await self.add_issue("review_match", "A/name", {"artist": 0.0, "files": 1, "matched": 1})
        alias = await self.add_issue("review_match", "A/ye", {"artist": 0.0, "files": 15, "matched": 15})
        partial = await self.add_issue("review_match", "A/part", {"artist": 1.0, "files": 4, "matched": 2})
        old = await self.add_issue("review_match", "A/old", {"score": 0.7})
        for name, expect in (("sure", {sure}), ("namesake", {namesake}),
                             ("unsure", {partial, old, alias})):
            _, items = await views.issues_for(kinds=["review_match"], filter_=name)
            self.assertEqual({i["id"] for i in items}, expect, name)
        _, items = await views.issues_for(kinds=["review_match"], q="A/na")
        self.assertEqual({i["id"] for i in items}, {namesake})

    async def test_accepting_a_match_pins_it_and_checks_again(self):
        from app.metadata import runner as runmod
        issue = await self.add_issue("review_match", "A/B", {"candidates": [
            {"release_mbid": "other", "selected": False}, {"release_mbid": "rel-9", "selected": True}]})
        r = runmod.Runner()
        check = mock.AsyncMock(return_value={"status": "resolved", "auto_ids": [77]})
        with mock.patch.object(r, "check_unit", check), \
                mock.patch.object(r, "auto_apply", mock.AsyncMock(return_value=True)), \
                mock.patch.object(r, "request_apply", mock.AsyncMock()) as request_apply, \
                mock.patch.object(r, "publish", mock.AsyncMock()), \
                mock.patch.object(r, "publish_issues", mock.AsyncMock()):
            self.assertEqual(await r.request_decide([issue], "accept_match"), 1)
            await r._decide(r._decisions)
        check.assert_awaited_once_with("A/B", manual_release="rel-9")
        request_apply.assert_awaited_once_with([77])
        row = await self.db.fetchone("SELECT manual, release_mbid, no_release FROM md_albums "
                                     "WHERE key = 'A/B'")
        self.assertEqual(tuple(row), (1, "rel-9", 0))
        state = await self.db.fetchone("SELECT state FROM md_issues WHERE id = ?", (issue,))
        self.assertEqual(state["state"], "applied")

    async def test_not_on_musicbrainz_is_remembered(self):
        from app.metadata import runner as runmod
        issue = await self.add_issue("unresolved", "A/C", {})
        r = runmod.Runner()
        with mock.patch.object(r, "check_unit", mock.AsyncMock(return_value={})), \
                mock.patch.object(r, "auto_apply", mock.AsyncMock(return_value=False)), \
                mock.patch.object(r, "publish", mock.AsyncMock()), \
                mock.patch.object(r, "publish_issues", mock.AsyncMock()):
            await r.request_decide([issue], "no_release")
            await r._decide(r._decisions)
        row = await self.db.fetchone("SELECT no_release FROM md_albums WHERE key = 'A/C'")
        self.assertEqual(row["no_release"], 1)

    async def test_a_decision_only_takes_issues_it_is_for(self):
        from app.metadata import runner as runmod
        tags = await self.add_issue("tags", "A/D", {})
        r = runmod.Runner()
        with mock.patch.object(r, "publish", mock.AsyncMock()):
            self.assertEqual(await r.request_decide([tags], "accept_match"), 0)


# ------------------------------------------------------------------ missing tracks
class FileInDownloadsTestCase(DbTestCase):
    """Missing tracks downloaded before they were filed into their album
    landed in a folder named the way YouTube Music names the album."""

    async def test_they_are_moved_into_the_album(self):
        from app.metadata import apply
        for folder in ("ABBA/(1980) Super Trouper [2001 Remaster]", "ABBA/(1980) Super Trouper"):
            (self.lib / folder).mkdir(parents=True)
        album = "ABBA/(1980) Super Trouper [2001 Remaster]"
        (self.lib / album / "01 - Super Trouper.flac").write_bytes(b"a")
        stray = "ABBA/(1980) Super Trouper/09 - Lay All Your Love On Me.m4a"
        (self.lib / stray).write_bytes(b"b")
        (self.lib / stray).with_suffix(".lrc").write_text("[00:01]la")
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, created_at) "
            "VALUES ('j1', 'u', 'youtube', 'album', 'completed', '')")
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, status, library_path, provider_track_id, created_at) "
            "VALUES ('t1', 'j1', 'completed', ?, 'vid9', '')", (stray,))
        for path in (f"{album}/01 - Super Trouper.flac", stray):
            await self.db.execute(
                "INSERT INTO library_index (norm_artist, norm_title, path, added_at, dirpath, "
                "artist_dir) VALUES ('a', 't', ?, '', ?, 'ABBA')", (path, str(Path(path).parent)))
        rows = [{"path": f"{album}/01 - Super Trouper.flac", "dirpath": album, "artist_dir": "ABBA"}]
        ctx = apply.Ctx(album, rows, None, "ABBA")
        moved, videos = await apply._file_in_downloads(
            {"detail": {"download": {"job_id": "j1"}}}, ctx)
        self.assertEqual((moved, videos), (1, {"vid9"}))
        self.assertTrue((self.lib / album / "09 - Lay All Your Love On Me.m4a").exists())
        self.assertTrue((self.lib / album / "09 - Lay All Your Love On Me.lrc").exists())
        row = await self.db.fetchone("SELECT dirpath FROM library_index WHERE path LIKE '%Lay All%'")
        self.assertEqual(row["dirpath"], album)

    async def test_a_spent_lookup_budget_leaves_the_download_wanted(self):
        from app import catalog
        from app.metadata import apply, missing
        await self.db.execute(
            "INSERT INTO md_issues (key, kind, entity, ref, artist_dir, album_key, severity, summary, "
            "detail, auto, state, created_at, updated_at) VALUES ('missing_tracks:rel-1', "
            "'missing_tracks', 'album', 'rel-1', 'A', 'A/B', 'warning', '', '{}', 0, 'open', '', '')")
        issue = {"ref": "rel-1", "detail": {}}
        spent = catalog.CatalogError("This hour's browsing budget (60 requests) is used up", 503)
        with mock.patch.object(missing, "compute", mock.AsyncMock(return_value={
                    "release_mbid": "rel-1", "album": "B", "artist": "A", "year": "2001",
                    "missing": [{"title": "x"}], "main_unit": "A/B"})), \
                mock.patch.object(missing, "_browse_from_history", mock.AsyncMock(return_value=None)), \
                mock.patch.object(catalog, "call", mock.AsyncMock(side_effect=spent)):
            with self.assertRaises(apply.KeepOpen):
                await apply.do_download_missing(issue, apply.Ctx("A/B", [], None, "A"))
        self.assertEqual(len(await missing.wanted_downloads()), 1)

    def test_a_target_folder_cannot_leave_the_library(self):
        from app import importer
        from app.paths import PathBuildError
        self.assertEqual(str(importer._in_folder("ABBA/(1980) Album", "01 - X.m4a")),
                         "ABBA/(1980) Album/01 - X.m4a")
        for bad in ("../etc", "/abs/path", "A/.mdl-trash", ""):
            with self.assertRaises(PathBuildError, msg=bad):
                importer._in_folder(bad, "01 - X.m4a")


if __name__ == "__main__":
    unittest.main()
