"""State store for meetings, commitments, and the event timeline.

Two backends behind the same functions:
  - Firestore (default) — real persistent state, used whenever
    config.OFFLINE_STORE is false.
  - In-memory dicts — used when config.OFFLINE_STORE is true, so the
    whole app can be run and tested with zero GCP credentials.

Collections / equivalent in-memory tables:
  meetings      - raw transcript + metadata per meeting
  commitments   - extracted commitments, one doc per commitment
  events        - append-only timeline log (for the dashboard timeline view)
  escalations   - drafted escalation emails and their approval status

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
_mem_escalations: dict[str, dict] = {}


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


def now():
    """Public alias for callers outside this module (e.g. the API layer
    stamping an approval time) so they don't reach for the private helper."""
    return _now()


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
    if config.OFFLINE_STORE:
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
    if config.OFFLINE_STORE:
        _mem_commitments[commitment_id] = doc
    else:
        db().collection("commitments").document(commitment_id).set(doc)
    log_event(meeting_id=meeting_id, commitment_id=commitment_id,
              kind="commitment_created", message=f"Commitment created: {description} (owner: {owner})")
    return commitment_id


def update_commitment(commitment_id: str, **fields):
    fields["updated_at"] = _now()
    if config.OFFLINE_STORE:
        if commitment_id in _mem_commitments:
            _mem_commitments[commitment_id].update(fields)
    else:
        db().collection("commitments").document(commitment_id).update(fields)
    if "state" in fields:
        cmt = get_commitment(commitment_id)
        log_event(meeting_id=cmt.get("meeting_id"), commitment_id=commitment_id,
                  kind="state_change", message=f"State -> {fields['state']}")


def get_commitment(commitment_id: str) -> dict:
    if config.OFFLINE_STORE:
        return dict(_mem_commitments.get(commitment_id, {}))
    doc = db().collection("commitments").document(commitment_id).get()
    return doc.to_dict() if doc.exists else {}


def list_commitments(meeting_id: str | None = None) -> list[dict]:
    if config.OFFLINE_STORE:
        items = list(_mem_commitments.values())
        if meeting_id:
            items = [c for c in items if c["meeting_id"] == meeting_id]
        return [dict(c) for c in items]
    from google.cloud import firestore
    col = db().collection("commitments")
    if meeting_id:
        # Keyword `filter=` form: the positional where() is deprecated and
        # emits a warning on every call in current google-cloud-firestore.
        col = col.where(filter=firestore.FieldFilter("meeting_id", "==", meeting_id))
    return [d.to_dict() for d in col.stream()]


# ---------- escalations (human approval gate) ----------
#
# An escalation is an outbound email the agent wants to send on the team's
# behalf. Sending one is irreversible and externally visible, so it is the one
# action in this system that an agent must never take on its own.
#
# The gate is enforced *here*, in state, not in the agent's prompt. Murph can
# only create records in PENDING_APPROVAL and ask send_escalation() to send
# them; send_escalation() refuses anything not already APPROVED. Nothing
# exposed to the model can move a record to APPROVED — only a human, through
# POST /escalations/{id}/approve.

ESCALATION_PENDING = "PENDING_APPROVAL"
ESCALATION_APPROVED = "APPROVED"
ESCALATION_SENT = "SENT"
ESCALATION_REJECTED = "REJECTED"
ESCALATION_FAILED = "SEND_FAILED"


def create_escalation(commitment_id: str, meeting_id: str | None, recipient: str,
                      subject: str, body: str, reason: str) -> str:
    escalation_id = new_id("esc")
    doc = {
        "id": escalation_id,
        "commitment_id": commitment_id,
        "meeting_id": meeting_id,
        "recipient": recipient,
        "subject": subject,
        "body": body,
        "reason": reason,
        "status": ESCALATION_PENDING,
        "approved_by": None,
        "created_at": _now(),
        "decided_at": None,
    }
    if config.OFFLINE_STORE:
        _mem_escalations[escalation_id] = doc
    else:
        db().collection("escalations").document(escalation_id).set(doc)
    return escalation_id


def get_escalation(escalation_id: str) -> dict:
    if config.OFFLINE_STORE:
        return dict(_mem_escalations.get(escalation_id, {}))
    doc = db().collection("escalations").document(escalation_id).get()
    return doc.to_dict() if doc.exists else {}


def update_escalation(escalation_id: str, **fields):
    if config.OFFLINE_STORE:
        if escalation_id in _mem_escalations:
            _mem_escalations[escalation_id].update(fields)
    else:
        db().collection("escalations").document(escalation_id).update(fields)


def list_escalations(status: str | None = None) -> list[dict]:
    if config.OFFLINE_STORE:
        items = [dict(e) for e in _mem_escalations.values()]
    else:
        from google.cloud import firestore
        col = db().collection("escalations")
        if status:
            col = col.where(filter=firestore.FieldFilter("status", "==", status))
        return [d.to_dict() for d in col.stream()]
    if status:
        items = [e for e in items if e["status"] == status]
    return sorted(items, key=lambda e: e["created_at"], reverse=True)


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
    if config.OFFLINE_STORE:
        _mem_events.append(doc)
    else:
        db().collection("events").document(event_id).set(doc)


def list_events(meeting_id: str | None = None, limit: int = 100) -> list[dict]:
    if config.OFFLINE_STORE:
        items = sorted(_mem_events, key=lambda e: e["timestamp"], reverse=True)
        if meeting_id:
            items = [e for e in items if e.get("meeting_id") == meeting_id]
        return items[:limit]
    from google.cloud import firestore
    col = db().collection("events")

    if not meeting_id:
        col = col.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
        return [d.to_dict() for d in col.stream()]

    # Equality filter + order_by on a different field needs a composite index
    # on (meeting_id ASC, timestamp DESC). firestore.indexes.json defines it
    # and deploy.sh creates it, but a project that has not run that deploy
    # would otherwise get a hard FailedPrecondition here. Falling back to a
    # client-side sort keeps the dashboard working; the log line says how to
    # make it fast.
    filtered = col.where(filter=firestore.FieldFilter("meeting_id", "==", meeting_id))
    try:
        q = filtered.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
        return [d.to_dict() for d in q.stream()]
    except Exception as e:
        if "index" not in str(e).lower():
            raise
        print("[store] composite index missing for (meeting_id, timestamp); "
              "sorting client-side. Run: gcloud firestore indexes composite create "
              "--collection-group=events --field-config=field-path=meeting_id,order=ascending "
              "--field-config=field-path=timestamp,order=descending")
        docs = [d.to_dict() for d in filtered.stream()]
        docs.sort(key=lambda d: d["timestamp"], reverse=True)
        return docs[:limit]
