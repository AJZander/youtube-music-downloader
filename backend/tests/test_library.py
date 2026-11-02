"""Run with: python -m unittest discover backend/tests"""
import asyncio
import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import library
from app.library import (LibraryError, album_key, find_album_duplicates,
                         find_split_artists, find_track_duplicates, score_file,
                         strict_key, title_keys, title_looks_like_junk)


@contextlib.asynccontextmanager
async def no_transaction():
    """db.transaction, for a database stubbed out statement by statement."""
    yield


def file_row(path, title, *, fmt="flac", album=None, album_artist=None,
             artist=None, track=None, duration=200.0, size=1_000_000,
             bitrate=None, year=None):
    parts = Path(path).parts
    return {
        "path": path, "title": title, "album": album, "album_artist": album_artist,
        "artist": artist or album_artist, "track_number": track,
        "disc_number": None, "year": year, "format": fmt,
        "bitrate_kbps": bitrate, "sample_rate": 44100, "bit_depth": 16,
        "duration_sec": duration, "filesize_bytes": size,
        "dirpath": str(Path(path).parent), "artist_dir": parts[0],
    }


class KeyTestCase(unittest.TestCase):
    def test_strict_key_collapses_punctuation_and_spacing(self):
        self.assertEqual(strict_key("AC_DC"), strict_key("AC/DC"))
        self.assertEqual(strict_key("3 Doors Down"), strict_key("3doorsdown"))
        self.assertNotEqual(strict_key("Nirvana"), strict_key("Nirvana UK"))

    def test_title_keys_recover_the_song_from_a_youtube_title(self):
        """v1 wrote whole video titles into the title tag; both readings of
        the dash have to be offered or the clean copy never matches."""
        junk = title_keys("Ariana Grande - Baby I (Official Video)", "Ariana Grande")
        self.assertTrue(junk & title_keys("Baby I", "Ariana Grande"))

    def test_title_keys_match_a_featured_credit_to_the_bare_title(self):
        featured = title_keys("MIKA - Popular Song ft. Ariana Grande", "Ariana Grande")
        self.assertTrue(featured & title_keys("Popular Song", "Ariana Grande"))

    def test_title_keys_keep_distinct_songs_apart(self):
        self.assertFalse(title_keys("Piano", "Ariana Grande")
                         & title_keys("Tattooed Heart", "Ariana Grande"))

    def test_album_key_survives_every_naming_convention(self):
        expected = album_key("Yours Truly", "Ariana Grande")
        for variant in ("(2013) - Ariana Grande - Yours Truly",
                        "2013 - Yours Truly",
                        "[2013] Yours Truly",
                        "Ariana Grande - Yours Truly [FLAC 24 bit-44100Hz]"):
            self.assertEqual(album_key(variant, "Ariana Grande"), expected, variant)

    def test_album_key_keeps_a_deluxe_edition_distinct(self):
        """A deluxe edition is a superset, not a duplicate — it must not
        collapse into the standard album on name alone."""
        self.assertNotEqual(album_key("My Everything", "Ariana Grande"),
                            album_key("My Everything (Deluxe)", "Ariana Grande"))

    def test_junk_title_detection(self):
        self.assertTrue(title_looks_like_junk("Artist - Song (Official Video)"))
        self.assertTrue(title_looks_like_junk(None))
        self.assertFalse(title_looks_like_junk("Honeymoon Avenue"))


class ScoreTestCase(unittest.TestCase):
    def test_lossless_outranks_any_bitrate(self):
        flac = score_file(file_row("A/B/1.flac", "S", fmt="flac"))[0]
        mp3 = score_file(file_row("A/B/1.mp3", "S", fmt="mp3", bitrate=320,
                                  album="B", album_artist="A", track=1, year="2013"))[0]
        self.assertGreater(flac, mp3)

    def test_truncation_beats_format(self):
        """A lossless file missing two minutes of the song is the worse copy,
        whatever its format says."""
        short = score_file(file_row("A/B/1.flac", "S", duration=60.0), 200.0)[0]
        full = score_file(file_row("A/B/1.opus", "S", fmt="opus", bitrate=128,
                                   duration=200.0), 200.0)[0]
        self.assertGreater(full, short)

    def test_tags_break_a_format_tie(self):
        tagged = score_file(file_row("A/B/1.opus", "Song", fmt="opus", bitrate=128,
                                     album="B", album_artist="A", track=1))[0]
        bare = score_file(file_row("A/C/1.opus", "A - Song (Official Video)",
                                   fmt="opus", bitrate=128))[0]
        self.assertGreater(tagged, bare)

    def test_reasons_explain_the_verdict(self):
        _, reasons = score_file(file_row("A/B/1.opus", "A - S (Official Video)",
                                         fmt="opus", bitrate=128))
        joined = " ".join(reasons)
        self.assertIn("128 kbps", joined)
        self.assertIn("missing", joined)


