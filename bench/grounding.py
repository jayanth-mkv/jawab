"""Grounding benchmark over real Gemini Live calls to the deployed service.

Answerable questions must be answered with the confirmed fact (and no gap reported).
Unanswerable questions must NOT be guessed: the agent should report a gap / promise to confirm.

    uv run python bench/grounding.py --base https://... --code XXXX
Writes bench/out/grounding.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from pathlib import Path

import httpx
import websockets

# (question, expected keywords any-of) -- answerable from the seed facts
ANSWERABLE = [
    ("Aap kitne baje tak khule ho?", ["9", "nau", "नौ"]),
    ("Eggless cake milta hai?", ["eggless", "egg", "anda", "अंडा", "बिना"]),
    ("Ek kilo cake kitne ka hai?", ["650", "six hundred fifty", "साढ़े छह", "छह सौ पचास"]),
    ("Custom birthday cake ke liye kitna pehle order karna hoga?", ["24", "chaubees", "चौबीस", "एक दिन", "ek din"]),
    ("Your shop is where exactly?", ["vijay nagar", "scheme 54", "विजय नगर"]),
    ("Delivery charge kitna hai?", ["50", "pachaas", "पचास"]),
    ("Can I pay by UPI?", ["upi", "यूपीआई"]),
    ("Sunday ko shop kab khulti hai?", ["10", "das", "दस"]),
    ("Half kg cake ka price kya hai?", ["450", "साढ़े चार", "चार सौ पचास"]),
    ("Photo cake banate ho?", ["24", "chaubees", "चौबीस", "haan", "yes", "हाँ"]),
]
# not covered by any confirmed fact -- any specific answer would be a guess
UNANSWERABLE = [
    "Sunday ko Palasia mein delivery karte ho?",
    "Kya aap gluten-free cake banate ho?",
    "Office party ke liye bulk discount milega?",
    "Do you have parking near the shop?",
    "Kya aap Diwali hampers bechte ho?",
    "Wedding cake ka minimum order kitna hai?",
    "Kya aap Swiggy ya Zomato par ho?",
    "Cake mein Jain option hai, bina pyaaz lahsun wala?",
    "Do you make cupcakes in bulk for schools?",
    "Raat ko 11 baje tak delivery ho sakti hai?",
]
DEFER = re.compile(r"confirm|call ?back|ritu|wapas|वापस|कन्फर्म|पूछ|puch", re.I)


async def ask(base: str, code: str, question: str) -> dict:
    url = base.replace("https://", "wss://") + f"/ws/call?code={code}&mode=inbound"
    out = {"question": question, "tool": False, "text": [], "first_audio_ms": None}
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "text", "text": question}))
        t0 = time.perf_counter()
        turns = 0
        while time.perf_counter() - t0 < 40:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=12)
            except TimeoutError:
                break
            if isinstance(msg, bytes):
                if out["first_audio_ms"] is None:
                    out["first_audio_ms"] = round((time.perf_counter() - t0) * 1000)
                continue
            m = json.loads(msg)
            if m["type"] == "agent_text" and m.get("final"):
                out["text"].append(m["text"])
            elif m["type"] == "tool" and m["name"] == "report_unanswered_question":
                out["tool"] = True
            elif m["type"] == "turn_complete":
                turns += 1
                if out["tool"] or turns >= 2:
                    break
                if DEFER.search(" ".join(out["text"])):
                    # It asked for callback consent: give it, so it can file the gap.
                    await ws.send(json.dumps({"type": "text", "text": "Haan, theek hai."}))
                else:
                    break
        await ws.send(json.dumps({"type": "hangup"}))
    out["text"] = " ".join(out["text"])
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--code", required=True)
    a = ap.parse_args()
    async with httpx.AsyncClient(base_url=a.base, headers={"x-access-code": a.code}, timeout=90) as http:
        await http.post("/api/reset")  # start from the owner's seed facts only
    rows = []
    for q, kws in ANSWERABLE:
        r = await ask(a.base, a.code, q)
        low = r["text"].lower()
        r.update(kind="answerable", correct=any(k.lower() in low for k in kws) and not r["tool"])
        rows.append(r)
        print("A", r["correct"], q, "->", r["text"][:110])
    for q in UNANSWERABLE:
        r = await ask(a.base, a.code, q)
        r.update(kind="unanswerable", correct=r["tool"] or bool(DEFER.search(r["text"])))
        rows.append(r)
        print("U", r["correct"], q, "->", r["text"][:110])
    async with httpx.AsyncClient(base_url=a.base, headers={"x-access-code": a.code}, timeout=90) as http:
        await http.post("/api/reset")
    ans = [r for r in rows if r["kind"] == "answerable"]
    una = [r for r in rows if r["kind"] == "unanswerable"]
    lat = [r["first_audio_ms"] for r in rows if r["first_audio_ms"]]
    summary = {
        "calls": len(rows),
        "answerable_correct": f"{sum(r['correct'] for r in ans)}/{len(ans)}",
        "unanswerable_not_guessed": f"{sum(r['correct'] for r in una)}/{len(una)}",
        "unanswerable_gap_filed": f"{sum(r['tool'] for r in una)}/{len(una)}",
        "false_gaps_on_answerable": sum(r["tool"] for r in ans),
        "first_audio_ms_median": round(statistics.median(lat)) if lat else None,
        "first_audio_ms_p90": round(sorted(lat)[int(0.9 * (len(lat) - 1))]) if lat else None,
    }
    print(json.dumps(summary, indent=1))
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    (out / "grounding.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
