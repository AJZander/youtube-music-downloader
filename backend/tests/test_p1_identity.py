"""One identity module: the same recording, the same album, the same artist
and path, and the better file, decided in one place (app.identity) and by
every caller the same way. About twenty matchers disagreed before (review
2026-11, §1 cause 2): the gate kept a 30 s-off copy as "have", the picker
took Black Friday (Live) for the studio song, the resolver rejected Come for
Came by 0.2 s, and a [Single] folder stood in for its album.

The rows are in identity_golden.py. Run with: python -m unittest discover backend/tests"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from identity_golden import ROWS
from test_attention import DbTestCase
from test_metadata import HAVE_FFMPEG


def rows_of(kind, policy=...):
    return [r for r in ROWS if r[0] == kind and (policy is ... or r[3] == policy)]


def name_of(side) -> str:
    return side if isinstance(side, str) else side.get("name") or side.get("album")


def hints_of(side) -> list:
    return [] if isinstance(side, str) else list(side.get("hints") or [])


def as_album(side, identity):
    """A golden album side as identity sees it: a name, or a file row."""
    if isinstance(side, str):
        return identity.album_id(side)
    if "path" in side:
        return identity.row_album_id(dict(side))
    return identity.album_id(side["name"], side.get("hints") or (), group=side.get("group"),
                             kind=side.get("kind"))


def folder_for(side) -> str:
    """Where a golden album side is filed: its row's folder, or an album
    folder named like it under its artist."""
    from app.paths import sanitize_component
    if not isinstance(side, str) and "path" in side:
        return side["path"].rsplit("/", 1)[0]
    artist = (hints_of(side) or ["A"])[0]
    return f"{artist}/{sanitize_component(name_of(side))}"


def policy_free(a, b, identity) -> bool:
    """Whether a golden album row's answer is the same by edition and by
    release group: then it holds for the callers that judge by any edition."""
    a, b = as_album(a, identity), as_album(b, identity)
    return bool(identity.same_album(a, b, "edition")) == bool(
        identity.same_album(a, b, "release_group"))


def tag_for(side) -> str:
    return side["album"] if not isinstance(side, str) and "path" in side else name_of(side)


def file_of(side: dict, path: str | None = None) -> dict:
    """A golden recording side as a library row."""
    path = path or side.get("path") or "A/Album/01.flac"
    folder, _, name = path.rpartition("/")
    row = {"path": path, "dirpath": folder, "artist_dir": path.split("/", 1)[0],
           "title": side["title"], "album": side.get("album"),
           "album_artist": path.split("/", 1)[0], "artist": side.get("artist"),
           "duration_sec": side.get("duration_sec"), "format": "flac", "bitrate_kbps": None,
           "sample_rate": 44100, "bit_depth": 16, "filesize_bytes": 1000,
           "track_number": side.get("track_number"), "disc_number": side.get("disc_number")}
    for key in ("mb_trackid", "mb_albumid", "mb_releasetrackid", "mb_releasegroupid"):
        if side.get(key):
            row[key] = side[key]
    return row


# ------------------------------------------------------------------ the table
class GoldenTestCase(unittest.TestCase):
    def test_the_table_is_big_enough(self):
        self.assertGreaterEqual(len(ROWS), 60)
        self.assertEqual(len({r[5] for r in ROWS}), len(ROWS), "case names are unique")

    def test_identity_agrees_with_the_table(self):
        from app import identity
        for kind, a, b, policy, expected, case in ROWS:
            with self.subTest(case=case):
                if kind == "recording" and policy is None:
                    verdict = identity.same_recording(a, b)
                    self.assertIsInstance(verdict, identity.Verdict)
                    self.assertEqual(bool(verdict), expected, verdict.reason)
                elif kind == "recording":
                    want = identity.side(a["title"], a["duration_sec"], number=a["track_number"],
                                         context=a.get("context") or ())
                    held = identity.side(b["title"], b["duration_sec"], number=b["track_number"],
                                         keys=identity.recording_keys(file_of(b)),
                                         upload=identity.title_looks_like_junk(b["title"]))
                    self.assertEqual(bool(identity.assign([want], [held])), expected)
                elif kind == "album" and policy == "split":
                    self.assertEqual(identity.split_key(a, None) == identity.split_key(b, None),
                                     expected)
                elif kind == "album":
                    verdict = identity.same_album(as_album(a, identity), as_album(b, identity),
                                                  policy)
                    self.assertEqual(bool(verdict), expected, verdict.reason)
                elif kind == "path":
                    self.assertEqual(identity.path_key(a) == identity.path_key(b), expected)
                elif kind == "artist":
                    self.assertEqual(bool(identity.artist_keys(a) & identity.artist_keys(b)),
                                     expected)
                elif kind == "quality":
                    ref = max(a["duration_sec"], b["duration_sec"])
                    for tags in (True, False):
                        better = (identity.file_quality(a, ref, tags=tags)[0]
                                  > identity.file_quality(b, ref, tags=tags)[0])
                        self.assertEqual(better, expected, f"tags={tags}")
                else:
                    self.fail(f"unknown kind {kind}")

    def test_unsure_is_not_the_same(self):
        from app import identity
        verdict = identity.same_album(identity.album_id(None), identity.album_id("X"))
        self.assertIsNone(verdict.same)
        self.assertFalse(verdict)

    def test_old_modules_still_answer_by_the_old_names(self):
        from app import identity, library, ownership
        from app.metadata import issues, units
        self.assertIs(library.strict_key, identity.strict_key)
        self.assertIs(library.title_keys, identity.title_keys)
        self.assertIs(ownership.title_keys, identity.release_title_keys)
        self.assertIs(ownership.version_markers, identity.version_markers)
        self.assertIs(units.split_key, identity.split_key)
        self.assertIs(issues.plain_recordings, identity.plain_recordings)


# ------------------------------------------------------------------ the callers
class CallersTestCase(DbTestCase):
    async def index_file(self, row: dict, provider_id: str | None = None) -> None:
        from app.engine.matcher import normalize
        await self.db.execute(
            "INSERT INTO library_index (norm_artist, norm_title, title, album, norm_album, "
            "album_artist, artist_dir, path, dirpath, duration_sec, provider_track_id, "
            "mb_releasegroupid, added_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (normalize(row.get("artist") or row["artist_dir"]), normalize(row["title"]),
             row["title"], row.get("album"), normalize(row.get("album") or ""),
             row.get("album_artist"), row["artist_dir"], row["path"], row["dirpath"],
             row.get("duration_sec"), provider_id, row.get("mb_releasegroupid"), "t"))

    async def lookup(self, row: dict, *, title, credit, album, length=None, live=False,
                     kind=None) -> dict:
        from app import ownership
        await self.db.execute("DELETE FROM library_index")
        await self.index_file(row)
        primary = (credit or "").split(",")[0].strip()
        index = await ownership.load_index({credit}, [primary], [])
        return index.lookup(title=title, credit=credit, album=album, length=length,
                            live_album=live, kind=kind)

    async def test_ownership_lookup_follows_the_table(self):
        """have exactly when the recording and the album are both the same.
        The album by name in any edition (release_group): what another
        edition holds is not downloaded again (§2), and a [Single] is not
        its album."""
        from app import identity
        for _, a, b, _, expected, case in rows_of("recording", None):
            if any(s.get(k) for s in (a, b) for k in ("mb_trackid", "mb_albumid")):
                continue    # a YouTube Music track has no MusicBrainz ids
            with self.subTest(case=case):
                found = await self.lookup(file_of(b), title=a["title"], credit=a["artist"],
                                          album=a["album"], length=a["duration_sec"],
                                          live=a.get("live", False))
                self.assertEqual(found["library"] == "have", expected, found)
        for _, a, b, policy, expected, case in rows_of("album"):
            if policy == "split" or any(isinstance(s, dict) and s.get("group") for s in (a, b)):
                continue    # no release group on the YouTube Music side
            with self.subTest(case=case):
                folder = folder_for(b)
                row = file_of({"title": "Song", "album": tag_for(b), "duration_sec": 200},
                              f"{folder}/01 - Song.flac")
                found = await self.lookup(row, title="Song", credit=row["artist_dir"],
                                          album=name_of(a), length=200,
                                          kind=None if isinstance(a, str) else a.get("kind"))
                same = bool(identity.same_album(as_album(a, identity), identity.row_album_id(row),
                                                "release_group"))
                if policy_free(a, b, identity):
                    self.assertEqual(same, expected)
                # …in the same version: a remix single's or a [Clean]
                # album's tracks are not the plain album's
                version = identity.markers_agree(identity.context_markers(name_of(a)),
                                                 identity.row_markers(row))
                self.assertEqual(found["library"] == "have", same and version, found)
        # Outnumbered: held at 265 s, wanted at 295 s, on its own album
        found = await self.lookup(
            file_of({"title": "Outnumbered", "album": "Zombie EP", "duration_sec": 265},
                    "The Devil Wears Prada/Zombie EP/03 - Outnumbered.opus"),
            title="Outnumbered", credit="The Devil Wears Prada", album="Zombie EP", length=295)
        self.assertEqual((found["library"], found["library_mismatch"]), ("elsewhere", True))
        self.assertEqual(found["library_folder"], "The Devil Wears Prada/Zombie EP")
        self.assertTrue(found["library_same_album"])

    async def test_a_same_video_match_stays_have(self):
        """A file downloaded from the very same video is the identical audio."""
        from app import ownership
        await self.index_file(file_of({"title": "Outnumbered", "album": "Zombie EP",
                                       "duration_sec": 265},
                                      "The Devil Wears Prada/Zombie EP/03 - Outnumbered.opus"),
                              provider_id="vid1")
        index = await ownership.load_index({"The Devil Wears Prada"}, ["The Devil Wears Prada"],
                                           ["vid1"])
        found = index.lookup(title="Outnumbered", credit="The Devil Wears Prada",
                             album="Zombie EP", video_id="vid1", length=295)
        self.assertEqual((found["library"], found["library_via"]), ("have", "video"))

    async def test_the_gate_lets_a_different_length_through(self):
        from app.library_index import find_duplicate_match
        await self.index_file(file_of({"title": "Outnumbered", "album": "Zombie EP",
                                       "duration_sec": 265},
                                      "The Devil Wears Prada/Zombie EP/03 - Outnumbered.opus"))
        self.assertIsNone(await find_duplicate_match(None, "The Devil Wears Prada", "Outnumbered",
                                                     "Zombie EP", 295))
        held = await find_duplicate_match(None, "The Devil Wears Prada", "Outnumbered",
                                          "Zombie EP", 266)
        self.assertEqual(held["library_path"],
                         "The Devil Wears Prada/Zombie EP/03 - Outnumbered.opus")

    async def test_a_bracketed_edition_filed_plainly_is_held(self):
        """Bring Me The Horizon came from "Sempiternal [Deluxe Edition]" and
        is filed and tagged as "(2013) Sempiternal". Queued again, the gate
        and the catalog hold it, by its very video and by its title (§2:
        nothing another edition holds is downloaded again)."""
        from app import ownership
        from app.library_index import find_duplicate_match
        path = "Bring Me The Horizon/(2013) Sempiternal/04 - Sleepwalking.m4a"
        await self.index_file(file_of({"title": "Sleepwalking", "album": "Sempiternal",
                                       "duration_sec": 230.13}, path), provider_id="vidS")
        for video in ("vidS", None):
            held = await find_duplicate_match(video, "Bring Me The Horizon", "Sleepwalking",
                                              "Sempiternal [Deluxe Edition]", 230.133)
            self.assertEqual((held or {}).get("library_path"), path, video)
        index = await ownership.load_index({"Bring Me The Horizon"}, ["Bring Me The Horizon"],
                                           ["vidS"])
        found = index.lookup(title="Sleepwalking", credit="Bring Me The Horizon",
                             album="Sempiternal [Deluxe Edition]", video_id="vidS",
                             length=230.133, kind="album")
        self.assertEqual((found["library"], found["library_via"]), ("have", "video"))

    async def test_a_single_is_held_by_its_own_track(self):
        """Slash's single "World on Fire" came from a Spotify playlist, whose
        album name says no type, and sits in "(2014) World on Fire
        [Single]". Queued again, the very same track is held. A release known
        to be the album is not held by it, even by one video (YouTube Music
        lists a single's video on its album too), nor is the album's track by
        title: a single doesn't hold its album's track (§2)."""
        from app.library_index import find_duplicate_match
        path = "Slash/(2014) World on Fire [Single]/01 - World on Fire.m4a"
        await self.index_file(file_of({"title": "World on Fire", "album": "World on Fire",
                                       "duration_sec": 271.07}, path), provider_id="sp1")
        for kind in (None, "single"):
            held = await find_duplicate_match("sp1", "Slash", "World on Fire", "World on Fire",
                                              271.07, kind=kind)
            self.assertEqual((held or {}).get("library_path"), path, kind)
        self.assertIsNone(await find_duplicate_match("sp1", "Slash", "World on Fire",
                                                     "World on Fire", 271.07, kind="album"))
        self.assertIsNone(await find_duplicate_match(None, "Slash", "World on Fire",
                                                     "World on Fire", 271.07))
        # the catalog knows each release's type: the single is held, the
        # album named like it is not
        from app import catalog, ownership
        index = await ownership.load_index({"Slash"}, ["Slash"], ["sp1"])
        release = {"title": "World on Fire", "artists": ["Slash"],
                   "tracks": [{"video_id": "sp1", "title": "World on Fire",
                               "duration_sec": 271, "available": True}]}
        for category, state in (("single", "have"), ("album", "elsewhere")):
            shown = catalog._annotate_album(release, index, None, category)
            self.assertEqual(shown["tracks"][0]["library"], state, category)

    async def test_the_worker_gives_the_gate_its_release_type(self):
        """A YouTube Music release's type comes from the tracklist its job
        was expanded from; a Spotify job's is not known, nor is an old
        job's whose tracklist is no longer cached."""
        from app import catalog, worker as worker_module
        await catalog._cache_put("album:MPREb_single1", {"type": "single", "tracks": []})
        await catalog._cache_put("album:MPREb_album1", {"type": "album", "tracks": []})
        cases = [("https://music.youtube.com/browse/MPREb_single1", "single"),
                 ("https://music.youtube.com/browse/MPREb_album1", "album"),
                 ("https://music.youtube.com/browse/MPREb_gone", None),
                 ("https://open.spotify.com/playlist/p1", None)]
        for n, (url, _) in enumerate(cases):
            await self.db.execute(
                "INSERT INTO jobs (id, source_url, provider, source_type, status, created_at) "
                "VALUES (?,?,?,?,?,?)", (f"j{n}", url, "youtube_music", "album", "downloading",
                                         "t"))
            await self.db.execute(
                "INSERT INTO tracks (id, job_id, position, provider, provider_track_id, artist, "
                "album, title, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"t{n}", f"j{n}", 1, "youtube_music", "v1", "A", "X", "Song", "pending", "t"))
        gate = mock.AsyncMock(return_value={"library_path": "A/X/01.m4a", "library_via": "video"})
        with mock.patch.object(worker_module, "find_duplicate_match", gate), \
                mock.patch.object(worker_module.bus, "publish"):
            worker = worker_module.Worker()
            for n in range(len(cases)):
                await worker._process_track(
                    await self.db.fetchone("SELECT * FROM tracks WHERE id = ?", (f"t{n}",)))
        self.assertEqual([c.kwargs.get("kind") for c in gate.await_args_list],
                         [kind for _, kind in cases])

    async def test_the_picker_and_the_resolver_share_one_matcher(self):
        from app import identity
        from app.metadata import missing, resolver
        for _, a, b, _, expected, case in rows_of("recording", "assign"):
            with self.subTest(case=case, caller="picker"):
                if not identity.title_looks_like_junk(b["title"]):    # album audio, no uploads
                    found, _ = missing._match(
                        [{"title": a["title"], "length": a["duration_sec"],
                          "position": a["track_number"], "disc": 1, "number": a["track_number"],
                          "context": a.get("context") or []}],
                        [{"video_id": "v1", "available": True, "title": b["title"],
                          "duration_sec": b["duration_sec"], "track_number": b["track_number"]}])
                    self.assertEqual(found == ["v1"], expected)
            with self.subTest(case=case, caller="resolver"):
                track = {"title": a["title"], "length": a["duration_sec"],
                         "number": a["track_number"], "disc": 1, "medium_title": "",
                         "_context": frozenset(a.get("context") or ())}
                row = {**file_of(b, f"{b.get('artist_dir') or 'A'}/X/01.flac"),
                       "disc_number": None}
                mapping = resolver.match_tracks([row], [track])
                self.assertEqual(bool(mapping), expected)
        # one matcher, both of them
        with mock.patch.object(identity, "assign", wraps=identity.assign) as spy:
            missing._match([{"title": "Pop", "length": 177, "position": 1}],
                           [{"video_id": "v", "available": True, "title": "Pop",
                             "duration_sec": 177, "track_number": 1}])
            resolver.match_tracks([file_of({"title": "Pop", "duration_sec": 177})],
                                  [{"title": "Pop", "length": 177, "number": 1, "disc": 1}])
        self.assertEqual(spy.call_count, 2)

    async def test_the_picker_assigns_the_whole_list_at_once(self):
        """Grinspoon's Best in Show: the live Black Friday sits at the studio
        song's place; the studio one is later on the YouTube Music album."""
        from app.metadata import missing
        found, not_found = missing._match(
            [{"title": "Black Friday", "length": 200, "position": 7}],
            [{"video_id": "live", "available": True, "title": "Black Friday (Live)",
              "duration_sec": 200, "track_number": 7},
             {"video_id": "studio", "available": True, "title": "Black Friday",
              "duration_sec": 201, "track_number": 12}])
        self.assertEqual((found, not_found), (["studio"], []))

    def test_track_duplicates_follow_the_table(self):
        """Never pairs two files the table calls different; pairs the plain
        same ones. (Trashing needs certainty: the duplicates finder keeps
        its 5 s with no upload slack, and only meets files by title key.)"""
        from app import library
        plain_same = {"Black Friday studio held as itself", "Chemical 231 s vs 230 s",
                      "a song really called 7 rings matches itself",
                      "Wake Up Dead studio held on disc 1",
                      "Paul Kelly & The Messengers credit Paul Kelly",
                      "a v1 video title is the song"}
        for _, a, b, _, expected, case in rows_of("recording", None):
            with self.subTest(case=case):
                held = file_of(b)
                folder = held["dirpath"]
                if case == "Wake Up Dead studio held on disc 1":
                    folder = held["dirpath"].rsplit("/", 1)[0] + "/Disc 2"
                wanted = file_of(a, a.get("path") or f"{folder}/zz - {a['title'][:20]}.flac")
                wanted.update({k: a[k] for k in ("mb_trackid", "mb_albumid", "mb_releasetrackid")
                               if a.get(k)})
                groups = library.find_track_duplicates([wanted, held])
                if not expected:
                    self.assertEqual(groups, [])
                elif case in plain_same:
                    self.assertEqual(len(groups), 1)

    def test_album_duplicates_follow_the_table(self):
        from app import library
        for _, a, b, policy, expected, case in rows_of("album", "edition"):
            if any(isinstance(s, dict) and (s.get("group") or s.get("kind")) for s in (a, b)):
                continue    # folders say no release type, and these have no MusicBrainz tags
            with self.subTest(case=case):
                rows = []
                for side, folder in ((a, folder_for(a)), (b, "A/Copies/" + folder_for(b).split("/", 1)[1])):
                    artist = (hints_of(side) or ["A"])[0]
                    for i, song in enumerate(("One", "Two", "Three"), start=1):
                        rows.append({**file_of({"title": song, "album": tag_for(side),
                                                "duration_sec": 200 + i},
                                               f"{folder}/{i:02d} - {song}.flac"),
                                     "artist_dir": "A", "album_artist": artist})
                groups = library.find_album_duplicates(rows)
                self.assertEqual(len(groups), 1 if expected else 0, groups)
        # a one-track [Single] folder tagged like its album is the single
        rows = [file_of({"title": "Walk", "album": "Innuendo", "duration_sec": 200},
                        "Queen/(1991) Innuendo [Single]/01 - Walk.flac")]
        rows += [file_of({"title": t, "album": "Innuendo", "duration_sec": 200 + i},
                         f"Queen/(1991) Innuendo/{i:02d} - {t}.flac")
                 for i, t in enumerate(("Innuendo", "Walk", "Bijou"), start=1)]
        rows[0]["duration_sec"] = 202
        self.assertEqual(library.find_album_duplicates(rows), [])

    def test_split_key_keeps_taylors_version_apart(self):
        from app.metadata import units
        from test_metadata import file_row
        for _, a, b, _, expected, case in rows_of("album", "split"):
            with self.subTest(case=case):
                rows = [file_row("A/B/1.flac", "x", album=a), file_row("A/B/2.flac", "y", album=b)]
                self.assertEqual(len(units.split_folder(rows)), 1 if expected else 2)
        rows = [file_row("Taylor Swift/1989/1.flac", "x", album="1989"),
                file_row("Taylor Swift/1989/2.flac", "y", album="1989 (Taylor's Version)")]
        self.assertEqual(len(units.split_folder(rows)), 2)
        rows = [file_row("2Pac/AAO/1.flac", "x", album="Against All Oddz"),
                file_row("2Pac/AAO/2.flac", "y", album="Against All Oddz (Collector's Edition)"),
                file_row("2Pac/AAO/3.flac", "z", album="Against All Oddz (Disc 2)")]
        self.assertEqual(list(units.split_folder(rows)), ["2Pac/AAO"])

    async def test_different_album_follows_the_table(self):
        from app import api, identity
        for _, a, b, policy, expected, case in rows_of("album", "edition"):
            if any(isinstance(s, dict) and (s.get("group") or s.get("kind")) for s in (a, b)):
                continue    # a skip outside Download missing is judged by names
            with self.subTest(case=case):
                await self.db.execute("DELETE FROM library_index")
                path = f"{folder_for(b)}/01 - Song.flac"
                await self.index_file(file_of({"title": "Song", "album": tag_for(b)}, path))
                skip = {"album": name_of(a), "library_path": path, "job_id": "j1"}
                label = api._different_album(skip, await api._album_context([skip]))
                verdict = identity.same_album(as_album(a, identity),
                                              identity.row_album_id(file_of(
                                                  {"title": "Song", "album": tag_for(b)}, path)),
                                              "release_group")
                self.assertEqual(label is None, bool(verdict), (label, verdict.reason))
                if policy_free(a, b, identity):
                    self.assertEqual(label is None, expected)

    def test_album_share_follows_the_table(self):
        from app import identity
        from app.metadata import plexsync
        for _, a, b, policy, expected, case in rows_of("album"):
            if policy == "split" or any(isinstance(s, dict) and s.get("kind") for s in (a, b)):
                continue
            with self.subTest(case=case):
                group = lambda s: None if isinstance(s, str) else s.get("group")
                info = {"album": {"rating_key": "1"}, "tracks": {},
                        "others": [{"path": "B/x/1.flac", "album": tag_for(b),
                                    "mb_releasegroupid": group(b)}]}
                share = plexsync.album_share(info, [{"album": tag_for(a),
                                                     "mb_releasegroupid": group(a)}])
                oracle = bool(identity.same_album(
                    identity.album_id(tag_for(a), group=group(a)),
                    identity.album_id(tag_for(b), group=group(b)), "release_group"))
                self.assertEqual(share["foreign"] == 0, oracle, share)
                # album tags alone: no folder, no artist to strip
                if policy == "release_group" or (isinstance(a, str) and isinstance(b, str)
                                                 and policy_free(a, b, identity)):
                    self.assertEqual(oracle, expected)

    async def test_discover_owned_follows_the_table(self):
        from app import discover, identity
        for _, a, b, policy, expected, case in rows_of("album"):
            if policy == "split" or any(isinstance(s, dict) and s.get("group") for s in (a, b)):
                continue
            with self.subTest(case=case):
                await self.db.execute("DELETE FROM library_index")
                path = f"{folder_for(b)}/01 - Song.flac"
                row = file_of({"title": "Song", "album": tag_for(b)}, path)
                await self.index_file(row)
                discover._feed.update(at=0.0)
                owned = await discover.owned_albums()
                kind = None if isinstance(a, str) else a.get("kind")
                got = discover._owned_by(owned, identity.strict_key(row["artist_dir"]),
                                         name_of(a), kind)
                oracle = bool(identity.same_album(as_album(a, identity),
                                                  identity.row_album_id(row), "release_group"))
                self.assertEqual(got, oracle)
        # the [Single] folder: the single is owned, the album is not
        await self.db.execute("DELETE FROM library_index")
        await self.index_file(file_of({"title": "Song", "album": "The Boys Light Up"},
                                      "Australian Crawl/(1980) The Boys Light Up [Single]/01.m4a"))
        discover._feed.update(at=0.0)
        owned = await discover.owned_albums()
        self.assertTrue(discover._owned_by(owned, "australiancrawl", "The Boys Light Up", "single"))
        self.assertFalse(discover._owned_by(owned, "australiancrawl", "The Boys Light Up", "album"))
        # the [Deluxe] the files came from, filed under the plain name
        await self.db.execute("DELETE FROM library_index")
        await self.index_file(file_of(
            {"title": "Playa [BONUS] (instrumental)", "album": "Me vs. Myself (Instrumental)"},
            "A Boogie Wit da Hoodie/(2022) Me vs. Myself (Instrumental)"
            "/23 - Playa [BONUS] (instrumental).m4a"))
        discover._feed.update(at=0.0)
        owned = await discover.owned_albums()
        self.assertTrue(discover._owned_by(owned, "aboogiewitdahoodie",
                                           "Me vs. Myself (Instrumental) [Deluxe]", "album"))

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
    async def test_one_quality_scorer(self):
        from app import identity, library
        from app.metadata import apply
        for _, a, b, _, expected, case in rows_of("quality"):
            ref = max(a["duration_sec"], b["duration_sec"])
            with self.subTest(case=case):
                for row in (a, b):
                    self.assertEqual(library.score_file(row, ref),
                                     identity.file_quality(row, ref, tags=True))
                    self.assertEqual(apply._audio_quality(row, ref),
                                     identity.file_quality(row, ref, tags=False)[0])
                self.assertEqual(library.score_file(a, ref)[0] > library.score_file(b, ref)[0],
                                 expected)
        # the copies merge keeps the copy file_quality ranks first
        from app.metadata import runner as runmod, units
        from test_p0_oscillation import KEEP, LibraryTestCase
        harness = LibraryTestCase("run")     # its fixtures, on this test's database
        harness.db, harness.lib, harness.runner = self.db, self.lib, runmod.Runner()
        with harness.services():
            await harness.make_thriller()
            await harness.check_all()
            [issue] = [i for i in await harness.plan_copies() if "rg-1" in i["key"]]
            rows = await units.load_unit(KEEP)
            want = json.loads((await self.db.fetchone(
                "SELECT desired FROM md_albums WHERE key = ?", (KEEP,)))["desired"])
            plan = await apply.copies_plan(issue, apply.Ctx(KEEP, rows, want, "Michael Jackson"))
        by_path = {r["path"]: r for r in rows}
        for move in plan["moves"]:
            if move["replaces"]:
                there = by_path[move["replaces"]]
                ref = max(move["row"]["duration_sec"], there["duration_sec"])
                self.assertGreater(identity.file_quality(move["row"], ref, tags=False)[0],
                                   identity.file_quality(there, ref, tags=False)[0])
                self.assertIn(move["replaces"], plan["trash"])
        self.assertTrue(any(m["replaces"] for m in plan["moves"]), plan)


if __name__ == "__main__":
    unittest.main()
