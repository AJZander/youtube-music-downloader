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
from .issues import SHARED_FIELDS, junk_fix_fields, same_file
from .plexsync import jlist
from .desired import plain as plain_title
from .units import folder_of
from ..paths import PathBuildError, build_track_relpath

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
        await _rekey_exclusions("track", old, new)


async def rekey_folder(old: str, new: str) -> None:
    await _rekey_exclusions("album", old, new)


async def _rekey_exclusions(scope: str, old: str, new: str) -> None:
    """"Not duplicates" decisions follow a renamed file or folder. One the
    new name already has (merged copies) is kept once."""
    for column in ("key_a", "key_b"):
        await db.execute(f"UPDATE OR IGNORE library_exclusions SET {column} = ? "
                         f"WHERE scope = ? AND {column} = ?", (new, scope, old))
        await db.execute(f"DELETE FROM library_exclusions WHERE scope = ? AND {column} = ?",
                         (scope, old))


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
    filed, videos = await _file_in_downloads(issue, ctx)
    try:
        result = await missing.queue_download(issue["ref"], skip_videos=videos)
    except missing.Busy as exc:
        # asked for, but YouTube Music can't be asked now: a later pass asks
        await missing.want_download(issue["ref"])
        raise KeepOpen(f"waiting for YouTube Music ({exc}); tried again on a later pass")
    except RuntimeError as exc:
        if filed:
            raise KeepOpen(f"filed {filed} downloaded tracks into the album; {exc}")
        raise ApplyError(str(exc)) from exc
    note = f"queued {result.get('queued', 0)} tracks for download"
    if filed:
        note = f"filed {filed} downloaded tracks into the album; {note}"
    if result.get("not_found"):
        note += f"; not on YouTube Music: {', '.join(result['not_found'][:5])}"
    raise KeepOpen(note)


async def _file_in_downloads(issue: dict, ctx: Ctx) -> tuple[int, set[str]]:
    """Tracks an earlier "Download missing" of this release fetched that
    landed in a folder of their own (named as YouTube Music names the album)
    instead of the album's: moved into the album's folder, where its next
    check tags and numbers them. Returns (files moved, their video ids)."""
    job_id = ((issue.get("detail") or {}).get("download") or {}).get("job_id")
    if not job_id or not ctx.rows:
        return 0, set()
    _require_online()
    root = settings.library_dir
    target = folder_of(ctx.rows[0])
    ours = {r["path"] for r in ctx.rows}
    found = await db.fetchall(
        "SELECT t.library_path, t.provider_track_id FROM tracks t JOIN library_index l "
        "ON l.path = t.library_path WHERE t.job_id = ? AND t.status = 'completed'", (job_id,))
    moves = []
    for t in found:
        src = t["library_path"]
        if src in ours or folder_of({"path": src}) == target:
            continue
        dst = f"{target}/{Path(src).name}"
        if not await _run((root / src).exists) or await _run((root / dst).exists):
            continue
        moves.append((src, dst, t["provider_track_id"]))
    if not moves:
        return 0, set()
    batch = ctx.new_batch()
    done = []
    for src, dst, _ in moves:
        try:
            await _run(_move_file, root / src, root / dst)
        except (OSError, ApplyError) as exc:
            log.info("could not file %s into %s: %s", src, target, exc)
            continue
        done.append((src, dst))
        await journal(batch, "move", src, "file", src, dst, f"Filed {Path(src).name} into {target}",
                      artist_dir=ctx.artist_dir, album_key=ctx.key)
        side = (root / src).with_suffix(".lrc")
        if await _run(side.exists) and not await _run((root / dst).with_suffix(".lrc").exists):
            await _run(_move_file, side, (root / dst).with_suffix(".lrc"))
            await journal(batch, "move", str(Path(src).with_suffix(".lrc")), "sidecar", None,
                          str(Path(dst).with_suffix(".lrc")), "Moved sidecar",
                          artist_dir=ctx.artist_dir, album_key=ctx.key)
    if not done:
        return 0, set()
    await rekey_paths(done)
    sources = {folder_of({"path": s}) for s, _ in done}
    await _tidy_folders(sources, target, batch, ctx)
    await db.execute("UPDATE md_changes SET summary = ? WHERE batch_id = ?",
                     (f"Filed {len(done)} downloaded tracks into {target}", batch))
    ctx.touched_folders |= sources | {target}
    ctx.new_folders.add(target)
    ctx.plex_dirty = True
    moved = {s for s, _ in done}
    return len(done), {v for s, _, v in moves if s in moved and v}


