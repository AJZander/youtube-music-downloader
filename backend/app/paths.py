"""Library path construction. Paths are always built here, in Python, from
verified tag values — never by handing a template string to a downloader.
A component that sanitizes to nothing is an error, not a fallback: the
'Unknown Album' folder class of bug has no code path to happen through."""
import re
import unicodedata
from pathlib import Path

# CIFS/Windows-illegal characters plus control chars
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
MAX_COMPONENT_BYTES = 180


class PathBuildError(ValueError):
    pass


def sanitize_component(name: str) -> str:
    if name is None:
        raise PathBuildError("path component is None")
    cleaned = unicodedata.normalize("NFC", str(name))
    cleaned = _FORBIDDEN.sub("_", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    # CIFS rejects trailing dots/spaces; also refuse dot-only names
    cleaned = cleaned.strip(". ")
    if not cleaned:
        raise PathBuildError(f"path component empty after sanitization: {name!r}")
    # Truncate to a byte budget at a character boundary
    encoded = cleaned.encode("utf-8")
    if len(encoded) > MAX_COMPONENT_BYTES:
        cleaned = encoded[:MAX_COMPONENT_BYTES].decode("utf-8", errors="ignore").rstrip(". ")
        if not cleaned:
            raise PathBuildError(f"path component empty after truncation: {name!r}")
    return cleaned


def build_track_relpath(
    album_artist: str,
    album: str,
    title: str,
    ext: str,
    track_number: int | None = None,
    disc_number: int | None = None,
) -> Path:
    """Relative path under the library root: Artist/Album/NN - Title.ext"""
    artist_dir = sanitize_component(album_artist)
    album_dir = sanitize_component(album)
    prefix = ""
    if track_number:
        if disc_number and disc_number > 1:
            prefix = f"{disc_number}-{int(track_number):02d} - "
        else:
            prefix = f"{int(track_number):02d} - "
    filename = sanitize_component(f"{prefix}{title}")
    ext = ext.lstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9]{2,5}", ext):
        raise PathBuildError(f"suspicious extension: {ext!r}")
    return Path(artist_dir) / album_dir / f"{filename}.{ext}"
