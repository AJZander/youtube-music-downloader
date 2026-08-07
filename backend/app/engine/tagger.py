"""Tag writing via mutagen. For Spotify-sourced tracks, Spotify metadata is
authoritative and overwrites whatever yt-dlp embedded; cover art is replaced
with the Spotify 640px image. Synced lyrics are written as .lrc sidecars —
Plex only reads sidecar lyrics, never embedded ones."""
import base64
import logging
import struct
from pathlib import Path

import httpx
from mutagen.flac import Picture
from mutagen.id3 import APIC, ID3, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TSRC
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

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


def _vorbis_picture_b64(image: bytes, mime: str = "image/jpeg") -> str:
    pic = Picture()
    pic.type = 3  # front cover
    pic.mime = mime
    pic.data = image
    return base64.b64encode(pic.write()).decode("ascii")


def write_tags(path: Path, meta: dict, cover: bytes | None = None) -> None:
    """meta keys: title, artist, album, album_artist, track_number,
    disc_number, release_date, isrc (all optional except title/artist/album)."""
    suffix = path.suffix.lower()
    year = (meta.get("release_date") or "")[:10]
    if len(year) == 8 and year.isdigit():  # yt-dlp style YYYYMMDD
        year = f"{year[:4]}-{year[4:6]}-{year[6:]}"
    if suffix == ".m4a":
        audio = MP4(path)
        audio["\xa9nam"] = [meta["title"]]
        audio["\xa9ART"] = [meta["artist"]]
        audio["aART"] = [meta["album_artist"]]
        audio["\xa9alb"] = [meta["album"]]
        if year:
            audio["\xa9day"] = [year]
        if meta.get("track_number"):
            audio["trkn"] = [(int(meta["track_number"]), int(meta.get("total_tracks") or 0))]
        if meta.get("disc_number"):
            audio["disk"] = [(int(meta["disc_number"]), 0)]
        if meta.get("isrc"):
            audio["----:com.apple.iTunes:ISRC"] = [meta["isrc"].encode()]
        if cover:
            audio["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
    elif suffix in (".opus", ".ogg"):
        audio = OggOpus(path) if suffix == ".opus" else OggVorbis(path)
        audio["title"] = [meta["title"]]
        audio["artist"] = [meta["artist"]]
        audio["albumartist"] = [meta["album_artist"]]
        audio["album"] = [meta["album"]]
        if year:
            audio["date"] = [year]
        if meta.get("track_number"):
            audio["tracknumber"] = [str(meta["track_number"])]
        if meta.get("disc_number"):
            audio["discnumber"] = [str(meta["disc_number"])]
        if meta.get("isrc"):
            audio["isrc"] = [meta["isrc"]]
        if cover:
            audio["metadata_block_picture"] = [_vorbis_picture_b64(cover)]
        audio.save()
    elif suffix == ".mp3":
        audio = ID3(path)
        audio.setall("TIT2", [TIT2(encoding=3, text=meta["title"])])
        audio.setall("TPE1", [TPE1(encoding=3, text=meta["artist"])])
        audio.setall("TPE2", [TPE2(encoding=3, text=meta["album_artist"])])
        audio.setall("TALB", [TALB(encoding=3, text=meta["album"])])
        if year:
            audio.setall("TDRC", [TDRC(encoding=3, text=year)])
        if meta.get("track_number"):
            audio.setall("TRCK", [TRCK(encoding=3, text=str(meta["track_number"]))])
        if meta.get("disc_number"):
            audio.setall("TPOS", [TPOS(encoding=3, text=str(meta["disc_number"]))])
        if meta.get("isrc"):
            audio.setall("TSRC", [TSRC(encoding=3, text=meta["isrc"])])
        if cover:
            audio.setall("APIC", [APIC(encoding=3, mime="image/jpeg", type=3,
                                       desc="Front cover", data=cover)])
        audio.save()
    else:
        log.warning("no tag writer for %s — leaving yt-dlp tags in place", suffix)


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
