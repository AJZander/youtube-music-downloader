"""Format-independent tag reading and writing.

One vocabulary of field names for every container, mapped onto the keys
MusicBrainz Picard writes (its "tag mapping" appendix), because those are the
keys Plex, Picard, beets and Lidarr all read — MusicBrainz ids above all:
Plex's music agent tries the file's MusicBrainz Album Id before anything else
when it matches an album.

Every value is a list of strings. Multi-valued fields (genre, artists) are
written as real multi-values — separate Vorbis comments, null-separated ID3v2.4
text, several MP4 atoms — never as one "Pop, Rock" string, which Plex reads as
a single genre called "Pop, Rock".

Writing also strips junk: the YouTube boilerplate yt-dlp embeds (description,
synopsis, purl, the "Music" genre) and release-group spam ("PMEDIA" in a dozen
fields, t.me links). Spam is recognised by value, not only by key, but the
fields that carry the music — titles, credits, lyrics, art, ReplayGain — are
never removed by that rule.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import mutagen
from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (APIC, ID3, TALB, TCMP, TCON, TDOR, TDRC, TIT2, TMOO, TPE1, TPE2,
                         TPOS, TPUB, TRCK, TSO2, TSOP, TSRC, TXXX, UFID)
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

FIELDS = (
    "title", "artist", "artists", "album", "albumartist", "albumartistsort", "artistsort",
    "tracknumber", "totaltracks", "discnumber", "totaldiscs", "date", "originaldate",
    "genre", "mood", "isrc", "label", "releasetype", "compilation",
    "mb_albumid", "mb_releasegroupid", "mb_albumartistid", "mb_artistid",
    "mb_trackid", "mb_releasetrackid",
)
MB_FIELDS = ("mb_albumid", "mb_releasegroupid", "mb_albumartistid", "mb_artistid",
             "mb_trackid", "mb_releasetrackid")
WRITABLE_SUFFIXES = {".flac", ".ogg", ".opus", ".mp3", ".m4a", ".mp4", ".aac", ".wav",
                     ".aif", ".aiff"}

# Vorbis comment keys (FLAC, Ogg Vorbis, Opus). Reads also accept the aliases.
_VORBIS = {
    "title": "TITLE", "artist": "ARTIST", "artists": "ARTISTS", "album": "ALBUM",
    "albumartist": "ALBUMARTIST", "albumartistsort": "ALBUMARTISTSORT",
    "artistsort": "ARTISTSORT", "tracknumber": "TRACKNUMBER", "totaltracks": "TRACKTOTAL",
    "discnumber": "DISCNUMBER", "totaldiscs": "DISCTOTAL", "date": "DATE",
    "originaldate": "ORIGINALDATE", "genre": "GENRE", "mood": "MOOD", "isrc": "ISRC",
    "label": "LABEL", "releasetype": "RELEASETYPE", "compilation": "COMPILATION",
    "mb_albumid": "MUSICBRAINZ_ALBUMID", "mb_releasegroupid": "MUSICBRAINZ_RELEASEGROUPID",
    "mb_albumartistid": "MUSICBRAINZ_ALBUMARTISTID", "mb_artistid": "MUSICBRAINZ_ARTISTID",
    "mb_trackid": "MUSICBRAINZ_TRACKID", "mb_releasetrackid": "MUSICBRAINZ_RELEASETRACKID",
}
_VORBIS_ALIASES = {
    "totaltracks": ("TOTALTRACKS",), "totaldiscs": ("TOTALDISCS",),
    "albumartist": ("ALBUM ARTIST", "ALBUM_ARTIST"), "label": ("ORGANIZATION", "PUBLISHER"),
    "originaldate": ("ORIGINALYEAR",), "releasetype": ("MUSICBRAINZ_ALBUMTYPE",),
}

# ID3v2.4. Simple text frames; the rest are TXXX descriptions or UFID.
_ID3_TEXT = {
    "title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
    "albumartistsort": "TSO2", "artistsort": "TSOP", "date": "TDRC", "originaldate": "TDOR",
    "genre": "TCON", "mood": "TMOO", "isrc": "TSRC", "label": "TPUB", "compilation": "TCMP",
}
_ID3_FRAME_CLASSES = {"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB, "TPE2": TPE2, "TSO2": TSO2,
                      "TSOP": TSOP, "TDRC": TDRC, "TDOR": TDOR, "TCON": TCON, "TMOO": TMOO,
                      "TSRC": TSRC, "TPUB": TPUB, "TCMP": TCMP}
_ID3_TXXX = {
    "artists": "ARTISTS", "releasetype": "MusicBrainz Album Type",
    "mb_albumid": "MusicBrainz Album Id", "mb_releasegroupid": "MusicBrainz Release Group Id",
    "mb_albumartistid": "MusicBrainz Album Artist Id", "mb_artistid": "MusicBrainz Artist Id",
    "mb_releasetrackid": "MusicBrainz Release Track Id",
}
_UFID_OWNER = "http://musicbrainz.org"

# MP4 atoms; freeform ones live under ----:com.apple.iTunes:<name>.
_MP4_ATOMS = {
    "title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "albumartist": "aART",
    "albumartistsort": "soaa", "artistsort": "soar", "date": "\xa9day", "genre": "\xa9gen",
}
_MP4_FREEFORM = {
    "artists": "ARTISTS", "isrc": "ISRC", "label": "LABEL", "mood": "MOOD",
    "originaldate": "ORIGINALDATE", "releasetype": "MusicBrainz Album Type",
    "mb_albumid": "MusicBrainz Album Id", "mb_releasegroupid": "MusicBrainz Release Group Id",
    "mb_albumartistid": "MusicBrainz Album Artist Id", "mb_artistid": "MusicBrainz Artist Id",
    "mb_trackid": "MusicBrainz Track Id", "mb_releasetrackid": "MusicBrainz Release Track Id",
}
_FREEFORM_PREFIX = "----:com.apple.iTunes:"

# ------------------------------------------------------------------ junk
SPAM_RE = re.compile(
    r"pmedia|t\.me/|telegram|https?://|www\.|youtube|youtu\.be|provided to youtube"
    r"|auto-generated by youtube|\b[\w-]+\.(?:com|net|org|ru|to|cc)\b", re.I)
# Keys that are never worth keeping, whatever they say.
_ALWAYS_JUNK = {"purl", "synopsis", "pmedia", "wwwaudiofile", "wwwaudiosource",
                "uploader", "provider", "ldes", "desc", "description", "youtube"}
# Genre values that describe nothing: YouTube's category, placeholders.
_JUNK_GENRES = {"music", "other", "unknown", "genre", "misc", "none", "general",
                "default", "various", "n/a"}
# Never removed by the spam rule (they are the music, or harmless).
_PROTECTED_VORBIS = {k.lower() for k in _VORBIS.values()} | {
    "lyrics", "unsyncedlyrics", "metadata_block_picture", "coverart", "coverartmime",
    "replaygain_track_gain", "replaygain_track_peak", "replaygain_album_gain",
    "replaygain_album_peak", "r128_track_gain", "r128_album_gain", "composer",
    "language", "encoder", "bpm", "discsubtitle", "barcode", "catalognumber",
    "releasecountry", "media", "script", "titlesort", "albumsort"}
_PROTECTED_ID3 = set(_ID3_TEXT.values()) | {"TRCK", "TPOS", "APIC", "USLT", "SYLT", "UFID",
                                            "RVA2", "TCOM", "TLAN", "TSSE", "TBPM", "TSOT",
                                            "TSOA", "TIT3", "TSST", "PRIV", "TLEN"}
_PROTECTED_MP4 = set(_MP4_ATOMS.values()) | {"trkn", "disk", "covr", "\xa9lyr", "cpil",
                                             "\xa9wrt", "tmpo", "pgap", "\xa9too", "sonm",
                                             "soal", "stik", "rtng"}


class TagError(Exception):
    pass


def junk_genre(value: str) -> bool:
    """A genre value that is noise: a placeholder, a URL, or a keyword soup
    ("Avril Lavigne, blackbear, Travis Barker, …, Music Video, official")."""
    v = (value or "").strip()
    if not v or v.casefold() in _JUNK_GENRES:
        return True
    if SPAM_RE.search(v):
        return True
    return v.count(",") >= 3 or len(v) > 60


def split_genres(values: list[str], split: bool = True) -> list[str]:
    """Genre tags as written by old taggers — "Pop, Rock; Metal" — into one
    genre per value, junk removed, order kept, duplicates dropped. With
    split=False values are taken as already one genre each."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        if junk_genre(value) and value.count(",") >= 3:
            continue
        parts = (re.split(r"\s*[;,/]\s*|\s+\|\s+|\x00", value or "") if split
                 else [value or ""])
        for part in parts:
            part = part.strip()
            if part and not junk_genre(part) and part.casefold() not in seen:
                seen.add(part.casefold())
                out.append(part)
    return out


