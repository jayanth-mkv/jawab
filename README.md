# Jawab — the voice agent that learns from every call it can't answer

**Build 2036 · Frontier 06: The Rise of New India (MSME digitization)**

Jawab answers customer calls for small businesses in **Hindi and English** using **Gemini 3.8 Live**. It speaks
only from facts the owner has confirmed. When a caller asks something it doesn't know, it **does not guess**:

1. **Gap detected** — it takes callback consent and files the question.
2. **Owner speaks** — the owner answers with a ~10-second Hindi voice note; Gemini turns it into a structured, bilingual fact.
3. **Customer told** — if the caller is still on the line, Jawab tells them mid-call ("Ritu ji ne abhi confirm kiya hai…");
   if they hung up, their phone rings with a callback.
4. **Next caller answered instantly** — the fact is now part of what Jawab knows.
5. **Published** — after one tap, the answer goes on the business website as FAQ + `FAQPage` structured data.
6. **Verified** — Jawab re-fetches the live page to confirm it's visible and machine-readable, then probes
   Gemini with Google Search grounding to track when AI answers start citing it.

We call this the **Missed-Answer Loop**: one spoken answer from the owner fixes the phone line, the website and
AI search at once.

## Try it in 2 minutes

> **▶ Try it now (one click, code included):** https://jawab-uwp55gclza-uc.a.run.app/try?code=cbnav1efhd
>
> **Demo access code: `cbnav1efhd`**. The link above fills it in for you. If a page ever asks, type this code.

Use headphones so the agent doesn't hear itself.

The **Try it live** page (`/try`) puts the customer's phone and the owner app side by side on one screen, with a
4-step guide at the top that moves forward on its own as you go:

| Step | You do | What happens |
| --- | --- | --- |
| 1 · Call and ask something new | Tap 📞, then tap the ✨ **Sunday delivery to Palasia?** chip (or just say it) | Jawab answers known questions from confirmed facts. For this one it **doesn't guess**: it asks consent to call back and files a gap. |
| 2 · Answer as the owner | The question pops up in the owner app. Tap 🎙️ and speak, or **type** the answer | Gemini turns it into an English + Hindi FAQ draft in about 3 s. |
| 3 · Confirm | Check the draft, keep **"Also put this answer on my website"** ticked, tap Confirm | The waiting caller hears *"Ritu ji ne abhi confirm kiya hai…"* about a second later. |
| 4 · It's on the web | Nothing: it happens on its own | The FAQ is published with `FAQPage` JSON-LD and the live page is re-checked. A banner shows the total loop time and links to the bakery site and dashboard. |

Then call again and ask the same question: Jawab answers instantly. **↺ Start over** resets the demo.

### Every page

