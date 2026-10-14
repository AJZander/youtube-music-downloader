"""Duplicate findings as issues, so they sit in the same "needs attention"
list as everything else and flag the albums they concern.

The analysis itself is the library manager's (same-album rule, scoring,
exclusions); this only mirrors its groups. Dismissing one records the same
"not duplicates" exclusion the duplicate screens always have.
"""
from __future__ import annotations

import hashlib

from .. import library
from . import issues
from .units import unit_dir


def _gkey(parts) -> str:
    return hashlib.blake2b("\0".join(sorted(parts)).encode(), digest_size=8).hexdigest()


async def sync() -> dict:
    analysis = await library._analyse()
    items: list[dict] = []
    for group in analysis["albums"]:
        dirpaths = [i["dirpath"] for i in group["instances"]]
        artists = sorted({i.get("artist_dir") or d.split("/", 1)[0]
                          for i, d in zip(group["instances"], dirpaths)})
        name = group.get("album") or dirpaths[0].rsplit("/", 1)[-1]
        items.append(issues.planned(
            "dup_album", f"albums:{_gkey(dirpaths)}",
            f"{name}: {len(dirpaths)} copies", {"dirpaths": dirpaths, "kind": group.get("kind"),
                                                "group_id": group.get("id"),
                                                "reclaimable_bytes": group.get("reclaimable_bytes")},
            # listed under the albums they are (a disc folder's is its album's)
            artist_dir=artists[0], album_key=unit_dir(dirpaths[0]),
            refs=[(d.split("/", 1)[0], unit_dir(d)) for d in dirpaths]))
    for group in analysis["tracks"]:
        if group.get("in_album_group"):
            continue
        paths = [f["path"] for f in group.get("copies") or []]
        if not paths:
            continue
        folders = sorted({unit_dir(p.rsplit("/", 1)[0]) for p in paths})
        title = group.get("title") or paths[0].rsplit("/", 1)[-1]
        items.append(issues.planned(
            "dup_track", f"tracks:{_gkey(paths)}", f"{title}: {len(paths)} copies",
            {"paths": paths, "group_id": group.get("id")}, entity="track",
            artist_dir=folders[0].split("/", 1)[0], album_key=folders[0],
            refs=[(f.split("/", 1)[0], f) for f in folders]))
    for group in analysis["artists"]:
        folders = [v["artist_dir"] for v in group.get("variants") or [] if v.get("artist_dir")]
        if len(folders) < 2:
            continue
        items.append(issues.planned(
            "split_artist", f"artists:{_gkey(folders)}",
            f"{group.get('suggested_keeper') or folders[0]}: {len(folders)} folders",
            {"folders": folders, "suggested": group.get("suggested_keeper"),
             "group_id": group.get("id")},
            entity="artist", artist_dir=folders[0], album_key=None,
            refs=[(f, None) for f in folders]))
    await issues.save(items, scope_kinds=issues.DUP_KINDS, all_scope=True)
    return {"albums": sum(1 for i in items if i["kind"] == "dup_album"),
            "tracks": sum(1 for i in items if i["kind"] == "dup_track"),
            "artists": sum(1 for i in items if i["kind"] == "split_artist")}


async def dismiss(issue: dict) -> None:
    """Record the user's "not duplicates" the way the duplicate screens do."""
    d = issue.get("detail") or {}
    if issue["kind"] == "dup_album" and d.get("dirpaths"):
        await library.add_exclusions("album", d["dirpaths"], "not duplicates")
    elif issue["kind"] == "dup_track" and d.get("paths"):
        await library.add_exclusions("track", d["paths"], "not duplicates")
    elif issue["kind"] == "split_artist" and d.get("folders"):
        await library.add_exclusions("artist", d["folders"], "not the same artist")