def _spammy(values) -> bool:
    return any(SPAM_RE.search(str(v)) for v in values)


# ------------------------------------------------------------------ open
def _open(path: Path):
    suffix = path.suffix.lower()
    try:
        if suffix == ".flac":
            return FLAC(path), "vorbis"
        if suffix == ".opus":
            return OggOpus(path), "vorbis"
        if suffix == ".ogg":
            return OggVorbis(path), "vorbis"
        if suffix == ".mp3":
            audio = mutagen.File(path)
            if audio is None:
                raise TagError(f"not an MP3 mutagen recognises: {path.name}")
            return audio, "id3"
        if suffix in (".m4a", ".mp4", ".aac"):
            if suffix == ".aac":
                audio = mutagen.File(path)
                if audio is None or not isinstance(audio, MP4):
                    raise TagError(f"no tag support for raw AAC: {path.name}")
                return audio, "mp4"
            return MP4(path), "mp4"
        if suffix == ".wav":
            return WAVE(path), "id3"
        if suffix in (".aif", ".aiff"):
            return AIFF(path), "id3"
    except TagError:
        raise
    except Exception as exc:
        raise TagError(f"cannot open {path.name}: {exc}") from exc
    raise TagError(f"no tag support for {suffix}")


def _info(audio) -> dict:
    info = getattr(audio, "info", None)
    bitrate = getattr(info, "bitrate", None)
    return {
        "length": getattr(info, "length", None),
        "bitrate_kbps": int(bitrate / 1000) if bitrate else None,
        "sample_rate": getattr(info, "sample_rate", None),
        "bits_per_sample": getattr(info, "bits_per_sample", None),
        "channels": getattr(info, "channels", None),
    }


