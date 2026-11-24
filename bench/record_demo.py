"""Record the demo video of the Missed-Answer Loop against the deployed service.

Two real Edge browsers run side by side: the customer phone (left) and the owner app (right).
Each has a fake microphone that plays Gemini-TTS Hindi speech, so the Gemini Live call, the gap,
the owner's voice note, the mid-call answer, publishing and verification all happen for real.
Captions and zooms are injected into the pages; ffmpeg stitches video + real audio.

    uv run --with playwright python -m bench.record_demo --base https://... --code XXXX
Output: bench/out/demo/jawab-demo.mp4
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import subprocess
import time
import wave
from pathlib import Path

import httpx
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Page, async_playwright

from bench.e2e import tts_client

OUT = Path(__file__).parent / "out" / "demo"
W, H = 640, 720  # each half of a 1280x720 video

HELPERS = """
window.__cap = (text, where) => {
  let d = document.getElementById('__cap');
  if (!d) {
    d = document.createElement('div'); d.id = '__cap';
    Object.assign(d.style, {position: 'fixed', left: '14px', right: '14px', zIndex: 99999,
      background: 'rgba(11,30,63,.94)', color: '#fff', font: '600 17px "Segoe UI", system-ui, sans-serif',
      padding: '12px 16px', borderRadius: '12px', transition: 'opacity .45s', boxShadow: '0 10px 30px rgba(0,0,0,.35)',
      lineHeight: '1.35'});
    document.body.appendChild(d);
  }
  d.style.top = where === 'top' ? '66px' : 'auto'; d.style.bottom = where === 'top' ? 'auto' : '14px';
  d.innerHTML = text || ''; d.style.opacity = text ? 1 : 0;
};
window.__zoom = (sel, s) => {
  const root = document.querySelector('main') || document.body;
  root.style.transition = 'transform .9s cubic-bezier(.2,.7,.2,1)';
  if (!sel) { root.style.transform = 'none'; return; }
  const el = document.querySelector(sel); if (!el) return;
  root.style.transform = 'none';
  const r = el.getBoundingClientRect(), rr = root.getBoundingClientRect();
  root.style.transformOrigin = `${r.left + r.width / 2 - rr.left}px ${r.top + r.height / 2 - rr.top}px`;
  root.style.transform = `scale(${s})`;
};
"""


_TTS = None


def tts_pcm(text: str, voice: str) -> bytes:
    global _TTS
    _TTS = _TTS or tts_client()  # keep one client alive; a dropped client closes its HTTP session
    resp = _TTS.models.generate_content(
        model="gemini-3.8-flash-tts", contents=text,
        config=types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)))))
    return resp.candidates[0].content.parts[0].inline_data.data


def write_wav(path: Path, segments: list[bytes | float]) -> None:
    """Segments are 24 kHz PCM16 bytes or seconds of silence."""
    pcm = b"".join(s if isinstance(s, bytes) else b"\x00\x00" * int(24000 * s) for s in segments)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)


def card(path: Path, title: str, lines: list[str]) -> None:
    img = Image.new("RGB", (1280, 720), (11, 30, 63))
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 76)
    mid = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 34)
    d.text((90, 210), title, font=big, fill=(255, 255, 255))
    d.rectangle([90, 312, 210, 318], fill=(215, 25, 31))
    for i, line in enumerate(lines):
        d.text((90, 350 + i * 52), line, font=mid, fill=(214, 224, 240))
    img.save(path)


async def wait_text(page: Page, selector: str, timeout: float = 45000) -> None:
    await page.wait_for_selector(selector, timeout=timeout)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--code", required=True)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- speech for the two fake microphones (Gemini TTS)
    cust = OUT / "customer.wav"
    own = OUT / "owner.wav"
    if not cust.exists():
        write_wav(cust, [1.5, tts_pcm("Namaste! Eggless cake milta hai? Ek kilo kitne ka hai?", "Puck"), 13.0,
                         tts_pcm("Achha, aur Sunday ko Palasia mein delivery karte ho? Pata na ho to call back kar dena.", "Puck"),
                         9.0, tts_pcm("Haan ji, theek hai.", "Puck"), 150.0])
    if not own.exists():
        write_wav(own, [0.6, tts_pcm("Haan, Sunday ko Palasia delivery karte hain, gyarah baje se paanch baje tak, assi rupaye charge.", "Kore"), 1.0])
    owner_len = wave.open(str(own)).getnframes() / 24000

    async with httpx.AsyncClient(base_url=a.base, headers={"x-access-code": a.code}, timeout=90) as http:
        await http.post("/api/reset")

    async with async_playwright() as p:
        def launch(wav: Path):
            return p.chromium.launch(channel="msedge", headless=True, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav.resolve()}%noloop", "--autoplay-policy=no-user-gesture-required"])

        bl, br = await launch(cust), await launch(own)
        cl = await bl.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT / "left"),
                                  record_video_size={"width": W, "height": H}, permissions=["microphone"])
        cr = await br.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT / "right"),
                                  record_video_size={"width": W, "height": H}, permissions=["microphone"])
        tl0 = time.time()
        left = await cl.new_page()
        tr0 = time.time()
        right = await cr.new_page()
        for pg in (left, right):
            await pg.add_init_script(HELPERS)
        await left.goto(f"{a.base}/call?code={a.code}")
        await left.add_style_tag(content="main .grid > section:first-child{display:none} .wrap{padding-top:10px} .phone{min-height:600px}")
        await right.goto(f"{a.base}/owner?code={a.code}")
        await left.wait_for_selector("#callBtn")
        await right.wait_for_selector(".card")
        t_start = time.time()
        await left.evaluate("__cap('Customer: calls Sweet Crumbs Bakery, Indore', 'top')")
        await right.evaluate("__cap('Owner app: Ritu ji, busy at the counter')")
        await asyncio.sleep(2.5)

        # ---- 1. call; known question answered from confirmed facts
        await left.click("#callBtn")
        await left.wait_for_function("document.getElementById('status').textContent.startsWith('On call')", timeout=20000)
        t_rec = await left.evaluate("""() => {
            const dest = playCtx.createMediaStreamDestination(); player.connect(dest);
            playCtx.createMediaStreamSource(micStream).connect(dest);
            window.__chunks = []; const r = new MediaRecorder(dest.stream, {mimeType: 'audio/webm;codecs=opus'});
            r.ondataavailable = (e) => e.data.size && window.__chunks.push(e.data); r.start(500); window.__rec = r;
            return performance.timeOrigin + performance.now(); }""") / 1000
        await left.evaluate("__cap('Known question → answered in Hinglish from the owner’s confirmed facts', 'top')")
        await left.wait_for_selector(".bub.agent", timeout=30000)
        await left.evaluate("__zoom('.feed', 1.18)")

        # ---- 2. unknown question: no guess, gap filed
        await left.wait_for_selector(".bub.sys", timeout=60000)
        await left.evaluate("__cap('Unknown question → <b>it doesn’t guess</b>. Takes callback consent, files the gap.', 'top')")
        await right.wait_for_selector(".gap", timeout=20000)
        await right.evaluate("__cap('The exact question reaches the owner — with the caller’s own words')")
        await right.evaluate("__zoom('.gap', 1.25)")
        await asyncio.sleep(4)

        # ---- 3. owner answers by voice
        await right.evaluate("__zoom(null)")
        await asyncio.sleep(0.8)
        await right.evaluate("__cap('Owner taps the mic and answers in Hindi (10 seconds)')")
        t_owner = time.time()
        # The record button pulses while recording, so Playwright never sees it as "stable": click via the DOM.
        await right.evaluate("document.querySelector('.rec').click()")
        await asyncio.sleep(owner_len + 0.6)
        await right.evaluate("document.querySelector('.rec').click()")
        await right.evaluate("__cap('Gemini turns the voice note into a bilingual fact…')")
        await right.wait_for_selector("[data-confirm]", timeout=45000)
        await right.evaluate("__cap('Transcript + English + Hindi answer, ready to confirm')")
        await right.evaluate("__zoom('#g-' + document.querySelector('[data-confirm]').dataset.confirm + ' .kv', 1.35)")
        await asyncio.sleep(4.5)
        await right.evaluate("__zoom(null)")
        await asyncio.sleep(0.9)

        # ---- 4. confirm -> the waiting caller hears it on the same call
        await right.evaluate("document.querySelector('[data-confirm]').scrollIntoView({block: 'center'})")
        await right.evaluate("__cap('Owner confirms ✓')")
        await asyncio.sleep(1.2)
        await right.click("[data-confirm]")
        await left.evaluate("__cap('The waiting customer hears the answer <b>on the same call</b>', 'top')")
        await left.wait_for_selector(".bub.ok", timeout=30000)
        await left.evaluate("__zoom('.feed', 1.25)")
        await asyncio.sleep(9)
        await left.evaluate("__zoom(null)")

        # ---- 5. publish to the website
        await right.wait_for_selector("[data-publish]", timeout=20000)
        await right.evaluate("document.querySelector('[data-publish]').scrollIntoView({block: 'center'})")
        await right.evaluate("__cap('One tap: put the answer on the shop’s website')")
        await asyncio.sleep(1.5)
        await right.click("[data-publish]")
        await asyncio.sleep(2.5)
        await left.click("#endBtn")
        await left.evaluate("__cap('Call ended. Next caller with this question is answered instantly.', 'top')")
        await right.goto(f"{a.base}/site")
        await right.wait_for_selector(".new", timeout=20000)
        await right.evaluate("document.querySelector('.new').closest('details').scrollIntoView({block: 'center'})")
        await right.evaluate("__cap('Live on the website — in English and Hindi, with FAQPage structured data for Google and AI')")
        await asyncio.sleep(1)
        await right.evaluate("__zoom('.new', 1.0)")
        await right.evaluate("(() => { const d = document.querySelector('.new').closest('details'); d.id = d.id || 'newfact'; __zoom('#' + d.id, 1.3); })()")
        await asyncio.sleep(5)

        # ---- 6. verified
        await right.goto(f"{a.base}/dashboard?code={a.code}")
        await right.wait_for_function("[...document.querySelectorAll('.step.done')].some(s => s.textContent.includes('Verified'))", timeout=60000)
        await right.evaluate("document.querySelector('#gaps').scrollIntoView({block: 'start'})")
        await right.evaluate("__cap('Verified: live page re-checked. Call → web in about half a minute.')")
        await asyncio.sleep(1)
        await right.evaluate("__zoom('.steps', 1.35)")
        await asyncio.sleep(6)
        t_end = time.time()

        blob_b64 = await left.evaluate("""() => new Promise((res) => { window.__rec.onstop = async () => {
            const b = new Blob(window.__chunks, {type: 'audio/webm'}); const buf = await b.arrayBuffer();
            let s = ''; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]);
            res(btoa(s)); }; window.__rec.stop(); })""")
        (OUT / "call-audio.webm").write_bytes(base64.b64decode(blob_b64))
        lv, rv = left.video, right.video
        await cl.close()
        await cr.close()
        await bl.close()
        await br.close()
        lpath, rpath = await lv.path(), await rv.path()

    # ---- compose with ffmpeg
    T0 = t_start - 0.5
    dur = t_end - T0
    card(OUT / "intro.png", "Jawab", ["The voice agent that learns from every call it can’t answer.",
                                       "Build 2036 · Frontier 06 · Indore MSMEs", "Live prototype: Gemini 3.8 Live + ADK + Cloud Run"])
    card(OUT / "outro.png", "One answer, everywhere.", ["Phone · website · AI search — verified.",
                                                         "github.com/jayanth-mkv/jawab", "Team Jawab · Jayanth-MKV"])
    rec_delay = max(0, int((t_rec - T0) * 1000))
    own_delay = max(0, int((t_owner - T0) * 1000) + 300)
    main_mp4 = OUT / "main.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(lpath), "-i", str(rpath), "-i", str(OUT / "call-audio.webm"), "-i", str(own),
        "-filter_complex",
        f"[0:v]trim=start={T0 - tl0:.3f}:duration={dur:.3f},setpts=PTS-STARTPTS,fps=30[l];"
        f"[1:v]trim=start={T0 - tr0:.3f}:duration={dur:.3f},setpts=PTS-STARTPTS,fps=30[r];"
        f"[l][r]hstack=inputs=2,drawbox=x=638:y=0:w=4:h=ih:color=0x0B1E3F:t=fill[v];"
        f"[2:a]aresample=48000,adelay={rec_delay}|{rec_delay}[a1];"
        f"[3:a]aresample=48000,volume=0.9,adelay={own_delay}|{own_delay}[a2];"
        f"[a1][a2]amix=inputs=2:normalize=0,apad,atrim=duration={dur:.3f}[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k", str(main_mp4)], check=True)
    for name in ("intro", "outro"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", "4", "-i", str(OUT / f"{name}.png"),
                        "-f", "lavfi", "-t", "4", "-i", "anullsrc=r=48000:cl=stereo",
                        "-vf", "fps=30,format=yuv420p,fade=t=in:st=0:d=0.5,fade=t=out:st=3.5:d=0.5",
                        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(OUT / f"{name}.mp4")], check=True)
    final = OUT / "jawab-demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(OUT / "intro.mp4"), "-i", str(main_mp4), "-i", str(OUT / "outro.mp4"),
                    "-filter_complex", "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]",
                    "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-c:a", "aac",
                    "-movflags", "+faststart", str(final)], check=True)
    print(f"saved {final} ({final.stat().st_size / 1e6:.1f} MB, {dur + 8:.0f} s)")


if __name__ == "__main__":
    asyncio.run(main())
