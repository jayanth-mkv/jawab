"""Leave one open question with an owner voice-note draft (for screenshots / a live demo start).

    uv run python bench/stage_demo.py --base https://... --code XXXX
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from bench.e2e import tts_wav
from bench.grounding import ask


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--code", required=True)
    a = ap.parse_args()
    r = await ask(a.base, a.code, "Kya aap gluten-free cake banate ho?")
    print("call:", r["tool"], r["text"][:120])
    async with httpx.AsyncClient(base_url=a.base, headers={"x-access-code": a.code}, timeout=90) as http:
        gap = next(g for g in (await http.get("/api/state")).json()["gaps"] if g["status"] == "open")
        wav = tts_wav("Haan, gluten-free cake bana dete hain, do din pehle order dena hoga, aur sau rupaye extra lagte hain.")
        draft = (await http.post(f"/api/gaps/{gap['id']}/voice", files={"audio": ("note.wav", wav, "audio/wav")})).json()
        print(json.dumps(draft, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