# ------------------------------------------------------------------ read
def _num_pair(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    parts = str(text).split("/", 1)
    first = parts[0].strip().lstrip("0") or ("0" if parts[0].strip() else None)
    second = parts[1].strip().lstrip("0") if len(parts) > 1 else None
    return first or None, second or None


def _read_vorbis(tags) -> tuple[dict, list[str]]:
    out: dict[str, list[str]] = {}
    for field, key in _VORBIS.items():
        values = []
        for k in (key, *_VORBIS_ALIASES.get(field, ())):
            try:
                values = [str(v) for v in tags.get(k, []) if str(v).strip()]
            except (ValueError, KeyError):
                values = []
            if values:
                break
        if values:
            out[field] = values
    for field in ("tracknumber", "discnumber"):
        if field in out:
            n, total = _num_pair(out[field][0])
            out[field] = [n] if n else []
            total_field = "totaltracks" if field == "tracknumber" else "totaldiscs"
            if total and not out.get(total_field):
                out[total_field] = [total]
    junk = []
    for key in {k.lower() for k in tags.keys()}:
        if key in _ALWAYS_JUNK:
            junk.append(key)
        elif key not in _PROTECTED_VORBIS and key not in {a.lower() for al in
                                                          _VORBIS_ALIASES.values() for a in al}:
            try:
                if _spammy(tags.get(key, [])):
                    junk.append(key)
            except (ValueError, KeyError):
                continue
        elif key in ("label", "organization", "publisher") and _spammy(tags.get(key, [])):
            junk.append(key)
    return out, sorted(junk)


def _id3_text(frame) -> list[str]:
    return [str(t) for t in getattr(frame, "text", []) if str(t).strip()]


def _read_id3(tags) -> tuple[dict, list[str]]:
    out: dict[str, list[str]] = {}
    for field, frame_id in _ID3_TEXT.items():
        values = []
        for frame in tags.getall(frame_id):
            values.extend(_id3_text(frame))
        if values:
            out[field] = values
    for field, desc in _ID3_TXXX.items():
        frames = tags.getall(f"TXXX:{desc}")
        if not frames and desc != desc.upper():
            frames = tags.getall(f"TXXX:{desc.upper()}")
        values = [v for f in frames for v in _id3_text(f)]
        if values:
            out[field] = values
    for frame in tags.getall("UFID"):
        if getattr(frame, "owner", "") == _UFID_OWNER and frame.data:
            out["mb_trackid"] = [frame.data.decode("ascii", "ignore")]
    for frame_id, field, total_field in (("TRCK", "tracknumber", "totaltracks"),
                                         ("TPOS", "discnumber", "totaldiscs")):
        frames = tags.getall(frame_id)
        if frames and _id3_text(frames[0]):
            n, total = _num_pair(_id3_text(frames[0])[0])
            if n:
                out[field] = [n]
            if total:
                out[total_field] = [total]
    if out.get("compilation") and out["compilation"][0] in ("0", ""):
        out.pop("compilation")
    junk = []
    for key, frame in tags.items():
        frame_id = getattr(frame, "FrameID", key[:4])
        if frame_id in ("WXXX", "WOAR", "WOAS", "WOAF", "WORS", "WPUB", "WCOM", "WCOP"):
            junk.append(key)
            continue
        if frame_id == "TXXX":
            desc = (getattr(frame, "desc", "") or "").lower()
            if desc in _ALWAYS_JUNK or (desc not in {d.lower() for d in _ID3_TXXX.values()}
                                        and _spammy(_id3_text(frame))):
                junk.append(key)
            continue
        if frame_id == "COMM":
            if _spammy(_id3_text(frame)) or not _id3_text(frame):
                junk.append(key)
            continue
        if frame_id == "TPUB" and _spammy(_id3_text(frame)):
            junk.append(key)
            continue
        if frame_id not in _PROTECTED_ID3 and hasattr(frame, "text") \
                and _spammy(_id3_text(frame)):
            junk.append(key)
    return out, sorted(junk)


def _mp4_values(values) -> list[str]:
    out = []
    for v in values or []:
        if isinstance(v, (bytes, MP4FreeForm)):
            out.append(bytes(v).decode("utf-8", "ignore"))
        else:
            out.append(str(v))
    return [v for v in out if v.strip()]


def _read_mp4(tags) -> tuple[dict, list[str]]:
    out: dict[str, list[str]] = {}
    for field, atom in _MP4_ATOMS.items():
        values = _mp4_values(tags.get(atom))
        if values:
            out[field] = values
    for field, name in _MP4_FREEFORM.items():
        values = _mp4_values(tags.get(_FREEFORM_PREFIX + name))
        if not values:
            values = _mp4_values(tags.get(_FREEFORM_PREFIX + name.upper()))
        if values:
            out[field] = values
    for atom, field, total_field in (("trkn", "tracknumber", "totaltracks"),
                                     ("disk", "discnumber", "totaldiscs")):
        pairs = tags.get(atom)
        if pairs:
            n, total = pairs[0] if isinstance(pairs[0], tuple) else (pairs[0], 0)
            if n:
                out[field] = [str(n)]
            if total:
                out[total_field] = [str(total)]
    if tags.get("cpil"):
        out["compilation"] = ["1"]
    junk = []
    known_ff = {(_FREEFORM_PREFIX + n).lower() for n in _MP4_FREEFORM.values()}
    for key in tags.keys():
        lowered = key.lower()
        if key in ("\xa9cmt", "desc", "ldes", "purl", "egid", "\xa9url"):
            junk.append(key)
        elif lowered.startswith("----:"):
            name = lowered.rsplit(":", 1)[-1]
            if name in _ALWAYS_JUNK or (lowered not in known_ff
                                        and _spammy(_mp4_values(tags.get(key)))):
                junk.append(key)
            elif lowered == (_FREEFORM_PREFIX + "label").lower() \
                    and _spammy(_mp4_values(tags.get(key))):
                junk.append(key)
        elif key not in _PROTECTED_MP4:
            try:
                if _spammy(_mp4_values(tags.get(key))):
                    junk.append(key)
            except Exception:
                continue
    return out, sorted(junk)


def read(path: Path) -> dict:
    """{"fields": {field: [values]}, "junk": [raw keys], "info": {...}}.
    Raises TagError when the file cannot be opened."""
    audio, kind = _open(path)
    tags = audio.tags
    if tags is None:
        fields, junk = {}, []
    elif kind == "vorbis":
        fields, junk = _read_vorbis(tags)
    elif kind == "id3":
        fields, junk = _read_id3(tags)
    else:
        fields, junk = _read_mp4(tags)
    genres = fields.get("genre") or []
    if genres and any(junk_genre(g) for g in genres):
        junk.append("genre")
    if any(len(g.split(",")) > 1 for g in genres):
        junk.append("genre-joined")
    return {"fields": fields, "junk": sorted(set(junk)), "info": _info(audio)}


# ------------------------------------------------------------------ write
def _clean(values) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, (str, int, float)):
        values = [values]
    cleaned = [str(v).strip() for v in values if v is not None and str(v).strip()]
    return cleaned


