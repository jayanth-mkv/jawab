"""Offline tests for the Missed-Answer Loop (no Google Cloud calls)."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import events
from app.agent import format_facts
from app.main import app, faq_jsonld
from app.store import GAP_FLOW, advance, new_id, now, store


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c


def make_gap(**kw):
    g = {"id": new_id(), "question": "Do you deliver on Sunday?", "caller_words": "Sunday delivery?",
         "callback_consent": True, "call_id": "call-1", "status": "open", "times": {"open": now()},
         "created_at": now(), "fact_id": None, "draft": None}
    g.update(kw)
    return g


def test_gap_status_only_moves_forward():
    g = make_gap()
    advance(g, "published")
    advance(g, "answered")  # late event must not move it backward
    assert g["status"] == "published"
    assert set(g["times"]) == {"open", "published", "answered"}
    assert GAP_FLOW[0] == "open" and GAP_FLOW[-1] == "verified"


def test_only_confirmed_facts_reach_the_agent():
    facts = [{"question": "A?", "answer": "yes", "status": "confirmed"},
             {"question": "B?", "answer": "draft", "status": "draft"}]
    text = format_facts(facts)
    assert "A?" in text and "B?" not in text


def test_faq_jsonld_shape():
    ld = faq_jsonld([{"question": "Q1", "answer": "A1"}])
    assert ld["@type"] == "FAQPage"
    assert ld["mainEntity"][0]["acceptedAnswer"]["text"] == "A1"


def test_site_publishes_structured_data(client):
    page = client.get("/site").text
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    nodes = json.loads(blocks[0].replace("<\\/", "</"))
    assert {n["@type"] for n in nodes} == {"Bakery", "FAQPage"}


@pytest.mark.anyio
async def test_confirm_reaches_live_call_and_is_unpublished_until_approved(client):
    gap = make_gap(call_id="live-call")
    await store.put_gap(gap)
    inbox = events.open_call_inbox("live-call")
    try:
        r = client.post(f"/api/gaps/{gap['id']}/confirm",
                        json={"faq_question": "Do you deliver on Sunday?", "answer_en": "Yes, 11am to 5pm."})
        body = r.json()
        assert body["delivered_in_call"] is True
        assert inbox.get_nowait()["fact"]["answer"] == "Yes, 11am to 5pm."
        saved = await store.get_gap(gap["id"])
        assert saved["status"] == "called_back" and saved["callback_channel"] == "same call"
        # Confirmed facts are speakable at once, but stay off the website until the owner approves.
        assert "11am to 5pm" not in client.get("/site").text
    finally:
        events.close_call_inbox("live-call")


@pytest.mark.anyio
async def test_callback_offered_when_caller_hung_up(client):
    gap = make_gap(call_id="ended-call")
    await store.put_gap(gap)
    q = events.subscribe()
    try:
        client.post(f"/api/gaps/{gap['id']}/confirm", json={"faq_question": "Q?", "answer_en": "A."})
        kinds = []
        while not q.empty():
            kinds.append(json.loads(q.get_nowait())["type"])
        assert "callback_ready" in kinds
        assert (await store.get_gap(gap["id"]))["status"] == "answered"
    finally:
        events.unsubscribe(q)


def test_access_code_gates_api(monkeypatch, client):
    from app import config
    monkeypatch.setattr(config, "ACCESS_CODE", "secret")
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"x-access-code": "secret"}).status_code == 200
    assert client.get("/site").status_code == 200  # the public website stays open
