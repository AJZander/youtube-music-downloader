"""In-process pub/sub bus feeding the SSE endpoint and the events table."""
import asyncio
import json
import logging

from .db import db, utcnow

log = logging.getLogger("mdl.events")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._seq = 0

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event_type: str, data: dict) -> None:
        self._seq += 1
        message = {"id": self._seq, "type": event_type, "data": data}
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # Slow consumer: drop it; the client's EventSource will reconnect
                self.unsubscribe(q)

    async def record(
        self,
        level: str,
        code: str,
        message: str,
        job_id: str | None = None,
        track_id: str | None = None,
    ) -> None:
        try:
            await db.execute(
                "INSERT INTO events (ts, job_id, track_id, level, code, message) VALUES (?,?,?,?,?,?)",
                (utcnow(), job_id, track_id, level, code, message),
            )
        except Exception:
            log.exception("failed to record event")
        self.publish("log", {"level": level, "code": code, "message": message,
                             "job_id": job_id, "track_id": track_id})


bus = EventBus()


def sse_format(message: dict) -> str:
    return f"id: {message['id']}\nevent: {message['type']}\ndata: {json.dumps(message['data'], default=str)}\n\n"