def _write_vorbis(audio, fields: dict, strip_junk: bool, before: dict) -> list[str]:
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    current, junk = _read_vorbis(tags)
    removed = []
    # Junk first: a spam LABEL is junk, and the real label written below
    # must not be swept away with it.
    if strip_junk:
        for key in junk:
            try:
                before[f"raw:{key}"] = [str(v) for v in tags.get(key, [])]
                del tags[key]
                removed.append(key)
            except (KeyError, ValueError):
                continue
    for field, values in fields.items():
        key = _VORBIS.get(field)
        if key is None:
            continue
        before[field] = current.get(field, [])
        for alias in (key, *_VORBIS_ALIASES.get(field, ())):
            try:
                if alias in tags:
                    del tags[alias]
            except (KeyError, ValueError):
                pass
        if values:
            tags[key] = values
    return removed


def _write_id3(audio, fields: dict, strip_junk: bool, before: dict) -> list[str]:
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    current, junk = _read_id3(tags)
    removed = []
    if strip_junk:
        for key in junk:
            if key in tags:
                frame = tags[key]
                before[f"raw:{key}"] = _id3_text(frame) or [str(frame)]
                del tags[key]
                removed.append(key)
    for field, values in fields.items():
        if field in ("tracknumber", "totaltracks", "discnumber", "totaldiscs"):
            continue
        before[field] = current.get(field, [])
        if field in _ID3_TEXT:
            frame_id = _ID3_TEXT[field]
            tags.delall(frame_id)
            if values:
                tags.add(_ID3_FRAME_CLASSES[frame_id](encoding=3, text=values))
        elif field in _ID3_TXXX:
            desc = _ID3_TXXX[field]
            tags.delall(f"TXXX:{desc}")
            tags.delall(f"TXXX:{desc.upper()}")
            if values:
                tags.add(TXXX(encoding=3, desc=desc, text=values))
        elif field == "mb_trackid":
            for key in [k for k, f in tags.items()
                        if getattr(f, "FrameID", "") == "UFID" and f.owner == _UFID_OWNER]:
                del tags[key]
            if values:
                tags.add(UFID(owner=_UFID_OWNER, data=values[0].encode("ascii")))
    for frame_id, field, total_field in (("TRCK", "tracknumber", "totaltracks"),
                                         ("TPOS", "discnumber", "totaldiscs")):
        if field in fields or total_field in fields:
            before[field] = current.get(field, [])
            before[total_field] = current.get(total_field, [])
            n = (fields.get(field) if field in fields else current.get(field)) or []
            total = (fields.get(total_field) if total_field in fields
                     else current.get(total_field)) or []
            tags.delall(frame_id)
            if n:
                text = f"{n[0]}/{total[0]}" if total else n[0]
                tags.add((TRCK if frame_id == "TRCK" else TPOS)(encoding=3, text=[text]))
    return removed