def _move_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if not same_file(src, dst):
            raise ApplyError(f"{dst.name} already exists at the destination")
        # case-only rename on a case-insensitive share: go via a temp name
        tmp = dst.with_name(f".mdl-rename-{uuid.uuid4().hex[:8]}{dst.suffix}")
        os.replace(src, tmp)
        os.replace(tmp, dst)
        return
    os.replace(src, dst)


async def do_move(issue: dict, ctx: Ctx) -> str:
    _require_online()
    from .issues import move_conflict, planned_moves
    if ctx.desired is None:
        raise ApplyError("the album is not identified")
    moves = planned_moves(ctx.rows, ctx.desired)
    if not moves:
        return "nothing to move"
    conflict = await move_conflict(ctx.key, ctx.rows, ctx.desired, moves)
    if conflict:
        raise ApplyError(conflict)
    return await _relocate(ctx, moves, ctx.new_batch())


async def _relocate(ctx: Ctx, moves: list[dict], batch: str, summary: str | None = None) -> str:
    """Move these files of the album to where ctx.desired puts them (the
    destination is known to be free), with their sidecars and leftovers,
    and follow the album to its new key."""
    root = settings.library_dir
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

    await _tidy_folders(source_dirs, target_dir, batch, ctx)
    await rekey_paths(done)
    for folder in source_dirs:
        await rekey_folder(folder, target_dir)
    new_artist = Path(target_dir).parts[0]
    new_key = target_dir
    await rekey_unit(ctx.key, new_key, target_dir, new_artist)
    await db.execute("UPDATE md_changes SET summary = ?, album_key = ?, artist_dir = ? "
                     "WHERE batch_id = ?",
                     (summary or f"Moved {len(done)} files to {target_dir}", new_key, new_artist,
                      batch))
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


def _tidy(folder: str, target_dir: str) -> list[tuple[str, str]]:
    """Folder leftovers (cover art, cue sheets, rip logs) follow the album
    when its old folder has no audio left; an emptied folder is removed only
    if it really is empty."""
    from ..library import AUDIO_SUFFIXES
    root = settings.library_dir
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


async def _tidy_folders(folders, target_dir: str, batch: str, ctx: Ctx) -> None:
    for folder in sorted(folders, key=lambda f: f.count("/"), reverse=True):
        try:
            extra = await _run(_tidy, folder, target_dir)
        except OSError as exc:
            log.info("could not tidy %s: %s", folder, exc)
            extra = []
        for old, new in extra:
            await journal(batch, "move", old, "leftover", None, new, "Moved folder file",
                          artist_dir=ctx.artist_dir, album_key=ctx.key)


def _audio_quality(row: dict, reference: float | None) -> int:
    """How good a copy is as audio: lossless over lossy, bitrate, and a cut-off
    copy last. Tags don't count: the copy kept gets the album's tags anyway."""
    from .. import library
    score = library.format_quality(row)
    duration = row.get("duration_sec")
    if reference and duration and duration < reference - 2 * library.DURATION_TOLERANCE:
        score -= 900
    return score


