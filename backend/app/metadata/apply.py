"""Executors: the code that changes files and Plex, one issue kind each, and
the journal that lets every change be undone.

Safety rules, the same as the importer's and the library manager's:
  * nothing touches the library while its mount sentinel is missing;
  * tag writes are in place (mutagen, padding-aware) and journal the values
    they replace — junk keys included — so a revert restores them exactly;
  * moves are renames on one filesystem (instant, byte-identical), never
    copies; a destination that already holds something is refused;
  * nothing is deleted. A folder emptied by a move goes only if it is empty;
  * Plex edits lock the field (or a refresh would put Plex's value back) and
    journal the old value and lock state; titles are never edited in Plex.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

import httpx

from ..config import settings
from ..db import db, utcnow
from ..engine import tagio
from ..importer import library_online
from ..plex import ALBUM, ARTIST, TRACK, PlexError, is_matched, plex
from . import plexsync
from .issues import junk_fix_fields
from .plexsync import jlist
from .units import folder_of

log = logging.getLogger("mdl.apply")

SIDECARS = (".lrc", ".txt")
FOLDER_LEFTOVERS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".cue", ".log", ".nfo", ".txt",
                    ".m3u", ".m3u8", ".pdf", ".sfv", ".md5", ".accurip", ".ffp"}


class ApplyError(Exception):
    pass


class KeepOpen(Exception):
    """The action ran, but the issue stays open until the world catches up
    (a download was queued; the tracks aren't here yet)."""


class Ctx:
    """What one album's apply knows and has done so far."""

    def __init__(self, key: str, rows: list[dict], desired: dict | None,
                 artist_dir: str | None) -> None:
        self.key = key
        self.rows = rows
        self.desired = desired
        self.artist_dir = artist_dir
        self.batches: list[str] = []
        self.touched_folders: set[str] = set()
        self.new_folders: set[str] = set()      # folders files were moved into
        self.moved: dict[str, str] = {}
        self.plex_dirty = False
        self.started = time.time()

    def new_batch(self) -> str:
        batch = uuid.uuid4().hex[:12]
        self.batches.append(batch)
        return batch


async def journal(batch: str, target: str, ref: str, field: str, old, new, summary: str,
                  *, artist_dir: str | None, album_key: str | None) -> None:
    await db.execute(
        "INSERT INTO md_changes (batch_id, target, ref, artist_dir, album_key, field, "
        "old_value, new_value, summary, applied_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (batch, target, ref, artist_dir, album_key, field,
         json.dumps(old, default=str), json.dumps(new, default=str), summary, utcnow()))


async def ours_locked(rating_key: str, field: str) -> bool:
    """Did we lock this Plex field? (A lock we didn't set is the user's.)"""
    row = await db.fetchone(
        "SELECT 1 FROM md_changes WHERE target = 'plex' AND ref = ? AND field = ? "
        "AND reverted_at IS NULL LIMIT 1", (rating_key, field))
    return row is not None


def _require_online() -> None:
    if not library_online():
        raise ApplyError("the library mount looks offline (sentinel missing)")


async def _run(func, *args):
    return await asyncio.get_running_loop().run_in_executor(None, func, *args)


# ------------------------------------------------------------------ index upkeep
async def reindex(paths: list[str]) -> None:
    """Re-read files we just changed, so the index (and the next plan) sees
    the new tags without waiting for a library scan."""
    from .. import library
    root = settings.library_dir

    def read_all() -> list[tuple]:
        out = []
        for rel in paths:
            try:
                st = (root / rel).stat()
            except OSError:
                continue
            out.append(library._read_file(root, rel, st.st_size, st.st_mtime))
        return out

    rows = await _run(read_all)
    if rows:
        await db.executemany(library._UPSERT, rows)


