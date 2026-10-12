"""Run with: python -m unittest discover backend/tests"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import validator
from app.validator import ValidationError, validate_track


def _probe(duration: float, codec: str = "aac") -> dict:
    return {
        "format": {"duration": str(duration), "bit_rate": "256000"},
        "streams": [{"codec_type": "audio", "codec_name": codec,
                     "sample_rate": "44100", "duration": str(duration)}],
    }


class ValidatorTestCase(unittest.TestCase):
    """Builds a staging dir holding one fake audio file of a chosen size, with
    ffprobe and the tag reader stubbed — the checks under test are size and
    duration arithmetic, not decoding."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def run_validate(self, duration, expected, size_bytes=None, force=False,
                     codec="aac"):
        if size_bytes is None:                       # plausible 256 kbps file
            size_bytes = max(1, int(duration * 32 * 1024))
        (self.dir / "track.m4a").write_bytes(b"\0" * size_bytes)
        with mock.patch.object(validator, "ffprobe",
                               mock.AsyncMock(return_value=_probe(duration, codec))), \
             mock.patch.object(validator.tagger, "read_tags", return_value={}):
            return asyncio.run(validate_track(self.dir, expected, force=force))


class TestShortTracks(ValidatorTestCase):
    """The v1 gate rejected everything under 30s, which blocked the intros,
    interludes and skits that make up a real chunk of an album."""

    def test_skit_matching_its_expected_duration_passes(self):
        # "Sweet Love (Skit)" — Spotify says 7s, we downloaded 7s.
        result = self.run_validate(duration=7.0, expected=7.0)
        self.assertEqual(result["duration_sec"], 7.0)

    def test_thirteen_second_outro_passes(self):
        self.assertEqual(self.run_validate(duration=13.0, expected=13.0)["duration_sec"], 13.0)

    def test_one_second_of_drift_on_a_short_track_passes(self):
        # 25s expected / 24s actual — inside the 3s floor tolerance.
        self.run_validate(duration=24.0, expected=25.0)

    def test_short_file_is_not_judged_by_the_full_length_size_floor(self):
        # 8s at 256 kbps is ~256KB, but at 96 kbps it is ~96KB — under the
        # 200KB floor that only ever made sense for a full-length track.
        self.run_validate(duration=8.0, expected=8.0, size_bytes=96 * 1024)

    def test_truncated_short_file_still_caught(self):
        with self.assertRaises(ValidationError) as ctx:
            self.run_validate(duration=8.0, expected=8.0, size_bytes=20 * 1024)
        self.assertEqual(ctx.exception.code, "too_small")

    def test_error_stub_still_caught(self):
        with self.assertRaises(ValidationError) as ctx:
            self.run_validate(duration=0.5, expected=None, size_bytes=1024)
        self.assertEqual(ctx.exception.code, "too_small")


class TestUnknownExpectedDuration(unittest.TestCase):
    """With no authoritative duration there is nothing to compare against, so a
    low floor stands in — low enough to pass a real interlude."""

    def setUp(self):
        ValidatorTestCase.setUp(self)
        self.run_validate = lambda **kw: ValidatorTestCase.run_validate(self, **kw)

    def test_ten_second_interlude_passes(self):
        self.run_validate(duration=10.0, expected=None)

    def test_sub_second_dead_air_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.run_validate(duration=1.5, expected=None, size_bytes=64 * 1024)
        self.assertEqual(ctx.exception.code, "too_short")


class TestForce(ValidatorTestCase):
    """Force is the reviewer overriding a judgement, not the integrity checks."""

    def test_force_lifts_duration_mismatch(self):
        with self.assertRaises(ValidationError):
            self.run_validate(duration=241.0, expected=205.0)
        self.run_validate(duration=241.0, expected=205.0, force=True)

    def test_force_lifts_too_short(self):
        with self.assertRaises(ValidationError):
            self.run_validate(duration=1.5, expected=None, size_bytes=64 * 1024)
        self.run_validate(duration=1.5, expected=None, size_bytes=64 * 1024, force=True)

    def test_force_does_not_lift_integrity_checks(self):
        for code, kwargs in (("too_small", {"duration": 200.0, "expected": 200.0,
                                            "size_bytes": 1024}),
                             ("bad_codec", {"duration": 200.0, "expected": 200.0,
                                            "codec": "wmav2"})):
            with self.subTest(code=code), self.assertRaises(ValidationError) as ctx:
                self.run_validate(force=True, **kwargs)
            self.assertEqual(ctx.exception.code, code)

    def test_force_does_not_lift_no_audio(self):
        with self.assertRaises(ValidationError) as ctx:
            with mock.patch.object(validator.tagger, "read_tags", return_value={}):
                asyncio.run(validate_track(self.dir, 200.0, force=True))
        self.assertEqual(ctx.exception.code, "no_audio")


class TestNormalTracks(ValidatorTestCase):
    """The behaviour that was already right must not move."""

    def test_full_length_track_passes(self):
        self.assertEqual(self.run_validate(duration=205.0, expected=205.0)["duration_sec"], 205.0)

    def test_wrong_version_still_flagged(self):
        # 241s where Spotify says 205s — a different recording, still review.
        with self.assertRaises(ValidationError) as ctx:
            self.run_validate(duration=241.0, expected=205.0)
        self.assertEqual(ctx.exception.code, "duration_mismatch")

    def test_truncated_full_length_track_flagged(self):
        with self.assertRaises(ValidationError) as ctx:
            self.run_validate(duration=12.0, expected=205.0)
        self.assertEqual(ctx.exception.code, "duration_mismatch")

    def test_five_percent_tolerance_still_applies(self):
        self.run_validate(duration=205.0, expected=200.0)     # 5s on 200s — ok
        with self.assertRaises(ValidationError):
            self.run_validate(duration=215.0, expected=200.0)  # 15s — not ok


if __name__ == "__main__":
    unittest.main()
