"""Jawab service: live voice calls, the owner's voice-answer loop, the public site and verification."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import statistics
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from app import config, events
from app.agent import format_facts, root_agent
from app.business import PROFILE, seed
from app.intelligence import extract_fact, probe_ai_answer
from app.store import advance, new_id, now, store

log = logging.getLogger("jawab")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

WEB = Path(__file__).resolve().parent.parent / "web"
APP_NAME = "jawab"
session_service = InMemorySessionService()
runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

app = FastAPI(title="Jawab")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")


@app.on_event("startup")
async def _startup() -> None:
    if not await store.list_facts():
        await seed()


def check_code(code: str | None) -> None:
    if config.ACCESS_CODE and code != config.ACCESS_CODE:
        raise HTTPException(status_code=401, detail="access code required")


def code_of(request: Request) -> str | None:
    return request.headers.get("x-access-code") or request.query_params.get("code")


# ---------------------------------------------------------------- pages

for _page in ("call", "owner", "dashboard", "demo"):
    def _make(page: str):
        async def _serve() -> FileResponse:
            return FileResponse(WEB / f"{page}.html")
        return _serve
    app.get(f"/{_page}", include_in_schema=False)(_make(_page))


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/health", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True}


def faq_jsonld(facts: list[dict]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": f["question"],
             "acceptedAnswer": {"@type": "Answer", "text": f["answer"]}}
            for f in facts
        ],
    }


@app.get("/site", response_class=HTMLResponse, include_in_schema=False)
async def site() -> HTMLResponse:
    """The bakery's public page. Published facts become visible FAQ + FAQPage JSON-LD."""
    facts = [f for f in await store.list_facts() if f["status"] == "confirmed" and f.get("published")]
    business_ld = {
        "@context": "https://schema.org", "@type": "Bakery", "name": PROFILE["name"],
        "address": PROFILE["address"], "telephone": PROFILE["phone"], "openingHours": PROFILE["hours"],
        "url": f"{config.PUBLIC_BASE_URL}/site",
    }
    items = []
    for f in facts:
        hi = f'<p class="hi" lang="hi">{html.escape(f["answer_hi"])}</p>' if f.get("answer_hi") else ""
        badge = '<span class="new">Answered by the owner</span>' if f.get("gap_id") else ""
        items.append(
            f'<details id="fact-{f["id"]}" open><summary>{html.escape(f["question"])} {badge}</summary>'
            f'<p>{html.escape(f["answer"])}</p>{hi}</details>'
        )
    page = (WEB / "site.html").read_text(encoding="utf-8")
    page = (page.replace("{{NAME}}", html.escape(PROFILE["name"]))
                .replace("{{ABOUT}}", html.escape(PROFILE["about"]))
                .replace("{{ADDRESS}}", html.escape(PROFILE["address"]))
                .replace("{{HOURS}}", html.escape(PROFILE["hours"]))
                .replace("{{PHONE}}", html.escape(PROFILE["phone"]))
                .replace("{{FAQ}}", "\n".join(items))
                .replace("{{JSONLD}}", json.dumps([business_ld, faq_jsonld(facts)], ensure_ascii=False).replace("</", "<\\/")))
    return HTMLResponse(page)


# ---------------------------------------------------------------- state and events

def metrics(gaps: list[dict]) -> dict:
    def durations(a: str, b: str) -> list[float]:
        return [g["times"][b] - g["times"][a] for g in gaps if a in g.get("times", {}) and b in g.get("times", {})]
    def med(xs: list[float]) -> float | None:
        return round(statistics.median(xs), 1) if xs else None
    count = lambda s: sum(1 for g in gaps if s in g.get("times", {}))  # noqa: E731
    return {
        "gaps": len(gaps), "answered": count("answered"), "called_back": count("called_back"),
        "published": count("published"), "verified": count("verified"),
        "median_seconds_to_owner_answer": med(durations("open", "answered")),
        "median_seconds_to_callback": med(durations("open", "called_back")),
        "median_seconds_to_verified": med(durations("open", "verified")),
    }


@app.get("/api/state")
async def state(request: Request) -> dict:
    check_code(code_of(request))
    gaps = await store.list_gaps()
    return {"profile": PROFILE, "facts": await store.list_facts(), "gaps": gaps, "metrics": metrics(gaps)}


@app.get("/api/events")
async def sse(request: Request) -> StreamingResponse:
    check_code(code_of(request))
    q = events.subscribe()

    async def stream():
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {msg}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/reset")
async def reset(request: Request) -> dict:
    check_code(code_of(request))
    await seed()
    events.publish("reset")
    return {"ok": True}


# ---------------------------------------------------------------- owner loop