async def merge_plan(ctx: Ctx) -> dict:
    """What merging this album into the copy at its destination does, without
    doing it: {"moves", "trash", "kept_there", "left"} — files moved in, worse
    copies trashed, the destination's copies kept, and files left where they
    are (nothing to pair them with safely). None of it touches a file."""
    from .. import library
    from .issues import conflict_of, other_album, planned_moves
    from .units import in_box_set
    if ctx.desired is None:
        raise ApplyError("the album is not identified")
    if await in_box_set(ctx.key, ctx.rows):
        # a plan made before box sets kept their albums: never merged out
        raise ApplyError("this album is part of a box set, which keeps its own copy")
    moves = planned_moves(ctx.rows, ctx.desired)
    plan = {"moves": moves, "trash": [], "kept_there": [], "left": [], "conflict": None}
    if not moves:
        return plan
    found = await conflict_of(ctx.key, ctx.rows, ctx.desired, moves)
    if found is None:
        return plan
    plan["conflict"] = found["kind"]
    if found["kind"] == "other" and await other_album(ctx.desired, found):
        raise ApplyError("the destination holds another album, not a copy of this one")
    folder = ctx.desired["folder"]
    ours = {r["path"] for r in ctx.rows}
    theirs = [dict(r) for r in await db.fetchall(
        f"SELECT {library._ROW_FIELDS}, mb_trackid FROM library_index "
        "WHERE dirpath = ? AND unreadable = 0", (folder,))]
    theirs = [t for t in theirs if t["path"] not in ours]

    def target_of(row):
        return (ctx.desired["tracks"].get(row["path"]) or {}).get("target") or row["path"]

    def matched(row) -> bool:
        return bool((ctx.desired["tracks"].get(row["path"]) or {}).get("matched"))

    # Two of ours that would take one name: the one identified as the
    # release's track goes on (else the first). The other is no proven copy
    # of it (an unmatched file keeps its own name: "Disc 3/05. Cemetery
    # Gates.flac", a demo, beside disc 1's), so it stays where it is.
    keep, losers = [], []
    by_name: dict[str, list[dict]] = {}
    for row in ctx.rows:
        by_name.setdefault(target_of(row).casefold(), []).append(row)
    for members in by_name.values():
        # one already at the name stays there; else the identified one goes on
        members.sort(key=lambda r: (target_of(r) != r["path"], not matched(r)))
        keep.append(members[0])
        plan["left"] += [m["path"] for m in members[1:]]

    # Each of ours identified as a track of the release, against the
    # destination's copy of that track. Only such a pair ever loses a file.
    index = library.AlbumInstance(folder, ctx.artist_dir or "", theirs).key_index()
    by_track = {t["mb_releasetrackid"]: t for t in theirs if t.get("mb_releasetrackid")}
    names_there = {t["path"].rsplit("/", 1)[-1].casefold() for t in theirs}
    paired, moving = set(), []
    for row in keep:
        want = ctx.desired["tracks"].get(row["path"]) or {}
        other = None
        if matched(row):
            other = by_track.get(want.get("releasetrack_mbid") or "")
            if other is not None and not library.same_recording(row, other):
                plan["left"].append(row["path"])    # one track, two lengths: not proven a copy
                continue
            other = other or library.match_in(row, index)
        if other is None and target_of(row).rsplit("/", 1)[-1].casefold() in names_there:
            plan["left"].append(row["path"])    # its name is another file's there
            continue
        if other is not None and other["path"] in paired:
            plan["left"].append(row["path"])    # a second file for a track already settled
            continue
        if other is None:
            moving.append(row)
            continue
        paired.add(other["path"])
        ref = max(row.get("duration_sec") or 0, other.get("duration_sec") or 0) or None
        if _audio_quality(row, ref) > _audio_quality(other, ref):
            losers.append(other["path"])
            moving.append(row)
        else:
            losers.append(row["path"])
            plan["kept_there"].append(other["path"])
    plan["trash"] = losers
    plan["moves"] = [{"from": r["path"], "to": target_of(r)} for r in moving
                     if target_of(r) != r["path"]]
    return plan


async def do_merge(issue: dict, ctx: Ctx) -> str:
    """A move blocked by another copy of this album at the destination,
    made one album: the destination gets the tracks it lacks, and where both
    copies have a track the better file stays and the other goes to Trash
    (restorable, and undone with the rest of the merge). A file the merge
    can't pair with a track is never trashed. Another album at the
    destination (a single of the same name, another edition) is not merged."""
    _require_online()
    from .. import library
    plan = await merge_plan(ctx)
    if plan["conflict"] is None:
        if not plan["moves"]:
            return "nothing to move"
        return await _relocate(ctx, plan["moves"], ctx.new_batch())
    folder = ctx.desired["folder"]
    source = folder_of(ctx.rows[0])
    batch = ctx.new_batch()
    trashed = 0
    losers = plan["trash"]
    if losers:
        result = await library.trash_paths(losers, reason=f"merged into {folder}")
        trashed = result["trashed"]
        if result.get("batch_id") and result["trashed"]:
            await journal(batch, "trash", result["batch_id"], "files", losers, None,
                          f"Trashed {len(losers)} worse copies merging into {folder}",
                          artist_dir=ctx.artist_dir, album_key=ctx.key)
        if result.get("failures"):
            raise ApplyError(f"could not trash {result['failures'][0]['path']}: "
                             f"{result['failures'][0]['error']}")
    gone = set(losers)
    ctx.rows = [r for r in ctx.rows if r["path"] not in gone]
    note = (f"merged into {folder}: {len(plan['moves'])} moved in, {trashed} worse copies trashed"
            + (f", {len(plan['kept_there'])} of the destination's kept" if plan["kept_there"] else "")
            + (f", {len(plan['left'])} left where they are" if plan["left"] else ""))
    ctx.touched_folders |= {folder, source}
    ctx.plex_dirty = True
    await db.execute("UPDATE md_albums SET next_check = 0 WHERE key = ?", (folder,))
    if plan["moves"]:
        await _relocate(ctx, plan["moves"], batch, summary=note[:1].upper() + note[1:])
    return note