class AlbumDuplicateTestCase(unittest.TestCase):
    def ariana(self):
        """The real case: a clean FLAC rip beside a v1 opus grab whose tags are
        half missing and whose titles are YouTube video titles."""
        clean = [file_row(f"Ariana Grande/(2013) - Ariana Grande - Yours Truly/{i:02d}. {t}.flac",
                          t, album="Yours Truly", album_artist="Ariana Grande",
                          track=i, size=30_000_000)
                 for i, t in enumerate(["Honeymoon Avenue", "Baby I", "Right There",
                                        "Tattooed Heart", "Piano"], start=1)]
        junk_titles = ["Honeymoon Avenue", "Ariana Grande - Baby I (Official Video)",
                       "Ariana Grande - Right There (Official Video) ft. Big Sean",
                       "Tattooed Heart", "Piano"]
        messy = [file_row(f"Ariana Grande/Yours Truly/{i:02d} - {t}.opus", t,
                          fmt="opus", bitrate=128,
                          album="Yours Truly" if " - " not in t else None,
                          size=4_000_000)
                 for i, t in enumerate(junk_titles, start=1)]
        return clean + messy

    def test_groups_two_folders_of_the_same_album(self):
        groups = find_album_duplicates(self.ariana())
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["kind"], "exact")

    def test_keeps_the_lossless_copy(self):
        group = find_album_duplicates(self.ariana())[0]
        self.assertIn("(2013)", group["instances"][0]["dirpath"])
        self.assertTrue(group["instances"][0]["is_keeper"])

    def test_every_junk_titled_track_is_matched_to_the_keeper(self):
        """The whole point of title_keys — if the junk titles do not match,
        they land in orphans and the duplicate is never cleaned up."""
        group = find_album_duplicates(self.ariana())[0]
        loser = group["instances"][1]
        self.assertEqual(len(loser["covered"]), 5)
        self.assertEqual(loser["orphans"], [])

    def test_extra_tracks_are_reported_as_orphans_not_removals(self):
        rows = self.ariana()
        rows.append(file_row("Ariana Grande/Yours Truly/06 - Daydreamin'.opus",
                             "Daydreamin'", fmt="opus", bitrate=128, size=4_000_000))
        group = find_album_duplicates(rows)[0]
        loser = group["instances"][1]
        self.assertEqual([f["title"] for f in loser["orphans"]], ["Daydreamin'"])
        self.assertTrue(group["has_orphans"])

    def test_different_albums_are_left_alone(self):
        rows = [file_row(f"A/Album One/{i}.flac", f"Song {i}", album="Album One",
                         album_artist="A") for i in range(1, 6)]
        rows += [file_row(f"A/Album Two/{i}.flac", f"Other {i}", album="Album Two",
                          album_artist="A") for i in range(1, 6)]
        self.assertEqual(find_album_duplicates(rows), [])

    def test_a_deluxe_edition_is_a_different_album(self):
        """It holds every track of the standard edition, but it is another
        release — the same-album rule keeps both."""
        rows = [file_row(f"A/Album/{i}.flac", f"Song {i}", album="Album",
                         album_artist="A") for i in range(1, 6)]
        rows += [file_row(f"A/Album (Deluxe)/{i}.flac", f"Song {i}",
                          album="Album (Deluxe)", album_artist="A")
                 for i in range(1, 6)]
        rows += [file_row(f"A/Album (Deluxe)/bonus{i}.flac", f"Bonus {i}",
                          album="Album (Deluxe)", album_artist="A") for i in (1, 2)]
        self.assertEqual(find_album_duplicates(rows), [])

    def test_a_remaster_is_the_same_album(self):
        rows = [file_row(f"A/Album/{i}.flac", f"Song {i}", album="Album",
                         album_artist="A") for i in range(1, 6)]
        rows += [file_row(f"A/Album (2011 Remaster)/{i}.mp3", f"Song {i}", fmt="mp3",
                          album="Album (Remastered)", album_artist="A")
                 for i in range(1, 6)]
        self.assertEqual(len(find_album_duplicates(rows)), 1)

    def test_an_edition_in_brackets_is_still_another_album(self):
        """The mover's layout puts the edition in [brackets] and MusicBrainz
        often titles a deluxe edition plainly, so the tags agree."""
        rows = [file_row(f"A/(2017) Album/{i}.flac", f"Song {i}", album="Album",
                         album_artist="A") for i in range(1, 6)]
        rows += [file_row(f"A/(2017) Album [Complete Edition]/{i}.flac", f"Song {i}",
                          album="Album", album_artist="A") for i in range(1, 6)]
        self.assertEqual(find_album_duplicates(rows), [])

    def test_a_remaster_in_brackets_is_the_same_album(self):
        rows = [file_row(f"A/(1974) Album/{i}.mp3", f"Song {i}", fmt="mp3", album="Album",
                         album_artist="A") for i in range(1, 6)]
        rows += [file_row(f"A/(1974) Album [2001 Remaster]/{i}.flac", f"Song {i}",
                          album="Album", album_artist="A") for i in range(1, 6)]
        self.assertEqual(len(find_album_duplicates(rows)), 1)

    def test_a_partial_copy_of_the_album_is_a_duplicate(self):
        rows = [file_row(f"A/(1979) Album/{i:02d}.mp3", f"Song {i}", fmt="mp3",
                         album="Album", album_artist="A") for i in range(1, 11)]
        rows += [file_row(f"A/Album/{i:02d}.opus", f"Song {i}", fmt="opus",
                          album="Album", album_artist="A") for i in (3, 7)]
        self.assertEqual(len(find_album_duplicates(rows)), 1)

    def test_another_release_group_is_another_album_whatever_the_name(self):
        """A single named like the album it comes from."""
        rows = [file_row(f"A/(2021) Flavors of love/{i}.flac", t, album="Flavors of love",
                         album_artist="A") for i, t in enumerate(["Flavors of love", "B"])]
        rows += [file_row("A/Flavors of love/1.flac", "Flavors of love",
                          album="Flavors of love", album_artist="A")]
        for row in rows:
            row["mb_releasegroupid"] = "single-rg" if row["dirpath"] == "A/Flavors of love" \
                else "album-rg"
        self.assertEqual(find_album_duplicates(rows), [])

    def test_a_box_sets_discs_are_not_copies_of_the_albums(self):
        rows = []
        for disc, name in ((1, "Jazz"), (2, "The Miracle")):
            rows += [file_row(f"Q/BOX-SETS/1995 - The Box, 20CD/CD 0{disc} - {name}/{i}.mp3",
                              f"{name} {i}", fmt="mp3", album=name, album_artist="Q")
                     for i in range(1, 6)]
            rows += [file_row(f"Q/(1978) {name}/{i}.flac", f"{name} {i}", album=name,
                              album_artist="Q") for i in range(1, 6)]
        self.assertEqual(find_album_duplicates(rows), [])

    def test_an_album_inside_a_dated_collection_is_the_collections(self):
        rows = [file_row(f"B/Compilations/(2010) B - The Mono Recordings/1964 - Another Side/{i}.flac",
                         f"Song {i}", album="Another Side", album_artist="B")
                for i in range(1, 6)]
        rows += [file_row(f"B/(1964) Another Side/{i}.flac", f"Song {i}", album="Another Side",
                          album_artist="B") for i in range(1, 6)]
        self.assertEqual(find_album_duplicates(rows), [])

    def test_the_discs_of_one_album_still_group_with_another_copy(self):
        rows = [file_row(f"B/Bootleg Series/(1991) Vols 1-3/CD{d}/{i}.flac", f"Song {d}{i}",
                         album="The Bootleg Series Volumes 1-3", album_artist="B")
                for d in (1, 2) for i in range(1, 4)]
        rows += [file_row(f"B/(1991) The Bootleg Series Volumes 1-3/{i}.flac", f"Song {d}{i}",
                          album="The Bootleg Series Volumes 1-3", album_artist="B")
                 for d in (1, 2) for i in range(1, 4)]
        self.assertEqual(len(find_album_duplicates(rows)), 1)

    def test_a_single_with_a_featured_credit_is_not_part_of_the_album(self):
        """MusicBrainz may match the single's one track to the album; the
        folder's credit says it came as the single."""
        rows = [file_row(f"F/(2012) Wild Ones/{i}.flac", t, album="Wild Ones", album_artist="F")
                for i, t in enumerate(["Wild Ones", "Whistle", "Let It Roll"], start=1)]
        rows += [file_row("F/Wild Ones (feat. Sia)/1.flac", "Wild Ones", album="Wild Ones",
                          album_artist="F")]
        self.assertEqual(find_album_duplicates(rows), [])

    def test_another_artists_version_with_the_same_title_is_not_a_copy(self):
        """A cover filed in the artist's folder, tagged like the original."""
        rows = [file_row("T/(2020) you broke me first/01.flac", "you broke me first",
                         album="you broke me first", album_artist="T", duration=169.0)]
        rows += [file_row("T/(2022) The Graes - you broke me first/01.flac", "you broke me first",
                          album="you broke me first", album_artist="T", duration=188.0)]
        self.assertEqual(find_album_duplicates(rows), [])

    def test_an_edited_cut_is_kept_not_covered(self):
        """Innuendo's vinyl cuts some songs short: those are not the CD's tracks."""
        lengths = [393, 270, 260, 400, 420]
        rows = [file_row(f"Q/(1991) Innuendo/{i}.flac", f"Song {i}", album="Innuendo",
                         album_artist="Q", duration=float(d))
                for i, d in enumerate(lengths, start=1)]
        rows += [file_row(f"Q/VINYL-RIP/1991 - Innuendo/{i}.mp3", f"Song {i}", fmt="mp3",
                          album="Innuendo", album_artist="Q",
                          duration=float(d - (60 if i == 5 else 1)))
                 for i, d in enumerate(lengths, start=1)]
        group = find_album_duplicates(rows)[0]
        loser = group["instances"][1]
        self.assertEqual([f["title"] for f in loser["orphans"]], ["Song 5"])
        self.assertEqual(len(loser["covered"]), 4)

    def test_edition_words_come_from_qualifiers_only(self):
        from app.library import edition_words
        self.assertEqual(edition_words("(2017) Dua Lipa (Deluxe Edition)"), {"deluxe"})
        self.assertEqual(edition_words("(1987) Bad [Special Edition]"), {"special"})
        self.assertEqual(edition_words("(1974) Waterloo [2001 Remaster]"), frozenset())
        self.assertEqual(edition_words("1988 - Live at Wembley"), frozenset())
        self.assertEqual(edition_words("Live Through This"), frozenset())
        self.assertEqual(edition_words("Megadeth - Rust In Peace", hints=("Megadeth",)),
                         frozenset())
        self.assertEqual(edition_words("Girl on Fire (Remixes)"), {"remix"})

    def test_loose_artist_folder_files_are_not_treated_as_an_album(self):
        rows = [file_row(f"A/{i}. Song {i}.mp3", f"Song {i}", fmt="mp3", bitrate=320)
                for i in range(1, 6)]
        rows += [file_row(f"A/Album/{i}.flac", f"Song {i}", album="Album",
                          album_artist="A") for i in range(1, 6)]
        self.assertEqual(find_album_duplicates(rows), [])


