"""Murph — the voice/command agent.

This is the natural-language interface layer: "what's blocked?",
"ask Rahul", "are all commitments done?". It reads Firestore state via
tools and can propose an escalation action, which the API layer gates
behind an explicit approval step before actually sending.
"""
from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

import config
import llm_retry
import store
from tools.email_tool import send_escalation_email


def list_commitment_status() -> dict:
    """Return the current status of every commitment across all meetings,
    including state, owner, deadline, and blocked reason if any."""
    commitments = store.list_commitments()
    return {
        "status": "success",
        "commitments": [
            {
                "id": c["id"],
                "description": c["description"],
                "owner": c["owner"],
                "deadline": c["deadline"],
                "state": c["state"],
                "blocked_reason": c.get("blocked_reason"),
            }
            for c in commitments
        ],
    }


# Domains a model reaches for when it does not actually know an address.
# An escalation sent to one of these silently goes nowhere, which is worse
# than not sending it at all — the human believes the ask was delivered.
_PLACEHOLDER_EMAIL_DOMAINS = ("example.com", "example.org", "example.net",
                              "test.com", "email.com", "domain.com")


def resolve_recipient(owner: str, suggested: str | None) -> tuple[str, str]:
    """Decide where an escalation actually goes.

    Returns (address, how_it_was_chosen).

    An LLM asked to escalate to "Kartikeya" will confidently produce
    kartikeya@example.com, because the transcript never contained an address.
    Trusting that means the demo approves an email that is delivered nowhere.
    So a model-supplied address is only used when it is plausibly real; the
    authority is TEAM_EMAIL_MAP, then ESCALATION_RECIPIENT.
    """
    mapped = config.TEAM_EMAIL_MAP.get((owner or "").strip().lower())
    if mapped:
        return mapped, "team_email_map"

    if suggested:
        candidate = suggested.strip()
        domain = candidate.rsplit("@", 1)[-1].lower() if "@" in candidate else ""
        looks_real = domain and domain not in _PLACEHOLDER_EMAIL_DOMAINS
        if looks_real:
            return candidate, "model_supplied"

    return config.ESCALATION_RECIPIENT, "escalation_recipient_default"


def propose_escalation(commitment_id: str, recipient_email: str, reason: str) -> dict:
    """Draft an escalation email for a blocked commitment and put it in the
    approval queue. This does NOT send anything. Tell the user the draft is
    waiting for their approval in the dashboard.

    Args:
        commitment_id: The commitment that is blocked.
        recipient_email: Who the escalation should go to. If you do not know
            the person's real address, pass an empty string — do not invent
            one. The system routes it to the configured escalation contact.
        reason: Why escalation is needed.
    """
    cmt = store.get_commitment(commitment_id)
    if not cmt:
        return {"status": "error", "message": "commitment not found"}

    recipient_email, routing = resolve_recipient(cmt.get("owner", ""), recipient_email)
    if not recipient_email:
        return {"status": "error",
                "message": "no recipient available: set ESCALATION_RECIPIENT or TEAM_EMAIL_MAP"}

    subject = f"[FollowThrough] Blocked: {cmt['description']}"
    body = (
        f"Hi,\n\nThe commitment \"{cmt['description']}\" (owner: {cmt['owner']}, "
        f"deadline: {cmt['deadline']}) is currently blocked.\n\n"
        f"Reason: {reason}\n\n"
        f"Could you help unblock this? This request was sent automatically "
        f"by FollowThrough on behalf of the team.\n"
    )
    escalation_id = store.create_escalation(
        commitment_id=commitment_id,
        meeting_id=cmt.get("meeting_id"),
        recipient=recipient_email,
        subject=subject,
        body=body,
        reason=reason,
    )
    store.log_event(
        meeting_id=cmt["meeting_id"], commitment_id=commitment_id,
        kind="escalation_drafted",
        message=f"Escalation drafted for {recipient_email}, awaiting human approval",
    )
    return {
        "status": "drafted_pending_approval",
        "escalation_id": escalation_id,
        "subject": subject,
        "body": body,
        "recipient": recipient_email,
        "recipient_routing": routing,
    }


def send_escalation(escalation_id: str) -> dict:
    """Send an escalation email that a human has already approved.

    This refuses to send anything that is not already in APPROVED status. You
    cannot approve an escalation yourself and there is no tool that lets you —
    approval only happens when a person clicks Approve in the dashboard. If
    this returns not_approved, tell the user the draft is still waiting on
    them; do not try to work around it.

    Args:
        escalation_id: The id returned by propose_escalation.
    """
    esc = store.get_escalation(escalation_id)
    if not esc:
        return {"status": "error", "message": "escalation not found"}

    if esc["status"] == store.ESCALATION_SENT:
        return {"status": "already_sent", "message": "this escalation was already sent"}

    if esc["status"] != store.ESCALATION_APPROVED:
        # The enforcement point. An agent reaching here without human approval
        # gets nothing sent, regardless of what it believes the user said.
        return {
            "status": "not_approved",
            "message": (
                f"Escalation {escalation_id} is {esc['status']}, not APPROVED. "
                "A human must approve it in the dashboard before it can be sent."
            ),
        }

    result = send_escalation_email(esc["recipient"], esc["subject"], esc["body"])
    if result.get("status") == "success":
        store.update_escalation(escalation_id, status=store.ESCALATION_SENT)
        store.update_commitment(esc["commitment_id"], state="ESCALATED")
        store.log_event(
            meeting_id=esc.get("meeting_id"), commitment_id=esc["commitment_id"],
            kind="escalation_sent", message=f"Escalation sent to {esc['recipient']}",
        )
    else:
        store.update_escalation(escalation_id, status=store.ESCALATION_FAILED)
        store.log_event(
            meeting_id=esc.get("meeting_id"), commitment_id=esc["commitment_id"],
            kind="escalation_failed",
            message=f"Escalation send failed: {result.get('message')}",
        )
    return result


