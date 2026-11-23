"""Gemini text/audio calls outside the live conversation.

* `extract_fact`: owner's voice note -> structured, bilingual fact (structured output).
* `probe_ai_answer`: ask Gemini with Google Search grounding whether the public web
  now answers the question, and whether our page is among the cited sources.
"""

from __future__ import annotations

import json
import time

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app import config
from app.business import PROFILE

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


class ExtractedFact(BaseModel):
    transcript: str = Field(description="Verbatim transcript of the owner's voice note, in its original language.")
    answers_question: bool = Field(description="True only if the note actually answers the customer's question.")
    answer_en: str = Field(description="The owner's answer as one or two clear customer-facing sentences in English. Only what the owner said.")
    answer_hi: str = Field(description="The same answer in natural Hindi (Devanagari).")
    faq_question: str = Field(description="The customer's question, rewritten as a clean FAQ question in English.")
    details_missing: str = Field(description="Anything a customer would still need that the owner did not say, or empty.")


EXTRACT_PROMPT = """A customer of {name} ({city}) asked: "{question}"
The customer's own words were: "{caller_words}"

The attached audio is the owner's voice note replying to that question. It may be in Hindi,
English or a mix. Transcribe it, then turn ONLY what the owner said into a customer-facing answer.
Do not add prices, times, areas or conditions the owner did not say."""


async def extract_fact(audio: bytes, mime_type: str, question: str, caller_words: str) -> tuple[ExtractedFact, float]:
    t0 = time.perf_counter()
    resp = await client().aio.models.generate_content(
        model=config.TEXT_MODEL,
        contents=[
            types.Part.from_bytes(data=audio, mime_type=mime_type),
            EXTRACT_PROMPT.format(**PROFILE, question=question, caller_words=caller_words),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedFact,
            temperature=0,
        ),
    )
    fact = resp.parsed if isinstance(resp.parsed, ExtractedFact) else ExtractedFact(**json.loads(resp.text))
    return fact, time.perf_counter() - t0


class ProbeResult(BaseModel):
    answered: bool
    answer: str
    cited_our_page: bool
    sources: list[str]


async def probe_ai_answer(question: str, our_url: str) -> ProbeResult:
    """Ask a grounded Gemini the customer's question as a stranger would."""
    q = f"{question} ({PROFILE['name']}, {PROFILE['area']}, {PROFILE['city']})"
    resp = await client().aio.models.generate_content(
        model=config.TEXT_MODEL,
        contents=q,
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0),
    )
    sources: list[str] = []
    try:
        gm = resp.candidates[0].grounding_metadata
        for ch in (gm.grounding_chunks or []):
            if ch.web and ch.web.uri:
                sources.append(ch.web.title or ch.web.uri)
    except (AttributeError, IndexError, TypeError):
        pass
    host = our_url.split("//")[-1].split("/")[0]
    text = resp.text or ""
    return ProbeResult(
        answered=bool(text.strip()),
        answer=text[:600],
        cited_our_page=any(host in s for s in sources),
        sources=sources[:8],
    )