def _write_mp4(audio, fields: dict, strip_junk: bool, before: dict) -> list[str]:
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    current, junk = _read_mp4(tags)
    removed = []
    if strip_junk:
        for key in junk:
            if key in tags:
                before[f"raw:{key}"] = _mp4_values(tags.get(key))
                del tags[key]
                removed.append(key)
    for field, values in fields.items():
        if field in ("tracknumber", "totaltracks", "discnumber", "totaldiscs", "compilation"):
            continue
        before[field] = current.get(field, [])
        if field in _MP4_ATOMS:
            atom = _MP4_ATOMS[field]
            if values:
                tags[atom] = values
            elif atom in tags:
                del tags[atom]
        elif field in _MP4_FREEFORM:
            atom = _FREEFORM_PREFIX + _MP4_FREEFORM[field]
            for existing in [k for k in tags.keys() if k.lower() == atom.lower()]:
                del tags[existing]
            if values:
                tags[atom] = [MP4FreeForm(v.encode("utf-8")) for v in values]
    for atom, field, total_field in (("trkn", "tracknumber", "totaltracks"),
                                     ("disk", "discnumber", "totaldiscs")):
        if field in fields or total_field in fields:
            before[field] = current.get(field, [])
            before[total_field] = current.get(total_field, [])
            n = (fields.get(field) if field in fields else current.get(field)) or []
            total = (fields.get(total_field) if total_field in fields
                     else current.get(total_field)) or []
            if n:
                tags[atom] = [(int(n[0]), int(total[0]) if total else 0)]
            elif atom in tags:
                del tags[atom]
    if "compilation" in fields:
        before["compilation"] = current.get("compilation", [])
        if fields["compilation"] and fields["compilation"][0] not in ("0", ""):
            tags["cpil"] = True
        elif "cpil" in tags:
            del tags["cpil"]
    return removed