async def copies_plan(issue: dict, ctx: Ctx) -> dict:
    """What merging an album's other copies into this one does, without
    doing it: {"moves", "trash", "left"}. Only a file identified as a
    recording on this album's release moves in or is trashed; for each
    recording the best file stays (ties keep this album's own)."""
    from .. import library
    from . import musicbrainz as mb
    from .issues import matched_recordings, plain_recordings
    from .units import in_box_set, load_unit
    if ctx.desired is None:
        raise ApplyError("the album is not identified")
    if await in_box_set(ctx.key, ctx.rows):
        raise ApplyError("this album is part of a box set, which keeps its own copy")
    try:
        rel = await mb.release(ctx.desired["release_mbid"])
    except mb.MusicBrainzError as exc:
        raise ApplyError(f"MusicBrainz: {exc}") from exc
    if not rel:
        raise ApplyError("the release is gone from MusicBrainz")
    multi = len(rel.get("media") or []) > 1
    plain = plain_recordings(rel)
    on_release = {t["recording_id"]: t for t in mb.release_tracks(rel)
                  if t.get("recording_id") in plain}
    folder = ctx.desired.get("folder") or folder_of(ctx.rows[0])
    by_path = {r["path"]: r for r in ctx.rows}
    # this album's own file for each recording (the first, if it holds two)
    held: dict[str, dict] = {}
    for rec, paths in matched_recordings(ctx.desired).items():
        rows = [by_path[p] for p in paths if p in by_path and not by_path[p].get("unreadable")]
        if rows:
            held[rec] = rows[0]
    plan = {"moves": [], "trash": [], "left": [], "pin": ctx.desired["release_mbid"]}
    taken = {r["path"].casefold() for r in ctx.rows}
    for key in issue["detail"].get("copies") or []:
        rows = await load_unit(key)
        record = await db.fetchone("SELECT desired, status FROM md_albums WHERE key = ?", (key,))
        if not rows or not record or record["status"] != "resolved" or not record["desired"] \
                or await in_box_set(key, rows):
            continue
        want = json.loads(record["desired"])
        for row in rows:
            t = want["tracks"].get(row["path"]) or {}
            rec = t.get("recording_mbid") if t.get("matched") else None
            if not rec or rec not in on_release or row.get("unreadable"):
                plan["left"].append(row["path"])    # not this album's recording: stays
                continue
            there = held.get(rec)
            if there is not None and not library.same_recording(row, there):
                plan["left"].append(row["path"])    # lengths disagree: not proven one
                continue
            track = on_release[rec]
            try:
                name = build_track_relpath(
                    ctx.desired.get("albumartist") or "x", ctx.desired.get("album") or "x",
                    plain_title(track["title"]), Path(row["path"]).suffix, track["number"],
                    track["disc"] if multi else None).name
            except PathBuildError:
                plan["left"].append(row["path"])
                continue
            target = f"{folder}/{name}"
            if there is not None:
                ref = max(row.get("duration_sec") or 0, there.get("duration_sec") or 0) or None
                if _audio_quality(row, ref) <= _audio_quality(there, ref):
                    plan["trash"].append(row["path"])
                    continue
                plan["trash"].append(there["path"])
                taken.discard(there["path"].casefold())
            if target.casefold() in taken:
                plan["left"].append(row["path"])    # the name is another file's
                continue
            plan["moves"].append({"from": row["path"], "to": target})
            taken.add(target.casefold())
            held[rec] = row
    # a file can lose only once (two copies of a track, both beaten)
    plan["trash"] = list(dict.fromkeys(p for p in plan["trash"]
                                       if p not in {m["from"] for m in plan["moves"]}))
    return plan


