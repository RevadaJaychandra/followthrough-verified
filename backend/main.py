"""FollowThrough backend — FastAPI app.

Endpoints:
  POST   /meetings                     -> upload transcript, triggers extraction
  GET    /commitments                  -> list all commitments (for dashboard)
  GET    /events                       -> list timeline events (for dashboard)
  POST   /murph                        -> voice/chat query endpoint
  GET    /escalations                  -> list drafted escalations + status
  POST   /escalations/{id}/approve     -> human approves, then it sends
  POST   /escalations/{id}/reject      -> human rejects, nothing sends
  POST   /commitments/{id}/label       -> add a github label (demo helper)
  DELETE /commitments/{id}/label/{lbl} -> remove a github label (demo helper)
  POST   /commitments/{id}/mock-merge  -> OFFLINE_MODE only, simulate PR merge

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
from agents.meeting_intel import extract_commitments, preflight_model
from agents.execution_agent import execute_commitment
from agents.monitoring_agent import check_all_commitments
from agents.murph_agent import ask_murph, send_escalation
from tools.github_tool import add_label, remove_label, mock_merge_pr

app = FastAPI(title="FollowThrough API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
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


class ApprovalRequest(BaseModel):
    approved_by: str | None = None


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


# ---------- escalation approval (human-in-the-loop gate) ----------
#
# Sending email on the team's behalf is irreversible and externally visible,
# so it is the one action an agent may never take alone. Murph can draft an
# escalation, but only these endpoints can approve one, and they are not
# exposed to any agent as a tool. send_escalation() refuses anything that is
# not already APPROVED, so the guarantee holds in state rather than in a prompt.

@app.get("/escalations")
def get_escalations(status: str | None = None):
    return store.list_escalations(status)


@app.post("/escalations/{escalation_id}/approve")
def approve_escalation(escalation_id: str, payload: ApprovalRequest | None = None):
    """Approve a drafted escalation and send it immediately."""
    esc = store.get_escalation(escalation_id)
    if not esc:
        raise HTTPException(status_code=404, detail="escalation not found")
    if esc["status"] == store.ESCALATION_SENT:
        return {"status": "already_sent", "escalation": esc}
    if esc["status"] != store.ESCALATION_PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"escalation is {esc['status']}, only {store.ESCALATION_PENDING} can be approved",
        )

    approved_by = (payload.approved_by if payload else None) or "dashboard-user"
    store.update_escalation(
        escalation_id,
        status=store.ESCALATION_APPROVED,
        approved_by=approved_by,
        decided_at=store.now(),
    )
    store.log_event(
        meeting_id=esc.get("meeting_id"), commitment_id=esc["commitment_id"],
        kind="escalation_approved",
        message=f"Escalation approved by {approved_by}",
    )
    send_result = send_escalation(escalation_id)
    return {"status": send_result.get("status"), "escalation": store.get_escalation(escalation_id)}


@app.post("/escalations/{escalation_id}/reject")
def reject_escalation(escalation_id: str, payload: ApprovalRequest | None = None):
    """Reject a drafted escalation. Nothing is sent, and it cannot be revived."""
    esc = store.get_escalation(escalation_id)
    if not esc:
        raise HTTPException(status_code=404, detail="escalation not found")
    if esc["status"] != store.ESCALATION_PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"escalation is {esc['status']}, only {store.ESCALATION_PENDING} can be rejected",
        )

    rejected_by = (payload.approved_by if payload else None) or "dashboard-user"
    store.update_escalation(
        escalation_id,
        status=store.ESCALATION_REJECTED,
        approved_by=rejected_by,
        decided_at=store.now(),
    )
    store.log_event(
        meeting_id=esc.get("meeting_id"), commitment_id=esc["commitment_id"],
        kind="escalation_rejected",
        message=f"Escalation rejected by {rejected_by}, nothing sent",
    )
    return {"status": "rejected", "escalation": store.get_escalation(escalation_id)}


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


@app.delete("/commitments/{commitment_id}/label/{label}")
def unlabel_commitment(commitment_id: str, label: str):
    """Remove a label from a commitment's GitHub issue — used to clear the
    'blocked' label live during the demo. The monitoring loop notices on its
    next pass and returns the commitment to IN_PROGRESS."""
    cmt = store.get_commitment(commitment_id)
    if not cmt or not cmt.get("github_issue_number"):
        raise HTTPException(status_code=404, detail="commitment or issue not found")
    return remove_label(cmt["github_issue_number"], label)


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
    """Liveness, plus the few facts the dashboard needs to render honestly —
    chiefly whether it is looking at real GitHub/Gemini/Gmail or in-memory
    stubs, so it never implies a real issue was filed when one was not."""
    return {
        "status": "ok",
        "offline_mode": config.OFFLINE_MODE,
        "github_repo": config.GITHUB_REPO if not config.OFFLINE_MODE else None,
        "model": config.GEMINI_MODEL,
    }


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


def _startup_preflight():
    """Fail loudly at boot rather than silently at demo time.

    Every check here is non-fatal on purpose: a missing Gmail password should
    not stop the server, it should print one obvious line saying escalation
    email will not work. What must never happen is discovering a bad model id
    or an unset GITHUB_REPO for the first time in front of an audience.
    """
    if config.OFFLINE_MODE:
        print("[startup] OFFLINE_MODE=true — Gemini, Firestore, Pub/Sub, GitHub "
              "and Gmail are all in-memory stubs. Nothing leaves this process.")
        return

    problems = config.validate()
    if problems:
        print("[startup] configuration problems:")
        for p in problems:
            print(f"[startup]   - {p}")
    else:
        print("[startup] config complete (all values present, none placeholders)")

    result = preflight_model()
    prefix = "[startup] " if result["ok"] else "[startup] WARNING: "
    print(f"{prefix}{result['message']}")


@app.on_event("startup")
def startup():
    _startup_preflight()
    pubsub_bus.ensure_topics_and_subscriptions()
    pubsub_bus.run_subscriber(config.TOPIC_MEETING_PROCESSED, _on_meeting_processed)
    threading.Thread(target=_monitoring_loop, daemon=True).start()
    print("[startup] FollowThrough backend ready")