class TrackDuplicateTestCase(unittest.TestCase):
    def test_the_same_track_on_the_same_album_twice_is_one_group(self):
        rows = [file_row("A/Album/01.flac", "Song", album="Album", album_artist="A"),
                file_row("A/Album/01 - Song.mp3", "Song", fmt="mp3", bitrate=256,
                         album="Album", album_artist="A")]
        groups = find_track_duplicates(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["copies"][0]["path"], "A/Album/01.flac")

    def test_the_same_song_on_another_album_is_not_a_duplicate(self):
        """The hits collection's copy belongs to the hits collection."""
        rows = [file_row("A/Album/01.flac", "Song", album="Album", album_artist="A"),
                file_row("A/Hits/07.mp3", "Song", fmt="mp3", bitrate=256,
                         album="Hits", album_artist="A")]
        self.assertEqual(find_track_duplicates(rows), [])

    def test_a_loose_copy_tagged_with_the_album_is_a_duplicate(self):
        rows = [file_row("A/Album/01.flac", "Song", album="Album", album_artist="A"),
                file_row("A/Song.mp3", "Song", fmt="mp3", album="Album", album_artist="A")]
        self.assertEqual(len(find_track_duplicates(rows)), 1)

    def test_untagged_loose_copies_side_by_side_are_duplicates(self):
        rows = [file_row("A/Song.mp3", "Song", fmt="mp3", album_artist="A"),
                file_row("A/Song (1).mp3", "Song", fmt="mp3", album_artist="A")]
        self.assertEqual(len(find_track_duplicates(rows)), 1)

    def test_different_lengths_are_different_versions(self):
        """Bob Marley's 'Keep On Moving' exists at 4:20 and 6:25 — grouping
        those and offering to delete one loses a mix."""
        rows = [file_row("A/Album/01.mp3", "Keep On Moving", fmt="mp3",
                         bitrate=320, duration=260.0, album_artist="A"),
                file_row("A/Other/02.mp3", "Keep On Moving", fmt="mp3",
                         bitrate=320, duration=385.0, album_artist="A")]
        self.assertEqual(find_track_duplicates(rows), [])

    def test_groups_inside_a_duplicate_album_are_flagged(self):
        rows = [file_row("A/Album/01.flac", "Song", album="Album", album_artist="A"),
                file_row("A/Album/01 - Song.opus", "Song", fmt="opus", bitrate=128,
                         album="Album", album_artist="A")]
        groups = find_track_duplicates(rows, covered_dirs={"A/Album", "A/Album Copy"})
        self.assertTrue(groups[0]["in_album_group"])

    def test_two_album_folders_are_compared_as_albums_not_track_by_track(self):
        """A song on the album and again on a single, or in a second album
        folder, is not a track duplicate: only the same album twice (or a
        full and a partial copy) is, and that is the album comparison's."""
        rows = [file_row("A/(2020) Album/01 - Song.flac", "Song", album="Album",
                         album_artist="A", track=1),
                file_row("A/Singles/A - Song.opus", "Song", fmt="opus", album="Album",
                         album_artist="A"),
                file_row("A/Album Copy/01.opus", "Song", fmt="opus", album="Album",
                         album_artist="A", track=1)]
        self.assertEqual(find_track_duplicates(rows), [])

    def test_two_tracks_of_one_release_are_not_duplicates(self):
        """A clean and an explicit take, both on the release."""
        rows = [file_row("A/Single/02 - Locked Up (remix).flac", "Locked Up (remix)",
                         album="Locked Up", album_artist="A", track=2),
                file_row("A/Single/03 - Locked Up (remix).flac", "Locked Up (remix)",
                         album="Locked Up", album_artist="A", track=3)]
        for i, row in enumerate(rows):
            row.update(mb_albumid="rel", mb_releasegroupid="rg", mb_releasetrackid=f"t{i}")
        self.assertEqual(find_track_duplicates(rows), [])
        for row in rows:
            row.pop("mb_releasetrackid")       # untagged: the numbers say the same
        self.assertEqual(find_track_duplicates(rows), [])

    def test_the_same_track_in_two_formats_in_its_folder_is_a_duplicate(self):
        rows = [file_row("A/(2012) Album/06 - Speaking in Tongues.m4a", "Speaking in Tongues",
                         fmt="m4a", album="Album", album_artist="Hilltop Hoods", track=6),
                file_row("A/(2012) Album/06 - Hilltop Hoods - Speaking In Tongues.opus",
                         "Hilltop Hoods - Speaking In Tongues (Official Video) ft. Chali 2na",
                         fmt="opus", album="Album", album_artist="Hilltop Hoods")]
        self.assertEqual(len(find_track_duplicates(rows)), 1)

    def test_a_shared_suffix_after_a_dash_is_not_the_song(self):
        """Every chapter of an audiobook, every song of a live album: what
        follows the dash is the album's, not the song's."""
        titles = ["Kapitel 2.2 - Chronicles, Vol. 1", "Kapitel 3.2 - Chronicles, Vol. 1",
                  "Freakum Dress - Live", "Broken-Hearted Girl - Live",
                  "If I Ain't Got You (Unplugged Live at BAM, Brooklyn, NY - July 2005)",
                  "If I Was Your Woman (Unplugged Live at BAM, Brooklyn, NY - July 2005)"]
        rows = [file_row(f"A/Album/{i:02d}.flac", t, album="Album", album_artist="A")
                for i, t in enumerate(titles, start=1)]
        self.assertEqual(find_track_duplicates(rows), [])

    def test_a_remix_named_after_a_featured_credit_stays_a_remix(self):
        rows = [file_row("A/Solo/01.flac", "Solo (feat. Demi Lovato)  (Wideboys Remix)",
                         album="Solo", album_artist="A"),
                file_row("A/Solo/02.flac", "Solo (feat. Demi Lovato)  (Hotel Garuda Remix)",
                         album="Solo", album_artist="A")]
        self.assertEqual(find_track_duplicates(rows), [])

    def test_a_loose_single_is_not_the_album_track_when_musicbrainz_says_single(self):
        rows = [file_row("A/(2021) Flavors of love/11 - Flavors of love.flac",
                         "Flavors of love", album="Flavors of love", album_artist="A", track=11),
                file_row("A/A - Flavors Of Love.flac", "Flavors Of Love",
                         album="Flavors of love", album_artist="A")]
        rows[0]["mb_releasegroupid"], rows[1]["mb_releasegroupid"] = "album-rg", "single-rg"
        self.assertEqual(find_track_duplicates(rows), [])

    def test_same_title_by_different_artists_is_not_a_duplicate(self):
        rows = [file_row("A/Album/01.flac", "Hello", album_artist="A"),
                file_row("B/Album/01.flac", "Hello", album_artist="B")]
        self.assertEqual(find_track_duplicates(rows), [])