async def rekey_paths(pairs: list[tuple[str, str]]) -> None:
    """Point everything that names a file at its new path."""
    for old, new in pairs:
        parts = Path(new).parts
        artist_dir = parts[0] if len(parts) > 1 else ""
        dirpath = str(Path(new).parent) if len(parts) > 1 else ""
        await db.execute(
            "UPDATE library_index SET path = ?, dirpath = ?, artist_dir = ? WHERE path = ?",
            (new, dirpath, artist_dir, old))
        await db.execute("UPDATE tracks SET library_path = ? WHERE library_path = ?", (new, old))
        await db.execute(
            "UPDATE library_exclusions SET key_a = ? WHERE scope = 'track' AND key_a = ?",
            (new, old))
        await db.execute(
            "UPDATE library_exclusions SET key_b = ? WHERE scope = 'track' AND key_b = ?",
            (new, old))


async def rekey_folder(old: str, new: str) -> None:
    await db.execute("UPDATE library_exclusions SET key_a = ? WHERE scope = 'album' AND key_a = ?",
                     (new, old))
    await db.execute("UPDATE library_exclusions SET key_b = ? WHERE scope = 'album' AND key_b = ?",
                     (new, old))


async def rekey_unit(old_key: str, new_key: str, new_folder: str, artist_dir: str) -> None:
    if old_key == new_key:
        return
    exists = await db.fetchone("SELECT 1 FROM md_albums WHERE key = ?", (new_key,))
    if exists:
        await db.execute("DELETE FROM md_albums WHERE key = ?", (old_key,))
    else:
        await db.execute(
            "UPDATE md_albums SET key = ?, dirpath = ?, artist_dir = ?, next_check = 0 "
            "WHERE key = ?", (new_key, new_folder, artist_dir, old_key))
    await db.execute("UPDATE md_changes SET album_key = ?, artist_dir = ? WHERE album_key = ?",
                     (new_key, artist_dir, old_key))
    # The old unit's open issues are moot; the re-check files new ones. Queued
    # ones are left alone: they are part of this apply, which re-plans them
    # under the new key (marking them "applied" here would claim a fix that
    # never ran).
    await db.execute(
        "UPDATE md_issues SET state = 'applied', updated_at = ? WHERE album_key = ? "
        "AND state IN ('open', 'failed')", (utcnow(), old_key))
    await db.execute("UPDATE md_issue_refs SET album_key = ? WHERE album_key = ?",
                     (new_key, old_key))


# ------------------------------------------------------------------ file stage
async def do_tags(issue: dict, ctx: Ctx) -> str:
    _require_online()
    root = settings.library_dir
    batch = ctx.new_batch()
    written, failures = [], []
    for row in ctx.rows:
        path = row["path"]
        if ctx.desired is not None:
            fields = (ctx.desired["tracks"].get(path) or {}).get("tags") or {}
        else:
            fields = junk_fix_fields(row)
        if not fields and not row.get("junk"):
            continue

        def write(p=root / path, f=fields):
            before_len = tagio.read(p)["info"].get("length")
            result = tagio.write(p, f, strip_junk=True)
            after_len = tagio.read(p)["info"].get("length")
            if before_len and after_len and abs(before_len - after_len) > 0.5:
                raise ApplyError(f"audio length changed after the tag write ({before_len} -> {after_len})")
            return result

        try:
            result = await _run(write)
        except (tagio.TagError, OSError, ApplyError) as exc:
            failures.append(f"{path}: {exc}")
            continue
        if result["changed"]:
            written.append(path)
            await journal(batch, "file", path, "tags", result["before"], fields,
                          f"Wrote tags on {Path(path).name}",
                          artist_dir=ctx.artist_dir, album_key=ctx.key)
    if written:
        await reindex(written)
        ctx.touched_folders.add(folder_of(ctx.rows[0]))
        ctx.plex_dirty = True
        await db.execute("UPDATE md_changes SET summary = ? WHERE batch_id = ?",
                         (f"Wrote tags on {len(written)} file{'s' if len(written) != 1 else ''}",
                          batch))
    if failures and not written:
        raise ApplyError("; ".join(failures[:3]))
    return f"tags written on {len(written)} files" + (
        f", {len(failures)} failed" if failures else "")