def list_pending_escalations() -> dict:
    """List escalation drafts that are still waiting for a human decision."""
    pending = store.list_escalations(status=store.ESCALATION_PENDING)
    return {
        "status": "success",
        "pending": [
            {"escalation_id": e["id"], "recipient": e["recipient"],
             "subject": e["subject"], "reason": e["reason"]}
            for e in pending
        ],
    }


murph_agent = Agent(
    name="murph",
    model=config.GEMINI_MODEL_MURPH,
    description="Voice/chat interface answering questions about commitments and handling approved escalations.",
    instruction=(
        "You are Murph, the voice assistant for FollowThrough. Answer "
        "questions about meeting commitments concisely and factually, "
        "using list_commitment_status to check current state — never "
        "guess. If asked to escalate or 'ask someone' about a blocked "
        "task, call propose_escalation to draft the message, then tell the "
        "user it is waiting for their approval in the dashboard. You do not "
        "know anyone's email address — never invent one. Pass an empty string "
        "for recipient_email and the system routes it correctly. Sending an "
        "email is irreversible, so you cannot approve one yourself: "
        "send_escalation only works on drafts a person has already approved, "
        "and it will refuse otherwise. If it refuses, say the draft still "
        "needs approval rather than trying another route. Keep spoken "
        "responses short, 1-3 sentences."
    ),
    tools=[list_commitment_status, propose_escalation, send_escalation,
           list_pending_escalations],
)

_runner = None


def _get_runner() -> InMemoryRunner:
    global _runner
    if _runner is None:
        _runner = InMemoryRunner(agent=murph_agent, app_name="followthrough-murph")
    return _runner


def _offline_ask_murph(query: str) -> str:
    """Zero-credential fallback: simple keyword matching over the same
    tool functions the real agent would call. Handles the two demo-
    critical intents (status check, escalate) well enough to test the
    full request/response wiring; real natural-language understanding
    needs the live Gemini call (OFFLINE_MODE off)."""
    q = query.lower()

    if "blocked" in q or "status" in q or "commitments" in q:
        result = list_commitment_status()
        cmts = result["commitments"]
        blocked = [c for c in cmts if c["state"] == "BLOCKED"]
        if not cmts:
            return "There are no commitments tracked yet."
        if blocked:
            b = blocked[0]
            return f"{len(blocked)} commitment(s) blocked. '{b['description']}' — {b.get('blocked_reason', 'reason unknown')}."
        done = [c for c in cmts if c["state"] in ("COMPLETED", "VERIFIED")]
        return f"{len(cmts)} commitments total, {len(done)} completed, none currently blocked."

    if "ask" in q or "escalat" in q:
        cmts = list_commitment_status()["commitments"]
        blocked = [c for c in cmts if c["state"] == "BLOCKED"]
        if not blocked:
            return "Nothing is currently blocked, so there's nothing to escalate."
        cmt = blocked[0]
        draft = propose_escalation(
            commitment_id=cmt["id"],
            recipient_email="",  # resolve_recipient decides; see there
            reason=cmt.get("blocked_reason", "blocked"),
        )
        if draft.get("status") == "error":
            return f"Couldn't draft that: {draft['message']}"
        return (
            f"Drafted an escalation for '{cmt['description']}' to {draft['recipient']}. "
            "It's waiting for your approval in the dashboard — I can't send it myself."
        )

    if "send" in q or "confirm" in q or "approv" in q:
        # Mirrors the real agent: try to send, and let the store's approval
        # gate decide. Saying "yes send it" out loud is not approval.
        pending = list_pending_escalations()["pending"]
        approved = store.list_escalations(status=store.ESCALATION_APPROVED)
        if approved:
            result = send_escalation(approved[0]["id"])
            if result.get("status") == "success":
                return "Escalation sent."
            return f"Failed to send: {result.get('message')}"
        if pending:
            return (
                "That draft still needs approval — click Approve in the dashboard "
                "and I'll send it. I can't approve it myself."
            )
        return "No pending escalation to send."

    return "I can tell you what's blocked, or help escalate a blocked task — try asking 'what's blocked?'"


async def ask_murph(query: str, session_id: str | None = None) -> dict:
    if config.OFFLINE_LLM:
        return {"reply": _offline_ask_murph(query), "session_id": session_id or "offline-session"}

    runner = _get_runner()
    if session_id:
        session = await runner.session_service.get_session(
            app_name="followthrough-murph", user_id="user", session_id=session_id
        )
    else:
        session = await runner.session_service.create_session(
            app_name="followthrough-murph", user_id="user"
        )

    async def _run() -> str:
        text = ""
        async for event in runner.run_async(
            user_id="user",
            session_id=session.id,
            new_message=genai_types.Content(role="user", parts=[genai_types.Part(text=query)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        text += part.text
        return text

    try:
        reply_text = await llm_retry.with_retry_async(_run, label="murph")
    except Exception as e:
        # Degrade to the deterministic keyword path rather than returning a 500.
        # Murph going quiet mid-demo because Gemini is busy is a far worse
        # failure than Murph answering from the same tools without the model.
        print(f"[murph] model unavailable, falling back to keyword path: {str(e)[:200]}")
        return {
            "reply": _offline_ask_murph(query),
            "session_id": session.id,
            "degraded": True,
            "degraded_reason": llm_retry.describe(e),
        }

    return {"reply": reply_text.strip(), "session_id": session.id}
