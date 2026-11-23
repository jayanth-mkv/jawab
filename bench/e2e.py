"""End-to-end run of the Missed-Answer Loop against a deployed Jawab service, with timings.

    uv run python bench/e2e.py --base https://... --code XXXX [--runs 3]

Each run:
  1. opens a real Gemini Live call over /ws/call and asks a question the bakery has not answered
  2. waits for `report_unanswered_question` (the agent must not guess)
  3. synthesises the owner's Hindi voice note with Gemini TTS and uploads it
  4. confirms the drafted fact while the call is still open; expects the agent to tell the caller
  5. publishes the answer and waits for the live-page verification
Writes bench/out/e2e.json.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import statistics
import time
import wave
from pathlib import Path

import httpx
import websockets
from google import genai
from google.genai import types

QUESTIONS = [
    ("Sunday ko Palasia mein delivery karte ho?",
     "Haan ji, Sunday ko bhi Palasia delivery karte hain, subah gyarah baje se shaam paanch baje tak, delivery charge assi rupaye."),
    ("Kya aap sugar-free cake banate ho?",
     "Haan, sugar-free cake bana dete hain, par ek din pehle order dena hoga, aur price same hai."),
    ("Do you take bulk orders for office parties?",
     "Haan, office party ke liye bulk order lete hain, kam se kam paanch kilo, aur das percent discount milta hai."),
]


def tts_client() -> genai.Client:
    """Vertex AI client; uses ACCESS_TOKEN (e.g. from `gcloud auth print-access-token`) if set, else ADC."""
    kw = {"vertexai": True, "project": os.environ["GOOGLE_CLOUD_PROJECT"], "location": "global"}
    if os.environ.get("ACCESS_TOKEN"):
        from google.oauth2.credentials import Credentials
        kw["credentials"] = Credentials(os.environ["ACCESS_TOKEN"])
    return genai.Client(**kw)


def tts_wav(text: str) -> bytes:
    """Owner's voice note, synthesised in Hindi by Gemini TTS (24 kHz PCM -> WAV)."""
    client = tts_client()
    resp = client.models.generate_content(
        model=os.environ.get("TTS_MODEL", "gemini-3.8-flash-tts"),
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore"))),
        ),
    )
    pcm = resp.candidates[0].content.parts[0].inline_data.data
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    return buf.getvalue()