NUMBER_FIELDS = ("tracknumber", "totaltracks", "discnumber", "totaldiscs")


async def do_track_order_files(issue: dict, ctx: Ctx) -> str:
    """Write the release's track and disc numbers (only those) to the files."""
    _require_online()
    if ctx.desired is None:
        raise ApplyError("the album is not identified")
    root = settings.library_dir
    batch = ctx.new_batch()
    written = []
    # the files where they are now (a move earlier in this apply renamed them)
    paths = [ctx.moved.get(f["path"], f["path"]) for f in issue["detail"].get("files") or []]
    for path in paths:
        tags = (ctx.desired["tracks"].get(path) or {}).get("tags") or {}
        fields = {k: tags.get(k) or [] for k in NUMBER_FIELDS}
        if not fields["tracknumber"]:
            continue
        try:
            result = await _run(lambda p=root / path, f=fields: tagio.write(p, f, strip_junk=False))
        except (tagio.TagError, OSError) as exc:
            raise ApplyError(f"{path}: {exc}") from exc
        if result["changed"]:
            written.append(path)
            await journal(batch, "file", path, "tags", result["before"], fields,
                          f"Renumbered {len(paths)} tracks",
                          artist_dir=ctx.artist_dir, album_key=ctx.key)
    if written:
        await reindex(written)
        ctx.touched_folders.add(folder_of(ctx.rows[0]))
        ctx.plex_dirty = True
    return f"renumbered {len(written)} files"


async def do_track_order_plex(issue: dict, ctx: Ctx) -> str:
    """Put Plex's track and disc numbers right (locked, like every edit)."""
    fixes = issue["detail"].get("plex") or []
    if not fixes:
        return "nothing to renumber in Plex"
    batch = ctx.new_batch()
    for fix in fixes:
        row = await db.fetchone("SELECT idx, parent_idx FROM plex_items WHERE rating_key = ?",
                                (fix["rating_key"],))
        old = {"index": row["idx"] if row else None, "disc": row["parent_idx"] if row else None}
        await plex.edit(TRACK, fix["rating_key"],
                        plex.field_pairs("index", fix["index"])
                        + plex.field_pairs("parentIndex", fix["disc"]))
        await journal(batch, "plex", fix["rating_key"], "index", old,
                      {"index": fix["index"], "disc": fix["disc"]},
                      f"Renumbered {len(fixes)} tracks in Plex",
                      artist_dir=ctx.artist_dir, album_key=ctx.key)
    await plexsync.refresh_items([f["rating_key"] for f in fixes])
    return f"renumbered {len(fixes)} tracks in Plex"


async def do_download_missing(issue: dict, ctx: Ctx) -> str:
    from . import missing
    try:
        result = await missing.queue_download(issue["ref"])
    except RuntimeError as exc:
        raise ApplyError(str(exc)) from exc
    note = f"queued {result.get('queued', 0)} tracks for download"
    if result.get("not_found"):
        note += f"; not on YouTube Music: {', '.join(result['not_found'][:5])}"
    raise KeepOpen(note)


def _move_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            same = os.path.samefile(src, dst)
        except OSError:
            same = False
        if not same:
            raise ApplyError(f"{dst.name} already exists at the destination")
        # case-only rename on a case-insensitive share: go via a temp name
        tmp = dst.with_name(f".mdl-rename-{uuid.uuid4().hex[:8]}{dst.suffix}")
        os.replace(src, tmp)
        os.replace(tmp, dst)
        return
    os.replace(src, dst)


