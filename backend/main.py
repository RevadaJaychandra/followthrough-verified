"""FollowThrough backend — FastAPI app.

Endpoints:
  POST /meetings                -> upload transcript, triggers extraction
  GET  /commitments              -> list all commitments (for dashboard)
  GET  /events                   -> list timeline events (for dashboard)
  POST /murph                    -> voice/chat query endpoint
  POST /commitments/{id}/label   -> manually add a github label (demo helper)

Async flow:
  1. POST /meetings -> Meeting Intelligence Agent extracts commitments,
     writes to Firestore, publishes to TOPIC_MEETING_PROCESSED.
  2. A background subscriber on TOPIC_MEETING_PROCESSED runs the
     Execution Agent per commitment (real Pub/Sub decoupling).
  3. A background timer loop runs the Monitoring Agent every N seconds
     (stands in for Cloud Scheduler -> Pub/Sub -> Cloud Run in prod).
"""
import asyncio
import time
import threading

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
import store
import pubsub_bus
from agents.meeting_intel import extract_commitments
from agents.execution_agent import execute_commitment
from agents.monitoring_agent import check_all_commitments
from agents.murph_agent import ask_murph
from tools.github_tool import add_label, mock_merge_pr

app = FastAPI(title="FollowThrough API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- request/response models ----------

class MeetingUpload(BaseModel):
    title: str = "Untitled meeting"
    transcript: str


class MurphQuery(BaseModel):
    query: str
    session_id: str | None = None


class LabelRequest(BaseModel):
    label: str


# ---------- meetings ----------

@app.post("/meetings")
def upload_meeting(payload: MeetingUpload):
    meeting_id = store.save_meeting(payload.transcript, payload.title)

    try:
        raw_items = extract_commitments(payload.transcript)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

    # First pass: create all commitments (without dependency links, since
    # dependency refers to another commitment's *id*, which we don't have yet)
    desc_to_id = {}
    for item in raw_items:
        cid = store.create_commitment(
            meeting_id=meeting_id,
            description=item["description"],
            owner=item["owner"],
            deadline=item.get("deadline", "unspecified"),
            depends_on=item.get("depends_on_description"),  # store description for now
        )
        desc_to_id[item["description"]] = cid

    pubsub_bus.publish(config.TOPIC_MEETING_PROCESSED, {"meeting_id": meeting_id})

    return {
        "meeting_id": meeting_id,
        "commitments_extracted": len(raw_items),
    }


@app.get("/commitments")
def get_commitments(meeting_id: str | None = None):
    return store.list_commitments(meeting_id)


@app.get("/events")
def get_events(meeting_id: str | None = None):
    return store.list_events(meeting_id)


# ---------- murph (voice/chat) ----------

@app.post("/murph")
async def murph_query(payload: MurphQuery):
    result = await ask_murph(payload.query, payload.session_id)
    return result


# ---------- demo helpers ----------

@app.post("/commitments/{commitment_id}/label")
def label_commitment(commitment_id: str, payload: LabelRequest):
    """Manually attach a label (e.g. 'blocked') to a commitment's GitHub
    issue — used to trigger the blocker scenario live during the demo."""
    cmt = store.get_commitment(commitment_id)
    if not cmt or not cmt.get("github_issue_number"):
        raise HTTPException(status_code=404, detail="commitment or issue not found")
    result = add_label(cmt["github_issue_number"], payload.label)
    return result


@app.post("/commitments/{commitment_id}/mock-merge")
def mock_merge(commitment_id: str):
    """OFFLINE_MODE only: simulate the linked PR being merged, so the
    verification path can be exercised without a real GitHub repo.
    In real mode, merge the actual PR on GitHub instead — the monitoring
    loop will detect it on its next pass."""
    if not config.OFFLINE_MODE:
        raise HTTPException(status_code=400, detail="mock-merge is only available in OFFLINE_MODE")
    cmt = store.get_commitment(commitment_id)
    if not cmt or not cmt.get("github_issue_number"):
        raise HTTPException(status_code=404, detail="commitment or issue not found")
    result = mock_merge_pr(cmt["github_issue_number"])
    return result


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- background wiring ----------

def _on_meeting_processed(payload: dict):
    meeting_id = payload["meeting_id"]
    commitments = store.list_commitments(meeting_id)
    for cmt in commitments:
        asyncio.run(execute_commitment(cmt["id"]))


def _monitoring_loop(interval_seconds: int = 5):
    while True:
        try:
            asyncio.run(check_all_commitments())
        except Exception as e:
            print(f"[monitor] error: {e}")
        time.sleep(interval_seconds)


@app.on_event("startup")
def startup():
    pubsub_bus.ensure_topics_and_subscriptions()
    pubsub_bus.run_subscriber(config.TOPIC_MEETING_PROCESSED, _on_meeting_processed)
    threading.Thread(target=_monitoring_loop, daemon=True).start()
    print("[startup] FollowThrough backend ready")
