"""Tag writing for downloads (via tagio). For Spotify-sourced tracks, Spotify metadata is
authoritative and overwrites whatever yt-dlp embedded; cover art is replaced
with the Spotify 640px image. Synced lyrics are written as .lrc sidecars —
Plex only reads sidecar lyrics, never embedded ones."""
import base64
import logging
import struct
from pathlib import Path

import httpx
from mutagen.flac import Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from . import tagio

log = logging.getLogger("mdl.tagger")


async def fetch_cover(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    except Exception as exc:
        log.warning("cover fetch failed: %s", exc)
        return None


def write_tags(path: Path, meta: dict, cover: bytes | None = None) -> None:
    """meta keys: title, artist, album, album_artist, track_number,
    disc_number, release_date, isrc (all optional except title/artist/album).

    yt-dlp's embedded metadata is mostly YouTube's, not the record's: genre is
    the video category ("Music"), and description/synopsis/purl/comment carry
    the video link and the "Provided to YouTube by…" blurb. Those go; the
    metadata manager writes real genres once the release is identified."""
    year = (meta.get("release_date") or "")[:10]
    if len(year) == 8 and year.isdigit():  # yt-dlp style YYYYMMDD
        year = f"{year[:4]}-{year[4:6]}-{year[6:]}"
    fields: dict[str, list[str]] = {
        "title": [meta["title"]],
        "artist": [meta["artist"]],
        "albumartist": [meta["album_artist"]],
        "album": [meta["album"]],
        "genre": [],
    }
    if year:
        fields["date"] = [year]
    if meta.get("track_number"):
        fields["tracknumber"] = [str(int(meta["track_number"]))]
        if meta.get("total_tracks"):
            fields["totaltracks"] = [str(int(meta["total_tracks"]))]
    if meta.get("disc_number"):
        fields["discnumber"] = [str(int(meta["disc_number"]))]
    if meta.get("isrc"):
        fields["isrc"] = [meta["isrc"]]
    try:
        tagio.write(path, fields, strip_junk=True, cover=cover)
    except tagio.TagError as exc:
        log.warning("no tag writer for %s (%s) — leaving yt-dlp tags in place",
                    path.suffix, exc)


def read_tags(path: Path) -> dict:
    """Best-effort read of the minimal tag set from any supported container."""
    import mutagen

    f = mutagen.File(path, easy=True)
    if f is None:
        return {}
    def first(key):
        v = f.get(key)
        return str(v[0]) if v else None
    track_no = first("tracknumber")
    if track_no and "/" in track_no:
        track_no = track_no.split("/")[0]
    disc_no = first("discnumber")
    if disc_no and "/" in disc_no:
        disc_no = disc_no.split("/")[0]
    def to_int(v):
        try:
            return int(v) if v else None
        except ValueError:
            return None
    return {
        "title": first("title"),
        "artist": first("artist"),
        "album": first("album"),
        "album_artist": first("albumartist") or first("artist"),
        "track_number": to_int(track_no),
        "disc_number": to_int(disc_no),
        "release_date": first("date"),
        "duration_sec": getattr(getattr(f, "info", None), "length", None),
    }


def extract_embedded_cover(path: Path) -> bytes | None:
    suffix = path.suffix.lower()
    try:
        if suffix == ".m4a":
            covers = MP4(path).get("covr")
            return bytes(covers[0]) if covers else None
        if suffix in (".opus", ".ogg"):
            audio = OggOpus(path) if suffix == ".opus" else OggVorbis(path)
            b64 = audio.get("metadata_block_picture")
            if b64:
                return Picture(base64.b64decode(b64[0])).data
        if suffix == ".mp3":
            for frame in ID3(path).getall("APIC"):
                return frame.data
    except Exception as exc:
        log.debug("embedded cover extract failed: %s", exc)
    return None


def write_lrc_sidecar(audio_path: Path, synced_lyrics: str) -> Path:
    lrc = audio_path.with_suffix(".lrc")
    lrc.write_text(synced_lyrics, encoding="utf-8")
    return lrc
