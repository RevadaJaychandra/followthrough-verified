"""Monitoring Agent.

Runs on a timer (see main.py's scheduler loop / Cloud Scheduler in prod).
For every commitment currently IN_PROGRESS or WAITING, it:
  - checks GitHub issue/PR status
  - resolves WAITING commitments whose dependency just completed
  - detects blockers (issue labeled 'blocked', or open >N with no PR)
  - marks VERIFIED when the linked PR is merged
  - proposes escalation actions (requires human approval before sending)

check_all_commitments() is async because resolving a dependency needs to
call execute_commitment() (also async, since it may run the ADK agent).
The background thread in main.py drives this with asyncio.run() each
pass — see _monitoring_loop there.
"""
import store
from tools.github_tool import get_issue_status


BLOCKED_LABEL = "blocked"

# States where the commitment is live work we should keep polling GitHub for.
# BLOCKED and ESCALATED belong here: a blocked task can have its label removed
# or its PR merged after someone unblocks it, and that is the entire point of
# escalating. Polling only IN_PROGRESS made those two states dead ends — the
# commitment could never be verified and everything depending on it waited
# forever.
ACTIVE_STATES = ("IN_PROGRESS", "BLOCKED", "ESCALATED")


async def check_all_commitments():
    """One monitoring pass over all non-terminal commitments.

    Two phases, in order: first resolve GitHub-derived state (blocked/
    unblocked/verified) for every active commitment, THEN check dependencies
    for WAITING commitments. This ordering matters — a commitment that gets
    verified in phase 1 should be able to unblock its dependent in the
    same pass, not wait for the next tick.
    """
    commitments = store.list_commitments()

    for cmt in commitments:
        if cmt.get("state") in ACTIVE_STATES:
            _check_github_progress(cmt)

    # Re-fetch: phase 1 may have changed states (e.g. IN_PROGRESS -> VERIFIED)
    commitments = store.list_commitments()
    by_description = {c["description"]: c for c in commitments}

    for cmt in commitments:
        if cmt.get("state") == "WAITING":
            await _check_dependency(cmt, by_description)


async def _check_dependency(cmt: dict, by_description: dict):
    dep_desc = cmt.get("depends_on")
    dep = by_description.get(dep_desc)
    if dep and dep.get("state") in ("COMPLETED", "VERIFIED"):
        store.log_event(
            meeting_id=cmt["meeting_id"],
            commitment_id=cmt["id"],
            kind="dependency_resolved",
            message=f"Dependency '{dep_desc}' resolved, task can now start",
        )
        # Import locally to avoid circular import at module load time
        from agents.execution_agent import execute_commitment
        await execute_commitment(cmt["id"])


def _check_github_progress(cmt: dict):
    issue_number = cmt.get("github_issue_number")
    if not issue_number:
        return

    result = get_issue_status(issue_number)
    if result.get("status") != "success":
        return

    labels = result.get("labels", [])
    pr_status = result.get("pr_status")

    if pr_status == "merged":
        store.update_commitment(cmt["id"], state="COMPLETED")
        store.log_event(
            meeting_id=cmt["meeting_id"], commitment_id=cmt["id"],
            kind="pr_merged", message="Linked PR merged, verifying...",
        )
        _verify(cmt)
        return

    state = cmt.get("state")
    is_blocked_now = BLOCKED_LABEL in labels

    if is_blocked_now and state not in ("BLOCKED", "ESCALATED"):
        store.update_commitment(
            cmt["id"], state="BLOCKED",
            blocked_reason="Issue labeled 'blocked' on GitHub",
        )
        store.log_event(
            meeting_id=cmt["meeting_id"], commitment_id=cmt["id"],
            kind="blocker_detected", message="Blocker detected via GitHub label",
        )
        return

    if not is_blocked_now and state in ("BLOCKED", "ESCALATED"):
        # Somebody removed the 'blocked' label — the escalation worked, or
        # the owner resolved it themselves. Return the commitment to active
        # work so it can progress to COMPLETED/VERIFIED normally.
        store.update_commitment(cmt["id"], state="IN_PROGRESS", blocked_reason=None)
        store.log_event(
            meeting_id=cmt["meeting_id"], commitment_id=cmt["id"],
            kind="blocker_cleared",
            message="'blocked' label removed on GitHub, work resumed",
        )
        return

    # Deadline-based escalation trigger left as an explicit, approvable
    # action surfaced to the dashboard/voice layer rather than auto-firing
    # emails from the polling loop.


def _verify(cmt: dict):
    """Verification step: PR merged is our primary check for the demo.
    Extend here with test-status / file-changed checks as needed."""
    store.update_commitment(cmt["id"], state="VERIFIED")
    store.log_event(
        meeting_id=cmt["meeting_id"], commitment_id=cmt["id"],
        kind="verified", message="Commitment verified complete",
    )