class SplitArtistTestCase(unittest.TestCase):
    def test_finds_spelling_variants_and_suggests_the_larger(self):
        rows = [file_row(f"AC_DC/Back in Black/{i}.flac", f"S{i}",
                         album_artist="AC/DC") for i in range(1, 11)]
        rows += [file_row("ACDC/Highway/1.flac", "S", album_artist="ACDC")]
        groups = find_split_artists(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["suggested_keeper"], "AC_DC")

    def test_distinct_artists_are_not_merged(self):
        rows = [file_row("Nirvana/A/1.flac", "S", album_artist="Nirvana"),
                file_row("Nirvana UK/A/1.flac", "S", album_artist="Nirvana UK")]
        self.assertEqual(find_split_artists(rows), [])


class SafePathTestCase(unittest.TestCase):
    """_safe_abs is the only thing between a browser-supplied string and
    os.replace on the music library."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "Artist" / "Album").mkdir(parents=True)
        (self.root / "Artist" / "Album" / "01.flac").write_bytes(b"x")
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(library.settings, "library_dir", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        trash = mock.patch.object(type(library.settings), "trash_dir",
                                  property(lambda s: self.root / ".mdl-trash"))
        trash.start()
        self.addCleanup(trash.stop)

    def test_accepts_a_path_inside_the_library(self):
        self.assertEqual(library._safe_abs("Artist/Album/01.flac"),
                         self.root / "Artist" / "Album" / "01.flac")

    def test_rejects_traversal(self):
        for bad in ("../outside", "Artist/../../etc/passwd", "/etc/passwd", ""):
            with self.assertRaises(LibraryError, msg=bad):
                library._safe_abs(bad)

    def test_rejects_the_library_root_itself(self):
        with self.assertRaises(LibraryError):
            library._safe_abs(".")

    def test_rejects_paths_inside_the_trash(self):
        with self.assertRaises(LibraryError):
            library._safe_abs(".mdl-trash/20260101-abc/Artist/Album/01.flac")


class TrashTestCase(unittest.TestCase):
    """The trash path moves real files, so it is exercised against a real
    temporary tree with the database stubbed out."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.album = self.root / "Artist" / "Album"
        self.album.mkdir(parents=True)
        for i in (1, 2):
            (self.album / f"0{i}.flac").write_bytes(b"audio" * 100)
            (self.album / f"0{i}.lrc").write_text("lyrics")
        (self.album / "folder.jpg").write_bytes(b"art")
        self.addCleanup(self._tmp.cleanup)

        self.executed = []
        patches = [
            mock.patch.object(library.settings, "library_dir", self.root),
            mock.patch.object(type(library.settings), "trash_dir",
                              property(lambda s: self.root / ".mdl-trash")),
            mock.patch.object(library, "_require_online", lambda: None),
            mock.patch.object(library, "_refresh_plex", mock.AsyncMock()),
            mock.patch.object(library.db, "execute", mock.AsyncMock()),
            mock.patch.object(library.db, "transaction", no_transaction),
            mock.patch.object(library.db, "executemany",
                              mock.AsyncMock(side_effect=lambda sql, rows:
                                             self.executed.extend(rows))),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_trash(self, paths):
        return asyncio.run(library.trash_paths(paths, "test"))

    def test_moves_the_file_and_its_lyrics_sidecar(self):
        result = self.run_trash(["Artist/Album/01.flac"])
        self.assertFalse((self.album / "01.flac").exists())
        self.assertFalse((self.album / "01.lrc").exists())
        self.assertTrue((self.album / "02.flac").exists())
        moved = {Path(e[2]).name for e in self.executed}
        self.assertEqual(moved, {"01.flac", "01.lrc"})
        self.assertGreater(result["freed_bytes"], 0)

    def test_emptied_album_folder_goes_too(self):
        """Leaving the folder behind with only cover art keeps a ghost album
        alive in Plex."""
        result = self.run_trash(["Artist/Album/01.flac", "Artist/Album/02.flac"])
        self.assertFalse(self.album.exists())
        self.assertTrue((self.root / "Artist").exists())
        self.assertIn("Artist/Album", result["emptied_folders"])

    def test_never_sweeps_away_an_artist_folder(self):
        loose = self.root / "Artist" / "loose.flac"
        loose.write_bytes(b"x")
        self.run_trash(["Artist/loose.flac"])
        self.assertTrue((self.root / "Artist").exists())

    def test_a_folder_and_a_file_inside_it_do_not_race(self):
        result = self.run_trash(["Artist/Album", "Artist/Album/01.flac"])
        self.assertFalse(self.album.exists())
        self.assertEqual(result["failures"], [])

    def test_refuses_to_escape_the_library(self):
        outside = self.root.parent / "do-not-touch.txt"
        outside.write_text("safe")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        result = self.run_trash([f"../{outside.name}"])
        self.assertTrue(outside.exists())
        self.assertEqual(len(result["failures"]), 1)


if __name__ == "__main__":
    unittest.main()


class ScanPruneTestCase(unittest.TestCase):
    """A scan whose walk came back short must not read that as deletions."""

    def setUp(self):
        self.deleted = []
        self.rows = [{"path": f"A/Album/{i}.flac", "filesize_bytes": 1,
                      "mtime": 1.0, "scanned_at": "2026-01-01T00:00:00Z"}
                     for i in range(100)]
        patches = [
            mock.patch.object(library.db, "fetchall",
                              mock.AsyncMock(return_value=self.rows)),
            mock.patch.object(library.db, "executemany", mock.AsyncMock()),
            mock.patch.object(library.db, "execute",
                              mock.AsyncMock(side_effect=lambda sql, params=():
                                             self.deleted.extend(params))),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def scan_seeing(self, visible: int):
        found = {r["path"]: (r["filesize_bytes"], r["mtime"])
                 for r in self.rows[:visible]}
        with mock.patch.object(library, "_walk", return_value=found):
            return asyncio.run(library.scan())

    def test_prunes_when_the_walk_looks_healthy(self):
        result = self.scan_seeing(90)
        self.assertEqual(result["removed"], 10)
        self.assertEqual(len(self.deleted), 10)

    def test_skips_pruning_when_most_of_the_library_vanished(self):
        result = self.scan_seeing(20)
        self.assertEqual(result["removed"], 0)
        self.assertEqual(self.deleted, [])
        self.assertIn("pruning skipped", result["error"])


class MergeArtistsTestCase(unittest.TestCase):
    """Merging folders moves real files around, so it runs against a real tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # "ACDC" is the small variant; "AC_DC" is the keeper.
        (self.root / "AC_DC" / "Back in Black").mkdir(parents=True)
        (self.root / "AC_DC" / "Back in Black" / "01.flac").write_bytes(b"keeper")
        (self.root / "ACDC" / "Highway to Hell").mkdir(parents=True)
        (self.root / "ACDC" / "Highway to Hell" / "01.flac").write_bytes(b"unique")
        (self.root / "ACDC" / "Back in Black").mkdir(parents=True)
        (self.root / "ACDC" / "Back in Black" / "01.flac").write_bytes(b"collides")
        (self.root / "ACDC" / "Back in Black" / "02.flac").write_bytes(b"new track")

        self.trashed = []
        patches = [
            mock.patch.object(library.settings, "library_dir", self.root),
            mock.patch.object(type(library.settings), "trash_dir",
                              property(lambda s: self.root / ".mdl-trash")),
            mock.patch.object(library, "_require_online", lambda: None),
            mock.patch.object(library, "_refresh_plex", mock.AsyncMock()),
            mock.patch.object(library, "_retag_album_artist", mock.AsyncMock(return_value=[])),
            # what names the moved files follows them: tested in test_p1_relocate
            mock.patch.object(library.relocate, "relocate", mock.AsyncMock(return_value={})),
            mock.patch.object(library.db, "fetchall", mock.AsyncMock(return_value=[])),
            mock.patch.object(library.db, "execute", mock.AsyncMock()),
            mock.patch.object(library.db, "transaction", no_transaction),
            mock.patch.object(library.db, "executemany",
                              mock.AsyncMock(side_effect=lambda sql, rows:
                                             self.trashed.extend(rows))),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.result = asyncio.run(
            library.merge_artist_folders(["ACDC"], "AC_DC", rewrite_tags=False))

    def test_albums_only_on_the_source_side_move_over(self):
        self.assertTrue((self.root / "AC_DC" / "Highway to Hell" / "01.flac").exists())

    def test_a_shared_album_folder_is_merged_track_by_track(self):
        self.assertTrue((self.root / "AC_DC" / "Back in Black" / "02.flac").exists())
        self.assertEqual(
            (self.root / "AC_DC" / "Back in Black" / "01.flac").read_bytes(), b"keeper")

    def test_a_colliding_track_is_trashed_not_deleted(self):
        """The old code rmtree'd whatever could not move, permanently losing a
        file the user was never asked about. It goes to the trash instead —
        as a folder, with the bytes intact and restorable."""
        self.assertGreaterEqual(self.result["trashed"], 1)
        self.assertIn("ACDC/Back in Black",
                      {entry[1] for entry in self.trashed})
        survivors = list((self.root / ".mdl-trash").rglob("01.flac"))
        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0].read_bytes(), b"collides")

    def test_the_source_folder_is_gone(self):
        self.assertFalse((self.root / "ACDC").exists())
        self.assertEqual(self.result["failures"], [])


class FakeTags(dict):
    saved = False

    def save(self):
        self.saved = True


class MergeRetagTestCase(unittest.TestCase):
    """The merge writes the target's own album artist, never its folder name."""

    def retag(self, tags, known):
        from app.engine import tagio
        root = Path(self._tmp.name)
        for name in tags:
            (root / "AC_DC" / name).touch()
        files = {str(root / "AC_DC" / n): t for n, t in tags.items()}
        row = {"album_artist": known} if known else None

        def write(path, fields, **kwargs):
            tags = files[str(path)]
            before = {k: list(tags.get(k) or []) for k in fields}
            tags.update(fields)
            tags.save()
            return {"before": before, "removed": [], "changed": True}

        with mock.patch.object(library.settings, "library_dir", root), \
                mock.patch.object(library.db, "fetchone", mock.AsyncMock(return_value=row)), \
                mock.patch.object(tagio, "read", lambda p: {"fields": dict(files[str(p)])}), \
                mock.patch.object(tagio, "write", write):
            return len(asyncio.run(library._retag_album_artist("AC_DC", ["ACDC"])))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        (Path(self._tmp.name) / "AC_DC").mkdir()

    def test_old_spellings_take_the_targets_tag_and_its_own_are_left(self):
        keeper = FakeTags(albumartist=["AC/DC"], artist=["AC/DC"])
        moved = FakeTags(albumartist=["ACDC"], artist=["ACDC"])
        bare = FakeTags(artist=["Brian Johnson"])
        changed = self.retag({"1.flac": keeper, "2.flac": moved, "3.flac": bare}, "AC/DC")
        self.assertEqual(changed, 2)
        self.assertFalse(keeper.saved)
        self.assertEqual((moved["albumartist"], moved["artist"]), (["AC/DC"], ["AC/DC"]))
        self.assertEqual((bare["albumartist"], bare["artist"]), (["AC/DC"], ["Brian Johnson"]))

    def test_a_target_without_tags_falls_back_to_its_folder_name(self):
        moved = FakeTags(albumartist=["ACDC"])
        self.assertEqual(self.retag({"1.flac": moved}, None), 1)
        self.assertEqual(moved["albumartist"], ["AC_DC"])


class AlbumGroupingEdgeTestCase(unittest.TestCase):
    def album(self, folder, titles, *, artist="A", album=None, fmt="flac"):
        return [file_row(f"{artist}/{folder}/{i:02d}.{fmt}", t, fmt=fmt,
                         album=album, album_artist=artist, track=i)
                for i, t in enumerate(titles, start=1)]

    def test_a_singles_folder_does_not_join_an_album_it_shares_one_track_with(self):
        """One shared song is not an album in common — a 1-track folder scores
        a perfect overlap against anything containing that song."""
        rows = self.album("Vulgar Display of Power",
                          ["Mouth for War", "Walk", "Hollow"],
                          album="Vulgar Display of Power")
        rows += self.album("Piss", ["Walk"], album="Piss")
        self.assertEqual(find_album_duplicates(rows), [])

    def test_a_single_still_groups_when_the_album_name_agrees(self):
        rows = self.album("Walk", ["Walk"], album="Vulgar Display of Power")
        rows += self.album("Vulgar Display of Power",
                           ["Mouth for War", "Walk", "Hollow"],
                           album="Vulgar Display of Power")
        self.assertEqual(len(find_album_duplicates(rows)), 1)

    def test_a_compilation_sharing_tracks_is_another_album(self):
        rows = self.album("Best Of", ["Walk", "Hollow"], album="Best Of")
        rows += self.album("Vulgar Display of Power",
                           ["Mouth for War", "Walk", "Hollow"],
                           album="Vulgar Display of Power")
        self.assertEqual(find_album_duplicates(rows), [])

    def test_the_group_is_named_by_consensus_not_by_the_keeper(self):
        """A box set tags every disc with the set's name, so the copy that wins
        on quality is the one least able to say what the album is."""
        rows = self.album("Box Set/Cowboys From Hell", ["One", "Two", "Three"],
                          album="The Complete Albums")            # 24-bit keeper
        for f in rows:
            f["bit_depth"] = 24
            f["sample_rate"] = 96000
        rows += self.album("Cowboys From Hell", ["One", "Two", "Three"],
                           album="Cowboys From Hell")
        rows += self.album("Cowboys From Hell [MP3]", ["One", "Two", "Three"],
                           album="Cowboys From Hell", fmt="mp3")
        rows += self.album("Cowboys From Hell (Deluxe)", ["One", "Two", "Three"],
                           album="Cowboys From Hell")
        group = find_album_duplicates(rows)[0]
        self.assertIn("Box Set", group["instances"][0]["dirpath"])
        self.assertEqual(group["album"], "Cowboys From Hell")
        # the deluxe edition is another album, whatever its tags say
        self.assertNotIn("A/Cowboys From Hell (Deluxe)",
                         [i["dirpath"] for i in group["instances"]])

    def test_group_ids_are_unique_when_a_box_set_keeps_several_groups(self):
        rows = self.album("Box/Album One", ["A1", "A2", "A3"], album="Box")
        rows += self.album("Album One", ["A1", "A2", "A3"], album="Album One")
        rows += self.album("Box/Album Two", ["B1", "B2", "B3"], album="Box")
        rows += self.album("Album Two", ["B1", "B2", "B3"], album="Album Two")
        groups = find_album_duplicates(rows)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len({g["id"] for g in groups}), 2)

    def test_a_name_heading_several_groups_is_discarded_as_a_box_set_name(self):
        """A name shared by two groups cannot name either of them."""
        rows = self.album("Box/Disc 1", ["A1", "A2", "A3"], album="The Complete Albums")
        rows += self.album("Cowboys From Hell", ["A1", "A2", "A3"],
                           album="The Complete Albums")
        rows += self.album("Box/Far Beyond Driven", ["B1", "B2", "B3"],
                           album="The Complete Albums")
        rows += self.album("Far Beyond Driven", ["B1", "B2", "B3"],
                           album="Far Beyond Driven")
        names = {g["album"] for g in find_album_duplicates(rows)}
        self.assertNotIn("The Complete Albums", names)
        self.assertEqual(names, {"Cowboys From Hell", "Far Beyond Driven"})

    def test_a_disc_folder_defers_to_a_sibling_with_a_real_name(self):
        rows = self.album("Box/CD 2", ["A1", "A2", "A3"], album="Set")
        rows += self.album("Box/Disc 1", ["B1", "B2", "B3"], album="Set")
        rows += self.album("Cowboys From Hell", ["A1", "A2", "A3"], album="Set")
        rows += self.album("Far Beyond Driven", ["B1", "B2", "B3"], album="Set")
        self.assertEqual({g["album"] for g in find_album_duplicates(rows)},
                         {"Cowboys From Hell", "Far Beyond Driven"})

    def test_all_disc_folders_fall_back_to_the_set_plus_the_disc(self):
        """Two discs of one set must not end up sharing a heading."""
        rows = self.album("Live Box/Disc 1", ["A1", "A2", "A3"], album="Set")
        rows += self.album("Other Box/Disc 1", ["A1", "A2", "A3"], album="Set")
        rows += self.album("Live Box/Disc 2", ["B1", "B2", "B3"], album="Set")
        rows += self.album("Other Box/Disc 2", ["B1", "B2", "B3"], album="Set")
        names = [g["album"] for g in find_album_duplicates(rows)]
        self.assertEqual(len(set(names)), 2, names)
        for name in names:
            self.assertRegex(name, r"Box — Disc [12]$")

    def test_folder_derived_names_drop_year_prefixes_and_scene_tags(self):
        from app.library import pretty_folder_name
        self.assertEqual(
            pretty_folder_name("(2003) Slayer - Soundtrack To The Apocalypse "
                               "(Deluxe Version) [16Bit-44.1kHz]", "Slayer"),
            "Soundtrack To The Apocalypse (Deluxe Version)")
        self.assertEqual(pretty_folder_name("Yours Truly", "Ariana Grande"),
                         "Yours Truly")

    def test_no_two_groups_share_a_heading(self):
        """Two rows the user cannot tell apart is a bug however it arises."""
        rows = []
        for disc, titles in enumerate([["A1", "A2", "A3"], ["B1", "B2", "B3"],
                                       ["C1", "C2", "C3"]], start=1):
            rows += self.album(f"Box/Disc {disc}", titles, album="The Set")
            rows += self.album(f"Reissue/Disc {disc}", titles, album="The Set")
        groups = find_album_duplicates(rows)
        self.assertEqual(len(groups), 3)
        self.assertEqual(len({g["album"] for g in groups}), 3)


class ExclusionTestCase(unittest.TestCase):
    """Decisions the user has already made must survive the next scan."""

    def album(self, folder, titles, *, artist="A", album=None):
        return [file_row(f"{artist}/{folder}/{i:02d}.flac", t, album=album,
                         album_artist=artist, track=i)
                for i, t in enumerate(titles, start=1)]

    def three_overlapping(self):
        """Three copies of one album under three folder conventions."""
        titles = ["One", "Two", "Three", "Four"]
        return (self.album("Mothership", titles, album="Mothership")
                + self.album("An Introduction", titles, album="Mothership")
                + self.album("BBC Sessions", titles, album="Mothership"))

    def test_pair_key_is_order_independent(self):
        self.assertEqual(library.pair_key("b", "a"), library.pair_key("a", "b"))

    def test_an_excluded_pair_does_not_group(self):
        rows = self.album("Mothership", ["One", "Two", "Three"], album="Mothership")
        rows += self.album("An Introduction", ["One", "Two", "Three"],
                           album="Mothership")
        excluded = {library.pair_key("A/Mothership", "A/An Introduction")}
        self.assertEqual(len(find_album_duplicates(rows)), 1)   # grouped without it
        self.assertEqual(find_album_duplicates(rows, excluded), [])

    def test_keeping_two_of_three_stops_only_that_pair_reappearing(self):
        """Keep Mothership and An Introduction, drop BBC Sessions. A rescan
        must not offer the kept pair again — but a genuinely new copy still
        gets reported."""
        rows = self.three_overlapping()
        self.assertEqual(len(find_album_duplicates(rows)), 1)
        excluded = {library.pair_key("A/Mothership", "A/An Introduction")}
        remaining = [r for r in rows if "BBC Sessions" not in r["dirpath"]]
        self.assertEqual(find_album_duplicates(remaining, excluded), [])
        # BBC Sessions coming back is new information and is reported again.
        self.assertEqual(len(find_album_duplicates(rows, excluded)), 1)

    def test_marking_a_whole_group_not_duplicates_silences_it(self):
        rows = self.three_overlapping()
        members = ["A/Mothership", "A/An Introduction", "A/BBC Sessions"]
        excluded = {library.pair_key(a, b)
                    for i, a in enumerate(members) for b in members[i + 1:]}
        self.assertEqual(find_album_duplicates(rows, excluded), [])

    def test_track_exclusions_are_honoured(self):
        rows = [file_row("A/Album/01.flac", "Song", album="Album", album_artist="A"),
                file_row("A/Album/07.flac", "Song", album="Album", album_artist="A")]
        self.assertEqual(len(find_track_duplicates(rows)), 1)
        excluded = {library.pair_key("A/Album/01.flac", "A/Album/07.flac")}
        self.assertEqual(find_track_duplicates(rows, None, excluded), [])

    def test_artist_exclusions_are_honoured(self):
        rows = [file_row(f"AC_DC/Back in Black/{i}.flac", f"S{i}",
                         album_artist="AC/DC") for i in range(1, 4)]
        rows += [file_row("ACDC/Highway/1.flac", "S", album_artist="ACDC")]
        self.assertEqual(len(find_split_artists(rows)), 1)
        excluded = {library.pair_key("AC_DC", "ACDC")}
        self.assertEqual(find_split_artists(rows, excluded), [])


class PlanTestCase(unittest.IsolatedAsyncioTestCase):
    """plan_album_merge drives the keep/remove checkboxes in the browser."""

    def setUp(self):
        titles = ["One", "Two", "Three"]
        self.rows = []
        for folder in ("Alpha", "Beta", "Gamma"):
            self.rows += [
                {**file_row(f"A/{folder}/{i:02d}.flac", t, album=folder,
                            album_artist="A", track=i)}
                for i, t in enumerate(titles, start=1)]
        self.rows.append(file_row("A/Gamma/99.flac", "Gamma Only",
                                  album="Gamma", album_artist="A"))
        patcher = mock.patch.object(
            library.db, "fetchall",
            mock.AsyncMock(side_effect=lambda sql, params=(): [
                r for r in self.rows if r["dirpath"] in set(params)]))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_order_follows_the_caller_not_the_scores(self):
        """The keeper jumping to the top is what made the radio look like it
        snapped back to the first option."""
        dirs = ["A/Alpha", "A/Beta", "A/Gamma"]
        plan = await library.plan_album_merge(dirs, ["A/Gamma"])
        self.assertEqual([i["dirpath"] for i in plan["instances"]], dirs)
        self.assertTrue(plan["instances"][2]["is_keeper"])

    async def test_keeping_two_marks_only_the_third_for_removal(self):
        plan = await library.plan_album_merge(
            ["A/Alpha", "A/Beta", "A/Gamma"], ["A/Alpha", "A/Beta"])
        keepers = [i["dirpath"] for i in plan["instances"] if i.get("is_keeper")]
        self.assertEqual(keepers, ["A/Alpha", "A/Beta"])
        gamma = next(i for i in plan["instances"] if i["dirpath"] == "A/Gamma")
        self.assertEqual(len(gamma["covered"]), 3)
        self.assertEqual([f["title"] for f in gamma["orphans"]], ["Gamma Only"])

    async def test_a_track_in_any_kept_copy_counts_as_covered(self):
        only_alpha = await library.plan_album_merge(
            ["A/Alpha", "A/Gamma"], ["A/Alpha"])
        gamma = next(i for i in only_alpha["instances"] if i["dirpath"] == "A/Gamma")
        self.assertEqual(len(gamma["orphans"]), 1)

    async def test_keeping_nothing_is_rejected(self):
        with self.assertRaises(LibraryError):
            await library.plan_album_merge(["A/Alpha", "A/Beta"], [])

    async def test_a_keeper_outside_the_group_is_rejected(self):
        with self.assertRaises(LibraryError):
            await library.plan_album_merge(["A/Alpha"], ["A/Elsewhere"])


class BoxSetSweepTestCase(unittest.TestCase):
    """Emptying the discs of a box set must take the box set with them."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.box = self.root / "Pantera" / "The Complete Albums"
        for disc in ("Disc 1", "Disc 2"):
            (self.box / disc).mkdir(parents=True)
            (self.box / disc / "01.flac").write_bytes(b"audio")
        (self.box / "cover.jpg").write_bytes(b"art")
        (self.root / "Pantera" / "Vulgar").mkdir()
        (self.root / "Pantera" / "Vulgar" / "01.flac").write_bytes(b"keep me")

        self.recorded = []
        patches = [
            mock.patch.object(library.settings, "library_dir", self.root),
            mock.patch.object(type(library.settings), "trash_dir",
                              property(lambda s: self.root / ".mdl-trash")),
            mock.patch.object(library, "_require_online", lambda: None),
            mock.patch.object(library, "_refresh_plex", mock.AsyncMock()),
            mock.patch.object(library.db, "execute", mock.AsyncMock()),
            mock.patch.object(library.db, "transaction", no_transaction),
            mock.patch.object(library.db, "executemany",
                              mock.AsyncMock(side_effect=lambda sql, rows:
                                             self.recorded.extend(rows))),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.result = asyncio.run(library.trash_paths(
            ["Pantera/The Complete Albums/Disc 1/01.flac",
             "Pantera/The Complete Albums/Disc 2/01.flac"], "test"))

    def test_the_box_set_folder_goes_not_just_the_discs(self):
        self.assertFalse(self.box.exists())

    def test_its_cover_art_is_trashed_rather_than_left_behind(self):
        """Left in place it keeps a ghost album alive in Plex."""
        survivors = list((self.root / ".mdl-trash").rglob("cover.jpg"))
        self.assertEqual(len(survivors), 1)
        self.assertEqual(survivors[0].read_bytes(), b"art")

    def test_the_artist_folder_and_its_other_albums_are_untouched(self):
        self.assertTrue((self.root / "Pantera" / "Vulgar" / "01.flac").exists())

    def test_the_emptied_parent_is_reported(self):
        self.assertIn("Pantera/The Complete Albums", self.result["emptied_folders"])
