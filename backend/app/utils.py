# backend/app/utils.py
# Pure utility functions — no I/O, no framework dependencies.
import re
from pathlib import Path


# ── Artist name cleaning ────────────────────────────────────────────────────

# Ordered from most specific to least so we don't partially match
_FEAT_PATTERNS = re.compile(
    r"\s*[\(\[]?"          # optional opening bracket
    r"(?:feat(?:uring)?|ft)"  # keyword
    r"\.?\s+"              # optional dot then whitespace
    r".+?"                 # featured artist(s) — non-greedy
    r"[\)\]]?"             # optional closing bracket
    r"\s*$",               # end of string
    re.IGNORECASE,
)

_AMPERSAND_SPLIT = re.compile(r"\s*&\s*")


def clean_artist_for_folder(raw: str | None) -> str:
    """
    Return a filesystem-safe *main* artist name suitable for folder naming.

    Strategy (in order):
    1. Take only the first artist when separated by comma or ampersand.
    2. Strip any "feat." / "ft." / "featuring" suffixes.
    3. Remove filesystem-unsafe characters.

    The raw/full string should still be written into ID3 tags separately.

    Examples:
        "Artist A, Artist B feat. Artist C" -> "Artist A"
        "Artist A feat. Artist B"           -> "Artist A"
        "Artist A & Artist B"               -> "Artist A"
        None / ""                           -> "Unknown Artist"
    """
    if not raw or not raw.strip():
        return "Unknown Artist"

    artist = raw.strip()

    # Step 1 – take only primary artist (before first comma or ampersand)
    artist = re.split(r"\s*,\s*", artist)[0].strip()
    artist = re.split(r"\s*&\s*", artist)[0].strip()

    # Step 2 – remove feat. / ft. / featuring tail
    artist = _FEAT_PATTERNS.sub("", artist).strip()

    # Step 3 – remove filesystem-unsafe characters
    artist = sanitize_path_component(artist)

    return artist or "Unknown Artist"


def sanitize_path_component(name: str) -> str:
    """
    Strip characters that are illegal in file/folder names on Windows, macOS, Linux.
    Collapse repeated spaces/underscores.
    """
    # Characters illegal on Windows (also covers Linux / macOS edge cases)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    # Normalise whitespace runs
    name = re.sub(r"\s+", " ", name).strip()
    # Avoid trailing dots/spaces (Windows quirk)
    name = name.rstrip(". ")
    return name


# ── Track title cleaning ────────────────────────────────────────────────────

def clean_title(raw: str | None) -> str:
    """Strip illegal chars from a track title for use in a filename."""
    if not raw:
        return "Unknown Track"
    return sanitize_path_component(raw)


# ── URL validation ──────────────────────────────────────────────────────────

_YOUTUBE_DOMAINS = re.compile(
    r"^https?://(music\.|www\.|m\.)?youtube\.com/",
    re.IGNORECASE,
)
_YOUTU_BE = re.compile(r"^https?://youtu\.be/", re.IGNORECASE)


def is_valid_youtube_url(url: str) -> bool:
    """Lightweight check — rejects obviously non-YouTube input early."""
    url = url.strip()
    return bool(_YOUTUBE_DOMAINS.match(url) or _YOUTU_BE.match(url))


# ── Download type detection ─────────────────────────────────────────────────

def detect_download_type(url: str, info: dict) -> str:
    """
    Classify the URL as song | album | playlist | artist.
    Prefers metadata hints over URL string matching.
    """
    url_lower = url.lower()
    is_playlist = info.get("_type") == "playlist"

    if not is_playlist:
        return "song"

    # YouTube Music album playlists use OLAK5uy_ prefix
    playlist_id = info.get("id", "")
    if playlist_id.startswith("OLAK5uy_") or "OLAK5uy_" in url_lower:
        return "album"

    if "channel" in url_lower or "artist" in url_lower:
        return "artist"

    return "playlist"