async def do_album_copies(issue: dict, ctx: Ctx) -> str:
    """One album held in several folders, made one: see copies_plan. The
    losing files go to Trash, and the whole merge is one undoable change."""
    _require_online()
    from .. import library
    plan = await copies_plan(issue, ctx)
    if not plan["moves"] and not plan["trash"]:
        return "nothing to merge"
    folder = ctx.desired.get("folder") or folder_of(ctx.rows[0])
    batch = ctx.new_batch()
    trashed = 0
    if plan["trash"]:
        result = await library.trash_paths(plan["trash"], reason=f"merged into {folder}")
        trashed = result["trashed"]
        if result.get("batch_id") and result["trashed"]:
            await journal(batch, "trash", result["batch_id"], "files", plan["trash"], None,
                          f"Trashed {len(plan['trash'])} worse copies merging into {folder}",
                          artist_dir=ctx.artist_dir, album_key=ctx.key)
        if result.get("failures"):
            raise ApplyError(f"could not trash {result['failures'][0]['path']}: "
                             f"{result['failures'][0]['error']}")
    root = settings.library_dir
    done = []
    for m in plan["moves"]:
        try:
            await _run(_move_file, root / m["from"], root / m["to"])
        except (OSError, ApplyError) as exc:
            log.info("could not merge %s into %s: %s", m["from"], folder, exc)
            continue
        done.append((m["from"], m["to"]))
        await journal(batch, "move", m["from"], "file", m["from"], m["to"],
                      f"Moved {Path(m['from']).name}", artist_dir=ctx.artist_dir, album_key=ctx.key)
        for suffix in SIDECARS:
            side, side_dst = (root / m["from"]).with_suffix(suffix), (root / m["to"]).with_suffix(suffix)
            if await _run(side.exists) and not await _run(side_dst.exists):
                await _run(_move_file, side, side_dst)
                await journal(batch, "move", str(Path(m["from"]).with_suffix(suffix)), "sidecar",
                              None, str(Path(m["to"]).with_suffix(suffix)), "Moved sidecar",
                              artist_dir=ctx.artist_dir, album_key=ctx.key)
    await rekey_paths(done)
    sources = {folder_of({"path": s}) for s in [m["from"] for m in plan["moves"]] + plan["trash"]}
    await _tidy_folders(sources - {folder}, folder, batch, ctx)
    note = (f"merged {len(issue['detail'].get('copies') or [])} copies into {folder}: "
            f"{len(done)} moved in, {trashed} worse copies trashed"
            + (f", {len(plan['left'])} other files left where they are" if plan["left"] else ""))
    await db.execute("UPDATE md_changes SET summary = ? WHERE batch_id = ?",
                     (note[:1].upper() + note[1:], batch))
    # the album is this release now, whatever the files moved in were tagged
    await db.execute("UPDATE md_albums SET manual = 1, release_mbid = ?, next_check = 0 "
                     "WHERE key = ?", (plan["pin"], ctx.key))
    keys = issue["detail"].get("copies") or []
    if keys:
        await db.execute(f"UPDATE md_albums SET next_check = 0 WHERE key IN "
                         f"({','.join('?' * len(keys))})", tuple(keys))
    ctx.moved.update(dict(done))
    ctx.touched_folders |= sources | {folder}
    ctx.new_folders.add(folder)
    ctx.plex_dirty = True
    return note


async def do_bad_file(issue: dict, ctx: Ctx) -> str:
    """Damaged files to Trash, once it is certain they are: a file that
    reads now (a NAS hiccup at scan time) is read into the index instead."""
    _require_online()
    from .. import library
    root = settings.library_dir
    paths = [r["path"] for r in ctx.rows if r.get("unreadable")]
    if not paths:
        return "no damaged files left"

    def still_bad(rel: str) -> bool:
        p = root / rel
        if not p.exists():
            return False
        if p.stat().st_size == 0:
            return True
        try:
            info = tagio.read(p)["info"]
        except Exception:
            return True
        return not info.get("length")

    bad = [p for p in paths if await _run(still_bad, p)]
    fine = [p for p in paths if p not in bad and (root / p).exists()]
    if fine:
        await reindex(fine)
    if not bad:
        return f"{len(fine)} files read fine now"
    result = await library.trash_paths(bad, reason="damaged: can't be read")
    if result.get("batch_id") and result["trashed"]:
        await journal(ctx.new_batch(), "trash", result["batch_id"], "files", bad, None,
                      f"Trashed {len(bad)} damaged file{'s' if len(bad) != 1 else ''}",
                      artist_dir=ctx.artist_dir, album_key=ctx.key)
    if result.get("failures"):
        raise ApplyError(f"could not trash {result['failures'][0]['path']}: "
                         f"{result['failures'][0]['error']}")
    gone = set(bad)
    ctx.rows = [r for r in ctx.rows if r["path"] not in gone]
    ctx.touched_folders.add(folder_of({"path": bad[0]}))
    ctx.plex_dirty = True
    return f"{len(bad)} damaged files trashed"


# A not-in-Plex issue open this long has had its scans; what is still
# missing is a file Plex holds but won't show, and it is re-added.
READD_AFTER = 3600
# Files hidden for a re-add, until they are back: a restart puts them back.
READD_PENDING = "plex_readd_pending"
# Held while files are hidden: a library scan would forget them meanwhile.
SCAN_HOLD = asyncio.Lock()


def _age(stamp: str | None) -> float:
    """Seconds since a utcnow() stamp."""
    import calendar
    if not stamp:
        return 0.0
    try:
        return time.time() - calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return 0.0


