"""Post-download validation gate. Kills the 1KB-stub class of failure before
anything can approach the library."""
import asyncio
import json
import logging
from pathlib import Path

from .config import settings
from .engine import tagger
from .models import ErrorClass

log = logging.getLogger("mdl.validator")

MIN_SIZE_BYTES = 200 * 1024          # floor for a normal-length track
ABSOLUTE_MIN_SIZE_BYTES = 16 * 1024  # below this nothing is real audio
MIN_BYTES_PER_SEC = 4 * 1024         # ~32 kbps — the floor for short files
ALLOWED_CODECS = {"aac", "opus", "flac", "mp3", "vorbis"}
ALLOWED_SUFFIXES = {".m4a", ".opus", ".mp3", ".ogg", ".flac"}


class ValidationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.error_class = ErrorClass.VALIDATION_FAILED


async def ffprobe(path: Path) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0:
        raise ValidationError("ffprobe_failed", f"ffprobe cannot parse {path.name}")
    return json.loads(out)


async def validate_track(track_dir: Path, expected_duration: float | None,
                         force: bool = False) -> dict:
    """Returns {path, format, codec, bitrate_kbps, sample_rate, duration_sec,
    filesize_bytes, tags} or raises ValidationError.

    force=True is the reviewer saying "I've looked at this file, take it": the
    duration judgements are downgraded to warnings. The integrity checks (is
    there a decodable audio file at all) still hold — forcing past those would
    only put a broken file in the library."""
    leftovers = [p for p in track_dir.rglob("*")
                 if p.suffix.lower() in (".part", ".ytdl", ".tmp") or p.name.endswith(".part")]
    if leftovers:
        raise ValidationError("partial_files", f"{len(leftovers)} partial files remain")

    audio_files = [p for p in track_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES]
    if not audio_files:
        raise ValidationError("no_audio", "no audio file produced")
    if len(audio_files) > 1:
        raise ValidationError("multiple_audio", f"{len(audio_files)} audio files in track dir")
    audio = audio_files[0]

    size = audio.stat().st_size
    if size < ABSOLUTE_MIN_SIZE_BYTES:
        raise ValidationError("too_small", f"file is {size} bytes — likely an error stub")

    info = await ffprobe(audio)
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise ValidationError("no_audio_stream", "no decodable audio stream")
    stream = streams[0]
    codec = stream.get("codec_name", "")
    if codec not in ALLOWED_CODECS:
        raise ValidationError("bad_codec", f"unexpected codec {codec!r}")

    duration = float(info.get("format", {}).get("duration") or stream.get("duration") or 0)

    # The size floor scales with duration: a 10s interlude is a few tens of KB
    # and must not be judged by a threshold built for 3-minute tracks.
    size_floor = min(MIN_SIZE_BYTES, int(duration * MIN_BYTES_PER_SEC)) if duration else MIN_SIZE_BYTES
    if size < size_floor:
        raise ValidationError(
            "too_small", f"file is {size} bytes for {duration:.0f}s — likely truncated")

    if expected_duration:
        # An authoritative duration is the only real length check. Intros,
        # interludes and skits are legitimately a few seconds long; when the
        # source says 13s and we got 13s, that is a correct download.
        tolerance = max(settings.duration_tolerance_sec, expected_duration * 0.05)
        if abs(duration - expected_duration) > tolerance:
            if not force:
                raise ValidationError(
                    "duration_mismatch",
                    f"duration {duration:.0f}s vs expected {expected_duration:.0f}s")
            log.warning("%s: forced past duration_mismatch (%.0fs vs expected %.0fs)",
                        audio.name, duration, expected_duration)
    elif duration < settings.min_duration_sec:
        # Nothing authoritative to compare against, so only a floor low enough
        # to catch stubs and dead air — not a judgement on track length.
        if not force:
            raise ValidationError(
                "too_short",
                f"duration {duration:.1f}s < {settings.min_duration_sec:g}s "
                f"and the source gave no expected duration to check it against")
        log.warning("%s: forced past too_short (%.1fs)", audio.name, duration)

    # Tag completeness is checked by the worker AFTER merging authoritative
    # expansion metadata (ytmusicapi/Spotify) over the file tags.
    tags = tagger.read_tags(audio)

    bitrate = int(info.get("format", {}).get("bit_rate") or 0) // 1000 or None
    return {
        "path": audio,
        "format": audio.suffix.lstrip(".").lower(),
        "codec": codec,
        "bitrate_kbps": bitrate,
        "sample_rate": int(stream.get("sample_rate") or 0) or None,
        "duration_sec": duration,
        "filesize_bytes": size,
        "tags": tags,
    }
