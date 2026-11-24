"""Record and edit the demo video of the Missed-Answer Loop against the deployed service.

Three browser contexts are recorded independently, each with its own high-resolution frame stream
(Chrome DevTools screencast) and its own event log:

  L  the customer's phone (/call), with a scripted microphone that speaks Hindi lines on cue
  R  the owner app (/owner), with a scripted microphone for the owner's voice note
  W  a desktop browser (home page, /try, the bakery website, the Answer Loop) with drawn browser chrome

Everything on screen happens for real: a Gemini Live call, the gap, the owner's voice note, the mid-call
answer, publishing and verification. A visible cursor moves and clicks like a person would.

The editor then composes a 1920x1080 video: both devices side by side, and for each key moment the
relevant side expands to full screen, zooms in on the detail, and returns to the two-column layout.

    uv run --with playwright --with opencv-python --with numpy python -m bench.record_demo record --base https://... --code XXXX
    uv run --with opencv-python --with numpy python -m bench.record_demo compose
Output: bench/out/demo/jawab-demo.mp4
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import bisect
import functools
import json
import math
import os
import subprocess
import time
import wave
from pathlib import Path

OUT = Path(__file__).parent / "out" / "demo"
WORK = OUT / "work"
FPS = 30
VW, VH = 1920, 1080
CHROME_H = 88
# Viewport (w, h) and CSS zoom Z. The screencast captures CSS pixels only, so pages are laid out at
# w/Z x h/Z and zoomed by Z: frames come out Z times sharper, which keeps full-screen zooms crisp.
SIDE = (1920, 2160, 2)  # L and R: a 960x1080 page at 2x
DESK = (2880, int((VH - CHROME_H) * 1.5), 1.5)  # W: a 1920x992 page at 1.5x

# ---------------------------------------------------------------------------------------------- speech

LINES = {
    "c1": ("Namaste! Eggless cake milta hai? Ek kilo kitne ka hai?", "Puck"),
    "c2": ("Achha. Aur Sunday ko Palasia mein delivery karte ho?", "Puck"),
    "c3": ("Haan ji, zaroor poochh lijiye. Main line pe hoon.", "Puck"),
    "c4": ("Arre waah, perfect! Bahut dhanyavaad, bas itna hi.", "Puck"),
    "o1": ("Haan, Sunday ko Palasia delivery karte hain, gyarah baje se paanch baje tak, assi rupaye charge.", "Kore"),
}


def make_clips() -> None:
    from google.genai import types

    from bench.e2e import tts_client

    client = None
    for name, (text, voice) in LINES.items():
        path = WORK / f"{name}.wav"
        if path.exists():
            continue
        client = client or tts_client()
        resp = client.models.generate_content(
            model="gemini-3.8-flash-tts", contents=text,
            config=types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
        pcm = resp.candidates[0].content.parts[0].inline_data.data
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 4800 + pcm + b"\x00\x00" * 4800)


# ---------------------------------------------------------------------------------------------- page helpers

# A scripted microphone (speaks clips on cue) and a visible cursor, injected into every page.
INIT = r"""
(() => {
  const Z = window.__Z || 1;
  if (window.top === window && Z !== 1) {
    const zoom = () => { if (document.documentElement) { document.documentElement.style.zoom = Z; return true; } };
    if (!zoom()) new MutationObserver((_, o) => zoom() && o.disconnect()).observe(document, {childList: true});
    // viewport units ignore zoom; keep the /try stage one screen tall
    const st = document.createElement('style');
    st.textContent = `.stage { height: calc(100svh / ${Z} - 65px) !important; min-height: 0 !important; }`;
    document.addEventListener('DOMContentLoaded', () => document.head.appendChild(st));
  }
  const ctx = new AudioContext(), dest = ctx.createMediaStreamDestination();
  const keep = ctx.createConstantSource(); keep.offset.value = 0; keep.connect(dest); keep.start();
  navigator.mediaDevices.getUserMedia = async () => { await ctx.resume(); return new MediaStream(dest.stream.getAudioTracks().map((t) => t.clone())); };
  window.__say = async (url) => {
    await ctx.resume();
    const buf = await ctx.decodeAudioData(await (await fetch(url)).arrayBuffer());
    const s = ctx.createBufferSource(); s.buffer = buf; s.connect(dest); s.start(); return buf.duration;
  };
  let el, x = -60, y = -60;
  const ensure = () => {
    if (el && el.isConnected) return el;
    el = document.createElement('div');
    el.innerHTML = '<svg width="30" height="30" viewBox="0 0 24 24"><path d="M4 2.5l15 9.2-6.6 1.3 3.9 7.4-2.7 1.4-3.9-7.4L4 19z" fill="#0b0c10" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(el.style, {position: 'fixed', left: 0, top: 0, zIndex: 2147483647, pointerEvents: 'none',
      transform: `translate(${x / Z}px,${y / Z}px)`, transition: 'transform .6s cubic-bezier(.3,.7,.2,1)', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.3))'});
    document.documentElement.appendChild(el); return el;
  };
  window.__cur = {
    place(nx, ny) { x = nx; y = ny; const e = ensure(); e.style.transition = 'none'; e.style.transform = `translate(${x / Z}px,${y / Z}px)`; },
    move(nx, ny, ms) { x = nx; y = ny; const e = ensure(); e.style.transition = `transform ${ms}ms cubic-bezier(.3,.7,.2,1)`; e.style.transform = `translate(${x / Z}px,${y / Z}px)`; },
    click() {
      const r = document.createElement('div');
      Object.assign(r.style, {position: 'fixed', left: (x / Z - 22) + 'px', top: (y / Z - 22) + 'px', width: '44px', height: '44px', borderRadius: '50%',
        background: 'rgba(215,25,31,.28)', border: '2px solid rgba(215,25,31,.55)', zIndex: 2147483646, pointerEvents: 'none',
        transform: 'scale(.2)', opacity: 1, transition: 'transform .45s ease-out, opacity .5s ease-out'});
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(1.3)'; r.style.opacity = 0; });
      setTimeout(() => r.remove(), 600);
      const e = ensure(); e.style.transition = 'transform .08s'; e.style.transform = `translate(${x / Z}px,${y / Z}px) scale(.85)`;
      setTimeout(() => { e.style.transform = `translate(${x / Z}px,${y / Z}px)`; }, 110);
    },
  };
})();
"""

SOFT_BG = "#f4f5f7"  # flat: gradients band badly once JPEG-compressed
CSS_L = f"""
.top, .explain {{ display: none !important; }}
html, body {{ background: {SOFT_BG} !important; border: 0 !important; overflow: hidden; }}
.wrap {{ padding: 70px 0 !important; max-width: none !important; }}
.grid.g2 {{ grid-template-columns: 1fr !important; }}
.phone {{ width: 450px; max-width: 450px !important; height: 940px; min-height: 0 !important; }}
"""
CSS_R = f"""
.top, .lead {{ display: none !important; }}
html, body {{ background: {SOFT_BG} !important; border: 0 !important; }}
body {{ padding: 52px 0; }}
.wrap {{ max-width: 860px !important; background: #fff; border-radius: 32px; border: 1px solid #e8eaee; padding: 40px 44px 48px !important;
  box-shadow: 0 2px 6px rgba(11,12,16,.05), 0 30px 80px -24px rgba(11,12,16,.22); min-height: 1016px; }}