async def do_move(issue: dict, ctx: Ctx) -> str:
    _require_online()
    from .issues import move_conflict
    if ctx.desired is None:
        raise ApplyError("the album is not identified")
    root = settings.library_dir
    moves = [{"from": r["path"], "to": ctx.desired["tracks"][r["path"]]["target"]}
             for r in ctx.rows if (ctx.desired["tracks"].get(r["path"]) or {}).get("target")
             and ctx.desired["tracks"][r["path"]]["target"] != r["path"]]
    if not moves:
        return "nothing to move"
    conflict = await move_conflict(ctx.key, ctx.rows, ctx.desired, moves)
    if conflict:
        raise ApplyError(conflict)
    batch = ctx.new_batch()
    done: list[tuple[str, str]] = []
    # the files' folders, and the album folder above any disc subfolders
    source_dirs = {m["from"].rsplit("/", 1)[0] for m in moves}
    source_dirs |= {folder_of({"path": m["from"]}) for m in moves}
    target_dir = ctx.desired["folder"]
    try:
        for m in moves:
            src, dst = root / m["from"], root / m["to"]
            await _run(_move_file, src, dst)
            done.append((m["from"], m["to"]))
            await journal(batch, "move", m["from"], "file", m["from"], m["to"],
                          f"Moved {Path(m['from']).name}",
                          artist_dir=ctx.artist_dir, album_key=ctx.key)
            for suffix in SIDECARS:
                side_src = src.with_suffix(suffix)
                if await _run(side_src.exists):
                    side_dst = dst.with_suffix(suffix)
                    try:
                        await _run(_move_file, side_src, side_dst)
                        await journal(batch, "move", str(Path(m["from"]).with_suffix(suffix)),
                                      "sidecar", None, str(Path(m["to"]).with_suffix(suffix)),
                                      "Moved sidecar", artist_dir=ctx.artist_dir,
                                      album_key=ctx.key)
                    except (ApplyError, OSError) as exc:
                        log.info("sidecar %s left behind: %s", side_src, exc)
    except (OSError, ApplyError) as exc:
        await rekey_paths(done)
        raise ApplyError(f"stopped after {len(done)} of {len(moves)} files: {exc}") from exc

    # Folder leftovers (cover art, cue sheets, rip logs) follow the album when
    # its old folder has no audio left; an emptied folder is removed only if
    # it really is empty.
    from ..library import AUDIO_SUFFIXES

    def tidy(folder: str) -> list[tuple[str, str]]:
        moved_extra = []
        src_dir = root / folder
        if not src_dir.is_dir() or folder == target_dir:
            return moved_extra
        entries = list(src_dir.iterdir())
        if any(e.is_file() and e.suffix.lower() in AUDIO_SUFFIXES for e in entries) or \
                any(e.is_dir() for e in entries):
            return moved_extra
        for e in entries:
            if e.is_file() and e.suffix.lower() in FOLDER_LEFTOVERS and not e.name.startswith("."):
                dst = root / target_dir / e.name
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(e, dst)
                    moved_extra.append((f"{folder}/{e.name}", f"{target_dir}/{e.name}"))
        try:
            src_dir.rmdir()
            parent = src_dir.parent
            if parent != root and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
        return moved_extra

    for folder in sorted(source_dirs, key=lambda f: f.count("/"), reverse=True):
        try:
            extra = await _run(tidy, folder)
        except OSError as exc:
            log.info("could not tidy %s: %s", folder, exc)
            extra = []
        for old, new in extra:
            await journal(batch, "move", old, "leftover", None, new, "Moved folder file",
                          artist_dir=ctx.artist_dir, album_key=ctx.key)
    await rekey_paths(done)
    for folder in source_dirs:
        await rekey_folder(folder, target_dir)
    new_artist = Path(target_dir).parts[0]
    new_key = target_dir
    await rekey_unit(ctx.key, new_key, target_dir, new_artist)
    await db.execute("UPDATE md_changes SET summary = ?, album_key = ?, artist_dir = ? "
                     "WHERE batch_id = ?",
                     (f"Moved {len(done)} files to {target_dir}", new_key, new_artist, batch))
    ctx.moved.update(dict(done))
    ctx.touched_folders |= source_dirs | {target_dir}
    ctx.new_folders.add(target_dir)
    ctx.plex_dirty = True
    ctx.key = new_key
    ctx.artist_dir = new_artist
    for row in ctx.rows:
        if row["path"] in ctx.moved:
            row["path"] = ctx.moved[row["path"]]
            row["dirpath"] = target_dir
            row["artist_dir"] = new_artist
    if ctx.desired:
        ctx.desired["tracks"] = {ctx.moved.get(p, p): t for p, t in ctx.desired["tracks"].items()}
    return f"moved {len(done)} files to {target_dir}"