# One re-add a week per folder: a file Plex can't take at all would
# otherwise be hidden and shown again every half hour.
READD_EVERY = 7 * 86400


async def do_scan(issue: dict, ctx: Ctx) -> str:
    folder = (issue.get("detail") or {}).get("folder") or (folder_of(ctx.rows[0]) if ctx.rows else None)
    if folder is None:
        raise ApplyError("no folder to scan")
    if plex.configured and ctx.rows and _age(issue.get("created_at")) > READD_AFTER:
        readable = [r["path"] for r in ctx.rows if not r.get("unreadable")]
        info = await plexsync.album_for_paths(readable)
        missing = [p for p in readable if p not in ((info or {}).get("tracks") or {})]
        if missing:
            mark = f"plex_readd:{folder}"
            row = await db.fetchone("SELECT value FROM md_state WHERE key = ?", (mark,))
            if row and time.time() - float(row["value"] or 0) < READD_EVERY:
                raise ApplyError(f"Plex still doesn't list {len(missing)} of these files after "
                                 f"re-adding them: check that Plex can play them")
            await db.execute("INSERT INTO md_state (key, value) VALUES (?, ?) ON CONFLICT(key) "
                             "DO UPDATE SET value = excluded.value", (mark, str(time.time())))
            ctx.touched_folders.add(folder)
            ctx.plex_dirty = True
            n = await readd(missing)
            return f"Plex made to forget {n} files it wouldn't show, and add them again"
    ctx.touched_folders.add(folder)
    ctx.plex_dirty = True
    return f"Plex asked to scan {folder}"


async def readd(paths: list[str]) -> int:
    """Have Plex forget files it holds but won't show and add them again.

    Plex can keep a track whose album it deleted: the file is in its
    database, so a scan finds it unchanged and skips it, and no list shows
    it. Each file is hidden under a dot name (Plex and our scans skip those)
    for one scan of its folder, Plex empties its trash, and the file comes
    back as a new item filed by its tags. Its play history goes with the old
    item."""
    _require_online()
    root = settings.library_dir
    pairs = [(p, str(Path(p).with_name(f".mdl-readd-{Path(p).name}"))) for p in paths]
    folders = sorted({str(Path(p).parent) for p in paths})
    async with SCAN_HOLD:
        await db.execute("INSERT INTO md_state (key, value) VALUES (?, ?) ON CONFLICT(key) "
                         "DO UPDATE SET value = excluded.value", (READD_PENDING, json.dumps(pairs)))
        hidden = []
        try:
            for rel, tmp in pairs:
                try:
                    await _run(os.replace, root / rel, root / tmp)
                    hidden.append((rel, tmp))
                except OSError as exc:
                    log.info("could not hide %s for a Plex re-add: %s", rel, exc)
            if hidden:
                for folder in folders:
                    await plex.refresh_path(folder)
                await plex.wait_for_scan(timeout=300)
                await plex.empty_trash()
                await plex.wait_for_scan(timeout=120, start=10)
        finally:
            await restore_hidden(hidden)
    for folder in folders:
        await plex.refresh_path(folder)
    log.info("plex re-add of %d files in %s", len(hidden), ", ".join(folders[:3]))
    return len(hidden)


async def restore_hidden(pairs: list | None = None) -> int:
    """Put files hidden for a re-add back under their names (after a re-add,
    or at startup for one a restart cut short)."""
    if pairs is None:
        row = await db.fetchone("SELECT value FROM md_state WHERE key = ?", (READD_PENDING,))
        pairs = json.loads(row["value"]) if row and row["value"] else []
    root = settings.library_dir
    back = 0
    for rel, tmp in pairs:
        try:
            if await _run((root / tmp).exists) and not await _run((root / rel).exists):
                await _run(os.replace, root / tmp, root / rel)
                back += 1
        except OSError as exc:
            log.warning("could not put back %s (still at %s): %s", rel, tmp, exc)
            return back
    await db.execute("DELETE FROM md_state WHERE key = ?", (READD_PENDING,))
    return back


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
    # Plex reads a changed file in about half a second, so a big apply's
    # rescan can run for hours.
    files = sum(len(ctx.rows) for ctx in dirty)
    since = time.time()
    if len(folders) <= 6 or len(artists) <= 3:
        for folder in (folders if len(folders) <= 6 else artists):
            await plex.refresh_path(folder)
        await plex.wait_for_scan(timeout=180 * max(1, len(artists)) + files)
        moved = [path for ctx in dirty for path in ctx.moved]
        if moved:
            # A track Plex followed to its new place keeps its updatedAt, so a
            # "what changed" sync misses it: read those items again by key.
            followed = await plexsync.keys_for_paths(moved)
            await plexsync.forget_paths(moved)
            await plexsync.refresh_items(followed)
        await plexsync.sync_recent(since - 30)
    else:
        await plex.refresh_all()
        await plex.wait_for_scan(timeout=min(86400, 1800 + 2 * files))
        await plexsync.sync(full=True)      # cheaper than item by item past a few thousand
    for ctx in dirty:
        ctx.plex_dirty = False
        ctx.touched_folders.clear()
        ctx.new_folders.clear()