html {{ scroll-behavior: smooth; }}
"""


class Recorder:
    """Event log + per-stream JPEG frames, all stamped with wall-clock time."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.frames: dict[str, list[tuple[float, str]]] = {}

    def ev(self, kind: str, **data) -> None:
        self.events.append({"t": time.time(), "k": kind, **data})
        print(f"{time.strftime('%H:%M:%S')} {kind} {json.dumps(data, ensure_ascii=False)[:110]}")

    def save(self) -> None:
        (WORK / "events.json").write_text(json.dumps({"events": self.events, "frames": self.frames}, ensure_ascii=False), encoding="utf-8")


class Cam:
    """Screencast one page (switchable) into numbered JPEGs."""

    def __init__(self, rec: Recorder, name: str, size: tuple[int, int, float]) -> None:
        self.rec, self.name, self.size = rec, name, size
        self.dir = WORK / name
        self.dir.mkdir(parents=True, exist_ok=True)
        for f in self.dir.glob("*.jpg"):
            f.unlink()
        rec.frames[name] = []
        self.cdp = None
        self.n = 0

    async def attach(self, page) -> None:
        if self.cdp:
            try:
                await self.cdp.send("Page.stopScreencast")
                await self.cdp.detach()
            except Exception:
                pass
        await page.bring_to_front()
        self.cdp = cdp = await page.context.new_cdp_session(page)

        def on_frame(p):
            asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]}))
            fn = f"{self.n:06d}.jpg"
            self.n += 1
            (self.dir / fn).write_bytes(base64.b64decode(p["data"]))
            self.rec.frames[self.name].append((time.time(), fn))

        cdp.on("Page.screencastFrame", on_frame)
        w, h, _ = self.size
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 86, "maxWidth": w, "maxHeight": h})

    async def stop(self) -> None:
        if self.cdp:
            try:
                await self.cdp.send("Page.stopScreencast")
            except Exception:
                pass


class Hand:
    """A person's hand on one page: moves the visible cursor, then clicks or types."""

    def __init__(self, rec: Recorder, pane: str, page, start=(480, 900), z=1.0) -> None:
        self.rec, self.pane, self.page, self.z = rec, pane, page, z
        self.pos = (start[0] * z, start[1] * z)  # start is in page CSS px, pos in viewport px

    async def place(self, x=None, y=None) -> None:
        if x is not None:
            self.pos = (x, y)
        await self.page.evaluate(f"__cur.place({self.pos[0]}, {self.pos[1]})")

    async def to(self, sel: str, frame=None, dx=0.5, dy=0.5, hover=0.25) -> None:
        loc = (frame or self.page).locator(sel).first
        await loc.scroll_into_view_if_needed()
        b = await loc.bounding_box()
        x, y = b["x"] + b["width"] * dx, b["y"] + b["height"] * dy
        ms = int(min(1100, max(420, 380 + 0.55 * math.dist(self.pos, (x, y)) / self.z)))
        await self.page.evaluate(f"__cur.move({x:.1f}, {y:.1f}, {ms})")
        self.pos = (x, y)
        await asyncio.sleep(ms / 1000 + hover)

    async def click(self, sel: str, frame=None, dom=False, **kw) -> None:
        await self.to(sel, frame, **kw)
        await self.page.evaluate("__cur.click()")
        self.rec.ev("click", pane=self.pane)
        loc = (frame or self.page).locator(sel).first
        if dom:  # pulsing elements never look "stable" to Playwright
            await loc.evaluate("e => e.click()")
        else:
            await loc.click()

    async def type(self, sel: str, text: str) -> None:
        await self.click(sel)
        for ch in text:
            await self.page.keyboard.type(ch)
            await asyncio.sleep(0.045 if ch != " " else 0.09)


async def rect(page, sel: str, z: float, pad=0) -> list[float]:
    """Element box in frame pixels (= viewport px), padded by pad CSS px."""
    b = await page.locator(sel).first.bounding_box()
    pad *= z
    return [b["x"] - pad, b["y"] - pad, b["width"] + 2 * pad, b["height"] + 2 * pad]


AGENT_LEN = "[...document.querySelectorAll('.bub.agent')].reduce((n, b) => n + b.textContent.length, 0)"