async def run_once(base: str, code: str, question: str, owner_says: str) -> dict:
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + f"/ws/call?code={code}&mode=inbound"
    r: dict = {"question": question}
    async with httpx.AsyncClient(base_url=base, headers={"x-access-code": code}, timeout=60) as http:
        t_start = time.perf_counter()
        async with websockets.connect(ws_url, max_size=None) as ws:
            r["connect_ms"] = round((time.perf_counter() - t_start) * 1000)

            async def next_msg(timeout: float):
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                return ("audio", None) if isinstance(msg, bytes) else ("json", json.loads(msg))

            async def drain(quiet: float = 2.0) -> None:
                """Consume whatever the agent is still saying until the line is quiet."""
                while True:
                    try:
                        await next_msg(quiet)
                    except TimeoutError:
                        return

            replies, gap_id, first_audio = [], None, None
            await ws.send(json.dumps({"type": "text", "text": question}))
            t_q = time.perf_counter()
            # Phase 1: agent replies; it must report a gap instead of inventing an answer.
            consent_given = False
            while time.perf_counter() - t_q < 45 and "gap_reported_ms" not in r:
                kind, m = await next_msg(45)
                if kind == "audio":
                    first_audio = first_audio or time.perf_counter()
                elif m["type"] == "agent_text" and m.get("final"):
                    replies.append(m["text"])
                elif m["type"] == "tool" and m["name"] == "report_unanswered_question":
                    r["gap_reported_ms"] = round((time.perf_counter() - t_q) * 1000)
                    r["callback_consent_arg"] = m["args"].get("callback_consent")
                elif m["type"] == "turn_complete" and replies and not consent_given:
                    consent_given = True  # the agent asked for callback consent; give it
                    await ws.send(json.dumps({"type": "text", "text": "Haan, call back kar dena."}))
            await drain()
            r["first_audio_ms"] = round((first_audio - t_q) * 1000) if first_audio else None
            r["agent_reply"] = " | ".join(replies)
            gaps = (await http.get("/api/state")).json()["gaps"]
            gap = next((g for g in gaps if g["status"] == "open"), None)
            if not gap:
                r["error"] = "no gap reported"
                return r
            gap_id = gap["id"]

            # Phase 2: owner voice note -> structured fact.
            wav = tts_wav(owner_says)
            t0 = time.perf_counter()
            draft = (await http.post(f"/api/gaps/{gap_id}/voice", files={"audio": ("note.wav", wav, "audio/wav")})).json()
            r["extract_ms"] = round((time.perf_counter() - t0) * 1000)
            r["draft"] = draft

            # Phase 3: confirm while the caller is still on the line.
            t0 = time.perf_counter()
            conf = (await http.post(f"/api/gaps/{gap_id}/confirm", json={
                "faq_question": draft["faq_question"], "answer_en": draft["answer_en"], "answer_hi": draft["answer_hi"]})).json()
            r["delivered_in_call"] = conf["delivered_in_call"]
            told, said, injected = None, [], False
            while time.perf_counter() - t0 < 40:
                kind, m = await next_msg(40)
                if kind == "audio":
                    if injected:
                        told = told or time.perf_counter()
                    continue
                if m["type"] == "owner_answer":
                    injected = True
                elif injected and m["type"] == "agent_text" and m.get("final"):
                    said.append(m["text"])
                elif injected and m["type"] == "turn_complete" and said:
                    break
            r["caller_told_ms"] = round((told - t0) * 1000) if told else None
            r["agent_told_caller"] = " ".join(said)
            await ws.send(json.dumps({"type": "hangup"}))

        # Phase 4: publish and verify on the live page.
        fact_id = conf["fact"]["id"]
        t0 = time.perf_counter()
        await http.post(f"/api/facts/{fact_id}/publish")
        while time.perf_counter() - t0 < 60:
            g = next(x for x in (await http.get("/api/state")).json()["gaps"] if x["id"] == gap_id)
            if g["status"] == "verified" or (g.get("verification") and not g["verification"].get("ok")):
                break
            await asyncio.sleep(0.5)
        r["verify_ms"] = round((time.perf_counter() - t0) * 1000)
        r["verification"] = g.get("verification")
        r["final_status"] = g["status"]
        r["loop_total_s"] = round(time.perf_counter() - t_start, 1)
    return r


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--runs", type=int, default=len(QUESTIONS))
    a = ap.parse_args()
    async with httpx.AsyncClient(base_url=a.base, headers={"x-access-code": a.code}, timeout=90) as http:
        await http.post("/api/reset")
    results = []
    for q, owner in QUESTIONS[: a.runs]:
        res = await run_once(a.base, a.code, q, owner)
        results.append(res)
        print(json.dumps({k: v for k, v in res.items() if k not in ("draft", "verification")}, ensure_ascii=False, indent=1))
    ok = [x for x in results if x.get("final_status") == "verified"]
    med = lambda k: statistics.median([x[k] for x in results if x.get(k) is not None]) if any(x.get(k) is not None for x in results) else None  # noqa: E731
    summary = {
        "runs": len(results), "loops_verified": len(ok),
        "gaps_reported_instead_of_guessing": sum(1 for x in results if "gap_reported_ms" in x),
        "told_in_same_call": sum(1 for x in results if x.get("delivered_in_call")),
        "median_first_audio_ms": med("first_audio_ms"), "median_gap_reported_ms": med("gap_reported_ms"),
        "median_extract_ms": med("extract_ms"), "median_caller_told_ms": med("caller_told_ms"),
        "median_verify_ms": med("verify_ms"), "median_loop_total_s": med("loop_total_s"),
    }
    print(json.dumps(summary, indent=1))
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    (out / "e2e.json").write_text(json.dumps({"summary": summary, "runs": results}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
