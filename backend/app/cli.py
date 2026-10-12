"""Maintenance / Phase-1 test CLI.

  python -m app.cli download <url>     end-to-end single URL (no API needed)
  python -m app.cli index --rebuild    rebuild library_index from the library
  python -m app.cli import-legacy <archive.txt>   load v1 archive (advisory)
  python -m app.cli canary             extractor + credential health probe
  python -m app.cli library scan [--full]        refresh the management index
  python -m app.cli library dupes [kind]         duplicate report (albums|tracks|artists)
  python -m app.cli library purge --days N       empty trash older than N days
"""
import argparse
import asyncio
import logging
import sys

from .db import db
from .models import JobStatus


async def _cmd_download(url: str) -> int:
    from .engine import ytdlp_engine
    from .worker import worker

    ytdlp_engine.assert_version_floor()
    job_id = await worker.enqueue(url)
    print(f"job {job_id} queued; processing...")
    # Drive the worker inline until the job reaches a terminal state
    while True:
        did_work = await worker._step()
        job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if job["status"] in (JobStatus.COMPLETED, JobStatus.PARTIAL,
                             JobStatus.FAILED, JobStatus.CANCELLED):
            break
        if not did_work:
            await asyncio.sleep(2)
    print(f"job {job_id}: {job['status']} "
          f"({job['completed_tracks']}/{job['total_tracks']} completed, "
          f"{job['skipped_tracks']} skipped, {job['failed_tracks']} failed)")
    tracks = await db.fetchall("SELECT * FROM tracks WHERE job_id = ?", (job_id,))
    for t in tracks:
        line = f"  [{t['status']}] {t['artist']} - {t['title']}"
        if t["library_path"]:
            line += f" -> {t['library_path']} ({t['format']}, {t['bitrate_kbps']}k)"
        if t["error_message"]:
            line += f" | {t['error_code']}: {t['error_message'][:120]}"
        print(line)
    return 0 if job["status"] == JobStatus.COMPLETED else 1


async def _cmd_index(rebuild: bool) -> int:
    from . import library_index
    if not rebuild:
        print("only --rebuild is supported")
        return 2

    def progress(done, total):
        print(f"\r  scanned {done}/{total}", end="", flush=True)

    result = await library_index.rebuild(progress_cb=progress)
    print(f"\nindexed {result['indexed']} tracks "
          f"({result['unreadable']} unreadable of {result['files']} files)")
    return 0


async def _cmd_import_legacy(path: str) -> int:
    from pathlib import Path

    from . import library_index
    n = await library_index.import_legacy_archive(Path(path))
    print(f"imported {n} legacy archive entries (advisory only)")
    return 0


def _mb(n) -> str:
    return f"{(n or 0) / 1e9:.1f} GB" if (n or 0) >= 1e9 else f"{(n or 0) / 1e6:.0f} MB"


async def _cmd_library(args) -> int:
    from . import library

    if args.action == "scan":
        def progress(done, total):
            print(f"\r  read {done}/{total}", end="", flush=True)

        result = await library.scan(full=args.full, progress_cb=progress)
        print(f"\nscan complete: +{result['added']} new, ~{result['updated']} updated, "
              f"-{result['removed']} gone, {result['unreadable']} unreadable")
        return 0

    if args.action == "dupes":
        report = await library.duplicates(args.kind, limit=args.limit)
        print(f"{report['total']} {args.kind} duplicate groups"
              + (f", {_mb(report.get('reclaimable_bytes'))} reclaimable"
                 if "reclaimable_bytes" in report else ""))
        for group in report["items"]:
            if args.kind == "albums":
                print(f"\n  {group['album_artist']} — {group['album']}  [{group['kind']}]")
                for i, inst in enumerate(group["instances"]):
                    mark = "KEEP" if i == 0 else "    "
                    print(f"    {mark} {inst['dirpath']}")
                    print(f"         {inst['track_count']} tracks, "
                          f"{'/'.join(inst['formats'])}, {_mb(inst['bytes'])}, "
                          f"score {inst['score']}"
                          + (f", {len(inst['orphans'])} not in the keeper"
                             if inst.get("orphans") else ""))
            elif args.kind == "tracks":
                print(f"\n  {group['artist']} — {group['title']} "
                      f"(spread {group['duration_spread']}s)")
                for i, copy in enumerate(group["copies"]):
                    print(f"    {'KEEP' if i == 0 else '    '} {copy['path']}  "
                          f"[{', '.join(copy['reasons'])}]")
            else:
                print(f"\n  keep {group['suggested_keeper']!r}")
                for variant in group["variants"]:
                    print(f"    {variant['artist_dir']!r}: {variant['track_count']} tracks, "
                          f"{variant['album_count']} albums, {_mb(variant['bytes'])}")
        return 0

    if args.action == "purge":
        if args.days is None:
            print("pass --days N (use --days 0 to purge everything)")
            return 2
        result = await library.purge_trash(older_than_days=args.days)
        print(f"purged {result['purged']} entries, freed {_mb(result['freed_bytes'])}")
        return 0
    return 2


async def _cmd_canary() -> int:
    from .engine import ytdlp_engine
    print(f"yt-dlp {ytdlp_engine.ytdlp_version()}")
    ok = await ytdlp_engine.canary_check()
    print(f"canary probe: {'ok' if ok else 'FAILED'}")
    cred = await ytdlp_engine.credential_check()
    print(f"authenticated cookies: {cred['authenticated_cookies']}")
    print(f"premium formats visible: {cred['premium_formats']}")
    return 0 if ok else 1


async def _main() -> int:
    parser = argparse.ArgumentParser(prog="mdl")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_dl = sub.add_parser("download")
    p_dl.add_argument("url")
    p_idx = sub.add_parser("index")
    p_idx.add_argument("--rebuild", action="store_true")
    p_leg = sub.add_parser("import-legacy")
    p_leg.add_argument("path")
    sub.add_parser("canary")
    p_lib = sub.add_parser("library")
    p_lib.add_argument("action", choices=["scan", "dupes", "purge"])
    p_lib.add_argument("kind", nargs="?", default="albums",
                       choices=["albums", "tracks", "artists"])
    p_lib.add_argument("--full", action="store_true")
    p_lib.add_argument("--limit", type=int, default=25)
    p_lib.add_argument("--days", type=int)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    await db.connect()
    try:
        if args.cmd == "download":
            return await _cmd_download(args.url)
        if args.cmd == "index":
            return await _cmd_index(args.rebuild)
        if args.cmd == "import-legacy":
            return await _cmd_import_legacy(args.path)
        if args.cmd == "canary":
            return await _cmd_canary()
        if args.cmd == "library":
            return await _cmd_library(args)
        return 2
    finally:
        await db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