async def agent_turn(page, before: int, timeout=45.0, quiet=1.0) -> None:
    """Wait for new agent speech to start, then for it to finish playing (never raises)."""
    try:
        await page.wait_for_function(f"{AGENT_LEN} > {before} || queuedSamples > 0", timeout=timeout * 1000)
    except Exception:
        print("  (no agent reply within", timeout, "s)")
        return
    last_busy, end = time.time(), time.time() + timeout
    while time.time() < end:
        if await page.evaluate("queuedSamples > 0 || !!live.agent"):
            last_busy = time.time()
        elif time.time() - last_busy >= quiet:
            return
        await asyncio.sleep(0.15)


async def n_agent(page) -> int:
    return await page.evaluate(AGENT_LEN)


# ---------------------------------------------------------------------------------------------- record

async def record(base: str, code: str) -> None:
    import httpx
    from playwright.async_api import async_playwright

    WORK.mkdir(parents=True, exist_ok=True)
    make_clips()
    async with httpx.AsyncClient(base_url=base, headers={"x-access-code": code}, timeout=90) as http:
        (await http.post("/api/reset")).raise_for_status()

    rec = Recorder()
    ev = rec.ev
    sL, sD = SIDE[2], DESK[2]
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True, args=["--autoplay-policy=no-user-gesture-required"])

        async def context(size):
            c = await browser.new_context(viewport={"width": size[0], "height": size[1]}, permissions=["microphone"])
            await c.add_init_script(f"window.__Z = {size[2]};")
            await c.add_init_script(INIT)

            async def clip(route):
                await route.fulfill(path=str(WORK / route.request.url.rsplit("/", 1)[1]), content_type="audio/wav")
            await c.route("**/__clip/*", clip)
            return c

        cL, cR, cW = await context(SIDE), await context(SIDE), await context(DESK)
        L, R, W = await cL.new_page(), await cR.new_page(), await cW.new_page()
        camL, camR, camW = Cam(rec, "L", SIDE), Cam(rec, "R", SIDE), Cam(rec, "W", DESK)
        hL, hR, hW = Hand(rec, "L", L, (700, 980), sL), Hand(rec, "R", R, (820, 900), sL), Hand(rec, "W", W, (1500, 760), sD)

        # Customer and owner devices load in the background while the desktop intro plays.
        await L.goto(f"{base}/call?code={code}")
        await L.add_style_tag(content=CSS_L)
        await R.goto(f"{base}/owner?code={code}")
        await R.add_style_tag(content=CSS_R)
        await L.wait_for_selector("#callBtn")
        await R.wait_for_selector(".empty")

        # ---- intro: a person opens Jawab in a desktop browser and goes to the live demo
        await W.goto(f"{base}/?code={code}")
        await W.wait_for_load_state("networkidle")
        await camW.attach(W)
        await hW.place()
        ev("rec_start")
        ev("chrome", tabs=["Jawab · the voice agent that learns"], active=0, url="jawab-uwp55gclza-uc.a.run.app")
        ev("layout", name="W")
        ev("cap", text="Jawab answers a small business’s phone in Hindi and English, and learns what it doesn’t know.")
        await asyncio.sleep(3.2)
        await hW.click("a.btn.big[href='/try']")
        await W.wait_for_selector(".stage")
        await asyncio.sleep(0.5)
        ev("chrome", tabs=["Try it live · Jawab"], active=0, url="jawab-uwp55gclza-uc.a.run.app/try")
        ev("cap", text="The live demo: the customer’s phone and the owner’s app, side by side.")
        await asyncio.sleep(2.2)
        await hW.to(".gstep[data-s='1'] b")
        ev("layout", name="zoom", pane="W", rect=await rect(W, ".rail", sD, 16))
        await asyncio.sleep(3.2)
        ev("layout", name="W")
        await asyncio.sleep(1.4)
        ev("cap", text="Let’s run it for real, on two devices: a customer and the bakery owner.")
        await asyncio.sleep(2.4)

        # ---- 1. the customer calls; a known question is answered from confirmed facts
        await camL.attach(L)
        await camR.attach(R)
        await hL.place()
        await hR.place()
        await asyncio.sleep(0.4)
        ev("layout", name="split")
        await camW.stop()
        ev("cap", text="A customer calls Sweet Crumbs Bakery in Indore", step=1)
        await asyncio.sleep(1.6)
        await hL.click("#callBtn")
        await L.wait_for_function("document.getElementById('status').textContent.startsWith('On call')", timeout=30000)
        t_rec = await L.evaluate("""() => {
            const dest = playCtx.createMediaStreamDestination(); player.connect(dest);
            playCtx.createMediaStreamSource(micStream).connect(dest);
            window.__chunks = []; const r = new MediaRecorder(dest.stream, {mimeType: 'audio/webm;codecs=opus'});
            r.ondataavailable = (e) => e.data.size && window.__chunks.push(e.data); r.start(500); window.__rec = r;
            return performance.timeOrigin + performance.now(); }""") / 1000
        ev("call_audio", t0=t_rec)
        cut0 = time.time()
        await agent_turn(L, 0, timeout=40)  # Jawab greets on pickup
        ev("cut", a=cut0 + 0.8, b=time.time() - 3.5)  # trim the connection wait, keep the end of the greeting
        await hL.to("#orb", dx=0.5, dy=0.85, hover=0)
        n = await n_agent(L)
        ev("say", who="c1")
        await asyncio.sleep(await L.evaluate("__say('/__clip/c1.wav')"))
        ev("cap", text="A question it knows: answered from the owner’s confirmed facts", step=1)
        await agent_turn(L, n)
        ev("layout", name="zoom", pane="L", rect=await rect(L, ".feed", sL, 24))
        await asyncio.sleep(3.4)
        ev("layout", name="split")
        await asyncio.sleep(0.8)

        # ---- 2. a new question: no guess, callback consent, gap filed to the owner
        n = await n_agent(L)
        ev("say", who="c2")
        ev("cap", text="Something new: Sunday delivery to Palasia?", step=2)
        await asyncio.sleep(await L.evaluate("__say('/__clip/c2.wav')"))
        await agent_turn(L, n)
        ev("cap", text="Jawab doesn’t guess. It offers to check with the owner.", step=2)
        ev("layout", name="zoom", pane="L", rect=await rect(L, ".feed", sL, 24))
        await asyncio.sleep(2.6)
        n = await n_agent(L)
        ev("say", who="c3")
        await asyncio.sleep(await L.evaluate("__say('/__clip/c3.wav')"))
        await R.wait_for_selector(".gap", timeout=40000)
        ev("layout", name="split")
        ev("cap", text="The owner gets the exact question, in the caller’s own words", step=2)
        await agent_turn(L, n, timeout=20)
        await asyncio.sleep(0.6)
        ev("layout", name="zoom", pane="R", rect=await rect(R, ".gap", sL, 20))
        await asyncio.sleep(3.6)
        ev("layout", name="split")
        await asyncio.sleep(0.9)

        # ---- 3. the owner answers with a voice note
        ev("cap", text="Ritu ji taps the mic and answers in Hindi", step=3)
        await hR.click(".rec", dom=True)
        ev("say", who="o1")
        dur = await R.evaluate("__say('/__clip/o1.wav')")
        await asyncio.sleep(0.5)
        ev("layout", name="zoom", pane="R", rect=await rect(R, ".gap", sL, 20))
        await asyncio.sleep(dur - 0.2)
        await hR.click(".rec", dom=True)
        ev("cap", text="Gemini turns the voice note into an English + Hindi FAQ draft", step=3)
        await asyncio.sleep(0.8)
        ev("layout", name="split")
        await R.wait_for_selector("[data-confirm]", timeout=45000)
        await asyncio.sleep(0.6)
        await R.evaluate("document.querySelector('.draft').scrollIntoView({block: 'start', behavior: 'smooth'})")
        await asyncio.sleep(1.2)
        ev("layout", name="zoom", pane="R", rect=await rect(R, ".draft", sL, 16))
        await asyncio.sleep(4.2)
        ev("layout", name="split")
        await asyncio.sleep(0.8)

        # ---- 4. confirm: the waiting caller hears it on the same call
        ev("cap", text="One tap to confirm, with “put it on my website” on", step=4)
        await R.evaluate("document.querySelector('[data-confirm]').scrollIntoView({block: 'center', behavior: 'smooth'})")
        await asyncio.sleep(1.0)
        await hR.to("label.toggle i", hover=0.5)
        n = await n_agent(L)
        await hR.click("[data-confirm]")
        await L.wait_for_selector(".bub.ok", timeout=30000)
        ev("cap", text="The customer, still on the line, hears the answer about a second later", step=4)
        ev("layout", name="zoom", pane="L", rect=await rect(L, ".feed", sL, 24))
        await agent_turn(L, n, timeout=40)
        ev("layout", name="split")
        await asyncio.sleep(0.6)
        ev("say", who="c4")
        n = await n_agent(L)
        await asyncio.sleep(await L.evaluate("__say('/__clip/c4.wav')"))
        try:
            await L.wait_for_function("/ended/.test(document.getElementById('status').textContent)", timeout=22000)
            ev("cap", text="Jawab says goodbye and hangs up. The next caller gets this answer instantly.")
        except Exception:
            await hL.click("#endBtn")
            ev("cap", text="Call done. The next caller gets this answer instantly.")
        await asyncio.sleep(3.0)

        blob = await L.evaluate("""() => new Promise((res) => { if (!window.__rec || window.__rec.state === 'inactive') {
              const b = new Blob(window.__chunks || [], {type: 'audio/webm'}); b.arrayBuffer().then((buf) => { let s = ''; const u = new Uint8Array(buf);
              for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); res(btoa(s)); }); return; }
            window.__rec.onstop = async () => { const b = new Blob(window.__chunks, {type: 'audio/webm'}); const buf = await b.arrayBuffer();
              let s = ''; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); res(btoa(s)); };
            window.__rec.stop(); })""")
        (WORK / "call-audio.webm").write_bytes(base64.b64decode(blob))

        # ---- 5. on the web: the person opens the bakery website in a new tab
        await camW.attach(W)
        await asyncio.sleep(0.3)
        ev("layout", name="W")
        await camL.stop()
        await camR.stop()
        ev("cap", text="Meanwhile, the guided demo has followed every step of the loop", step=5)
        await asyncio.sleep(2.6)
        S = await cW.new_page()
        ev("chrome", tabs=["Try it live · Jawab", "New tab"], active=1, url="")
        await asyncio.sleep(0.5)
        ev("chrome", tabs=["Try it live · Jawab", "New tab"], active=1, url="jawab-uwp55gclza-uc.a.run.app/site", typing=1.1)
        await asyncio.sleep(1.4)
        await S.goto(f"{base}/site")
        await S.wait_for_selector(".new", timeout=30000)
        hS = Hand(rec, "W", S, (1400, 700), sD)
        await camW.attach(S)
        await hS.place()
        ev("chrome", tabs=["Try it live · Jawab", "Sweet Crumbs Bakery"], active=1, url="jawab-uwp55gclza-uc.a.run.app/site")
        ev("cap", text="Published on the bakery’s own website, in English and Hindi", step=5)
        await asyncio.sleep(1.0)
        await S.evaluate("document.querySelector('.new').closest('details').scrollIntoView({block: 'center', behavior: 'smooth'})")
        await asyncio.sleep(1.4)
        await hS.to(".new", hover=0.2)
        ev("layout", name="zoom", pane="W", rect=await rect(S, "details:has(.new)", sD, 30))
        ev("cap", text="…with FAQPage structured data, so Google and AI assistants can read it", step=5)
        await asyncio.sleep(4.2)
        ev("layout", name="W")
        await asyncio.sleep(1.0)

        # ---- 6. verified, then back to the guided demo
        D = await cW.new_page()
        ev("chrome", tabs=["Try it live · Jawab", "Sweet Crumbs Bakery", "New tab"], active=2, url="")
        await asyncio.sleep(0.4)
        ev("chrome", tabs=["Try it live · Jawab", "Sweet Crumbs Bakery", "New tab"], active=2,
           url="jawab-uwp55gclza-uc.a.run.app/dashboard", typing=1.0)
        await asyncio.sleep(1.3)
        await D.goto(f"{base}/dashboard?code={code}")
        cut0 = time.time()
        await D.wait_for_function("[...document.querySelectorAll('.step.done')].some(s => s.textContent.includes('Verified'))", timeout=90000)
        if time.time() - cut0 > 2:
            ev("cut", a=cut0 + 0.3, b=time.time() - 0.2)
        await camW.attach(D)
        hD = Hand(rec, "W", D, (1400, 700), sD)
        await hD.place()
        ev("chrome", tabs=["Try it live · Jawab", "Sweet Crumbs Bakery", "Answer Loop · Jawab"], active=2,
           url="jawab-uwp55gclza-uc.a.run.app/dashboard")
        ev("cap", text="Verified: Jawab re-fetched the live page and found the answer", step=6)
        await D.evaluate("document.querySelector('#gaps').scrollIntoView({block: 'start', behavior: 'smooth'})")
        await asyncio.sleep(1.2)
        await hD.to(".steps", dx=0.9)
        ev("layout", name="zoom", pane="W", rect=await rect(D, "#gaps .gap", sD, 12))
        await asyncio.sleep(4.4)
        ev("layout", name="W")
        await asyncio.sleep(1.0)
        await W.bring_to_front()
        await camW.attach(W)
        await hW.place()
        ev("chrome", tabs=["Try it live · Jawab", "Sweet Crumbs Bakery", "Answer Loop · Jawab"], active=0,
           url="jawab-uwp55gclza-uc.a.run.app/try")
        await W.wait_for_selector("#doneBanner:not(.hidden)", timeout=30000)
        ev("cap", text="Loop closed: one spoken answer fixed the phone line, the website and AI search.")
        await asyncio.sleep(1.0)
        await hW.to("#doneBanner .btn.light", hover=0.2)
        ev("layout", name="zoom", pane="W", rect=await rect(W, ".rail", sD, 16))
        await asyncio.sleep(4.2)
        ev("layout", name="W")
        await asyncio.sleep(1.6)
        ev("rec_end")
        await camW.stop()
        rec.save()
        await browser.close()
    print("recorded:", {k: len(v) for k, v in rec.frames.items()})


