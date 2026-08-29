"""Execution Agent.

Triggered (via Pub/Sub, see main.py subscriber) when a meeting has been
processed. For each commitment with no unmet dependency, it creates a
GitHub issue and updates Firestore state to PLANNED/IN_PROGRESS.

Built as an ADK LlmAgent so the model decides *how* to phrase the issue
body and *whether* an action is safe to auto-run vs needs approval,
using its tools rather than fully hardcoded logic.
"""
from google.adk.agents import Agent
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

import config
import store
from tools.github_tool import create_github_issue


def _github_username_guess(name: str) -> str:
    # Best-effort: for the demo, we maintain a tiny name->github mapping.
    # In a real system this would look up a directory/HRIS.
    mapping = {
        # "Kartikeya": "0kartik",
    }
    return mapping.get(name, "")


execution_agent = Agent(
    name="execution_agent",
    model=config.GEMINI_MODEL,
    description="Turns an approved commitment into a real GitHub issue with clear context.",
    instruction=(
        "You are the Execution Agent for FollowThrough. You are given one "
        "commitment (description, owner, deadline). Your job is to call "
        "create_github_issue with a clear title (matching the commitment "
        "description) and a body that states the owner, the deadline, and "
        "that this issue was created automatically from a meeting by "
        "FollowThrough. Always call the tool — do not just describe what "
        "you would do."
    ),
    tools=[create_github_issue],
)

_runner = None


def _get_runner() -> InMemoryRunner:
    global _runner
    if _runner is None:
        _runner = InMemoryRunner(agent=execution_agent, app_name="followthrough")
    return _runner


async def execute_commitment(commitment_id: str):
    """Run the Execution Agent for a single commitment: create its GitHub
    issue (if not already created) and advance its state.

    If this commitment depends on another one, only proceed once that
    dependency has actually reached COMPLETED/VERIFIED — otherwise park
    it as WAITING. The monitoring agent re-calls this once the
    dependency resolves (see monitoring_agent._check_dependency)."""
    cmt = store.get_commitment(commitment_id)
    if not cmt:
        return

    dep_desc = cmt.get("depends_on")
    if dep_desc:
        dep_resolved = False
        for other in store.list_commitments(meeting_id=cmt.get("meeting_id")):
            if other["description"] == dep_desc and other["state"] in ("COMPLETED", "VERIFIED"):
                dep_resolved = True
                break
        if not dep_resolved:
            if cmt.get("state") != "WAITING":
                store.update_commitment(commitment_id, state="WAITING")
            return

    if cmt.get("github_issue_number"):
        return  # already executed

    if config.OFFLINE_MODE:
        # Skip the LLM call (no credentials needed); call the same
        # (already offline-aware) tool function the agent would call.
        tool_result = create_github_issue(
            title=cmt["description"],
            body=f"Owner: {cmt['owner']}\nDeadline: {cmt['deadline']}\nCreated automatically by FollowThrough (offline mode).",
        )
    else:
        runner = _get_runner()
        session = await runner.session_service.create_session(
            app_name="followthrough", user_id="system"
        )

        prompt = (
            f"Commitment: {cmt['description']}\n"
            f"Owner: {cmt['owner']}\n"
            f"Deadline: {cmt['deadline']}\n"
            f"Create the GitHub issue now."
        )

        tool_result = None
        async for event in runner.run_async(
            user_id="system",
            session_id=session.id,
            new_message=genai_types.Content(
                role="user", parts=[genai_types.Part(text=prompt)]
            ),
        ):
            if event.get_function_responses():
                for fr in event.get_function_responses():
                    if fr.name == "create_github_issue":
                        tool_result = fr.response

    if tool_result and tool_result.get("status") == "success":
        store.update_commitment(
            commitment_id,
            state="IN_PROGRESS",
            github_issue_number=tool_result["issue_number"],
            github_issue_url=tool_result["issue_url"],
        )
        store.log_event(
            meeting_id=cmt["meeting_id"],
            commitment_id=commitment_id,
            kind="github_issue_created",
            message=f"GitHub issue #{tool_result['issue_number']} created",
        )
    else:
        store.log_event(
            meeting_id=cmt["meeting_id"],
            commitment_id=commitment_id,
            kind="execution_failed",
            message=f"Failed to create GitHub issue: {tool_result}",
        )
