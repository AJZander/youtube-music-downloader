"""Genre and mood vocabularies.

Genres use MusicBrainz's genre names (the user's choice), title-cased for
display: "hip hop" -> "Hip Hop", "r&b" -> "R&B". Community tags (Last.fm)
only count when they *are* MusicBrainz genre names — folksonomy is full of
"seen live", "favorites" and band names, and the genre list is the filter.

Moods use Plex's own mood vocabulary (AllMusic's, harvested from the
section), so a mood filled in for an obscure album sits in the same list as
the moods Plex gives matched ones.
"""
from __future__ import annotations

import json
import re

from ..db import db
from . import musicbrainz

# Spellings that mean a MusicBrainz genre but don't match its name.
_GENRE_ALIASES = {
    "hiphop": "hip hop", "hip-hop": "hip hop", "rap music": "hip hop",
    "rnb": "r&b", "r and b": "r&b", "rhythm and blues": "rhythm & blues",
    "electronica": "electronic", "electro": "electro", "edm": "electronic dance music",
    "dnb": "drum and bass", "d&b": "drum and bass", "drum n bass": "drum and bass",
    "alt rock": "alternative rock", "alt-rock": "alternative rock",
    "indie": "indie rock", "synthpop": "synth-pop", "synth pop": "synth-pop",
    "electropop": "electropop", "kpop": "k-pop", "jpop": "j-pop",
    "rock n roll": "rock and roll", "rock & roll": "rock and roll",
    "post punk": "post-punk", "pop punk": "pop punk", "lofi": "lo-fi",
    "lo fi": "lo-fi", "trip hop": "trip hop", "triphop": "trip hop",
    "classic rock": "classic rock", "aussie hip hop": "australian hip hop",
    "soundtrack": "soundtrack", "ost": "soundtrack",
}
_UPPER = {"r&b": "R&B", "edm": "EDM", "uk": "UK", "us": "US", "dj": "DJ", "idm": "IDM",
          "ebm": "EBM", "nwobhm": "NWOBHM", "aor": "AOR", "mpb": "MPB", "j-pop": "J-Pop",
          "k-pop": "K-Pop", "c-pop": "C-Pop", "j-rock": "J-Rock", "k-rap": "K-Rap",
          "uk garage": "UK Garage", "uk drill": "UK Drill", "uk funky": "UK Funky",
          "lo-fi": "Lo-Fi", "lo-fi hip hop": "Lo-Fi Hip Hop", "g-funk": "G-Funk"}

# Last.fm mood-ish tags -> a word that appears in Plex's mood list.
_MOOD_ALIASES = {
    "chill": "laid-back", "chillout": "laid-back", "relaxing": "relaxed", "relax": "relaxed",
    "melancholic": "melancholy", "sad": "melancholy", "depressing": "bleak",
    "happy": "happy", "upbeat": "cheerful", "feel good": "cheerful", "fun": "fun",
    "party": "party/celebratory", "dance": "rousing", "energetic": "energetic",
    "aggressive": "aggressive", "angry": "angry", "dark": "brooding", "moody": "brooding",
    "romantic": "romantic", "love": "romantic", "sexy": "sensual", "sensual": "sensual",
    "dreamy": "dreamy", "atmospheric": "atmospheric", "ambient": "atmospheric",
    "epic": "epic", "uplifting": "uplifting", "inspirational": "uplifting",
    "mellow": "mellow", "calm": "calm/peaceful", "peaceful": "calm/peaceful",
    "nostalgic": "nostalgic", "hypnotic": "hypnotic", "intense": "intense",
    "playful": "playful", "quirky": "quirky", "sentimental": "sentimental",
    "bittersweet": "bittersweet", "haunting": "haunting", "ethereal": "ethereal",
    "confident": "confident", "cool": "cool", "gritty": "gritty", "rebellious": "rebellious",
    "spiritual": "spiritual", "summer": "summery", "summery": "summery",
}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def display_genre(name: str) -> str:
    """MusicBrainz genre names are lower case; Plex shows tags as written."""
    n = norm(name)
    if n in _UPPER:
        return _UPPER[n]
    words = []
    for w in n.split(" "):
        if w in ("and", "of", "n", "&", "de", "la"):
            words.append(w)
        elif w in _UPPER:
            words.append(_UPPER[w])
        else:
            words.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(words)


class Vocab:
    """Loaded lazily; MusicBrainz's list is cached for a month."""

    def __init__(self) -> None:
        self._genres: dict[str, str] | None = None   # norm -> MB name
        self._moods: dict[str, str] | None = None    # norm -> Plex mood

    async def genres(self) -> dict[str, str]:
        if self._genres is None or not self._genres:
            try:
                names = await musicbrainz.genre_names()
            except musicbrainz.MusicBrainzError:
                names = []
            self._genres = {norm(n): n for n in names}
        return self._genres

    async def genre_list(self) -> list[str]:
        return sorted({display_genre(n) for n in (await self.genres()).values()})

    async def to_genre(self, tag: str) -> str | None:
        """The MusicBrainz genre a tag names, or None."""
        known = await self.genres()
        n = norm(tag)
        n = _GENRE_ALIASES.get(n, n)
        if n in known:
            return known[n]
        alt = n.replace("-", " ")
        if alt in known:
            return known[alt]
        alt = n.replace(" ", "-")
        return known.get(alt)

    async def moods(self) -> dict[str, str]:
        if self._moods is None:
            row = await db.fetchone("SELECT value FROM md_state WHERE key = 'plex_moods'")
            moods = json.loads(row["value"]) if row and row["value"] else []
            self._moods = {norm(m): m for m in moods}
        return self._moods

    def set_moods(self, moods: list[str]) -> None:
        self._moods = {norm(m): m for m in moods}

    async def to_mood(self, tag: str) -> str | None:
        moods = await self.moods()
        if not moods:
            return None
        n = norm(tag)
        if n in moods:
            return moods[n]
        target = _MOOD_ALIASES.get(n)
        if not target:
            return None
        if target in moods:
            return moods[target]
        for key, value in moods.items():      # "Party/Celebratory", "Laid-Back/Mellow"
            if target in key.split("/"):
                return value
        return None


vocab = Vocab()


async def genres_from_tags(tags: list[tuple[str, int]], limit: int,
                           min_share: float = 0.1) -> list[str]:
    """MusicBrainz genre names among community tags, strongest first. A tag
    needs a fair share of the top tag's weight to count."""
    if not tags:
        return []
    top = max((c for _, c in tags), default=0) or 1
    out: list[str] = []
    for name, count in tags:
        if top and count / top < min_share:
            continue
        genre = await vocab.to_genre(name)
        if genre and genre not in out:
            out.append(genre)
        if len(out) >= limit:
            break
    return out


async def moods_from_tags(tags: list[tuple[str, int]], limit: int) -> list[str]:
    out: list[str] = []
    for name, _count in tags:
        mood = await vocab.to_mood(name)
        if mood and mood not in out:
            out.append(mood)
        if len(out) >= limit:
            break
    return out