# ------------------------------------------------------------------ plex stage
async def _set_plex_tags(batch: str, type_num: int, keys: list[str], tag: str,
                         values: list[str], ctx: Ctx, summary: str) -> None:
    """Set a tag on items, journalling each item's old values and lock.

    The items are read from Plex before the edit (the values to remove are
    what Plex has, not what the mirror last saw) and after it: an edit
    leaves Plex's updatedAt alone, so no sync would ever read them again."""
    await plexsync.refresh_items(keys)
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
    await plexsync.refresh_items(keys)


async def _tags_now(keys: list[str], column: str = "genres") -> dict[str, set[str]]:
    """What the mirror (just refreshed from Plex) says these items carry."""
    out = {}
    for key in keys:
        row = await db.fetchone(f"SELECT {column} FROM plex_items WHERE rating_key = ?", (key,))
        if row is not None:
            out[key] = {v.casefold() for v in jlist(row[column])}
    return out


SHARED_NOTE = "left alone: Plex files other albums' tracks in this album"


async def _shared(ctx: Ctx, rating_key: str) -> bool:
    """Whether Plex files other albums' tracks in this album, so that it is
    not this one's to match or tag (a fix planned before that was known)."""
    if not ctx.rows:
        return False
    info = await plexsync.album_for_paths([ctx.moved.get(r["path"], r["path"]) for r in ctx.rows])
    if ((info or {}).get("album") or {}).get("rating_key") != rating_key:
        return True      # the tracks have left that album: it is not theirs now
    return plexsync.shares_album(info, ctx.rows, (ctx.desired or {}).get("releasegroup_mbid"))


async def do_genres(issue: dict, ctx: Ctx) -> str:
    d = issue["detail"]
    want = (ctx.desired or {}).get("genres") or d.get("to") or []
    if not want:
        raise ApplyError("no genres to set")
    batch = ctx.new_batch()
    tracks = d.get("tracks") or []
    wanted = {g.casefold() for g in want}
    if d.get("album") is False or await _shared(ctx, d["rating_key"]):
        # the tracks sit in an album Plex shares with other albums': theirs only
        await _set_plex_tags(batch, TRACK, tracks, "genre", want, ctx,
                             f"Set genres: {', '.join(want)}")
        _check_tags(await _tags_now(tracks), wanted, want)
        return f"genres set on {len(tracks)} tracks"
    await _set_plex_tags(batch, ALBUM, [d["rating_key"]], "genre", want, ctx,
                         f"Set genres: {', '.join(want)}")
    if tracks:
        await _set_plex_tags(batch, TRACK, tracks, "genre", want, ctx,
                             f"Set genres: {', '.join(want)}")
    _check_tags(await _tags_now([d["rating_key"], *tracks]), wanted, want)
    return f"genres set on the album and {len(tracks)} tracks"


def _check_tags(now: dict[str, set[str]], wanted: set[str], want: list[str]) -> None:
    """Fail the fix (rather than close it) when Plex didn't take the edit."""
    wrong = [k for k, got in now.items() if got != wanted]
    if wrong:
        raise ApplyError(f"Plex shows {sorted(now[wrong[0]])} on {len(wrong)} of {len(now)} "
                         f"items after the edit, expected {want}")