async def do_scan(issue: dict, ctx: Ctx) -> str:
    folder = (issue.get("detail") or {}).get("folder") or (folder_of(ctx.rows[0]) if ctx.rows else None)
    if folder is None:
        raise ApplyError("no folder to scan")
    ctx.touched_folders.add(folder)
    ctx.plex_dirty = True
    return f"Plex asked to scan {folder}"


async def plex_catch_up(*ctxs: Ctx) -> None:
    """After files changed: have Plex rescan what moved or changed, wait for
    it, and pull the result into the mirror. One rescan serves a whole batch
    of albums: the few folders they touched, their artists' folders, or, past
    a handful of artists, the library (one scan beats a minute per artist)."""
    dirty = [ctx for ctx in ctxs if ctx.plex_dirty]
    if not dirty or not plex.configured:
        return
    # Where files arrived is scanned before where they left: Plex empties its
    # trash after a scan, and a file it finds gone before it finds it moved
    # comes back as a new item without its history.
    new = {f for ctx in dirty for f in ctx.new_folders}
    folders = sorted({f for ctx in dirty for f in ctx.touched_folders},
                     key=lambda f: (f not in new, f))
    artists = list(dict.fromkeys(Path(f).parts[0] for f in folders if Path(f).parts))
    since = time.time()
    if len(folders) <= 6 or len(artists) <= 3:
        for folder in (folders if len(folders) <= 6 else artists):
            await plex.refresh_path(folder)
        await plex.wait_for_scan(timeout=180 * max(1, len(artists)))
    else:
        await plex.refresh_all()
        await plex.wait_for_scan(timeout=1800)
    moved = [path for ctx in dirty for path in ctx.moved]
    if moved:
        # A track Plex followed to its new place keeps its updatedAt, so a
        # "what changed" sync misses it: read those items again by key.
        followed = await plexsync.keys_for_paths(moved)
        await plexsync.forget_paths(moved)
        await plexsync.refresh_items(followed)
    await plexsync.sync_recent(since - 30)
    for ctx in dirty:
        ctx.plex_dirty = False
        ctx.touched_folders.clear()
        ctx.new_folders.clear()


# ------------------------------------------------------------------ plex stage
async def _set_plex_tags(batch: str, type_num: int, keys: list[str], tag: str,
                         values: list[str], ctx: Ctx, summary: str) -> None:
    """Set a tag on items, journalling each item's old values and lock."""
    old: dict[str, dict] = {}
    for key in keys:
        row = await db.fetchone("SELECT genres, moods, styles, locked FROM plex_items "
                                "WHERE rating_key = ?", (key,))
        current = jlist(row[{"genre": "genres", "mood": "moods", "style": "styles"}[tag]]) if row else []
        old[key] = {"values": current, "locked": tag in jlist(row["locked"]) if row else False}
    # one request per distinct current set (Plex applies an edit to every id)
    groups: dict[tuple, list[str]] = {}
    for key, info in old.items():
        groups.setdefault(tuple(info["values"]), []).append(key)
    for current, members in groups.items():
        for i in range(0, len(members), 50):
            await plex.set_tags(type_num, members[i:i + 50], tag, values, list(current))
    for key, info in old.items():
        await journal(batch, "plex", key, tag, info, {"values": values, "type": type_num},
                      summary, artist_dir=ctx.artist_dir, album_key=ctx.key)


