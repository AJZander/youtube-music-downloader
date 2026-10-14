"""Run with: python -m unittest discover backend/tests

The tag tests write real files (generated with ffmpeg), because the whole
point of tagio is what lands in each container."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import tagio
from app.metadata import desired, issues, resolver, seed, units

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def as_spelled():
    """build_album without a library index: folders are taken as spelled."""
    return mock.patch.object(desired, "on_disk", mock.AsyncMock(side_effect=lambda f, *_: f))


def make_audio(path: Path, seconds: float = 2.0) -> Path:
    codec = {".flac": ["-c:a", "flac"], ".mp3": ["-c:a", "libmp3lame"],
             ".m4a": ["-c:a", "aac"], ".opus": ["-c:a", "libopus"],
             ".ogg": ["-c:a", "libvorbis"], ".wav": ["-c:a", "pcm_s16le"]}[path.suffix]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={seconds}", *codec, str(path)],
                   check=True)
    return path


FORMATS = (".flac", ".mp3", ".m4a", ".opus", ".ogg", ".wav")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class TagioTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.templates = {ext: make_audio(cls.dir / f"template{ext}") for ext in FORMATS}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def fresh(self, ext: str) -> Path:
        p = self.dir / f"t-{self._testMethodName}{ext}"
        shutil.copy(self.templates[ext], p)
        return p

    FIELDS = {
        "title": ["Pop"], "artist": ["Drake feat. Rihanna"], "artists": ["Drake", "Rihanna"],
        "album": ["Celebrity"], "albumartist": ["*NSYNC"], "tracknumber": ["3"],
        "totaltracks": ["12"], "discnumber": ["1"], "totaldiscs": ["2"],
        "date": ["2001-07-24"], "originaldate": ["2001-07-24"],
        "genre": ["Pop", "Dance-Pop"], "isrc": ["USJI10100512"], "label": ["Jive"],
        "releasetype": ["album"], "compilation": ["1"],
        "mb_albumid": ["e61cdc79-76c8-4a76-9a97-60e36675d8d9"],
        "mb_releasegroupid": ["11111111-2222-3333-4444-555555555555"],
        "mb_albumartistid": ["603ba565-3967-4be1-931e-9cb945394e86"],
        "mb_artistid": ["aaaaaaaa-0000-0000-0000-000000000001",
                        "aaaaaaaa-0000-0000-0000-000000000002"],
        "mb_trackid": ["9bbea079-80e5-44ef-92ad-03940a520761"],
        "mb_releasetrackid": ["39478c98-cb6e-3e50-a10f-eee21bf7b3bb"],
    }

    def test_round_trip_every_format(self):
        for ext in FORMATS:
            with self.subTest(ext=ext):
                p = self.fresh(ext)
                tagio.write(p, self.FIELDS)
                got = tagio.read(p)["fields"]
                for field, want in self.FIELDS.items():
                    self.assertEqual(got.get(field), want, f"{ext} {field}")

    def test_genres_are_real_multi_values(self):
        """Plex reads "Pop, Rock" as one genre called "Pop, Rock"."""
        for ext in (".flac", ".mp3", ".m4a"):
            p = self.fresh(ext)
            tagio.write(p, {"genre": ["Pop", "Rock"]})
            self.assertEqual(tagio.read(p)["fields"]["genre"], ["Pop", "Rock"])

    def test_youtube_and_pmedia_junk_is_stripped_and_restorable(self):
        p = self.fresh(".flac")
        from mutagen.flac import FLAC
        audio = FLAC(p)
        audio["title"] = "Song"
        audio["genre"] = "Music"
        audio["comment"] = "PMEDIA"
        audio["purl"] = "https://www.youtube.com/watch?v=x"
        audio["label"] = "PMEDIA"
        audio["composer"] = "Real Composer"
        audio.save()
        info = tagio.read(p)
        self.assertIn("genre", info["junk"])
        self.assertIn("purl", info["junk"])
        self.assertIn("comment", info["junk"])
        result = tagio.write(p, {"genre": [], "label": ["Jive"]})
        after = FLAC(p)
        self.assertNotIn("purl", after)
        self.assertNotIn("comment", after)
        self.assertEqual(after["label"], ["Jive"])          # junk label replaced, not lost
        self.assertEqual(after["composer"], ["Real Composer"])
        self.assertNotIn("genre", after)
        tagio.restore(p, result["before"])
        back = FLAC(p)
        self.assertEqual(back["purl"], ["https://www.youtube.com/watch?v=x"])
        self.assertEqual(back["genre"], ["Music"])
        self.assertEqual(back["label"], ["PMEDIA"])

    def test_m4a_youtube_description_is_junk(self):
        p = self.fresh(".m4a")
        from mutagen.mp4 import MP4
        audio = MP4(p)
        audio["desc"] = ["Provided to YouTube by Stem"]
        audio["\xa9cmt"] = ["https://www.youtube.com/watch?v=x"]
        audio["\xa9gen"] = ["Music"]
        audio.save()
        tagio.write(p, {"genre": ["Hip Hop"]})
        after = MP4(p)
        self.assertNotIn("desc", after)
        self.assertNotIn("\xa9cmt", after)
        self.assertEqual(after["\xa9gen"], ["Hip Hop"])

    def test_write_reports_no_change_when_nothing_differs(self):
        p = self.fresh(".opus")
        tagio.write(p, {"title": ["Same"]})
        self.assertFalse(tagio.write(p, {"title": ["Same"]})["changed"])

    def test_audio_is_untouched(self):
        p = self.fresh(".mp3")
        before = tagio.read(p)["info"]["length"]
        tagio.write(p, self.FIELDS)
        self.assertAlmostEqual(tagio.read(p)["info"]["length"], before, places=2)


class GenreTextTestCase(unittest.TestCase):
    def test_split_and_junk(self):
        self.assertEqual(tagio.split_genres(["Pop, Rock; Metal"]), ["Pop", "Rock", "Metal"])
        self.assertEqual(tagio.split_genres(["Music"]), [])
        soup = "Avril Lavigne, blackbear, Travis Barker, Love Sux, Music Video, official"
        self.assertEqual(tagio.split_genres([soup]), [])

    def test_genre_display(self):
        from app.metadata.vocab import display_genre
        self.assertEqual(display_genre("hip hop"), "Hip Hop")
        self.assertEqual(display_genre("r&b"), "R&B")
        self.assertEqual(display_genre("synth-pop"), "Synth-Pop")
        self.assertEqual(display_genre("drum and bass"), "Drum and Bass")


def release(title="Celebrity", artist="*NSYNC", artist_id="603ba565", tracks=None,
            date="2001-07-24", disambiguation="", rg_date="2001-07-24", secondary=None):
    tracks = tracks or [("Pop", 177.0), ("Celebrity", 196.0), ("The Game Is Over", 230.0)]
    return {
        "id": "rel-1", "title": title, "date": date, "disambiguation": disambiguation,
        "status": "Official", "country": "US",
        "artist-credit": [{"name": artist, "joinphrase": "",
                           "artist": {"id": artist_id, "name": artist, "sort-name": artist}}],
        "release-group": {"id": "rg-1", "primary-type": "Album",
                          "secondary-types": secondary or [], "first-release-date": rg_date},
        "label-info": [{"label": {"name": "Jive"}}],
        "media": [{"position": 1, "format": "CD", "track-count": len(tracks), "tracks": [
            {"id": f"t{i}", "position": i, "number": str(i), "title": t, "length": int(d * 1000),
             "recording": {"id": f"r{i}", "title": t, "isrcs": []}}
            for i, (t, d) in enumerate(tracks, start=1)]}],
    }


def file_row(path, title, *, album="Celebrity", album_artist="*NSYNC", track=None,
             duration=177.0, **extra):
    parts = Path(path).parts
    return {"path": path, "title": title, "artist": album_artist, "album": album,
            "album_artist": album_artist, "track_number": track, "disc_number": None,
            "duration_sec": duration, "dirpath": str(Path(path).parent),
            "artist_dir": parts[0], "year": "2001", **extra}


class ResolverTestCase(unittest.TestCase):
    def test_youtube_titles_and_numbers_match_the_tracklist(self):
        rows = [file_row("NSYNC/Celebrity/a.m4a", "*NSYNC - Pop (Official Video)", duration=190.0),
                file_row("NSYNC/Celebrity/b.m4a", "Celebrity", track=2, duration=196.5),
                file_row("NSYNC/Celebrity/c.m4a", "Track 3", track=3, duration=230.4)]
        score, mapping = resolver.score_release(rows, release())
        self.assertEqual({p: t["title"] for p, t in mapping.items()}, {
            "NSYNC/Celebrity/a.m4a": "Pop", "NSYNC/Celebrity/b.m4a": "Celebrity",
            "NSYNC/Celebrity/c.m4a": "The Game Is Over"})
        self.assertGreater(score, 0.9)

    def test_a_live_version_is_not_the_studio_track(self):
        rows = [file_row("A/B/1.flac", "Pop (Live)", duration=178.0)]
        _, mapping = resolver.score_release(rows, release())
        self.assertEqual(mapping, {})

    def test_wrong_lengths_do_not_match(self):
        rows = [file_row("A/B/1.flac", "Pop", duration=260.0)]
        _, mapping = resolver.score_release(rows, release())
        self.assertEqual(mapping, {})

    def test_other_album_scores_low(self):
        rows = [file_row(f"X/Y/{i}.flac", t, album="Something Else", album_artist="Someone",
                         duration=d)
                for i, (t, d) in enumerate([("Alpha", 100.0), ("Beta", 120.0)])]
        score, _ = resolver.score_release(rows, release())
        self.assertLess(score, 0.55)

    def test_someone_elses_song_of_the_same_name_is_never_automatic(self):
        rows = [file_row("Demi Lovato/(2018) Ali Abellan - Warrior/1.flac", "Warrior",
                         album="Warrior", album_artist="Ali Abellan", duration=200.0)]
        rel = release(title="Warrior", artist="Miss Tun Pickney", artist_id="x",
                      tracks=[("Warrior", 201.0)])
        score, mapping = resolver.score_release(rows, rel)
        self.assertEqual(len(mapping), 1)
        self.assertLess(score, 0.85)
        self.assertGreaterEqual(score, 0.55)       # offered for review

    def test_a_remix_of_the_same_length_is_not_the_original(self):
        rows = [file_row("Demi Lovato/Solo (Latin Remix)/1.flac", "Solo (Latin Remix)", track=1,
                         album="Solo", album_artist="Clean Bandit", duration=222.7)]
        rel = release(title="Solo", artist="Clean Bandit", artist_id="cb",
                      tracks=[("Solo", 222.0)])
        _, mapping = resolver.score_release(rows, rel)
        self.assertEqual(mapping, {})

    def test_songs_marked_live_match_a_live_album(self):
        rows = [file_row("A/B/1.flac", "Pop (Live)", duration=181.0)]
        _, mapping = resolver.score_release(rows, release(secondary=["Live"]))
        self.assertEqual([t["title"] for t in mapping.values()], ["Pop"])

    def test_songs_marked_live_match_a_live_disc(self):
        rel = release()
        rel["media"].append({"position": 2, "title": "Live at the Crocodile Cafe", "format": "CD",
                             "track-count": 1, "tracks": [
                                 {"id": "t9", "position": 1, "number": "1", "title": "Pop",
                                  "length": 181000, "recording": {"id": "r9", "title": "Pop"}}]})
        rows = [file_row("A/B/CD2/1.flac", "Pop (Live)", track=1, disc_number=2, duration=181.0)]
        _, mapping = resolver.score_release(rows, rel)
        self.assertEqual([t["track_id"] for t in mapping.values()], ["t9"])

    def test_a_cover_in_the_original_artists_folder_is_never_automatic(self):
        rows = [file_row("Demi Lovato/(2021) Nomentory - Heart Attack/1.flac", "Heart Attack",
                         album="Heart Attack", album_artist="Nomentory", duration=209.1)]
        rel = release(title="Heart Attack", artist="Demi Lovato", artist_id="dl",
                      tracks=[("Heart Attack", 210.0)])
        score, mapping = resolver.score_release(rows, rel)
        self.assertEqual(len(mapping), 1)
        self.assertLess(score, 0.85)

    def test_the_folder_names_the_artist_when_the_files_do_not(self):
        rows = [file_row("Demi Lovato/Heart Attack/1.flac", "Heart Attack",
                         album="Heart Attack", album_artist=None, duration=209.1)]
        rel = release(title="Heart Attack", artist="Demi Lovato", artist_id="dl",
                      tracks=[("Heart Attack", 210.0)])
        score, _ = resolver.score_release(rows, rel)
        self.assertGreater(score, 0.85)

    def test_a_featured_artist_counts_as_ours(self):
        rows = [file_row("Pitbull/Singles/1.flac", "Take Ova", album="No Gravity",
                         album_artist="Pitbull", duration=200.0)]
        rel = release(title="No Gravity", artist="Shontelle", artist_id="s",
                      tracks=[("Take Ova", 201.0), ("Other", 180.0)])
        rel["media"][0]["tracks"][0]["artist-credit"] = [
            {"name": "Shontelle", "joinphrase": " feat. ", "artist": {"id": "s", "name": "Shontelle"}},
            {"name": "Pitbull", "joinphrase": "", "artist": {"id": "p", "name": "Pitbull"}}]
        score, _ = resolver.score_release(rows, rel)
        self.assertGreaterEqual(score, 0.85)

    def pressings(self, *variants, plex=None, rows=None):
        rows = rows or [file_row("Beatles/PPM/14.m4a", "Twist and Shout", album="Please Please Me",
                                 album_artist="The Beatles", duration=155.0)]
        rels = {}
        for rid, date, dis in variants:
            rel = release(title="Please Please Me", artist="The Beatles", date=date,
                          disambiguation=dis, rg_date="1963-03-22",
                          tracks=[(f"Song {i}", 120.0) for i in range(1, 14)]
                          + [("Twist and Shout", 155.0)])
            rel["id"] = rid
            rels[rid] = rel
        hits = [{"id": rid, "score": 100, "track-count": 14} for rid in rels if rid != plex]

        async def fetch(mbid):
            return rels[mbid]
        with mock.patch.object(resolver.mb, "enabled", return_value=True), \
                mock.patch.object(resolver.mb, "release", mock.AsyncMock(side_effect=fetch)), \
                mock.patch.object(resolver.mb, "isrc_releases", mock.AsyncMock(return_value=[])), \
                mock.patch.object(resolver.mb, "search_releases",
                                  mock.AsyncMock(return_value=hits)):
            return asyncio.run(resolver.resolve(rows, plex_mbid=plex))

    def test_the_original_pressing_beats_plexs_reissue(self):
        res = self.pressings(("box", "1969", "mono, one EMI box"),
                             ("first", "1963-03-22", "mono, 1st press"),
                             ("fourth", "1963-05", "mono, 4th press"), plex="box")
        self.assertEqual(res.release["id"], "first")

    def test_the_explicit_version_beats_the_clean_one(self):
        res = self.pressings(("clean", "1963-03-22", "clean lyrics"),
                             ("explicit", "1963-03-23", ""))
        self.assertEqual(res.release["id"], "explicit")

    def test_plexs_reissue_of_a_full_album_still_loses_to_the_original(self):
        rows = [file_row(f"B/PPM/{i}.m4a", f"Song {i}", album="Please Please Me",
                         album_artist="The Beatles", duration=120.0) for i in range(1, 14)]
        rows.append(file_row("B/PPM/14.m4a", "Twist and Shout", album="Please Please Me",
                             album_artist="The Beatles", duration=155.0))
        res = self.pressings(("reissue", "2009-09-09", "2009 remaster"),
                             ("first", "1963-03-22", ""), plex="reissue", rows=rows)
        self.assertEqual(res.release["id"], "first")
        self.assertEqual({c["release_mbid"] for c in res.candidates}, {"reissue", "first"})

    def test_a_merged_release_id_resolves_to_the_surviving_release(self):
        # Files tagged with a release MusicBrainz has since merged away: the
        # lookup answers with the surviving release, under its own id.
        rows = [file_row(f"NSYNC/Celebrity/{i}.flac", t, track=i, duration=d, mb_albumid="old-id")
                for i, (t, d) in enumerate([("Pop", 177.0), ("Celebrity", 196.0),
                                            ("The Game Is Over", 230.0)], start=1)]
        with mock.patch.object(resolver.mb, "enabled", return_value=True), \
                mock.patch.object(resolver.mb, "release", mock.AsyncMock(return_value=release())):
            res = asyncio.run(resolver.resolve(rows, plex_mbid="old-id"))
            self.assertEqual(res.status, "resolved")
            self.assertEqual(res.release["id"], "rel-1")
            self.assertEqual([c["release_mbid"] for c in res.candidates], ["rel-1"])
            picked = asyncio.run(resolver.resolve(rows, manual="old-id"))
            self.assertEqual(picked.release["id"], "rel-1")


class SearchTermsTestCase(unittest.TestCase):
    def test_clean_title(self):
        self.assertEqual(resolver.clean_title("Como No (feat. Becky G)"), "Como No")
        self.assertEqual(resolver.clean_title("(2013) - Yours Truly [FLAC 24bit]"), "Yours Truly")
        self.assertEqual(resolver.clean_title("Paradise (Official Audio)"), "Paradise")

    def test_primary_artist(self):
        self.assertEqual(resolver.primary_artist("3%, Fred Leone"), "3%")
        self.assertEqual(resolver.primary_artist("AFTER MIDNIGHT, Matroda, San Pacho"),
                         "AFTER MIDNIGHT")
        self.assertEqual(resolver.primary_artist("Drake feat. Rihanna"), "Drake")
        self.assertEqual(resolver.primary_artist("Paul Kelly"), "Paul Kelly")

    def test_artist_name_inside_the_folder_is_dropped(self):
        rows = [file_row("Ariana Grande/(2013) - Ariana Grande - Yours Truly/1.flac", "x",
                         album=None, album_artist="Ariana Grande")]
        self.assertEqual(resolver.search_terms(rows), ("Yours Truly", "Ariana Grande"))


class DesiredTestCase(unittest.TestCase):
    def test_edition_for_a_remaster(self):
        rel = release(date="2004-03-22", disambiguation="2004 remaster", rg_date="1977-02-04")
        self.assertEqual(desired.edition_of(rel, "1977"), ("2004 Remaster", None))

    def test_edition_for_a_plain_reissue(self):
        rel = release(date="2011-01-01", rg_date="1977-02-04")
        self.assertEqual(desired.edition_of(rel, "1977"), ("2011 Reissue", None))

    def test_original_release_has_no_edition(self):
        self.assertEqual(desired.edition_of(release(), "2001"), (None, None))

    def test_explicit_is_dropped_from_an_edition(self):
        rel = release(date="2005-01-01", disambiguation="2005 digitally remastered, explicit",
                      rg_date="1996-02-13")
        self.assertEqual(desired.edition_of(rel, "1996"), ("2005 Digitally Remastered", None))

    def test_a_variant_is_named_but_a_pressing_is_not(self):
        rel = release(disambiguation="Australian version")
        self.assertEqual(desired.edition_of(rel, "2001"), (None, "Australian Version"))
        rel = release(disambiguation="mono, 1st press")
        self.assertEqual(desired.edition_of(rel, "2001"), (None, None))
        rel = release(date="1969", disambiguation="mono, one EMI box", rg_date="1963-03-22")
        self.assertEqual(desired.edition_of(rel, "1963"), ("1969 Reissue", None))

    def test_an_edition_the_title_already_carries_is_not_repeated(self):
        self.assertTrue(desired._said_in("2021 20th Anniversary, Deluxe",
                                         "The Better Life (20th anniversary deluxe)"))
        self.assertTrue(desired._said_in("2023 Reissue", "Big Overdrive (Deluxe)"))
        self.assertTrue(desired._said_in("2023 Reissue", "Big Overdrive Deluxe"))
        self.assertFalse(desired._said_in("2004 Remaster", "Rumours"))
        self.assertFalse(desired._said_in("2009 Reissue", "Station to Station"))
        rel = release(date="2021-01-01", disambiguation="20th anniversary / deluxe",
                      rg_date="2000-02-08")
        self.assertEqual(desired.edition_of(rel, "2000"), ("2021 20th Anniversary, Deluxe", None))

    def test_explicit_is_not_an_edition(self):
        rel = release(disambiguation="explicit")
        self.assertEqual(desired.edition_of(rel, "2001"), (None, None))

    def test_various_artists(self):
        rel = release(artist="Various Artists",
                      artist_id="89ad4ac3-39f7-470e-963a-56509c546377")
        aa = desired.album_artist_of(rel)
        self.assertTrue(aa["various"])
        self.assertEqual(aa["name"], "Various Artists")

    def test_the_album_artist_is_named_as_credited(self):
        rel = release(artist="Kanye West")
        rel["artist-credit"][0]["artist"] = {"id": "ye", "name": "Ye", "sort-name": "Ye"}
        aa = desired.album_artist_of(rel)
        self.assertEqual((aa["name"], aa["sort"], aa["mbid"]), ("Kanye West", "Kanye West", "ye"))

    def test_a_spelling_of_the_artists_name_is_written_as_listed(self):
        rel = release(artist="Art vs Science")
        rel["artist-credit"][0]["artist"] = {"id": "avs", "name": "Art vs. Science",
                                             "sort-name": "Art vs. Science"}
        self.assertEqual(desired.album_artist_of(rel)["name"], "Art vs. Science")

    def test_typographic_punctuation_is_made_plain(self):
        rel = release(artist="Wu\u2010Tang Clan")
        self.assertEqual(desired.album_artist_of(rel)["name"], "Wu-Tang Clan")
        self.assertEqual(desired.plain("\u201974 Jailbreak\u2026"), "'74 Jailbreak...")
        self.assertEqual(desired.plain("25\u1d57\u02b0 Anniversary"), "25th Anniversary")
        self.assertEqual(desired.plain("Volumes 1\u20133 \u2014 12\u2033"), 'Volumes 1-3 - 12"')
        self.assertEqual(desired.plain("La\U000e0031ufey"), "Laufey")

    def test_primary_artist_is_the_album_artist(self):
        rel = release()
        rel["artist-credit"] = [
            {"name": "3%", "joinphrase": " & ", "artist": {"id": "a1", "name": "3%"}},
            {"name": "Fred Leone", "joinphrase": "", "artist": {"id": "a2", "name": "Fred Leone"}}]
        aa = desired.album_artist_of(rel)
        self.assertEqual(aa["name"], "3%")
        self.assertEqual(aa["credit"], "3% & Fred Leone")

    def test_build_album_layout_and_tags(self):
        rows = [file_row("NSYNC/old/a.flac", "Pop", track=1, duration=177.0),
                file_row("NSYNC/old/b.flac", "Celebrity", track=2, duration=196.0),
                file_row("NSYNC/old/x.flac", "Bonus Thing", duration=100.0)]
        rel = release(date="2004-03-22", disambiguation="2004 remaster", rg_date="2001-07-24")
        score, mapping = resolver.score_release(rows, rel)
        res = resolver.Resolution(status="resolved", release=rel, score=score, source="test",
                                  track_map=mapping)
        rg = {"id": "rg-1", "first-release-date": "2001-07-24",
              "genres": [{"name": "pop", "count": 5}, {"name": "dance-pop", "count": 2}]}
        with mock.patch.object(desired.mb, "release_group", mock.AsyncMock(return_value=rg)), \
                mock.patch.object(desired.lastfm, "album_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(desired.lastfm, "artist_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(desired.mb, "artist", mock.AsyncMock(return_value={})), \
                as_spelled():
            want = asyncio.run(desired.build_album(rows, res, plex_album=None, plex_artist=None,
                                                   overrides={}))
        self.assertEqual(want["folder"], "_NSYNC/(2001) Celebrity [2004 Remaster]")
        self.assertEqual(want["tracks"]["NSYNC/old/a.flac"]["target"],
                         "_NSYNC/(2001) Celebrity [2004 Remaster]/01 - Pop.flac")
        self.assertEqual(want["genres"], ["Pop", "Dance-Pop"])
        extra = want["tracks"]["NSYNC/old/x.flac"]
        self.assertFalse(extra["matched"])
        self.assertEqual(extra["target"], "_NSYNC/(2001) Celebrity [2004 Remaster]/x.flac")
        tags = want["tracks"]["NSYNC/old/b.flac"]["tags"]
        self.assertEqual(tags["mb_albumid"], ["rel-1"])
        self.assertEqual(tags["mb_releasetrackid"], ["t2"])
        self.assertEqual(tags["originaldate"], ["2001-07-24"])
        self.assertEqual(tags["date"], ["2004-03-22"])

    def test_overrides_win(self):
        rows = [file_row("A/B/a.flac", "Pop", track=1)]
        rel = release()
        score, mapping = resolver.score_release(rows, rel)
        res = resolver.Resolution(status="resolved", release=rel, score=score, track_map=mapping)
        with mock.patch.object(desired.mb, "release_group", mock.AsyncMock(return_value=None)), \
                mock.patch.object(desired.lastfm, "album_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(desired.lastfm, "artist_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(desired.mb, "artist", mock.AsyncMock(return_value={})), \
                as_spelled():
            want = asyncio.run(desired.build_album(rows, res, plex_album=None, plex_artist=None,
                                                   overrides={"genres": ["Boy Band"]}))
        self.assertEqual(want["genres"], ["Boy Band"])
        self.assertEqual(want["genre_source"], "override")


class IssuesTestCase(unittest.TestCase):
    def test_tag_diff_ignores_what_is_already_right(self):
        row = file_row("A/B/a.flac", "Pop", track=1, genre="Pop; Dance-Pop", mb_albumid="rel-1")
        want = {"title": ["Pop"], "tracknumber": ["1"], "genre": ["Pop", "Dance-Pop"],
                "mb_albumid": ["rel-1"], "album": ["Celebrity"], "albumartist": ["*NSYNC"]}
        self.assertEqual(issues.tag_diff(row, want), {})

    def test_tag_diff_reports_changes(self):
        row = file_row("A/B/a.flac", "Pop (Official Video)", track=None)
        diff = issues.tag_diff(row, {"title": ["Pop"], "tracknumber": ["1"]})
        self.assertEqual(set(diff), {"title", "tracknumber"})

    def test_junk_fix_splits_joined_genres(self):
        row = file_row("A/B/a.flac", "Pop", genre="Pop, Rock")
        self.assertEqual(issues.junk_fix_fields(row), {"genre": ["Pop", "Rock"]})
        self.assertEqual(issues.junk_fix_fields(file_row("A/B/a.flac", "x", genre="Rock")), {})


class TrackOrderTestCase(unittest.TestCase):
    def plan(self, rows, plex=None):
        rel = release()
        score, mapping = resolver.score_release(rows, rel)
        res = resolver.Resolution(status="resolved", release=rel, score=score, track_map=mapping)
        with mock.patch.object(desired.mb, "release_group", mock.AsyncMock(return_value=None)), \
                mock.patch.object(desired.lastfm, "album_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(desired.lastfm, "artist_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(desired.mb, "artist", mock.AsyncMock(return_value={})), \
                mock.patch.object(issues, "move_conflict", mock.AsyncMock(return_value=None)), \
                as_spelled():
            want = asyncio.run(desired.build_album(rows, res, plex_album=None, plex_artist=None,
                                                   overrides={}))
            return asyncio.run(issues.plan_unit("A/B", rows, want, res, plex,
                                                plex_synced=False,
                                                ours_locked=mock.AsyncMock(return_value=False)))

    def test_wrong_numbers_are_a_track_order_issue(self):
        rows = [file_row("A/B/1.flac", "Pop", track=2, duration=177.0),
                file_row("A/B/2.flac", "Celebrity", track=1, duration=196.0)]
        plan = {p["kind"]: p for p in self.plan(rows)}
        self.assertIn("track_order", plan)
        self.assertEqual({f["path"] for f in plan["track_order"]["detail"]["files"]},
                         {"A/B/1.flac", "A/B/2.flac"})
        self.assertNotIn("tracknumber", (plan.get("tags") or {}).get("detail", {}).get("fields", {}))

    def test_plex_order_is_checked_when_the_files_are_right(self):
        rows = [file_row("A/B/1.flac", "Pop", track=1, duration=177.0)]
        plex = {"album": None, "tracks": {"A/B/1.flac": {"rating_key": "9", "idx": 4,
                                                         "parent_idx": 1, "parent_key": "x"}}}
        plan = {p["kind"]: p for p in self.plan(rows, plex)}
        self.assertEqual(plan["track_order"]["detail"]["plex"][0]["index"], 1)

    def test_right_numbers_raise_nothing(self):
        rows = [file_row("A/B/1.flac", "Pop", track=1, duration=177.0)]
        self.assertNotIn("track_order", {p["kind"] for p in self.plan(rows)})


class MissingMatchTestCase(unittest.TestCase):
    def test_missing_tracks_found_on_the_youtube_album(self):
        from app.metadata import missing
        want = [{"title": "Celebrity", "length": 196.0, "disc": 1, "number": 2},
                {"title": "Gone (Live)", "length": 250.0, "disc": 1, "number": 9}]
        ytm = [{"video_id": "a", "title": "Pop", "duration_sec": 177, "available": True},
               {"video_id": "b", "title": "Celebrity", "duration_sec": 197, "available": True},
               {"video_id": "c", "title": "Gone", "duration_sec": 250, "available": True}]
        found, not_found = missing._match(want, ytm)
        self.assertEqual(found, ["b"])             # the studio "Gone" is not the live one
        self.assertEqual([t["title"] for t in not_found], ["Gone (Live)"])

    def test_respelled_titles_match_by_place_and_length(self):
        from app.metadata import missing
        want = [{"title": "All Bout U", "length": 277.0, "disc": 1, "number": 2, "position": 2},
                {"title": "What’z Ya Phone #", "length": 310.0, "disc": 1, "number": 14,
                 "position": 14}]
        ytm = [{"video_id": "x", "title": "All About U (feat. Snoop Dogg)", "duration_sec": 277,
                "available": True, "track_number": 2},
               {"video_id": "y", "title": "What's Ya Phone # (feat. Danny Boy)", "duration_sec": 310,
                "available": True, "track_number": 14}]
        self.assertEqual(missing._match(want, ytm)[0], ["x", "y"])

    def test_length_guard(self):
        from app.metadata import missing
        want = [{"title": "Celebrity", "length": 196.0, "disc": 1, "number": 2}]
        ytm = [{"video_id": "b", "title": "Celebrity", "duration_sec": 260, "available": True}]
        self.assertEqual(missing._match(want, ytm)[0], [])


class UnitsTestCase(unittest.TestCase):
    def test_a_folder_of_singles_splits_by_album_tag(self):
        rows = [file_row("Akon/Singles/a.flac", "A", album="Single A"),
                file_row("Akon/Singles/b.flac", "B", album="Single B"),
                file_row("Akon/Singles/c.flac", "C", album=None)]
        out = units.split_folder(rows)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(k.startswith("Akon/Singles|") for k in out))
        self.assertEqual(sum(len(v) for v in out.values()), 3)

    def test_edition_and_disc_suffixes_do_not_split_a_folder(self):
        rows = [file_row("2Pac/AAO/1.flac", "x", album="Against All Oddz"),
                file_row("2Pac/AAO/2.flac", "y", album="Against All Oddz (Collector's Edition)"),
                file_row("2Pac/AAO/3.flac", "z", album="Against All Oddz (Disc 2)")]
        self.assertEqual(list(units.split_folder(rows)), ["2Pac/AAO"])

    def test_an_album_folder_is_one_unit(self):
        rows = [file_row("A/B/1.flac", "x"), file_row("A/B/2.flac", "y")]
        self.assertEqual(list(units.split_folder(rows)), ["A/B"])


    def test_disc_subfolders_are_one_album(self):
        rows = [file_row("Queen/On Air/CD 1 - The BBC Sessions/1.flac", "x", album="On Air"),
                file_row("Queen/On Air/CD 2 - The BBC Sessions/1.flac", "y", album="On Air"),
                file_row("Kendrick/Mr. Morale/CD2/1.flac", "z", album="Mr. Morale")]
        self.assertEqual(sorted(units.build_units(rows)), ["Kendrick/Mr. Morale", "Queen/On Air"])

    def test_only_a_disc_folder_inside_an_album_folder_joins_it(self):
        self.assertEqual(units.unit_dir("A/B/Disc 2"), "A/B")
        self.assertEqual(units.unit_dir("A/B/disc2 - Live at the Crocodile Cafe"), "A/B")
        self.assertEqual(units.unit_dir("A/CD1"), "A/CD1")
        self.assertEqual(units.unit_dir("A/B/Discovery"), "A/B/Discovery")

class SeedTestCase(unittest.TestCase):
    def test_seed_has_tracks_and_lengths(self):
        rows = [file_row("401 WST/Move/1.m4a", "Move", track=1, duration=180.5,
                         provider_track_id="abc")]
        out = seed.seed_fields(rows)
        self.assertEqual(out["action"], "https://musicbrainz.org/release/add")
        f = out["fields"]
        self.assertEqual(f["mediums.0.track.0.name"], "Move")
        self.assertEqual(f["mediums.0.track.0.length"], "180500")
        self.assertEqual(f["urls.0.url"], "https://music.youtube.com/watch?v=abc")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not available")
class ApplyRoundTripTestCase(unittest.IsolatedAsyncioTestCase):
    """Tags, then a move, then undo both — on a scratch library and DB."""

    async def asyncSetUp(self):
        from app import library
        from app.config import settings
        from app.db import db
        self.tmp = Path(tempfile.mkdtemp())
        self.lib = self.tmp / "library"
        (self.lib / "NSYNC" / "old").mkdir(parents=True)
        (self.lib / ".mdl-library-online").touch()
        self.patches = [mock.patch.object(settings, "data_dir", self.tmp / "data"),
                        mock.patch.object(settings, "library_dir", self.lib),
                        mock.patch.object(settings, "plex_token", "")]
        for p in self.patches:
            p.start()
        await db.connect()
        self.db = db
        for i, (title, secs) in enumerate([("Pop", 3.0), ("Celebrity", 4.0)], start=1):
            path = make_audio(self.lib / "NSYNC" / "old" / f"{i:02d} {title}.flac", secs)
            # what yt-dlp leaves behind: YouTube's category as the genre
            tagio.write(path, {"title": [title], "artist": ["NSYNC"], "album": ["Celebrity"],
                               "genre": ["Music"]}, clean_genres=False)
        (self.lib / "NSYNC" / "old" / "folder.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 10)
        await library.scan(full=True)

    async def asyncTearDown(self):
        await self.db.close()
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_tags_move_and_undo(self):
        from app.metadata import apply
        rows = await units.load_unit("NSYNC/old")
        self.assertEqual(len(rows), 2)
        rel = release(tracks=[("Pop", 3.0), ("Celebrity", 4.0)])
        score, mapping = resolver.score_release(rows, rel)
        self.assertEqual(len(mapping), 2)
        res = resolver.Resolution(status="resolved", release=rel, score=score, track_map=mapping)
        with mock.patch.object(desired.mb, "release_group", mock.AsyncMock(return_value=None)), \
                mock.patch.object(desired.lastfm, "album_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(desired.lastfm, "artist_tags", mock.AsyncMock(return_value=[])), \
                mock.patch.object(desired.mb, "artist", mock.AsyncMock(return_value={})):
            want = await desired.build_album(rows, res, plex_album=None, plex_artist=None,
                                             overrides={})
        plan = await issues.plan_unit("NSYNC/old", rows, want, res, None, plex_synced=False,
                                      ours_locked=apply.ours_locked)
        kinds = {p["kind"] for p in plan}
        self.assertIn("tags", kinds)
        self.assertIn("move", kinds)

        # moved first, as an apply does (Plex follows only an unchanged file)
        ctx = apply.Ctx("NSYNC/old", rows, want, "NSYNC")
        await apply.do_move({"kind": "move", "detail": {}}, ctx)
        await apply.do_tags({"kind": "tags", "detail": {}}, ctx)
        target = self.lib / "_NSYNC" / "(2001) Celebrity" / "01 - Pop.flac"
        self.assertTrue(target.exists())
        self.assertTrue((self.lib / "_NSYNC" / "(2001) Celebrity" / "folder.jpg").exists())
        self.assertFalse((self.lib / "NSYNC" / "old").exists())
        fields = tagio.read(target)["fields"]
        self.assertEqual(fields["albumartist"], ["*NSYNC"])
        self.assertEqual(fields["mb_albumid"], ["rel-1"])
        self.assertNotIn("genre", fields)    # junk "Music" gone, no real genre known
        row = await self.db.fetchone("SELECT path FROM library_index WHERE path = ?",
                                     ("_NSYNC/(2001) Celebrity/01 - Pop.flac",))
        self.assertIsNotNone(row)

        # undo the move, then the tags (written after it, so found back home)
        move_batch, tags_batch = ctx.batches
        result = await apply.revert(move_batch)
        self.assertFalse(result["failures"], result)
        original = self.lib / "NSYNC" / "old" / "01 Pop.flac"
        self.assertTrue(original.exists())
        result = await apply.revert(tags_batch)
        self.assertFalse(result["failures"], result)
        back = tagio.read(original)["fields"]
        self.assertEqual(back["genre"], ["Music"])
        self.assertEqual(back["artist"], ["NSYNC"])
        self.assertNotIn("mb_albumid", back)


class PacingTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_request_starts_are_spaced_by_the_interval(self):
        import time
        from app.metadata import musicbrainz as mbmod
        client = mbmod.Client()
        with mock.patch.object(mbmod.settings, "musicbrainz_interval", 0.2):
            t = time.monotonic()
            await asyncio.gather(*(client._slot() for _ in range(4)))
            self.assertGreaterEqual(time.monotonic() - t, 0.55)   # 3 gaps of 0.2s


class PlexScanFilterTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_music_library_counts(self):
        from app import plex as plexmod
        client = plexmod.PlexClient()
        client._section_title = "Music"
        other = [{"type": "library.update.section", "title": "Scanning TV Shows"},
                 {"type": "media.generate.music.analysis", "title": "Sonic Analysis"}]
        with mock.patch.object(client, "activities", mock.AsyncMock(return_value=other)):
            self.assertFalse(await client.scanning())
        ours = other + [{"type": "library.update.section", "title": "Scanning Music"}]
        with mock.patch.object(client, "activities", mock.AsyncMock(return_value=ours)):
            self.assertTrue(await client.scanning())
        by_context = [{"type": "library.update.section", "title": "Scanning",
                       "Context": {"librarySectionID": "4"}}]
        with mock.patch.object(client, "activities", mock.AsyncMock(return_value=by_context)), \
                mock.patch.object(plexmod.settings, "plex_section_id", 5):
            self.assertFalse(await client.scanning())


class ApplyQueueTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_fixes_clicked_separately_run_as_one_apply(self):
        from app.metadata import runner as runmod
        r = runmod.Runner()
        calls = []

        async def fake_apply(ids):
            calls.append(ids)
            return {"applied": len(ids), "failed": 0}

        r._apply_queue = [[3], [1], [2, 3]]
        with mock.patch.object(r, "apply_ids", fake_apply), \
                mock.patch.object(r, "publish", mock.AsyncMock()):
            task = asyncio.create_task(r._apply_loop())
            r._apply_wake.set()
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(calls, [[1, 2, 3]])


class PlexCatchUpTestCase(unittest.IsolatedAsyncioTestCase):
    """One Plex rescan per batch of applied albums, as narrow as it can be."""

    async def catch_up(self, folders_by_album):
        from app.metadata import apply
        ctxs = []
        for folders in folders_by_album:
            ctx = apply.Ctx("k", [], None, None)
            ctx.touched_folders = set(folders)
            ctx.plex_dirty = True
            ctxs.append(ctx)
        fake = mock.MagicMock(configured=True, refresh_path=mock.AsyncMock(),
                              refresh_all=mock.AsyncMock(), wait_for_scan=mock.AsyncMock())
        with mock.patch.object(apply, "plex", fake), \
                mock.patch.object(apply.plexsync, "sync_recent", mock.AsyncMock()), \
                mock.patch.object(apply.plexsync, "forget_paths", mock.AsyncMock()):
            await apply.plex_catch_up(*ctxs)
        self.assertFalse(any(ctx.plex_dirty for ctx in ctxs))
        return fake

    async def test_a_few_folders_are_scanned_themselves(self):
        fake = await self.catch_up([["A/x"], ["A/y", "A/x"]])
        self.assertEqual(sorted(c.args[0] for c in fake.refresh_path.call_args_list),
                         ["A/x", "A/y"])
        fake.refresh_all.assert_not_called()

    async def test_where_files_arrived_is_scanned_before_where_they_left(self):
        from app.metadata import apply
        ctx = apply.Ctx("k", [], None, None)
        ctx.touched_folders = {"ACDC/old", "AC_DC/(1980) Back in Black"}
        ctx.new_folders = {"AC_DC/(1980) Back in Black"}
        ctx.plex_dirty = True
        fake = mock.MagicMock(configured=True, refresh_path=mock.AsyncMock(),
                              refresh_all=mock.AsyncMock(), wait_for_scan=mock.AsyncMock())
        with mock.patch.object(apply, "plex", fake), \
                mock.patch.object(apply.plexsync, "sync_recent", mock.AsyncMock()), \
                mock.patch.object(apply.plexsync, "forget_paths", mock.AsyncMock()):
            await apply.plex_catch_up(ctx)
        self.assertEqual([c.args[0] for c in fake.refresh_path.call_args_list],
                         ["AC_DC/(1980) Back in Black", "ACDC/old"])
        self.assertEqual(ctx.touched_folders, set())

    async def test_many_albums_of_one_artist_scan_the_artist(self):
        fake = await self.catch_up([[f"A/{i}"] for i in range(10)])
        self.assertEqual([c.args[0] for c in fake.refresh_path.call_args_list], ["A"])

    async def test_many_artists_share_one_library_scan(self):
        fake = await self.catch_up([[f"{a}/x"] for a in "ABCDEFGH"])
        fake.refresh_path.assert_not_called()
        fake.refresh_all.assert_awaited_once()


class ApplyBatchTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_moves_then_tags_then_plex_with_one_rescan_after_each_round(self):
        from app.metadata import apply
        from app.metadata import runner as runmod
        r = runmod.Runner()
        order = []

        async def start(key, members):
            order.append(("start", key))
            ctx = apply.Ctx(key, [], None, "A")
            return 0, 0, {"key": key, "ctx": ctx, "old_artist": "A", "plex_stage": [],
                          "files": [{"kind": "tags"}, {"kind": "move"}]}

        async def simple(todo, ctx, executors):
            order.extend((i["kind"], ctx.key) for i in todo)
            ctx.plex_dirty = True
            return len(todo), 0

        async def plex_stage(state):
            order.append(("plex", state["key"]))
            return 1, 0

        catch_up = mock.AsyncMock(side_effect=lambda *ctxs: order.append(("scan", len(ctxs))))
        with mock.patch.object(r, "_start_album", start), \
                mock.patch.object(r, "_apply_simple", simple), \
                mock.patch.object(r, "_apply_plex", plex_stage), \
                mock.patch.object(r, "publish", mock.AsyncMock()), \
                mock.patch.object(apply, "plex_catch_up", catch_up):
            result = await r._apply_albums([("A/x", []), ("A/y", [])])
        self.assertEqual(result, (6, 0))
        self.assertEqual(order, [("start", "A/x"), ("start", "A/y"),
                                 ("move", "A/x"), ("move", "A/y"), ("scan", 2),
                                 ("tags", "A/x"), ("tags", "A/y"), ("scan", 2),
                                 ("plex", "A/x"), ("plex", "A/y")])


if __name__ == "__main__":
    unittest.main()


class LibraryAwareTestCase(unittest.IsolatedAsyncioTestCase):
    """Rules that read the library index: folder spelling, partial albums."""

    async def asyncSetUp(self):
        from app.config import settings
        from app.db import db
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(settings, "data_dir", self.tmp / "data")]
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

    async def index(self, *paths):
        for path in paths:
            await self.db.execute(
                "INSERT INTO library_index (norm_artist, norm_title, path, added_at, dirpath, "
                "artist_dir) VALUES ('a', 't', ?, '2026-01-01', ?, ?)",
                (path, str(Path(path).parent), path.split("/", 1)[0]))

    async def test_existing_folders_keep_their_spelling(self):
        await self.index("SAM SMITH/old/01.flac", "SAM SMITH/(2019) How Do You Sleep_/01.flac",
                         "WU-TANG CLAN/x/01.flac")
        self.assertEqual(await desired.on_disk("Sam Smith/(2019) How Do You Sleep_"),
                         "SAM SMITH/(2019) How Do You Sleep_")
        self.assertEqual(await desired.on_disk("Sam Smith/(2014) In the Lonely Hour"),
                         "SAM SMITH/(2014) In the Lonely Hour")
        self.assertEqual(await desired.on_disk("Wu-Tang Clan/(2007) 8 Diagrams"),
                         "WU-TANG CLAN/(2007) 8 Diagrams")
        self.assertEqual(await desired.on_disk("Shontelle/(2010) No Gravity"),
                         "Shontelle/(2010) No Gravity")

    async def test_a_unit_reads_its_disc_subfolders(self):
        await self.index("Queen/On Air/CD1/01.flac", "Queen/On Air/CD2/01.flac",
                         "Queen/On Air Extra/01.flac")
        rows = await units.load_unit("Queen/On Air")
        self.assertEqual(sorted(r["path"] for r in rows),
                         ["Queen/On Air/CD1/01.flac", "Queen/On Air/CD2/01.flac"])

    async def test_a_name_spelled_with_a_combining_accent_is_the_file_plex_has(self):
        from app.metadata import plexsync
        ours = "Megadeth/Live/09. a\u0300 Toute Le Monde.flac"         # as the share lists it
        theirs = "Megadeth/Live/09. \u00e0 Toute Le Monde.flac"        # as Plex reports it
        await self.index(ours)
        await self.db.execute(
            "INSERT INTO plex_items (rating_key, type, path, folder, parent_key, seen_at) "
            "VALUES ('1', 'track', ?, 'Megadeth/Live', '2', 0)", (theirs,))
        found = await plexsync.album_for_paths([ours])
        self.assertEqual(list(found["tracks"]), [ours])
        self.assertEqual(await issues.plan_ghosts(), [])

    async def test_moved_tracks_are_found_by_their_plex_keys(self):
        from app.metadata import plexsync
        await self.db.execute(
            "INSERT INTO plex_items (rating_key, type, path, folder, parent_key, seen_at) "
            "VALUES ('7', 'track', 'A/old/01.flac', 'A/old', '6', 0)")
        self.assertEqual(await plexsync.keys_for_paths(["A/old/01.flac"]), ["7", "6"])

    async def test_issues_of_an_album_that_moved_away_are_closed(self):
        await self.db.execute(
            "INSERT INTO md_issues (key, kind, entity, ref, album_key, severity, summary, state, "
            "created_at, updated_at) VALUES ('g', 'genres', 'album', 'A/old', 'A/old', 'info', "
            "'x', 'queued', '', '')")
        await units.sync_registry()
        row = await self.db.fetchone("SELECT state FROM md_issues WHERE key = 'g'")
        self.assertEqual(row["state"], "applied")

    async def test_a_release_credited_to_a_short_name_files_with_the_artist(self):
        await self.index("Alanis Morissette/Jagged Little Pill/01.flac")
        self.assertEqual(await desired.on_disk("Alanis/(2021) On the Road Again",
                                               ("Alanis Morissette",)),
                         "Alanis Morissette/(2021) On the Road Again")

    async def album(self, key, have: int, total: int):
        from app.metadata import missing
        rel = release(tracks=[(f"Song {i}", 100.0) for i in range(1, total + 1)])
        tracks = {f"{key}/{i}.flac": {"matched": True, "releasetrack_mbid": f"t{i}"}
                  for i in range(1, have + 1)}
        await self.db.execute(
            "INSERT INTO md_albums (key, dirpath, artist_dir, release_mbid, status, desired) "
            "VALUES (?, ?, ?, 'rel-1', 'resolved', ?)",
            (key, key, key.split("/")[0], json.dumps({"album": "Celebrity", "tracks": tracks})))
        with mock.patch.object(missing.mb, "release", mock.AsyncMock(return_value=rel)):
            await missing.refresh("rel-1")
            return await missing.compute("rel-1"), await self.db.fetchone(
                "SELECT state FROM md_issues WHERE key = 'missing_tracks:rel-1'")

    async def test_one_song_of_an_album_is_not_an_album_with_gaps(self):
        info, issue = await self.album("NSYNC/Singles", have=1, total=13)
        self.assertTrue(info["partial"])
        self.assertEqual(len(info["missing"]), 12)
        self.assertIsNone(issue)

    async def test_most_of_an_album_is_flagged(self):
        info, issue = await self.album("NSYNC/Celebrity", have=10, total=13)
        self.assertFalse(info["partial"])
        self.assertEqual(issue["state"], "open")

    async def test_a_downloaded_album_is_flagged_however_little_arrived(self):
        await self.db.execute(
            "INSERT INTO jobs (id, source_url, provider, source_type, status, created_at) "
            "VALUES ('j1', 'https://music.youtube.com/browse/MPREb_abc', 'ytm', 'album', "
            "'done', '2026-01-01')")
        await self.db.execute(
            "INSERT INTO tracks (id, job_id, status, created_at, library_path) "
            "VALUES ('t1', 'j1', 'done', '2026-01-01', 'NSYNC/Celebrity/1.flac')")
        info, issue = await self.album("NSYNC/Celebrity", have=1, total=13)
        self.assertFalse(info["partial"])
        self.assertEqual(issue["state"], "open")
