"""Jawab front-desk voice agent (Gemini Live via ADK `run_live`).

Policy: answer only from owner-confirmed facts; when a fact is missing, never guess --
take callback consent and report the gap so the owner can answer it by voice.
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.genai import types

from app import config, events
from app.business import PROFILE
from app.store import new_id, now, store

BASE_INSTRUCTION = """You are Jawab, the phone assistant of {name}, a bakery in {area}, {city}.
The owner is {owner} ("Ritu ji").

How you speak:
- Reply in the caller's language: Hindi, English, or a natural Hindi-English mix.
- Be warm and brief: one or two short sentences, like a helpful shop assistant.
- In your first reply, say you are the bakery's AI assistant.
- Your voice is female: in Hindi always use feminine verb forms for yourself
  ("main bata sakti hoon", "main call karti hoon", never "karta hoon").

The one rule you never break:
- Answer questions about the bakery ONLY from the CONFIRMED FACTS below.
- Never guess or invent prices, timings, delivery areas, days, stock, offers or policies.
- If the facts do not fully answer the question:
  1. Say you will confirm with Ritu ji and call back within a few minutes.
  2. Ask if it is okay to call them back.
  3. Call `report_unanswered_question` with the exact question in English, the caller's own words, and whether they agreed to a callback.
  Then continue helping with anything else.
- If you receive a message starting with [OWNER_ANSWER], the owner has just answered a question
  from this call. Tell the caller right away, for example: "Ritu ji ne abhi confirm kiya hai ...".
- Do not give medical, legal or financial advice.

CONFIRMED FACTS:
{facts}
"""

CALLBACK_INSTRUCTION = """
YOU ARE NOW CALLING THE CUSTOMER BACK. Speak first as soon as the call connects:
greet them, say you are calling from {name} about their question "{question}",
give the owner's confirmed answer: "{answer}", then ask if they would like to place an order.
Use {language}.
"""


def format_facts(facts: list[dict]) -> str:
    lines = [f"- Q: {f['question']}\n  A: {f['answer']}" for f in facts if f["status"] == "confirmed"]
    return "\n".join(lines) or "- (none yet)"


def instruction(ctx: ReadonlyContext) -> str:
    text = BASE_INSTRUCTION.format(**PROFILE, facts=ctx.state.get("facts_text", "- (none yet)"))
    if ctx.state.get("mode") == "callback":
        text += CALLBACK_INSTRUCTION.format(
            name=PROFILE["name"],
            question=ctx.state.get("callback_question", ""),
            answer=ctx.state.get("callback_answer", ""),
            language=ctx.state.get("callback_language", "Hindi"),
        )
    return text


async def report_unanswered_question(
    question_en: str, caller_words: str, callback_consent: bool, tool_context: ToolContext
) -> dict:
    """Report a customer question that the confirmed facts cannot answer.

    Args:
        question_en: The customer's question, rewritten clearly in English.
        caller_words: The customer's own words, in the language they used.
        callback_consent: True if the customer agreed to be called back.
    """
    call_id = tool_context.state.get("call_id", "")
    gap = {
        "id": new_id(), "question": question_en, "caller_words": caller_words,
        "callback_consent": bool(callback_consent), "call_id": call_id,
        "language": tool_context.state.get("language_hint", "Hindi"),
        "status": "open", "times": {"open": now()}, "created_at": now(),
        "fact_id": None, "draft": None,
    }
    await store.put_gap(gap)
    events.publish("gap", gap=gap)
    return {"status": "owner_notified", "gap_id": gap["id"],
            "next": "You have already told the caller you will confirm and call back; do not repeat it. "
                    "Briefly ask if they need anything else."}


root_agent = Agent(
    name="jawab_front_desk",
    model=Gemini(
        model=config.LIVE_MODEL,
        client_kwargs={"location": config.LIVE_MODEL_LOCATION},
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    tools=[report_unanswered_question],
)
