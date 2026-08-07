"""Run with: python -m unittest discover backend/tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.paths import PathBuildError, build_track_relpath, sanitize_component


class TestSanitize(unittest.TestCase):
    def test_forbidden_chars(self):
        self.assertEqual(sanitize_component('AC/DC: "Best" <of>?*'), "AC_DC_ _Best_ _of___")

    def test_fullwidth_bar_survives_but_template_never_built(self):
        # The v1 bug string — as a *tag value* it is legal text; the point is
        # that no template fallback path exists anymore.
        self.assertEqual(sanitize_component("album｜Unknown Album"), "album｜Unknown Album")

    def test_empty_raises(self):
        with self.assertRaises(PathBuildError):
            sanitize_component("   ")
        with self.assertRaises(PathBuildError):
            sanitize_component("...")
        self.assertEqual(sanitize_component("???"), "___")  # non-empty, kept

    def test_trailing_dots_spaces(self):
        self.assertEqual(sanitize_component("Album Vol. 2. "), "Album Vol. 2")

    def test_truncation_byte_budget(self):
        long = "ü" * 300
        out = sanitize_component(long)
        self.assertLessEqual(len(out.encode("utf-8")), 180)
        self.assertGreater(len(out), 0)


class TestBuildPath(unittest.TestCase):
    def test_basic(self):
        p = build_track_relpath("Muse", "Absolution", "Hysteria", ".m4a", 8)
        self.assertEqual(str(p), "Muse/Absolution/08 - Hysteria.m4a")

    def test_multidisc(self):
        p = build_track_relpath("Muse", "HAARP", "Knights of Cydonia", "opus", 3, 2)
        self.assertEqual(str(p), "Muse/HAARP/2-03 - Knights of Cydonia.opus")

    def test_no_tracknumber(self):
        p = build_track_relpath("Artist", "Album", "Song", "mp3")
        self.assertEqual(str(p), "Artist/Album/Song.mp3")

    def test_missing_album_is_error_not_fallback(self):
        with self.assertRaises(PathBuildError):
            build_track_relpath("Artist", "", "Song", "mp3")

    def test_bad_extension(self):
        with self.assertRaises(PathBuildError):
            build_track_relpath("A", "B", "C", ".m4a/../..")


if __name__ == "__main__":
    unittest.main()
