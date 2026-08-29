"""State store for meetings, commitments, and the event timeline.

Two backends behind the same functions:
  - Firestore (default) — real persistent state, used whenever
    config.OFFLINE_MODE is false.
  - In-memory dicts — used when config.OFFLINE_MODE is true, so the
    whole app can be run and tested with zero GCP credentials.

Collections / equivalent in-memory tables:
  meetings      - raw transcript + metadata per meeting
  commitments   - extracted commitments, one doc per commitment
  events        - append-only timeline log (for the dashboard timeline view)

Commitment state machine:
  CREATED -> PLANNED -> IN_PROGRESS -> WAITING -> BLOCKED -> ESCALATED
          -> IN_PROGRESS -> COMPLETED -> VERIFIED
"""
import datetime
import uuid

import config

_db = None

# ---------- offline in-memory backend ----------
_mem_meetings: dict[str, dict] = {}
_mem_commitments: dict[str, dict] = {}
_mem_events: list[dict] = []


def db():
    """Real Firestore client. Only constructed (and only needs
    credentials) when OFFLINE_MODE is false."""
    global _db
    if _db is None:
        from google.cloud import firestore
        _db = firestore.Client(project=config.GCP_PROJECT_ID)
    return _db


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------- meetings ----------

def save_meeting(transcript: str, title: str = "Untitled meeting") -> str:
    meeting_id = new_id("mtg")
    doc = {
        "id": meeting_id,
        "title": title,
        "transcript": transcript,
        "created_at": _now(),
    }
    if config.OFFLINE_MODE:
        _mem_meetings[meeting_id] = doc
    else:
        db().collection("meetings").document(meeting_id).set(doc)
    log_event(meeting_id=meeting_id, kind="meeting_uploaded", message=f"Meeting '{title}' uploaded")
    return meeting_id


# ---------- commitments ----------

def create_commitment(meeting_id: str, description: str, owner: str,
                       deadline: str, depends_on: str | None = None) -> str:
    commitment_id = new_id("cmt")
    doc = {
        "id": commitment_id,
        "meeting_id": meeting_id,
        "description": description,
        "owner": owner,
        "deadline": deadline,
        "depends_on": depends_on,
        "state": "CREATED",
        "github_issue_number": None,
        "github_issue_url": None,
        "blocked_reason": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    if config.OFFLINE_MODE:
        _mem_commitments[commitment_id] = doc
    else:
        db().collection("commitments").document(commitment_id).set(doc)
    log_event(meeting_id=meeting_id, commitment_id=commitment_id,
              kind="commitment_created", message=f"Commitment created: {description} (owner: {owner})")
    return commitment_id


def update_commitment(commitment_id: str, **fields):
    fields["updated_at"] = _now()
    if config.OFFLINE_MODE:
        if commitment_id in _mem_commitments:
            _mem_commitments[commitment_id].update(fields)
    else:
        db().collection("commitments").document(commitment_id).update(fields)
    if "state" in fields:
        cmt = get_commitment(commitment_id)
        log_event(meeting_id=cmt.get("meeting_id"), commitment_id=commitment_id,
                  kind="state_change", message=f"State -> {fields['state']}")


def get_commitment(commitment_id: str) -> dict:
    if config.OFFLINE_MODE:
        return dict(_mem_commitments.get(commitment_id, {}))
    doc = db().collection("commitments").document(commitment_id).get()
    return doc.to_dict() if doc.exists else {}


def list_commitments(meeting_id: str | None = None) -> list[dict]:
    if config.OFFLINE_MODE:
        items = list(_mem_commitments.values())
        if meeting_id:
            items = [c for c in items if c["meeting_id"] == meeting_id]
        return [dict(c) for c in items]
    col = db().collection("commitments")
    if meeting_id:
        col = col.where("meeting_id", "==", meeting_id)
    return [d.to_dict() for d in col.stream()]


# ---------- events (timeline) ----------

def log_event(kind: str, message: str, meeting_id: str | None = None,
              commitment_id: str | None = None):
    event_id = new_id("evt")
    doc = {
        "id": event_id,
        "meeting_id": meeting_id,
        "commitment_id": commitment_id,
        "kind": kind,
        "message": message,
        "timestamp": _now(),
    }
    if config.OFFLINE_MODE:
        _mem_events.append(doc)
    else:
        db().collection("events").document(event_id).set(doc)


def list_events(meeting_id: str | None = None, limit: int = 100) -> list[dict]:
    if config.OFFLINE_MODE:
        items = sorted(_mem_events, key=lambda e: e["timestamp"], reverse=True)
        if meeting_id:
            items = [e for e in items if e.get("meeting_id") == meeting_id]
        return items[:limit]
    from google.cloud import firestore
    col = db().collection("events").order_by(
        "timestamp", direction=firestore.Query.DESCENDING
    ).limit(limit)
    if meeting_id:
        col = col.where("meeting_id", "==", meeting_id)
    return [d.to_dict() for d in col.stream()]
