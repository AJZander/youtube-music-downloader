"""One Plex album, one unit: a Plex album that holds another unit's files
is no unit's to match, tag, mood or give a cover (plex_foreign), and a Plex
item matched to two releases within 14 days is not matched again.

Snoop Dogg's Doggystyle: two editions tagged alike became one Plex album,
235578, and each edition's check matched it as its own: 13 matches from
10-14 to 11-01, each undoing the last (review 2026-11, 4.E).

Run with: python -m unittest discover backend/tests"""
import contextlib
import copy
import json
import sys
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.metadata import issues, units
from test_attention import DbTestCase

X = "Snoop Dogg/(1993) Doggystyle"
Y = "Snoop Dogg/(1993) Doggystyle [2005 Reissue]"
SONGS = {"r1": ("Bathtub", 110.0), "r2": ("G Funk Intro", 144.0), "r3": ("Gin and Juice", 211.0),
         "r4": ("Tha Shiznit", 250.0)}
ORIG, REISSUE = "d7a96c61-531f-4d2f-9873-4acaf3ba4ce0", "a2a412fb-4453-49f0-b1e6-aa049bf3f51d"


def doggystyle(rid: str, date: str, recordings: list[str]) -> dict:
    return {
        "id": rid, "title": "Doggystyle", "date": date, "disambiguation": "",
        "status": "Official", "country": "US",
        "artist-credit": [{"name": "Snoop Dogg", "joinphrase": "",
                           "artist": {"id": "snoop", "name": "Snoop Dogg",
                                      "sort-name": "Dogg, Snoop"}}],
        "release-group": {"id": "rg-dogg", "primary-type": "Album", "secondary-types": [],
                          "first-release-date": "1993-11-23"},
        "label-info": [{"label": {"name": "Death Row"}}],
        "media": [{"position": 1, "format": "CD", "track-count": len(recordings), "tracks": [
            {"id": f"{rid}-t{n}", "position": n, "number": str(n), "title": SONGS[rec][0],
             "length": int(SONGS[rec][1] * 1000),
             "recording": {"id": rec, "title": SONGS[rec][0], "isrcs": []}}
            for n, rec in enumerate(recordings, start=1)]}],
    }


# Two distinct editions of one release group: the reissue has a recording
# of its own, so both stay albums of their own.
RELEASES = {ORIG: doggystyle(ORIG, "1993-11-23", ["r1", "r2", "r3"]),
            REISSUE: doggystyle(REISSUE, "2005-10-25", ["r2", "r3", "r4"])}
FILES = {X: [(ORIG, "r1"), (ORIG, "r2"), (ORIG, "r3")],
         Y: [(REISSUE, "r2"), (REISSUE, "r3"), (REISSUE, "r4")]}


def stamp(days_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * 86400))