@app.post("/api/gaps/{gap_id}/voice")
async def owner_voice(gap_id: str, request: Request, audio: UploadFile = File(...)) -> dict:
    """Owner's voice note -> draft fact (not yet confirmed)."""
    check_code(code_of(request))
    gap = await store.get_gap(gap_id)
    if not gap:
        raise HTTPException(404, "gap not found")
    data = await audio.read()
    if len(data) > 8_000_000:
        raise HTTPException(413, "voice note too long")
    mime = (audio.content_type or "audio/webm").split(";")[0]
    fact, seconds = await extract_fact(data, mime, gap["question"], gap.get("caller_words", ""))
    gap["draft"] = {**fact.model_dump(), "extract_seconds": round(seconds, 2)}
    await store.put_gap(gap)
    events.publish("gap", gap=gap)
    return gap["draft"]


class ConfirmBody(BaseModel):
    faq_question: str
    answer_en: str
    answer_hi: str = ""


@app.post("/api/gaps/{gap_id}/confirm")
async def owner_confirm(gap_id: str, body: ConfirmBody, request: Request) -> dict:
    """Owner confirms the drafted fact. It becomes speakable immediately; the waiting caller hears it."""
    check_code(code_of(request))
    gap = await store.get_gap(gap_id)
    if not gap:
        raise HTTPException(404, "gap not found")
    fact = {
        "id": new_id(), "question": body.faq_question.strip(), "answer": body.answer_en.strip(),
        "answer_hi": body.answer_hi.strip(), "source": "owner voice note", "status": "confirmed",
        "published": False, "gap_id": gap_id, "created_at": now(),
        "transcript": (gap.get("draft") or {}).get("transcript", ""),
    }
    await store.put_fact(fact)
    gap["fact_id"] = fact["id"]
    advance(gap, "answered")
    delivered = events.deliver_to_call(gap.get("call_id", ""), {"gap_id": gap_id, "fact": fact})
    if delivered:
        gap["callback_channel"] = "same call"
        advance(gap, "called_back")
    await store.put_gap(gap)
    events.publish("gap", gap=gap)
    events.publish("fact", fact=fact)
    if not delivered and gap.get("callback_consent", True):
        events.publish("callback_ready", gap_id=gap_id, call_id=gap.get("call_id", ""))
    return {"fact": fact, "delivered_in_call": delivered}


async def verify_published(gap_id: str, fact_id: str) -> None:
    """Re-fetch the live public page and check the fact is visible and in FAQPage JSON-LD."""
    gap, fact = await store.get_gap(gap_id), await store.get_fact(fact_id)
    if not gap or not fact:
        return
    url = f"{config.PUBLIC_BASE_URL}/site"
    result: dict[str, Any] = {"url": url, "checked_at": now()}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
            t0 = time.perf_counter()
            r = await http.get(url, headers={"Cache-Control": "no-cache"})
            result["http_status"] = r.status_code
            result["fetch_ms"] = round((time.perf_counter() - t0) * 1000)
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
        faq_ok = False
        for b in blocks:
            for node in json.loads(b.replace("<\\/", "</")) if b.strip().startswith("[") else [json.loads(b)]:
                if node.get("@type") == "FAQPage":
                    faq_ok = any(q.get("name") == fact["question"] and q["acceptedAnswer"]["text"] == fact["answer"]
                                 for q in node.get("mainEntity", []))
        result.update(visible=html.escape(fact["answer"]) in r.text, structured_data=faq_ok)
        result["ok"] = r.status_code == 200 and result["visible"] and faq_ok
    except Exception as exc:  # noqa: BLE001 -- verification must report, never crash
        result.update(ok=False, error=str(exc)[:200])
    gap["verification"] = result
    if result.get("ok"):
        advance(gap, "verified")
    await store.put_gap(gap)
    events.publish("gap", gap=gap)
    asyncio.create_task(run_probe(gap_id))


async def run_probe(gap_id: str) -> None:
    gap = await store.get_gap(gap_id)
    fact = gap and gap.get("fact_id") and await store.get_fact(gap["fact_id"])
    if not gap or not fact:
        return
    try:
        res = await probe_ai_answer(fact["question"], config.PUBLIC_BASE_URL)
        gap["ai_probe"] = {**res.model_dump(), "checked_at": now()}
    except Exception as exc:  # noqa: BLE001
        gap["ai_probe"] = {"error": str(exc)[:200], "checked_at": now()}
    await store.put_gap(gap)
    events.publish("gap", gap=gap)


@app.post("/api/facts/{fact_id}/publish")
async def publish_fact(fact_id: str, request: Request) -> dict:
    """Owner approves publishing the answer on the public site; verification follows."""
    check_code(code_of(request))
    fact = await store.get_fact(fact_id)
    if not fact:
        raise HTTPException(404, "fact not found")
    fact["published"] = True
    fact["published_at"] = now()
    await store.put_fact(fact)
    if fact.get("gap_id") and (gap := await store.get_gap(fact["gap_id"])):
        advance(gap, "published")
        await store.put_gap(gap)
        events.publish("gap", gap=gap)
        asyncio.create_task(verify_published(gap["id"], fact_id))
    events.publish("fact", fact=fact)
    return {"ok": True}


@app.post("/api/gaps/{gap_id}/probe")
async def probe(gap_id: str, request: Request) -> dict:
    check_code(code_of(request))
    await run_probe(gap_id)
    return (await store.get_gap(gap_id) or {}).get("ai_probe", {})


