"""Facts ledger and gap tracker, backed by Firestore (prod) or memory (dev/tests).

A *fact* is something the owner has confirmed; only confirmed facts may be spoken.
A *gap* is a customer question the agent could not answer from confirmed facts. Its
status moves forward through the Missed-Answer Loop:

    open -> answered -> called_back -> published -> verified
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app import config

GAP_FLOW = ["open", "answered", "called_back", "published", "verified"]


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class MemoryStore:
    def __init__(self) -> None:
        self.facts: dict[str, dict[str, Any]] = {}
        self.gaps: dict[str, dict[str, Any]] = {}

    async def list_facts(self) -> list[dict[str, Any]]:
        return sorted(self.facts.values(), key=lambda f: f["created_at"])

    async def put_fact(self, fact: dict[str, Any]) -> dict[str, Any]:
        self.facts[fact["id"]] = fact
        return fact

    async def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        return self.facts.get(fact_id)

    async def list_gaps(self) -> list[dict[str, Any]]:
        return sorted(self.gaps.values(), key=lambda g: g["created_at"], reverse=True)

    async def put_gap(self, gap: dict[str, Any]) -> dict[str, Any]:
        self.gaps[gap["id"]] = gap
        return gap

    async def get_gap(self, gap_id: str) -> dict[str, Any] | None:
        return self.gaps.get(gap_id)

    async def clear(self) -> None:
        self.facts.clear()
        self.gaps.clear()


class FirestoreStore:
    def __init__(self) -> None:
        from google.cloud import firestore

        self.db = firestore.AsyncClient(project=config.PROJECT or None)
        root = self.db.collection("businesses").document(config.BUSINESS_ID)
        self.facts_col = root.collection("facts")
        self.gaps_col = root.collection("gaps")

    async def list_facts(self) -> list[dict[str, Any]]:
        docs = [d.to_dict() async for d in self.facts_col.stream()]
        return sorted(docs, key=lambda f: f["created_at"])

    async def put_fact(self, fact: dict[str, Any]) -> dict[str, Any]:
        await self.facts_col.document(fact["id"]).set(fact)
        return fact

    async def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        snap = await self.facts_col.document(fact_id).get()
        return snap.to_dict() if snap.exists else None

    async def list_gaps(self) -> list[dict[str, Any]]:
        docs = [d.to_dict() async for d in self.gaps_col.stream()]
        return sorted(docs, key=lambda g: g["created_at"], reverse=True)

    async def put_gap(self, gap: dict[str, Any]) -> dict[str, Any]:
        await self.gaps_col.document(gap["id"]).set(gap)
        return gap

    async def get_gap(self, gap_id: str) -> dict[str, Any] | None:
        snap = await self.gaps_col.document(gap_id).get()
        return snap.to_dict() if snap.exists else None

    async def clear(self) -> None:
        batch, n = self.db.batch(), 0
        for col in (self.facts_col, self.gaps_col):
            async for d in col.stream():
                batch.delete(d.reference)
                n += 1
                if n % 400 == 0:
                    await batch.commit()
                    batch = self.db.batch()
        await batch.commit()


store: MemoryStore | FirestoreStore = FirestoreStore() if config.STORE == "firestore" else MemoryStore()


def advance(gap: dict[str, Any], status: str) -> dict[str, Any]:
    """Move a gap forward (never backward) and stamp the time it reached `status`."""
    if GAP_FLOW.index(status) > GAP_FLOW.index(gap["status"]):
        gap["status"] = status
    gap.setdefault("times", {})[status] = now()
    return gap
