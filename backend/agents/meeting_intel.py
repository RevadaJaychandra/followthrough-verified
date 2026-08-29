"""Meeting Intelligence Agent.

Takes a raw transcript and extracts structured commitments:
description, owner, deadline, and (optionally) a dependency on another
commitment in the same meeting.

We use a plain Gemini call with a JSON-schema instruction rather than a
full ADK tool-calling loop here, since this step is a single-shot
transform (transcript -> structured data), not a multi-turn tool-using
task. It's still an ADK-orchestrated step: the FastAPI endpoint that
calls this is the entry point of our ADK-based pipeline (see main.py).

Auth: uses Vertex AI (project/region + application-default credentials)
by default. If GEMINI_API_KEY is set instead, uses the plain Gemini API
key path — useful if a teammate wants to test this file alone before
full GCP IAM is wired up (get a key at https://aistudio.google.com/apikey).

If config.OFFLINE_MODE is set, no network call is made at all — a small
rule-based extractor handles the bundled sample transcript so the full
pipeline (extraction -> Firestore -> Pub/Sub -> GitHub -> dashboard) can
be exercised with zero credentials.
"""
import json
import os
import re
from google import genai
from google.genai import types

import config

_client = None


def client() -> genai.Client:
    global _client
    if _client is None:
        gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_api_key:
            _client = genai.Client(api_key=gemini_api_key)
        else:
            _client = genai.Client(
                vertexai=True,
                project=config.GCP_PROJECT_ID,
                location=config.GCP_REGION,
            )
    return _client


EXTRACTION_PROMPT = """You are a meeting intelligence system. Read the transcript below and extract every concrete commitment made — a specific task someone agreed to do.

For each commitment, identify:
- description: short, specific description of the task
- owner: the person's name who committed to it
- deadline: the deadline mentioned, or "unspecified" if none given
- depends_on_description: if this task can only start after another extracted task finishes, give that other task's exact description text; otherwise null

Return ONLY a JSON array, no markdown fences, no prose. Example format:
[
  {"description": "Finish authentication API", "owner": "Kartikeya", "deadline": "Friday", "depends_on_description": null},
  {"description": "Review authentication API", "owner": "Rahul", "deadline": "unspecified", "depends_on_description": "Finish authentication API"}
]

Transcript:
---
{transcript}
---

JSON array:"""


def _offline_extract(transcript: str) -> list[dict]:
    """Zero-credential fallback: naive line-based extraction. Good enough
    to exercise the rest of the pipeline end-to-end; not meant to handle
    arbitrary transcripts well — real extraction needs the live Gemini call."""
    print("[offline] using rule-based extraction, not Gemini")
    commitments = []
    prev_desc = None
    for line in transcript.splitlines():
        line = line.strip()
        m = re.match(r"^([A-Za-z]+):\s*(.+)$", line)
        if not m:
            continue
        speaker, text = m.group(1), m.group(2)
        if not text or len(text) < 5:
            continue
        deadline_match = re.search(
            r"\b(Friday|Monday|Tuesday|Wednesday|Thursday|Saturday|Sunday|today|tomorrow)\b",
            text, re.IGNORECASE,
        )
        deadline = deadline_match.group(0) if deadline_match else "unspecified"
        depends_on = prev_desc if re.search(r"\b(once|after|review it)\b", text, re.IGNORECASE) else None
        commitments.append({
            "description": text.rstrip("."),
            "owner": speaker,
            "deadline": deadline,
            "depends_on_description": depends_on,
        })
        prev_desc = text.rstrip(".")
    return commitments


def extract_commitments(transcript: str) -> list[dict]:
    if config.OFFLINE_MODE:
        return _offline_extract(transcript)

    prompt = EXTRACTION_PROMPT.replace("{transcript}", transcript)

    response = client().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )

    text = response.text.strip()
    # Defensive cleanup in case the model wraps in fences despite instruction
    text = re.sub(r"^```json\s*|\s*```$", "", text.strip())

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(f"Model did not return valid JSON: {text[:500]}")

    if not isinstance(items, list):
        raise ValueError("Expected a JSON array of commitments")

    return items
