"""Response cache for the metadata sources (MusicBrainz, Last.fm)."""
import json
import time

from ..db import db

RETENTION = 120 * 86400


async def get(key: str, max_age: float):
    row = await db.fetchone("SELECT data, fetched_at FROM meta_cache WHERE key = ?", (key,))
    if row is None or time.time() - row["fetched_at"] > max_age:
        return None
    return json.loads(row["data"])


async def put(key: str, data) -> None:
    await db.execute(
        "INSERT INTO meta_cache (key, data, fetched_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET data = excluded.data, fetched_at = excluded.fetched_at",
        (key, json.dumps(data, separators=(",", ":")), time.time()))


async def prune() -> None:
    await db.execute("DELETE FROM meta_cache WHERE fetched_at < ?", (time.time() - RETENTION,))