async def do_genres(issue: dict, ctx: Ctx) -> str:
    d = issue["detail"]
    want = (ctx.desired or {}).get("genres") or d.get("to") or []
    if not want:
        raise ApplyError("no genres to set")
    batch = ctx.new_batch()
    await _set_plex_tags(batch, ALBUM, [d["rating_key"]], "genre", want, ctx,
                         f"Set genres: {', '.join(want)}")
    tracks = d.get("tracks") or []
    if tracks:
        await _set_plex_tags(batch, TRACK, tracks, "genre", want, ctx,
                             f"Set genres: {', '.join(want)}")
    await plexsync.refresh_items([d["rating_key"]])
    row = await db.fetchone("SELECT genres FROM plex_items WHERE rating_key = ?",
                            (d["rating_key"],))
    got = {g.casefold() for g in jlist(row["genres"])} if row else set()
    if got != {g.casefold() for g in want}:
        raise ApplyError(f"Plex shows {sorted(got)} after the edit, expected {want}")
    return f"genres set on the album and {len(tracks)} tracks"


async def do_moods(issue: dict, ctx: Ctx, type_num: int = ALBUM) -> str:
    d = issue["detail"]
    want = d.get("to") or []
    batch = ctx.new_batch()
    await _set_plex_tags(batch, type_num, [d["rating_key"]], "mood", want, ctx,
                         f"Set moods: {', '.join(want)}")
    await plexsync.refresh_items([d["rating_key"]])
    return "moods set"


async def do_artist_genres(issue: dict, ctx: Ctx) -> str:
    d = issue["detail"]
    batch = ctx.new_batch()
    await _set_plex_tags(batch, ARTIST, [d["rating_key"]], "genre", d["to"], ctx,
                         f"Set artist genres: {', '.join(d['to'])}")
    await plexsync.refresh_items([d["rating_key"]])
    return "artist genres set"


async def _match(rating_key: str, mbid: str, name: str, ctx: Ctx, *, parent: str | None = None,
                 year: str | None = None, kind: str) -> str:
    before = await plex.metadata(rating_key)
    if before is None:
        raise ApplyError("the item is gone from Plex")
    old = {"guid": before.get("guid"), "title": before.get("title"), "year": before.get("year")}
    wanted = f"mbid://{mbid}"
    guid = None
    try:
        for candidate in await plex.matches(rating_key, title=name, parent_title=parent, year=year):
            if candidate.get("guid") == wanted:
                guid = wanted
                name = candidate.get("name") or name
                year = candidate.get("year") or year
                break
    except PlexError as exc:
        log.info("plex match search failed for %s: %s", rating_key, exc)
    try:
        await plex.match(rating_key, guid or wanted, name, year)
    except PlexError as exc:
        raise ApplyError(f"Plex refused the match: {exc}") from exc
    batch = ctx.new_batch()
    await journal(batch, "plex", rating_key, "match", old, {"guid": guid or wanted},
                  f"Matched {name} in Plex", artist_dir=ctx.artist_dir, album_key=ctx.key)
    after = None
    for _ in range(10):
        await asyncio.sleep(3)
        after = await plex.metadata(rating_key)
        if after and (is_matched(after.get("guid")) or (after.get("guid") or "").startswith("mbid://")):
            break
    await plexsync.refresh_items([rating_key])
    if not after or (after.get("guid") or "").startswith("local://"):
        raise ApplyError("Plex did not take the match")
    return f"matched ({after.get('guid')})"


