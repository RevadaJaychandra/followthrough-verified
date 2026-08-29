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


def propose_escalation(commitment_id: str, recipient_email: str, reason: str) -> dict:
    """Prepare (but do NOT send) an escalation email for a blocked
    commitment. This requires a separate human approval step — call this
    to draft it, then tell the user you need their approval before sending.

    Args:
        commitment_id: The commitment that is blocked.
        recipient_email: Who the escalation should go to.
        reason: Why escalation is needed.
    """
    cmt = store.get_commitment(commitment_id)
    if not cmt:
        return {"status": "error", "message": "commitment not found"}

    subject = f"[FollowThrough] Blocked: {cmt['description']}"
    body = (
        f"Hi,\n\nThe commitment \"{cmt['description']}\" (owner: {cmt['owner']}, "
        f"deadline: {cmt['deadline']}) is currently blocked.\n\n"
        f"Reason: {reason}\n\n"
        f"Could you help unblock this? This request was sent automatically "
        f"by FollowThrough on behalf of the team.\n"
    )
    store.log_event(
        meeting_id=cmt["meeting_id"], commitment_id=commitment_id,
        kind="escalation_drafted", message=f"Escalation drafted for {recipient_email}, awaiting approval",
    )
    return {
        "status": "drafted_pending_approval",
        "subject": subject,
        "body": body,
        "recipient": recipient_email,
    }


def send_approved_escalation(commitment_id: str, recipient_email: str,
                              subject: str, body: str) -> dict:
    """Actually send an escalation email. ONLY call this if the user has
    explicitly approved sending it in this conversation."""
    result = send_escalation_email(recipient_email, subject, body)
    if result.get("status") == "success":
        cmt = store.get_commitment(commitment_id)
        store.update_commitment(commitment_id, state="ESCALATED")
        store.log_event(
            meeting_id=cmt.get("meeting_id"), commitment_id=commitment_id,
            kind="escalation_sent", message=f"Escalation sent to {recipient_email}",
        )
    return result


murph_agent = Agent(
    name="murph",
    model=config.GEMINI_MODEL,
    description="Voice/chat interface answering questions about commitments and handling approved escalations.",
    instruction=(
        "You are Murph, the voice assistant for FollowThrough. Answer "
        "questions about meeting commitments concisely and factually, "
        "using list_commitment_status to check current state — never "
        "guess. If asked to escalate or 'ask someone' about a blocked "
        "task, call propose_escalation to draft the message and then ask "
        "the user to confirm before it's sent — do NOT call "
        "send_approved_escalation unless the user has explicitly said yes "
        "in this conversation. Keep spoken responses short, 1-3 sentences."
    ),
    tools=[list_commitment_status, propose_escalation, send_approved_escalation],
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
            recipient_email=config.ESCALATION_RECIPIENT or "teammate@example.com",
            reason=cmt.get("blocked_reason", "blocked"),
        )
        return f"Drafted an escalation for '{cmt['description']}' to {draft['recipient']}. Say 'yes send it' to confirm."

    if "yes" in q and ("send" in q or "confirm" in q):
        cmts = list_commitment_status()["commitments"]
        blocked = [c for c in cmts if c["state"] == "BLOCKED"]
        if not blocked:
            return "No pending escalation to send."
        cmt = blocked[0]
        result = send_approved_escalation(
            commitment_id=cmt["id"],
            recipient_email=config.ESCALATION_RECIPIENT or "teammate@example.com",
            subject=f"[FollowThrough] Blocked: {cmt['description']}",
            body=f"The commitment '{cmt['description']}' is blocked: {cmt.get('blocked_reason', '')}",
        )
        return "Escalation sent." if result.get("status") == "success" else f"Failed to send: {result.get('message')}"

    return "I can tell you what's blocked, or help escalate a blocked task — try asking 'what's blocked?'"


async def ask_murph(query: str, session_id: str | None = None) -> dict:
    if config.OFFLINE_MODE:
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

    reply_text = ""
    async for event in runner.run_async(
        user_id="user",
        session_id=session.id,
        new_message=genai_types.Content(role="user", parts=[genai_types.Part(text=query)]),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    reply_text += part.text

    return {"reply": reply_text.strip(), "session_id": session.id}