| Page | For | Notes |
| --- | --- | --- |
| `/` | Overview | The loop in six steps, a link to the guided demo and the video. |
| `/try` | **Judges and first-time visitors** | Guided, one-screen demo (both panes are the real `/call` and `/owner` pages in embed mode). |
| `/call` | The customer | Phone UI with live transcripts. **Suggested-question chips** work without a microphone (they start the call if needed). If the customer hangs up before the owner answers, the phone **rings back**. |
| `/owner` | The business owner | Open questions, a 🎙️ voice note **or typed answer** (for a noisy shop, or anyone who doesn't speak Hindi), editable EN/HI draft, one-tap confirm + publish. |
| `/dashboard` | Proof | Every gap with its five timed steps, page verification results and the AI-search probe. |
| `/site` | The public | The bakery's own website: FAQ + `FAQPage` structured data that Google and AI assistants read. |
| `/demo` | Anyone | The 95-second recorded walkthrough. |

### Design

The app uses the same visual language as our Build 2036 deck, so the demo, the deck and the video read as one product:
a white canvas, ink-black Arial headings, a **red (`#d7191f`) accent** (primary buttons, active step, the bottom bar),
soft-grey cards, and colored icon circles for each step. The bakery's public site (`/site`) keeps its own warm
bakery style on purpose, because it stands for the business's website, not Jawab's. All tokens live in
`web/static/style.css`; the layout works down to phone width.

### Access code

The public demo is protected by an access code (Secret Manager → Cloud Run env). Each page checks it as soon as it
loads and prompts if it is missing or wrong; `?code=…` on any link stores it. A call without a valid code is accepted
and then closed with WebSocket code `4401`, so the page can ask for the code instead of failing silently.

## Measured on the deployed service

| Metric (Cloud Run, us-central1, measured from India) | Result |
| --- | --- |
| Full loop: call → owner voice note → caller told → published → verified | **3/3 verified**, median **38 s** |
| Callers told the owner's answer on the same call | **3/3** |
| Owner voice note → structured bilingual fact (Gemini 3.5 Flash-Lite) | median **2.8 s** |
| Owner confirms → waiting caller starts hearing the answer | median **1.0 s** |
| Publish → live page verified (visible + FAQPage JSON-LD) | median **1.1 s** |
| Known questions (owner-confirmed) answered correctly | **10/10**, 0 false gaps |
| Unknown questions not guessed / gap filed | **10/10** / **10/10** |
| Time to first audio on a live call | median **0.95 s**, p90 **1.04 s** |

Reproduce with `bench/e2e.py` and `bench/grounding.py` (real Gemini Live calls through the public service); raw results in `bench/out/`.

## Architecture

```
Browser (customer phone / owner app / dashboard)
   │  WebSocket: PCM16 16 kHz up, 24 kHz down        SSE: live events
   ▼
Cloud Run (FastAPI, single instance, non-root container)
   ├── ADK Runner.run_live ──► Gemini 3.8 Live (Vertex AI, us-central1)
   │      tool: report_unanswered_question  ·  [OWNER_ANSWER] injection mid-call
   ├── Owner voice note ──► Gemini 3.5 Flash-Lite (structured output: transcript, EN/HI answer, FAQ question)
   ├── /site: public page with FAQ + FAQPage JSON-LD
   ├── Verifier: re-fetch live page · Gemini + Google Search grounding probe
   └── Firestore: facts ledger + gap timeline     Secret Manager: demo access code
```

Infrastructure is fully in Terraform (`infra/`): a dedicated project, only the needed APIs, a least-privilege runtime
service account (`aiplatform.user`, `datastore.user`, `logging.logWriter`, one secret accessor), Firestore,
Artifact Registry with a keep-last-5 cleanup policy, Cloud Run capped at one instance with a 6-minute per-call limit,
and an INR billing budget with alerts at 25/50/90/100%.

## Run it

```bash
uv sync
STORE=memory GOOGLE_CLOUD_PROJECT=<project> uv run uvicorn app.main:app --port 8080   # local, needs ADC with Vertex AI
uv run pytest                                                                          # offline tests

# Google Cloud
cd infra && cp ../infra/terraform.tfvars.example terraform.tfvars   # set billing_account
terraform init && terraform apply
GCLOUD_ACCOUNT=you@example.com ../scripts/deploy.sh                # Cloud Build -> Terraform rollout
#   or keep a named gcloud config: gcloud config configurations create jawab --no-activate, then
#   CLOUDSDK_ACTIVE_CONFIG_NAME=jawab ../scripts/deploy.sh          # uses that config's account
terraform output -raw access_code                                  # demo access code
```

Open `/try` (or `/call` and `/owner` side by side; demo access code `cbnav1efhd`). Ask the bakery *"Sunday ko Palasia delivery karte ho?"*, answer it from the
owner app with a voice note, and watch `/dashboard` and `/site`.

## Built with

Gemini 3.8 Live · Gemini 3.5 Flash-Lite · Google ADK 2.15 (`run_live`, bidi streaming) · Vertex AI (Gemini Enterprise
Agent Platform) · Cloud Run · Firestore · Secret Manager · Artifact Registry · Cloud Build · Terraform · FastAPI · uv ·
AudioWorklet. Patterns adapted from Google's [adk-samples voice agent](https://github.com/google/adk-samples).

## Guardrails

- Speaks only from owner-confirmed facts; never invents prices, timings, areas or policies.
- Introduces itself as an AI assistant; asks consent before calling back.
- Owner confirms every fact and approves every published answer; confirmed ≠ published.
- Public demo protected by an access code, single-instance cap, per-call time limit and a billing budget.

Apache-2.0