async def do_match(issue: dict, ctx: Ctx) -> str:
    d = issue["detail"]
    desired = ctx.desired or {}
    return await _match(d["rating_key"], d["release_mbid"], desired.get("album") or d.get("title"),
                        ctx, parent=desired.get("albumartist"),
                        year=desired.get("original_year"), kind=issue["kind"])


async def do_artist_match(issue: dict, ctx: Ctx) -> str:
    d = issue["detail"]
    return await _match(d["rating_key"], d["mbid"], d["name"], ctx, kind=issue["kind"])


async def _fetch_image(urls: list[str]) -> bytes | None:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in urls:
            try:
                r = await client.get(url)
                if r.status_code == 200 and r.content and len(r.content) > 1000:
                    return r.content
            except httpx.HTTPError:
                continue
    return None


async def do_poster(issue: dict, ctx: Ctx) -> str:
    d = issue["detail"]
    urls = [f"https://coverartarchive.org/release/{d['release_mbid']}/front-500"]
    if d.get("releasegroup_mbid"):
        urls.append(f"https://coverartarchive.org/release-group/{d['releasegroup_mbid']}/front-500")
    image = await _fetch_image(urls)
    if image is None and ctx.rows:
        from .art import local_cover
        image = await local_cover(folder_of(ctx.rows[0]), [r["path"] for r in ctx.rows])
    if image is None:
        raise ApplyError("no cover found on the Cover Art Archive or in the folder")
    await plex.upload_poster(d["rating_key"], data=image)
    batch = ctx.new_batch()
    await journal(batch, "plex", d["rating_key"], "poster", None, {"bytes": len(image)},
                  "Added cover art", artist_dir=ctx.artist_dir, album_key=ctx.key)
    await plexsync.refresh_items([d["rating_key"]])
    return "cover added"


async def do_artist_poster(issue: dict, ctx: Ctx) -> str:
    d = issue["detail"]
    image = await _fetch_image([d["url"]])
    if image is None:
        raise ApplyError("could not fetch the artist image")
    await plex.upload_poster(d["rating_key"], data=image)
    batch = ctx.new_batch()
    await journal(batch, "plex", d["rating_key"], "poster", None, {"url": d["url"]},
                  "Added artist image", artist_dir=ctx.artist_dir, album_key=None)
    await plexsync.refresh_items([d["rating_key"]])
    return "artist image added"


async def do_ghost(issue: dict, ctx: Ctx) -> str:
    folder = issue["detail"].get("folder") or ""
    await plex.refresh_path(folder or ".")
    await plex.wait_for_scan(timeout=120)
    await plex.empty_trash()
    await plexsync.forget_paths(issue["detail"].get("paths") or [])
    return "Plex cleaned up"


QUEUE_STAGE = {"missing_tracks": do_download_missing}
FILE_STAGE = {"tags": do_tags, "track_order": do_track_order_files, "move": do_move,
              "not_in_plex": do_scan}
PLEX_STAGE = {
    "track_order": do_track_order_plex,
    "unmatched": do_match, "wrong_match": do_match, "genres": do_genres,
    "moods": do_moods, "poster": do_poster, "artist_unmatched": do_artist_match,
    "artist_genres": do_artist_genres,
    "artist_moods": lambda issue, ctx: do_moods(issue, ctx, ARTIST),
    "artist_poster": do_artist_poster, "plex_ghost": do_ghost,
}


# ------------------------------------------------------------------ revert
async def current_path(path: str) -> str:
    """Where a file is now: along any moves journalled after `path`, and back
    along moves into `path` that were undone (tags written after a move,
    undone after the move was)."""
    seen = set()
    while path not in seen:
        seen.add(path)
        row = await db.fetchone(
            "SELECT new_value FROM md_changes WHERE target = 'move' AND ref = ? "
            "AND reverted_at IS NULL ORDER BY id DESC LIMIT 1", (path,))
        if row is not None:
            path = json.loads(row["new_value"])
            continue
        row = await db.fetchone(
            "SELECT ref FROM md_changes WHERE target = 'move' AND new_value = ? "
            "AND reverted_at IS NOT NULL ORDER BY id DESC LIMIT 1",
            (json.dumps(path, default=str),))
        if row is None:
            return path
        path = row["ref"]
    return path


