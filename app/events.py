"""In-process event bus.

* Dashboard/owner/customer pages subscribe over Server-Sent Events.
* Live call sessions register an inbox so an owner's answer can be spoken mid-call.

The Cloud Run service runs a single instance (max_instance_count = 1), so in-process
delivery is consistent; durable state lives in the store.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

_subscribers: set[asyncio.Queue[str]] = set()
_call_inboxes: dict[str, asyncio.Queue[dict[str, Any]]] = {}


def publish(kind: str, **data: Any) -> None:
    msg = json.dumps({"type": kind, **data})
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def subscribe() -> asyncio.Queue[str]:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue[str]) -> None:
    _subscribers.discard(q)


def open_call_inbox(session_id: str) -> asyncio.Queue[dict[str, Any]]:
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _call_inboxes[session_id] = q
    return q


def close_call_inbox(session_id: str) -> None:
    _call_inboxes.pop(session_id, None)


def deliver_to_call(session_id: str, message: dict[str, Any]) -> bool:
    """Push a message into a live call. Returns False if that call has ended."""
    q = _call_inboxes.get(session_id)
    if q is None:
        return False
    q.put_nowait(message)
    return True
