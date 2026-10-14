"""Fan-out of server-sent events to every open browser tab."""
import asyncio
import json
from typing import Any

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(kind: str, data: Any) -> None:
    msg = f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            # A tab that stopped reading; drop it rather than buffer forever.
            _subscribers.discard(q)