def _embed_cover(audio, kind: str, cover: bytes, mime: str = "image/jpeg") -> None:
    if kind == "mp4":
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio.tags["covr"] = [MP4Cover(cover, imageformat=fmt)]
    elif kind == "id3":
        audio.tags.setall("APIC", [APIC(encoding=3, mime=mime, type=3,
                                        desc="Front cover", data=cover)])
    elif isinstance(audio, FLAC):
        audio.clear_pictures()
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, cover
        audio.add_picture(pic)
    else:
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, cover
        audio.tags["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]


def write(path: Path, fields: dict, *, strip_junk: bool = True,
          cover: bytes | None = None, clean_genres: bool = True) -> dict:
    """Set `fields` (field -> list of values; an empty list or None deletes the
    field; fields not mentioned are left alone) and remove junk.

    Returns {"before": {field: old values, "raw:<key>": removed values},
    "removed": [raw keys], "changed": bool}. The caller journals `before` so
    the write can be undone."""
    audio, kind = _open(path)
    wanted = {}
    for field, values in fields.items():
        if field not in FIELDS:
            raise TagError(f"unknown field {field!r}")
        wanted[field] = _clean(values) or []
    if "genre" in wanted and clean_genres:
        wanted["genre"] = split_genres(wanted["genre"], split=False)
    before: dict[str, list[str]] = {}
    if kind == "vorbis":
        removed = _write_vorbis(audio, wanted, strip_junk, before)
    elif kind == "id3":
        removed = _write_id3(audio, wanted, strip_junk, before)
    else:
        removed = _write_mp4(audio, wanted, strip_junk, before)
    if cover:
        _embed_cover(audio, kind, cover)
    changed = bool(removed) or bool(cover) or any(
        (before.get(f) or []) != (wanted.get(f) or []) for f in wanted)
    if changed:
        if kind == "id3" and isinstance(audio.tags, ID3) and path.suffix.lower() == ".mp3":
            audio.tags.save(path, v2_version=4)
        else:
            audio.save()
    return {"before": before, "removed": removed, "changed": changed}


def restore(path: Path, before: dict) -> None:
    """Undo a write: put back the journalled values, raw junk keys included."""
    fields = {k: v for k, v in before.items() if not k.startswith("raw:") and k in FIELDS}
    raw = {k[4:]: v for k, v in before.items() if k.startswith("raw:")}
    write(path, fields, strip_junk=False, clean_genres=False)
    if not raw:
        return
    audio, kind = _open(path)
    if audio.tags is None:
        audio.add_tags()
    for key, values in raw.items():
        if kind == "vorbis":
            audio.tags[key] = values
        elif kind == "mp4":
            if key.startswith("----:"):
                audio.tags[key] = [MP4FreeForm(v.encode("utf-8")) for v in values]
            else:
                audio.tags[key] = values
        else:
            frame_id = key.split(":", 1)[0]
            if frame_id == "TXXX":
                audio.tags.add(TXXX(encoding=3, desc=key.split(":", 1)[1], text=values))
            elif frame_id == "COMM":
                from mutagen.id3 import COMM
                parts = key.split(":")
                audio.tags.add(COMM(encoding=3, desc=parts[1] if len(parts) > 1 else "",
                                    lang=parts[2] if len(parts) > 2 else "eng", text=values))
            elif frame_id.startswith("W"):
                from mutagen.id3 import Frames
                cls = Frames.get(frame_id)
                if cls is not None and values:
                    try:
                        audio.tags.add(cls(url=values[0]))
                    except TypeError:
                        pass
            else:
                from mutagen.id3 import Frames
                cls = Frames.get(frame_id)
                if cls is not None:
                    audio.tags.add(cls(encoding=3, text=values))
    if kind == "id3" and path.suffix.lower() == ".mp3":
        audio.tags.save(path, v2_version=4)
    else:
        audio.save()
