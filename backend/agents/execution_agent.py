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

import asyncio

import config
import llm_retry
import pubsub_bus
import store
from tools.github_tool import create_github_issue, comment_on_issue




def github_username_for(name: str) -> str:
    """Map a speaker's name from the transcript to a GitHub username.

    A real deployment would look this up in a directory or HRIS. For the demo
    it reads GITHUB_USER_MAP from the environment, formatted as
    "Kartikeya=0kartik,Rahul=rahul-dev". Returns "" when unknown, which makes
    the issue unassigned rather than failing — assigning a nonexistent user
    makes GitHub reject the whole create_issue call.
    """
    return config.GITHUB_USER_MAP.get(name.strip().lower(), "")


execution_agent = Agent(
    name="execution_agent",
    model=config.GEMINI_MODEL_EXECUTION,
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

# Commitments currently being executed. The monitoring loop re-drives anything
# stuck at PLANNED, but a live ADK call can take longer than the 5s monitoring
# tick — without this guard the retry fires while the first attempt is still in
# flight and the commitment gets two GitHub issues. Observed exactly that:
# issues #8 and #9 created for one commitment.
_in_flight: set[str] = set()


def _get_runner() -> InMemoryRunner:
    global _runner
    if _runner is None:
        _runner = InMemoryRunner(agent=execution_agent, app_name="followthrough")
    return _runner


async def _run_agent_with_retry(prompt: str, cmt: dict, commitment_id: str,
                                attempts: int = 3):
    """Run the ADK agent, retrying Gemini's capacity errors.

    Gemini answers 503 UNAVAILABLE ("experiencing high demand") under load.
    Without a retry that becomes a commitment stuck at PLANNED with no issue
    and no explanation — which is what it looked like the first time it
    happened here, mid-run. Retrying costs a few seconds; not retrying costs
    the demo.

    Returns the create_github_issue tool result, or None if every attempt
    failed. The monitoring agent re-drives anything left at PLANNED, so None
    is recoverable rather than terminal.
    """
    runner = _get_runner()
    delay = 2.0

    for attempt in range(1, attempts + 1):
        try:
            session = await runner.session_service.create_session(
                app_name="followthrough", user_id="system"
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
            return tool_result

        except Exception as e:
            if not llm_retry.is_transient(e) or attempt == attempts:
                store.log_event(
                    meeting_id=cmt.get("meeting_id"), commitment_id=commitment_id,
                    kind="execution_failed",
                    message=f"Execution agent failed after {attempt} attempt(s): {str(e)[:200]}",
                )
                return None
            print(f"[execution] transient model error (attempt {attempt}/{attempts}), "
                  f"retrying in {delay:.0f}s: {str(e)[:120]}")
            await asyncio.sleep(delay)
            delay *= 2


async def execute_commitment(commitment_id: str):
    """Run the Execution Agent for a single commitment: create its GitHub
    issue (if not already created) and advance its state.

    If this commitment depends on another one, only proceed once that
    dependency has actually reached COMPLETED/VERIFIED — otherwise park
    it as WAITING. The monitoring agent re-calls this once the
    dependency resolves (see monitoring_agent._check_dependency)."""
    if commitment_id in _in_flight:
        return  # already being executed by another caller
    _in_flight.add(commitment_id)
    try:
        await _execute_commitment_inner(commitment_id)
    finally:
        _in_flight.discard(commitment_id)


async def _execute_commitment_inner(commitment_id: str):
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

    # PLANNED: the dependency check passed and this commitment is cleared for
    # execution, but no issue exists yet. It is a real, observable step — the
    # dashboard rail shows it — and it is what distinguishes "not started
    # because it is waiting" from "not started because issue creation failed".
    if cmt.get("state") != "PLANNED":
        store.update_commitment(commitment_id, state="PLANNED")

    assignee = github_username_for(cmt["owner"])

    if config.OFFLINE_LLM:
        # Skip the LLM call (no credentials needed); call the same
        # (already offline-aware) tool function the agent would call.
        tool_result = create_github_issue(
            title=cmt["description"],
            body=f"Owner: {cmt['owner']}\nDeadline: {cmt['deadline']}\nCreated automatically by FollowThrough (offline mode).",
            owner_github_username=assignee,
        )
    else:
        prompt = (
            f"Commitment: {cmt['description']}\n"
            f"Owner: {cmt['owner']}\n"
            f"Deadline: {cmt['deadline']}\n"
            + (f"Assign to GitHub user: {assignee}\n" if assignee else
               "The owner's GitHub username is unknown; leave it unassigned.\n")
            + "Create the GitHub issue now."
        )
        tool_result = await _run_agent_with_retry(prompt, cmt, commitment_id)

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
        # Announce on the issue itself, so the trail is visible to someone who
        # only ever looks at GitHub and never opens this dashboard.
        comment_on_issue(
            tool_result["issue_number"],
            f"Tracked by FollowThrough. Committed by **{cmt['owner']}** in a meeting, "
            f"deadline: **{cmt['deadline']}**.\n\n"
            "Add the `blocked` label if this is stuck — FollowThrough will pick it up "
            "and offer to escalate. Reference this issue in a PR and it will be marked "
            "verified once that PR merges.",
        )
        pubsub_bus.publish(config.TOPIC_TASK_CREATED, {
            "commitment_id": commitment_id,
            "meeting_id": cmt["meeting_id"],
            "issue_number": tool_result["issue_number"],
        })
    else:
        store.log_event(
            meeting_id=cmt["meeting_id"],
            commitment_id=commitment_id,
            kind="execution_failed",
            message=f"Failed to create GitHub issue: {tool_result}",
        )