class PlexAlbumTestCase(DbTestCase):
    """The library index and the Plex mirror as rows; the real registry,
    checks, plans and executors; MusicBrainz, Last.fm and Plex faked."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.config import settings
        from app.metadata import runner as runmod
        for p in (mock.patch.object(settings, "metadata_write_plex", True),
                  mock.patch.object(settings, "metadata_move_files", False)):
            p.start()
            self.patches.append(p)
        self.runner = runmod.Runner()
        self.paths: dict[str, list[str]] = {}
        for folder, files in FILES.items():
            self.paths[folder] = []
            for n, (rid, rec) in enumerate(files, start=1):
                title, length = SONGS[rec]
                path = f"{folder}/{n:02d} - {title}.m4a"
                self.paths[folder].append(path)
                await self.db.execute(
                    "INSERT INTO library_index (norm_artist, norm_title, path, added_at, title, "
                    "artist, album, album_artist, track_number, disc_number, year, format, "
                    "duration_sec, filesize_bytes, mtime, dirpath, artist_dir, mb_albumid, "
                    "mb_releasegroupid, mb_trackid, mb_releasetrackid, tags_version) "
                    "VALUES (?,?,?,?,?,?,?,?,?,1,'1993','m4a',?,1000,1.0,?,'Snoop Dogg',?,"
                    "'rg-dogg',?,?,9)",
                    ("snoopdogg", title.casefold(), path, "2026-10-14T10:39:00Z", title,
                     "Snoop Dogg", "Doggystyle", "Snoop Dogg", n, length, folder, rid, rec,
                     f"{rid}-t{n}"))
        await units.sync_registry()
        await self.mirror("10", "artist", title="Snoop Dogg")
        await self.db.execute("INSERT INTO md_state (key, value) VALUES ('plex_last_sync', ?)",
                              (str(time.time()),))
        self.plex = self.fake_plex()

    async def mirror(self, key, type_, **fields):
        fields.setdefault("seen_at", time.time())
        cols = ", ".join(["rating_key", "type", *fields])
        await self.db.execute(
            f"INSERT INTO plex_items ({cols}) VALUES ({','.join('?' * (len(fields) + 2))}) "
            "ON CONFLICT(rating_key) DO UPDATE SET "
            + ", ".join(f"{c} = excluded.{c}" for c in fields),
            (key, type_, *fields.values()))

    async def album(self, key, folder, guid=None, mbid=None):
        await self.mirror(key, "album", title="Doggystyle", guid=guid or f"local://{key}",
                          mbid=mbid, parent_key="10", thumb="/thumb", genres=None, folder=folder)

    async def file_in(self, album_key, folder, first_key):
        """Plex files `folder`'s tracks in `album_key`."""
        for n, path in enumerate(self.paths[folder], start=1):
            await self.mirror(str(first_key + n), "track", title=Path(path).stem[5:],
                              parent_key=album_key, grandparent_key="10", idx=n, parent_idx=1,
                              path=path, folder=folder)

    def fake_plex(self):
        """Plex's side of a match: it takes the guid it was given."""
        fake = mock.MagicMock(configured=True)
        guids: dict[str, str] = {}

        async def metadata(rk):
            return {"ratingKey": rk, "guid": guids.get(rk, f"local://{rk}"),
                    "title": "Doggystyle", "year": 1993}

        async def match(rk, guid, name, year=None):
            guids[rk] = guid
        fake.metadata = mock.AsyncMock(side_effect=metadata)
        fake.matches = mock.AsyncMock(return_value=[])
        fake.match = mock.AsyncMock(side_effect=match)
        fake.set_tags = mock.AsyncMock()
        fake.upload_poster = mock.AsyncMock()
        fake.edit = mock.AsyncMock()
        return fake

    @contextlib.contextmanager
    def services(self):
        from app.metadata import apply, lastfm, musicbrainz as mb
        with mock.patch.object(mb, "enabled", return_value=True), \
                mock.patch.object(mb, "release", mock.AsyncMock(
                    side_effect=lambda m: copy.deepcopy(RELEASES.get(m)))), \
                mock.patch.object(mb, "release_group", mock.AsyncMock(return_value=None)), \
                mock.patch.object(mb, "search_releases", mock.AsyncMock(return_value=[])), \
                mock.patch.object(mb, "isrc_releases", mock.AsyncMock(return_value=[])), \
                mock.patch.object(mb, "artist", mock.AsyncMock(return_value={})), \
                mock.patch.object(mb, "artist_aliases", mock.AsyncMock(return_value=[])), \
                mock.patch.object(lastfm, "album_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(lastfm, "artist_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(apply, "plex", self.plex), \
                mock.patch.object(apply.plexsync, "refresh_items", mock.AsyncMock()), \
                mock.patch.object(apply.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(self.runner, "auto_apply", mock.AsyncMock(return_value=False)), \
                mock.patch.object(self.runner, "publish", mock.AsyncMock()), \
                mock.patch.object(self.runner, "publish_issues", mock.AsyncMock()):
            yield

    async def wants(self, key, **fields):
        for field, value in fields.items():
            await self.db.execute(
                "INSERT INTO md_overrides (entity, ref, field, value, created_at) "
                "VALUES ('album', ?, ?, ?, ?)", (key, field, json.dumps(value), stamp(0)))

    async def open_issues(self, key) -> dict[str, dict]:
        return {i["kind"]: i for i in (issues.issue_out(r) for r in await self.db.fetchall(
            "SELECT * FROM md_issues WHERE album_key = ? AND state IN ('open', 'failed')",
            (key,)))}

    async def plex_stage(self, key) -> None:
        """Apply a unit's open Plex fixes, as the runner does."""
        from app.metadata import apply
        members = [i for i in (await self.open_issues(key)).values()
                   if i["kind"] in apply.PLEX_STAGE and i["applyable"]]
        _, _, state = await self.runner._start_album(key, members)
        await self.runner._apply_plex(state)

    async def journal_match(self, rk, album_key, mbid, days_ago):
        await self.db.execute(
            "INSERT INTO md_changes (batch_id, target, ref, artist_dir, album_key, field, "
            "old_value, new_value, summary, applied_at) VALUES (?, 'plex', ?, 'Snoop Dogg', ?, "
            "'match', ?, ?, 'Matched Doggystyle in Plex', ?)",
            (uuid.uuid4().hex[:12], rk, album_key, json.dumps({"guid": "local://1"}),
             json.dumps({"guid": f"mbid://{mbid}"}), stamp(days_ago)))


class ForeignAlbumTestCase(PlexAlbumTestCase):
    async def test_an_album_holding_another_units_files_gets_no_album_edits(self):
        await self.album("50", X)
        await self.file_in("50", X, 100)
        await self.file_in("50", Y, 200)
        await self.wants(X, genres=["Hip Hop"], moods=["Gritty"])
        with self.services():
            await self.runner.check_unit(X)
        plan = await self.open_issues(X)
        self.assertIn("plex_foreign", plan)
        foreign = plan["plex_foreign"]
        self.assertEqual(foreign["severity"], "warning")
        self.assertFalse(foreign["applyable"])
        self.assertEqual(foreign["detail"]["rating_key"], "50")
        self.assertEqual(foreign["detail"]["reason"], "shared")
        self.assertEqual(foreign["detail"]["folders"], [Y])
        self.assertEqual(foreign["detail"]["units"], [Y])
        self.assertIn(Y, foreign["summary"])
        for kind in ("unmatched", "wrong_match", "moods", "poster", "plex_shared"):
            self.assertNotIn(kind, plan)
        # our own tracks still get their genres; the album doesn't
        self.assertIs(plan["genres"]["detail"]["album"], False)
        self.assertEqual(sorted(plan["genres"]["detail"]["tracks"]), ["101", "102", "103"])

    async def test_one_match_then_plex_foreign_and_no_flip_flop(self):
        # Plex has the original's files in an album of their own, and the
        # reissue's not yet
        await self.album("50", X)
        await self.file_in("50", X, 100)
        with self.services():
            await self.runner.check_unit(X)
            self.assertIn("unmatched", await self.open_issues(X))
            await self.plex_stage(X)
            self.assertEqual(self.plex.match.await_count, 1)
            self.assertEqual(self.plex.match.await_args.args[:2], ("50", f"mbid://{ORIG}"))
            await self.album("50", X, guid=f"mbid://{ORIG}", mbid=ORIG)
            # the re-add files the reissue's tracks, tagged alike, in the same album
            await self.file_in("50", Y, 200)
            for _ in range(2):
                for key in (X, Y):
                    await self.runner.check_unit(key)
                    await self.plex_stage(key)
        for key, other in ((X, Y), (Y, X)):
            plan = await self.open_issues(key)
            with self.subTest(unit=key):
                self.assertIn("plex_foreign", plan)
                self.assertEqual(plan["plex_foreign"]["detail"]["folders"], [other])
                self.assertNotIn("unmatched", plan)
                self.assertNotIn("wrong_match", plan)
        self.assertEqual(self.plex.match.await_count, 1)

    async def test_a_stale_match_on_a_foreign_album_is_not_applied(self):
        from app.metadata import apply
        await self.album("50", X, guid=f"mbid://{ORIG}", mbid=ORIG)
        await self.file_in("50", X, 100)
        await self.file_in("50", Y, 200)
        rows = await units.load_unit(Y)
        ctx = apply.Ctx(Y, rows, {"album": "Doggystyle", "release_mbid": REISSUE}, "Snoop Dogg")
        issue = {"kind": "unmatched", "detail": {"rating_key": "50", "release_mbid": REISSUE}}
        with self.services():
            self.assertEqual(await apply.do_match(issue, ctx), apply.SHARED_NOTE)
            self.assertEqual(await apply.do_poster(
                {"kind": "poster", "detail": {"rating_key": "50", "release_mbid": REISSUE}},
                ctx), apply.SHARED_NOTE)
            self.assertEqual(await apply.do_moods(
                {"kind": "moods", "detail": {"rating_key": "50", "to": ["Gritty"]}}, ctx),
                apply.SHARED_NOTE)
            # genres planned for the album go on the unit's own tracks only
            with mock.patch.object(apply, "_tags_now", mock.AsyncMock(return_value={
                    k: {"hip hop"} for k in ("201", "202", "203")})):
                await apply.do_genres({"kind": "genres", "detail": {
                    "rating_key": "50", "to": ["Hip Hop"], "tracks": ["201", "202", "203"]}},
                    ctx)
        self.plex.match.assert_not_awaited()
        self.plex.upload_poster.assert_not_awaited()
        self.assertEqual([c.args[0] for c in self.plex.set_tags.await_args_list],
                         [apply.TRACK])


class FlipFlopTestCase(PlexAlbumTestCase):
    """Plex album 235578, matched as the original and as the reissue in turn."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # the album holds only the reissue's files now, and the original's
        # match is on it
        await self.album("235578", Y, guid=f"mbid://{ORIG}", mbid=ORIG)
        await self.file_in("235578", Y, 200)

    async def test_two_mbids_in_14_days_stop_further_matches(self):
        from app.metadata import apply
        await self.journal_match("235578", X, ORIG, days_ago=12)
        await self.journal_match("235578", Y, REISSUE, days_ago=10)
        await self.journal_match("235578", X, ORIG, days_ago=1)
        rows = await units.load_unit(Y)
        ctx = apply.Ctx(Y, rows, {"album": "Doggystyle", "release_mbid": REISSUE}, "Snoop Dogg")
        with self.services():
            with self.assertRaises(apply.ApplyError) as caught:
                await apply._match("235578", REISSUE, "Doggystyle", ctx, kind="unmatched")
            self.plex.match.assert_not_awaited()
            self.plex.metadata.assert_not_awaited()
            self.assertIn(X, str(caught.exception))
            self.assertIn(Y, str(caught.exception))
            await self.runner.check_unit(Y)
        plan = await self.open_issues(Y)
        self.assertNotIn("unmatched", plan)
        self.assertEqual(plan["plex_foreign"]["detail"]["reason"], "flip_flop")
        self.assertEqual(sorted(plan["plex_foreign"]["detail"]["units"]), sorted([X, Y]))
        self.assertEqual(plan["plex_foreign"]["detail"]["rating_key"], "235578")

    async def test_old_matches_do_not_trip_it(self):
        await self.journal_match("235578", X, ORIG, days_ago=30)
        await self.journal_match("235578", Y, REISSUE, days_ago=20)
        await self.journal_match("235578", X, ORIG, days_ago=15)
        with self.services():
            await self.runner.check_unit(Y)
            plan = await self.open_issues(Y)
            self.assertIn("unmatched", plan)
            self.assertNotIn("plex_foreign", plan)
            await self.plex_stage(Y)
        self.assertEqual(self.plex.match.await_args.args[:2], ("235578", f"mbid://{REISSUE}"))


if __name__ == "__main__":
    unittest.main()