async def revert(batch_id: str) -> dict:
    rows = [dict(r) for r in await db.fetchall(
        "SELECT * FROM md_changes WHERE batch_id = ? AND reverted_at IS NULL ORDER BY id DESC",
        (batch_id,))]
    if not rows:
        return {"reverted": 0, "failures": [{"ref": batch_id, "error": "nothing to undo"}]}
    root = settings.library_dir
    reverted, failures = 0, []
    touched: set[str] = set()
    moved_back: list[tuple[str, str]] = []
    reindex_paths: list[str] = []
    for change in rows:
        try:
            if change["target"] == "file":
                _require_online()
                path = await current_path(change["ref"])
                before = json.loads(change["old_value"])
                await _run(tagio.restore, root / path, before)
                reindex_paths.append(path)
                touched.add(str(Path(path).parent))
            elif change["target"] == "move":
                _require_online()
                new = json.loads(change["new_value"])
                old = change["ref"]
                await _run(_move_file, root / new, root / old)
                if change["field"] == "file":
                    moved_back.append((new, old))
                touched |= {str(Path(new).parent), str(Path(old).parent)}
            elif change["target"] == "plex":
                old = json.loads(change["old_value"]) if change["old_value"] else None
                new = json.loads(change["new_value"]) if change["new_value"] else {}
                if change["field"] in ("genre", "mood", "style"):
                    type_num = new.get("type") or ALBUM
                    await plex.set_tags(type_num, change["ref"], change["field"],
                                        old["values"], new.get("values"),
                                        lock=bool(old.get("locked")))
                elif change["field"] == "match":
                    if not old or not is_matched(old.get("guid")) and \
                            not (old.get("guid") or "").startswith("mbid://"):
                        # What Plex's own "Unmatch" does. (A refresh through
                        # the music agent afterwards would just match it again.)
                        await plex.unmatch(change["ref"])
                    else:
                        await plex.match(change["ref"], old["guid"], old.get("title") or "",
                                         old.get("year"))
                elif change["field"] == "index":
                    pairs = []
                    if old and old.get("index") is not None:
                        pairs += plex.field_pairs("index", old["index"], lock=False)
                    if old and old.get("disc") is not None:
                        pairs += plex.field_pairs("parentIndex", old["disc"], lock=False)
                    if pairs:
                        await plex.edit(TRACK, change["ref"], pairs)
                elif change["field"] == "poster":
                    pass   # a filled-in poster has nothing to go back to
                await plexsync.refresh_items([change["ref"]])
            await db.execute("UPDATE md_changes SET reverted_at = ? WHERE id = ?",
                             (utcnow(), change["id"]))
            reverted += 1
        except Exception as exc:
            failures.append({"ref": change["ref"], "error": str(exc)[:200]})
    if moved_back:
        await rekey_paths(moved_back)
        for new_dir, old_dir in {(str(Path(n).parent), str(Path(o).parent))
                                 for n, o in moved_back}:
            await rekey_folder(new_dir, old_dir)

        # tidy now-empty folders the files came back out of
        def tidy():
            for new, _old in moved_back:
                d = (root / new).parent
                try:
                    if d.is_dir() and not any(d.iterdir()):
                        d.rmdir()
                        if d.parent != root and not any(d.parent.iterdir()):
                            d.parent.rmdir()
                except OSError:
                    pass
        await _run(tidy)
    if reindex_paths:
        await reindex(reindex_paths)
    if touched and plex.configured:
        for folder in sorted(touched)[:10]:
            await plex.refresh_path(folder)
    return {"reverted": reverted, "failures": failures, "folders": sorted(touched)}