# ---------------------------------------------------------------------------------------------- compose

def ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 4 * p * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def lerp(a, b, p):
    return [x + (y - x) * p for x, y in zip(a, b, strict=True)]


def cover(sw, sh, dw, dh):
    """Source crop (x, y, w, h) that fills a dw x dh box without distortion, centred."""
    if sw / sh > dw / dh:
        w = sh * dw / dh
        return [(sw - w) / 2, 0, w, sh]
    h = sw * dh / dw
    return [0, (sh - h) / 2, sw, h]


def zoom_crop(r, sw, sh, max_zoom=2.2):
    """A 16:9 crop around rect r, at most max_zoom x (relative to fitting the whole width), inside the source."""
    x, y, w, h = r
    cw = max(w * 1.08, h * 1.08 * 16 / 9, sw / max_zoom)
    cw = min(cw, sw, sh * 16 / 9)
    ch = cw * 9 / 16
    cx, cy = x + w / 2, y + h / 2
    return [min(max(cx - cw / 2, 0), sw - cw), min(max(cy - ch / 2, 0), sh - ch), cw, ch]


def compose() -> None:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    data = json.loads((WORK / "events.json").read_text(encoding="utf-8"))
    events, frames = data["events"], data["frames"]
    T_start = next(e["t"] for e in events if e["k"] == "rec_start")
    T_end = next(e["t"] for e in events if e["k"] == "rec_end")
    cuts = sorted((e["a"], e["b"]) for e in events if e["k"] == "cut" and e["b"] > e["a"])

    # output time -> wall time, skipping the cuts
    segs, t = [], T_start
    for a, b in cuts:
        if a > t:
            segs.append((t, a))
        t = max(t, b)
    segs.append((t, T_end))
    seg_out = [0.0]
    for a, b in segs:
        seg_out.append(seg_out[-1] + (b - a))
    total = seg_out[-1]

    def wall(to):
        i = min(bisect.bisect_right(seg_out, to) - 1, len(segs) - 1)
        return segs[i][0] + (to - seg_out[i])

    # stream sizes
    size = {}
    for name, fl in frames.items():
        if fl:
            im = cv2.imread(str(WORK / name / fl[0][1]))
            size[name] = (im.shape[1], im.shape[0])
    times = {n: [f[0] for f in fl] for n, fl in frames.items()}

    # pane layouts
    M, G, TOP = 36, 28, 104  # TOP leaves a band for the caption above the two devices
    pw = (VW - 2 * M - G) / 2
    split_dst = {"L": [M, TOP, pw, VH - TOP - M], "R": [M + pw + G, TOP, pw, VH - TOP - M]}
    W_dst = [0, CHROME_H, VW, VH - CHROME_H]

    def state_for(e, prev):
        st = {k: (dict(v) if isinstance(v, dict) else v) for k, v in prev.items()}
        name = e["name"]
        if name == "W":
            st["W"].update(dst=W_dst, src=[0, 0, *size["W"]], op=1, rad=0)
            st["chrome"] = 1
            for k in "LR":
                st[k]["op"] = 0
        elif name == "split":
            st["W"]["op"] = 0
            st["chrome"] = 0
            for k in "LR":
                sw, sh = size[k]
                d = split_dst[k]
                st[k].update(dst=d, src=cover(sw, sh, d[2], d[3]), op=1, rad=28)
        elif name == "zoom":
            k = e["pane"]
            sw, sh = size[k]
            st[k].update(dst=[0, 0, VW, VH], src=zoom_crop(e["rect"], sw, sh, 2.2 if k != "W" else 1.9), op=1, rad=0)
            if k == "W":
                st["chrome"] = 0
            else:
                o = "R" if k == "L" else "L"
                d = list(split_dst[o])
                d[0] += VW if o == "R" else -VW
                st[o].update(dst=d, op=1)
        return st

    # hidden panes wait at their split position so they fade in place instead of flying in
    st0 = {"chrome": 0}
    for k in "LR":
        sw, sh = size.get(k, (1920, 2160))
        st0[k] = {"dst": list(split_dst[k]), "src": cover(sw, sh, split_dst[k][2], split_dst[k][3]), "op": 0, "rad": 28}
    st0["W"] = {"dst": list(W_dst), "src": [0, 0, *size["W"]], "op": 0, "rad": 0}
    lays = [e for e in events if e["k"] == "layout"]
    TR = 0.8
    keyed = []  # (start time, from state, to state)
    for e in lays:
        if not keyed:
            first = state_for(e, st0)
            keyed.append((e["t"] - TR, first, first))
            continue
        t0, a, b = keyed[-1]
        keyed.append((e["t"], mix(a, b, ease((e["t"] - t0) / TR)), state_for(e, b)))

    def at(tw):
        i = bisect.bisect_right([k[0] for k in keyed], tw) - 1
        if i < 0:
            return keyed[0][2]
        t0, a, b = keyed[i]
        return mix(a, b, ease((tw - t0) / TR))

    # captions
    caps = [e for e in events if e["k"] == "cap"]
    def fig(size, weight="SemiBold"):  # Figtree, the Jawab typeface (DESIGN.md)
        f = ImageFont.truetype(str(Path(__file__).parent / "fonts" / "Figtree.ttf"), size)
        f.set_variation_by_name(weight)
        return f

    f_cap = fig(30)
    f_step = fig(22, "Bold")
    f_tab = fig(19, "Medium")
    f_url = fig(20, "Regular")

    @functools.lru_cache(maxsize=64)
    def caption_img(text, step):
        pad_x, h = 30, 66
        tw = int(f_cap.getlength(text))
        chip = 44 if step else 0
        w = tw + 2 * pad_x + (chip + 14 if step else 0)
        im = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([20, 26, 20 + w, 26 + h], 33, fill=(0, 0, 0, 90))
        sh = Image.fromarray(cv2.GaussianBlur(np.array(sh), (0, 0), 9))
        im.alpha_composite(sh)
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([20, 20, 20 + w, 20 + h], 33, fill=(11, 12, 16, 238))
        x = 20 + pad_x - (8 if step else 0)
        if step:
            d.ellipse([x - 8, 20 + 11, x - 8 + chip, 20 + 11 + chip], fill=(215, 25, 31))
            d.text((x - 8 + chip / 2, 20 + 11 + chip / 2), str(step), font=f_step, fill="white", anchor="mm")
            x += chip + 6
        d.text((x, 20 + h / 2), text, font=f_cap, fill=(255, 255, 255), anchor="lm")
        return np.array(im)

    def cap_at(tw):
        cur, prev_t = None, None
        for e in caps:
            if e["t"] <= tw:
                cur, prev_t = e, e["t"]
        if not cur or not cur.get("text"):
            return None, 0
        return cur, min(1.0, (tw - prev_t) / 0.35)

    # browser chrome
    chrome_ev = [e for e in events if e["k"] == "chrome"]

    def chrome_img(tw):
        e = None
        for c in chrome_ev:
            if c["t"] <= tw:
                e = c
        if not e:
            return None
        url = e["url"]
        if e.get("typing"):
            p = min(1.0, (tw - e["t"]) / e["typing"])
            url = url[: int(len(url) * p)]
        return chrome_cached(tuple(e["tabs"]), e["active"], url, int(tw * 2) % 2 == 0 and bool(e.get("typing")) and len(url) < len(e["url"]))

    @functools.lru_cache(maxsize=256)
    def chrome_cached(tabs, active, url, caret):
        im = Image.new("RGB", (VW, CHROME_H), (232, 234, 238))
        d = ImageDraw.Draw(im)
        for i, c in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
            d.ellipse([22 + i * 24, 16, 36 + i * 24, 30], fill=c)
        x = 110
        for i, t in enumerate(tabs):
            w = 260
            if i == active:
                d.rounded_rectangle([x, 8, x + w, 52], 12, fill=(255, 255, 255))
            dot = (215, 25, 31) if "Jawab" in t else (226, 98, 27) if "Bakery" in t else (160, 165, 175)
            d.ellipse([x + 16, 22, x + 32, 38], fill=dot)
            label = t if f_tab.getlength(t) < w - 70 else t[: max(4, int(len(t) * (w - 80) / f_tab.getlength(t)))] + "…"
            d.text((x + 42, 30), label, font=f_tab, fill=(30, 32, 38) if i == active else (95, 100, 110), anchor="lm")
            d.text((x + w - 22, 30), "×", font=f_tab, fill=(140, 145, 155), anchor="mm")
            x += w + 6
        d.text((x + 18, 30), "+", font=f_url, fill=(95, 100, 110), anchor="mm")
        d.rectangle([0, 48, VW, CHROME_H], fill=(255, 255, 255))
        for i, g in enumerate(["‹", "›", "↻"]):
            d.text((34 + i * 40, 68), g, font=f_url, fill=(110, 115, 125), anchor="mm")
        d.rounded_rectangle([150, 54, VW - 150, 82], 14, fill=(241, 243, 246))
        if url:
            d.rounded_rectangle([176, 63, 188, 74], 2, fill=(110, 115, 125))
            d.arc([178, 56, 186, 68], 180, 360, fill=(110, 115, 125), width=2)
        tx = 200 if url else 178
        d.text((tx, 68), url or "Search or type a URL", font=f_url, fill=(30, 32, 38) if url else (140, 145, 155), anchor="lm")
        if caret:
            cx = tx + f_url.getlength(url) + 2
            d.line([cx, 58, cx, 78], fill=(30, 32, 38), width=2)
        d.line([0, CHROME_H - 1, VW, CHROME_H - 1], fill=(225, 228, 233))
        return np.array(im)[:, :, ::-1].copy()

    # background
    yy, xx = np.mgrid[0:VH, 0:VW].astype(np.float32)
    bg = np.ones((VH, VW, 3), np.float32) * np.array([252, 251, 251], np.float32)
    for cx, cy, r, col, a in [(0.12, -0.1, 0.55, (31, 25, 215), 0.10), (0.9, 0.0, 0.5, (60, 150, 255), 0.12)]:
        dd = np.sqrt(((xx / VW - cx) / r) ** 2 + ((yy / VH - cy) / (r * 1.2)) ** 2)
        k = np.clip(1 - dd, 0, 1)[..., None] ** 2 * a
        bg = bg * (1 - k) + np.array(col, np.float32) * k
    bg = bg.astype(np.uint8)

    @functools.lru_cache(maxsize=48)
    def rmask(w, h, r):
        m = np.zeros((h, w), np.uint8)
        r = int(min(r, w // 2, h // 2))
        if r <= 0:
            m[:] = 255
            return m
        cv2.rectangle(m, (r, 0), (w - r - 1, h - 1), 255, -1)
        cv2.rectangle(m, (0, r), (w - 1, h - r - 1), 255, -1)
        for cx, cy in [(r, r), (w - r - 1, r), (r, h - r - 1), (w - r - 1, h - r - 1)]:
            cv2.circle(m, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
        return m

    @functools.lru_cache(maxsize=8)
    def shadow(w, h):
        pad = 60
        m = np.zeros((h + 2 * pad, w + 2 * pad), np.uint8)
        cv2.rectangle(m, (pad, pad + 16), (pad + w, pad + h + 16), 255, -1)
        return cv2.GaussianBlur(m, (0, 0), 24).astype(np.float32) / 255 * 0.22, pad

    bg_split = bg.copy()
    for k in "LR":
        d = [int(round(v)) for v in split_dst[k]]
        sm, pad = shadow(d[2], d[3])
        reg = bg_split[max(d[1] - pad, 0):d[1] - pad + sm.shape[0], max(d[0] - pad, 0):d[0] - pad + sm.shape[1]]
        sub = sm[max(pad - d[1], 0):max(pad - d[1], 0) + reg.shape[0], max(pad - d[0], 0):max(pad - d[0], 0) + reg.shape[1]]
        reg[:] = (reg * (1 - sub[..., None])).astype(np.uint8)
    split_int = {k: [int(round(v)) for v in split_dst[k]] for k in "LR"}

    def in_split(st):
        return all(st[k]["op"] > 0.99 and [int(round(v)) for v in st[k]["dst"]] == split_int[k] for k in "LR")

    cache = {}

    def frame_of(name, tw):
        ts = times.get(name) or []
        i = bisect.bisect_right(ts, tw) - 1
        if i < 0:
            if not ts:
                return None
            i = 0
        if cache.get(name, (None,))[0] != i:
            cache[name] = (i, cv2.imread(str(WORK / name / frames[name][i][1])))
        return cache[name][1]

    def paste(canvas, img, src, dst, op, rad):
        if op <= 0.01 or img is None:
            return
        sx, sy, sw, sh = src
        sx, sy = max(0, sx), max(0, sy)
        sw, sh = min(sw, img.shape[1] - sx), min(sh, img.shape[0] - sy)
        dx, dy, dw, dh = [int(round(v)) for v in dst]
        if dw < 4 or dh < 4:
            return
        crop = img[int(sy):int(sy + sh), int(sx):int(sx + sw)]
        interp = cv2.INTER_AREA if dw < crop.shape[1] else cv2.INTER_CUBIC
        piece = cv2.resize(crop, (dw, dh), interpolation=interp)
        x0, y0, x1, y1 = max(dx, 0), max(dy, 0), min(dx + dw, VW), min(dy + dh, VH)
        if x1 <= x0 or y1 <= y0:
            return
        piece = piece[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
        reg = canvas[y0:y1, x0:x1]
        if op < 0.995:
            piece = cv2.addWeighted(piece, op, reg, 1 - op, 0)
        if rad > 0.5:
            cv2.copyTo(piece, rmask(dw, dh, rad)[y0 - dy:y1 - dy, x0 - dx:x1 - dx], reg)
        else:
            reg[:] = piece

    def overlay_rgba(canvas, rgba, x, y, op=1.0):
        h, w = rgba.shape[:2]
        x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, VW), min(y + h, VH)
        if x1 <= x0 or y1 <= y0:
            return
        part = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
        a = part[..., 3:4].astype(np.float32) / 255 * op
        reg = canvas[y0:y1, x0:x1]
        reg[:] = (part[..., 2::-1] * a + reg * (1 - a)).astype(np.uint8)

    # ---- audio: call recording + owner voice note + soft clicks, laid on the wall clock, then cut
    sr = 48000
    n_wall = int((T_end - T_start) * sr) + sr
    audio = np.zeros(n_wall, np.float32)

    def place(sig, t):
        i = int((t - T_start) * sr)
        if i < 0:
            sig, i = sig[-i:], 0
        j = min(n_wall, i + len(sig))
        audio[i:j] += sig[: j - i]

    def decode(path):
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.float32)

    ca = next((e for e in events if e["k"] == "call_audio"), None)
    if ca and (WORK / "call-audio.webm").exists():
        place(decode(WORK / "call-audio.webm"), ca["t0"])
    for e in events:
        if e["k"] == "say" and e["who"].startswith("o"):
            place(decode(WORK / f"{e['who']}.wav") * 0.95, e["t"])
        if e["k"] == "click":
            n = int(0.03 * sr)
            tick = (np.random.default_rng(1).standard_normal(n) * np.exp(-np.linspace(0, 9, n))).astype(np.float32) * 0.05
            place(tick, e["t"])
    kept = np.concatenate([audio[int((a - T_start) * sr):int((b - T_start) * sr)] for a, b in segs])
    peak = float(np.abs(kept).max() or 1)
    if peak > 0.95:
        kept = kept * (0.95 / peak)
    with wave.open(str(WORK / "mix.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((kept * 32767).astype(np.int16).tobytes())

    # ---- frames
    main_mp4 = WORK / "main.mp4"
    enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{VW}x{VH}", "-r", str(FPS),
                            "-i", "-", "-i", str(WORK / "mix.wav"), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-shortest", str(main_mp4)], stdin=subprocess.PIPE)
    n_frames = int(min(total, float(os.environ.get("DEMO_LIMIT", total))) * FPS)  # DEMO_LIMIT=s for a quick preview
    t_clock = time.time()
    for fi in range(n_frames):
        tw = wall(fi / FPS)
        st = at(tw)
        canvas = (bg_split if in_split(st) else bg).copy()
        order = sorted(["L", "R", "W"], key=lambda k: st[k]["dst"][2])
        for k in order:
            s = st[k]
            paste(canvas, frame_of(k, tw), s["src"], s["dst"], s["op"], s["rad"])
        if st["chrome"] > 0.01 and st["W"]["op"] > 0.01:
            ch = chrome_img(tw)
            if ch is not None:
                a = st["chrome"] * st["W"]["op"]
                y = int(-CHROME_H * (1 - st["chrome"]))
                reg = canvas[max(y, 0):y + CHROME_H]
                part = ch[CHROME_H - reg.shape[0]:]
                reg[:] = part if a > 0.995 else cv2.addWeighted(part, a, reg, 1 - a, 0)
        c, p = cap_at(tw)
        if c:
            ci = caption_img(c["text"], c.get("step"))
            y = 14 + (CHROME_H if st["chrome"] > 0.5 else 0) + int((1 - p) * -12)
            overlay_rgba(canvas, ci, (VW - ci.shape[1]) // 2, y, p)
        enc.stdin.write(canvas.tobytes())
        if fi % 300 == 0:
            print(f"frame {fi}/{n_frames}  {time.time() - t_clock:.0f}s", flush=True)
    enc.stdin.close()
    enc.wait()

    # ---- intro / outro cards and final cut
    def card(path, eyebrow, title, sub, foot):
        im = Image.fromarray(bg[:, :, ::-1])
        d = ImageDraw.Draw(im)
        fe, ft, fs, ff = fig(26), fig(104, "Bold"), fig(40, "Regular"), fig(28, "Medium")
        d.ellipse([160, 352, 174, 366], fill=(215, 25, 31))
        d.text((190, 359), eyebrow, font=fe, fill=(215, 25, 31), anchor="lm")
        d.text((156, 400), title, font=ft, fill=(11, 12, 16))
        d.text((160, 530), sub, font=fs, fill=(74, 79, 91))
        d.text((160, 640), foot, font=ff, fill=(134, 139, 150))
        im.save(path)

    card(WORK / "intro.png", "BUILD 2036 · FRONTIER 06 · INDORE MSMEs", "Jawab",
         "The voice agent that learns from every call it can’t answer.", "Live prototype · Gemini Live + ADK + Cloud Run")
    card(WORK / "outro.png", "TRY IT YOURSELF", "One answer, everywhere.", "Phone line · website · AI search — verified.",
         "jawab-uwp55gclza-uc.a.run.app/try   ·   github.com/jayanth-mkv/jawab")
    for name in ("intro", "outro"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", "3.5", "-i", str(WORK / f"{name}.png"),
                        "-f", "lavfi", "-t", "3.5", "-i", "anullsrc=r=48000:cl=mono",
                        "-vf", f"fps={FPS},format=yuv420p,fade=t=in:st=0:d=0.4,fade=t=out:st=3.1:d=0.4",
                        "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-shortest", str(WORK / f"{name}.mp4")], check=True)
    final = OUT / "jawab-demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(WORK / "intro.mp4"), "-i", str(main_mp4), "-i", str(WORK / "outro.mp4"),
                    "-filter_complex", "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(final)], check=True)
    print(f"saved {final} ({final.stat().st_size / 1e6:.1f} MB, {total + 7:.0f} s)")


def mix(a, b, p):
    out = {"chrome": a["chrome"] + (b["chrome"] - a["chrome"]) * p}
    for k in "LRW":
        out[k] = {key: lerp(a[k][key], b[k][key], p) if isinstance(a[k][key], list) else a[k][key] + (b[k][key] - a[k][key]) * p
                  for key in ("dst", "src", "op", "rad")}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--base", required=True)
    r.add_argument("--code", required=True)
    sub.add_parser("compose")
    a = ap.parse_args()
    if a.cmd == "record":
        asyncio.run(record(a.base, a.code))
    else:
        compose()


if __name__ == "__main__":
    main()