async def do_plex_shared(issue: dict, ctx: Ctx) -> str:
    """Put a Plex album that holds several albums' tracks back the way Plex
    had it before any of them matched or tagged it: the earliest journalled
    value of each field is the one from before us."""
    rk = issue["detail"]["rating_key"]
    marks = ",".join("?" * len(SHARED_FIELDS))
    changes = [dict(r) for r in await db.fetchall(
        "SELECT * FROM md_changes WHERE target = 'plex' AND ref = ? AND reverted_at IS NULL "
        f"AND field IN ({marks}) ORDER BY id", (rk, *SHARED_FIELDS))]
    if not changes:
        return "nothing of ours to put back"
    first: dict[str, dict] = {}
    for change in changes:
        first.setdefault(change["field"], change)
    row = await db.fetchone("SELECT genres, moods FROM plex_items WHERE rating_key = ?", (rk,))
    for field, change in first.items():
        old = json.loads(change["old_value"]) if change["old_value"] else None
        if field == "match":
            if not old or not is_matched(old.get("guid")) and \
                    not (old.get("guid") or "").startswith("mbid://"):
                await plex.unmatch(rk)
            else:
                await plex.match(rk, old["guid"], old.get("title") or "", old.get("year"))
        else:
            current = jlist(row[{"genre": "genres", "mood": "moods"}[field]]) if row else []
            await plex.set_tags(ALBUM, rk, field, (old or {}).get("values") or [], current,
                                lock=bool((old or {}).get("locked")))
    now = utcnow()
    for change in changes:
        await db.execute("UPDATE md_changes SET reverted_at = ? WHERE id = ?", (now, change["id"]))
    await plexsync.refresh_items([rk])
    return f"put back {', '.join(sorted(first))} on the shared album ({len(changes)} changes undone)"


async def do_moods(issue: dict, ctx: Ctx, type_num: int = ALBUM) -> str:
    d = issue["detail"]
    if type_num == ALBUM and await _shared(ctx, d["rating_key"]):
        return SHARED_NOTE
    want = d.get("to") or []
    batch = ctx.new_batch()
    await _set_plex_tags(batch, type_num, [d["rating_key"]], "mood", want, ctx,
                         f"Set moods: {', '.join(want)}")
    return "moods set"


async def do_artist_genres(issue: dict, ctx: Ctx) -> str:
    d = issue["detail"]
    batch = ctx.new_batch()
    await _set_plex_tags(batch, ARTIST, [d["rating_key"]], "genre", d["to"], ctx,
                         f"Set artist genres: {', '.join(d['to'])}")
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
    if await _shared(ctx, d["rating_key"]):
        return SHARED_NOTE
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
    if await _shared(ctx, d["rating_key"]):
        return SHARED_NOTE
    urls =[f"https://coverartarchive.org/release/{d['release_mbid']}/front-500"]
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


async def scan_artists(folders: set[str]) -> None:
    """Ask Plex to rescan these artist folders, without waiting. Plex's own
    scans pick up a moved album's new folder but never notice its old one is
    gone; a rescan of the folder above it does, and Plex then empties its
    trash of the old entries."""
    if not plex.configured:
        return
    root = settings.library_dir
    for folder in sorted(f for f in folders if f):
        if await _run((root / folder).is_dir):
            await plex.refresh_path(folder)


def _nearest_folder(folder: str) -> str:
    """The closest folder at or above `folder` that still exists ("" for the
    library root): a vanished folder can't be scanned."""
    root = settings.library_dir
    p = Path(folder or "")
    while p.parts and not (root / p).is_dir():
        p = p.parent
    return str(p) if p.parts else ""


async def clean_ghosts(found: list[dict]) -> str:
    """Plex entries whose files are gone, all at once: rescan the nearest
    folders that still hold them (a rescan of the folder above is what makes
    Plex notice), and the whole library for any folder gone up to the root;
    wait, then empty Plex's trash."""
    targets = sorted({await _run(_nearest_folder, (i.get("detail") or {}).get("folder") or "")
                      for i in found})
    for folder in (t for t in targets if t):
        await plex.refresh_path(folder)
    if "" in targets:
        await plex.refresh_all()
    await plex.wait_for_scan(timeout=min(6 * 3600, 120 * len(targets)))
    await plex.empty_trash()
    await plexsync.forget_paths([p for i in found for p in (i.get("detail") or {}).get("paths") or []])
    return f"Plex rescanned {len(targets)} folders and emptied its trash"


async def do_ghost(issue: dict, ctx: Ctx) -> str:
    return await clean_ghosts([issue])


QUEUE_STAGE = {"missing_tracks": do_download_missing}
FILE_STAGE = {"bad_file": do_bad_file, "tags": do_tags, "track_order": do_track_order_files,
              "move": do_move, "move_conflict": do_merge, "album_copies": do_album_copies,
              "not_in_plex": do_scan}
PLEX_STAGE = {
    "track_order": do_track_order_plex,
    "unmatched": do_match, "wrong_match": do_match, "plex_shared": do_plex_shared,
    "genres": do_genres,
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
            elif change["target"] == "trash":
                from .. import library
                result = await library.restore_batch(change["ref"])
                if result["failures"]:
                    raise ApplyError(f"{result['failures'][0]['path']}: "
                                     f"{result['failures'][0]['error']}")
                reindex_paths += json.loads(change["old_value"] or "[]")
                touched |= {str(Path(p).parent) for p in json.loads(change["old_value"] or "[]")}
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