# ---------------------------------------------------------------- live voice

@app.websocket("/ws/call")
async def call(ws: WebSocket) -> None:
    """Browser <-> Gemini Live bridge. Binary frames: PCM16 audio. Text frames: JSON control/events."""
    params = ws.query_params
    if config.ACCESS_CODE and params.get("code") != config.ACCESS_CODE:
        await ws.close(code=4401)
        return
    await ws.accept()

    mode = params.get("mode", "inbound")
    call_id = params.get("call_id") or new_id()
    facts = await store.list_facts()
    state: dict[str, Any] = {"call_id": call_id, "mode": mode, "facts_text": format_facts(facts)}
    callback_gap = None
    if mode == "callback" and (callback_gap := await store.get_gap(params.get("gap", ""))):
        fact = await store.get_fact(callback_gap.get("fact_id") or "")
        state.update(callback_question=callback_gap.get("caller_words") or callback_gap["question"],
                     callback_answer=fact["answer"] if fact else "", callback_language="Hindi mixed with English")
    session = await session_service.create_session(app_name=APP_NAME, user_id=f"caller-{call_id}", state=state)

    lrq = LiveRequestQueue()
    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=config.VOICE_NAME))
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    inbox = events.open_call_inbox(call_id)
    started = time.perf_counter()
    first_audio_ms: list[float] = []
    await ws.send_text(json.dumps({"type": "session", "call_id": call_id, "mode": mode}))
    events.publish("call", call_id=call_id, status="started", mode=mode)

    if callback_gap:
        advance(callback_gap, "called_back")
        callback_gap["callback_channel"] = "outbound callback"
        await store.put_gap(callback_gap)
        events.publish("gap", gap=callback_gap)
        lrq.send_content(types.Content(role="user", parts=[types.Part(text="[CALL_CONNECTED] The customer picked up. Speak now.")]))

    async def upstream() -> None:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if (data := msg.get("bytes")) is not None:
                lrq.send_realtime(types.Blob(data=data, mime_type="audio/pcm;rate=16000"))
            elif (text := msg.get("text")) is not None:
                ctrl = json.loads(text)
                if ctrl.get("type") == "hangup":
                    return
                if ctrl.get("type") == "text":
                    lrq.send_content(types.Content(role="user", parts=[types.Part(text=ctrl["text"])]))

    async def downstream() -> None:
        user_turn_at: float | None = None
        async for event in runner.run_live(user_id=session.user_id, session_id=session.id,
                                           live_request_queue=lrq, run_config=run_config):
            if event.input_transcription and event.input_transcription.text:
                user_turn_at = user_turn_at or time.perf_counter()
                await ws.send_text(json.dumps({"type": "caller_text", "text": event.input_transcription.text,
                                               "final": bool(event.input_transcription.finished)}))
            if event.output_transcription and event.output_transcription.text:
                await ws.send_text(json.dumps({"type": "agent_text", "text": event.output_transcription.text,
                                               "final": bool(event.output_transcription.finished)}))
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.inline_data and part.inline_data.data and (part.inline_data.mime_type or "").startswith("audio/"):
                        if user_turn_at is not None:
                            first_audio_ms.append((time.perf_counter() - user_turn_at) * 1000)
                            await ws.send_text(json.dumps({"type": "latency", "ms": round(first_audio_ms[-1])}))
                            user_turn_at = None
                        await ws.send_bytes(part.inline_data.data)
            for fc in event.get_function_calls() or []:
                await ws.send_text(json.dumps({"type": "tool", "name": fc.name, "args": fc.args}))
            if event.interrupted:
                await ws.send_text(json.dumps({"type": "interrupted"}))
            if event.turn_complete:
                user_turn_at = None
                await ws.send_text(json.dumps({"type": "turn_complete"}))

    async def owner_answers() -> None:
        while True:
            item = await inbox.get()
            f = item["fact"]
            lrq.send_content(types.Content(role="user", parts=[types.Part(
                text=f"[OWNER_ANSWER] Question: {f['question']} Answer from Ritu ji: {f['answer']} Tell the caller now.")]))
            await ws.send_text(json.dumps({"type": "owner_answer", "fact": f}))

    async def timer() -> None:
        await asyncio.sleep(config.MAX_CALL_SECONDS)
        await ws.send_text(json.dumps({"type": "time_limit"}))

    tasks = [asyncio.create_task(c) for c in (upstream(), downstream(), owner_answers(), timer())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            if t.exception() and not isinstance(t.exception(), WebSocketDisconnect):
                log.error("call %s ended with error: %r", call_id, t.exception())
    finally:
        for t in tasks:
            t.cancel()
        lrq.close()
        events.close_call_inbox(call_id)
        events.publish("call", call_id=call_id, status="ended",
                       seconds=round(time.perf_counter() - started),
                       median_first_audio_ms=round(statistics.median(first_audio_ms)) if first_audio_ms else None)
        try:
            await ws.close()
        except RuntimeError:
            pass


@app.exception_handler(HTTPException)
async def _http_exc(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
