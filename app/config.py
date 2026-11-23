"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os

# Vertex AI (Gemini Enterprise Agent Platform) with ADC; no API keys.
os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "true")

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

# Live models are regional; text models are served from `global`.
LIVE_MODEL = os.environ.get("LIVE_MODEL", "gemini-3.8-live")
LIVE_MODEL_LOCATION = os.environ.get("LIVE_MODEL_LOCATION", "us-central1")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "gemini-3.5-flash-lite")
VOICE_NAME = os.environ.get("VOICE_NAME", "Aoede")

# "firestore" in Cloud Run, "memory" for local development and tests.
STORE = os.environ.get("STORE", "memory")
BUSINESS_ID = os.environ.get("BUSINESS_ID", "sweet-crumbs-indore")

# Shared demo code that gates the call/owner/API surfaces (the public site stays open).
ACCESS_CODE = os.environ.get("ACCESS_CODE", "")

# Public base URL of this service; the verifier fetches the published page from here.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8080").rstrip("/")

# Hard cap on a single voice session, to bound Live API spend on a public demo.
MAX_CALL_SECONDS = int(os.environ.get("MAX_CALL_SECONDS", "360"))
