"""Run with: python -m unittest discover backend/tests
Needs the app's runtime deps (fastapi et al) — run inside the mdl-app image."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import _album_key, _matched_album


def different_album(track_album, library_path):
    """Mirrors the flag list_skipped computes per row."""
    matched = _matched_album(library_path)
    return bool(matched and track_album
                and _album_key(matched) != _album_key(track_album))


class TestMatchedAlbum(unittest.TestCase):
    def test_pulls_album_from_library_layout(self):
        self.assertEqual(
            _matched_album("Das EFX/The Very Best Of Das EFX/03 - They Want EFX.m4a"),
            "The Very Best Of Das EFX")

    def test_album_with_slashes_in_its_name(self):
        # "What Is It//Kill It" is sanitised to "What Is It__Kill It" on disk,
        # so the path still has exactly three components.
        self.assertEqual(
            _matched_album("Vended/What Is It__Kill It/02 - Asylum.m4a"),
            "What Is It__Kill It")

    def test_missing_or_flat_path(self):
        self.assertIsNone(_matched_album(None))
        self.assertIsNone(_matched_album("stray.m4a"))


class TestDifferentAlbumFlag(unittest.TestCase):
    """The flag is what makes 2000+ skips reviewable, so it has to be right."""

    def test_compilation_blocking_the_studio_album_is_flagged(self):
        # The real case: the studio-album track was skipped because a
        # greatest-hits copy of it was already indexed.
        self.assertTrue(different_album(
            "Real Hip-Hop", "Das EFX/The Very Best Of Das EFX/03 - They Want EFX.m4a"))

    def test_same_album_is_a_true_duplicate(self):
        self.assertFalse(different_album(
            "Vended", "Vended/Vended/04 - Am I The Only One.m4a"))

    def test_punctuation_and_case_do_not_count_as_different(self):
        self.assertFalse(different_album(
            "no time for dreaming!", "Charles Bradley/No Time For Dreaming/01 - x.m4a"))

    def test_unknown_album_is_not_flagged(self):
        # Nothing to compare — do not claim a false positive we cannot see.
        self.assertFalse(different_album(None, "A/B/01 - x.m4a"))
        self.assertFalse(different_album("", "A/B/01 - x.m4a"))

    def test_unknown_match_path_is_not_flagged(self):
        self.assertFalse(different_album("Some Album", None))

    def test_slash_in_album_name_is_not_a_different_album(self):
        # The folder is sanitised to '_', so a raw comparison would call this
        # album different from itself and re-download a genuine duplicate.
        self.assertFalse(different_album(
            "Speakerboxxx/The Love Below",
            "Outkast/Speakerboxxx_The Love Below/01 - Ghetto Musick.m4a"))
        self.assertFalse(different_album(
            "What Is It//Kill It", "Vended/What Is It__Kill It/02 - Asylum.m4a"))

    def test_colon_in_album_name_is_not_a_different_album(self):
        self.assertFalse(different_album(
            "Vol. 2: Hard Knock Life",
            "Jay-Z/Vol. 2_ Hard Knock Life/01 - Hand It Down.m4a"))

    def test_genuinely_different_album_with_punctuation_still_flagged(self):
        self.assertTrue(different_album(
            "Speakerboxxx/The Love Below",
            "Outkast/Stankonia/13 - Good Hair (Interlude).m4a"))


class TestForceFlags(unittest.TestCase):
    """Regression guard for a bug that got in twice, in two different shapes,
    each time by letting one flag mean two things. Validation may only be
    silenced by someone who has LOOKED AT the downloaded file — that is
    FORCE_VALIDATION (Force import, file already in quarantine) and nothing
    else. Every other 'force' is a claim about the library, not the audio."""

    def flags(self, job_force, track_force):
        from app.worker import force_flags
        return force_flags({"force": job_force}, {"force": track_force})

    def test_no_force_at_all(self):
        self.assertEqual(self.flags(0, 0), (False, False))

    def test_job_force_never_silences_validation(self):
        # Shape 1: old force_import set force on the whole JOB; job force then
        # silenced validation, so retrying the job imported 120 unvalidated.
        self.assertEqual(self.flags(1, 0), (True, False))

    def test_force_download_never_silences_validation(self):
        # Shape 2: "that wasn't a duplicate" is a claim about the library, made
        # before the file exists. It must not vouch for the audio.
        from app.worker import FORCE_GUARDS
        self.assertEqual(self.flags(0, FORCE_GUARDS), (True, False))

    def test_only_force_import_silences_validation(self):
        from app.worker import FORCE_VALIDATION
        self.assertEqual(self.flags(0, FORCE_VALIDATION), (True, True))

    def test_job_force_does_not_escalate_a_guards_only_track(self):
        from app.worker import FORCE_GUARDS
        self.assertEqual(self.flags(1, FORCE_GUARDS), (True, False))

    def test_null_track_force_is_safe(self):
        self.assertEqual(self.flags(0, None), (False, False))


if __name__ == "__main__":
    unittest.main()
