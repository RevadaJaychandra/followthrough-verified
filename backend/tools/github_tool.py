"""GitHub tool functions.

Each function is a plain Python function with a docstring — ADK wraps
these automatically as callable tools for an LlmAgent based on the
signature + docstring.

When config.OFFLINE_GITHUB is true, these functions never call the real
GitHub API — they simulate issue creation/state in memory so the rest
of the app (extraction -> execution -> monitoring -> dashboard) can be
tested end-to-end before a real GITHUB_TOKEN exists.
"""
import itertools
from github import Github, GithubException

import config

_gh_client = None

# ---------- offline/mock state (only used when config.OFFLINE_GITHUB) ----------
_mock_issues: dict[int, dict] = {}
_mock_issue_counter = itertools.count(1)


def _gh() -> Github:
    global _gh_client
    if _gh_client is None:
        _gh_client = Github(config.GITHUB_TOKEN)
    return _gh_client


def _repo():
    return _gh().get_repo(config.GITHUB_REPO)


def create_github_issue(title: str, body: str, owner_github_username: str = "") -> dict:
    """Create a GitHub issue for a commitment.

    Args:
        title: Short issue title, should match the commitment description.
        body: Longer description including owner and deadline context.
        owner_github_username: GitHub username to assign, if known. Leave
            blank if unknown — the issue will be created unassigned.

    Returns:
        dict with status, issue_number, and issue_url.
    """
    if config.OFFLINE_GITHUB:
        n = next(_mock_issue_counter)
        _mock_issues[n] = {
            "number": n, "title": title, "body": body,
            "labels": [], "state": "open", "pr_status": "none",
        }
        return {"status": "success", "issue_number": n, "issue_url": f"https://github.com/offline-mode/issues/{n}"}

    try:
        repo = _repo()
        assignees = [owner_github_username] if owner_github_username else []
        issue = repo.create_issue(title=title, body=body, assignees=assignees)
        return {
            "status": "success",
            "issue_number": issue.number,
            "issue_url": issue.html_url,
        }
    except GithubException as e:
        return {"status": "error", "message": str(e)}


def get_issue_status(issue_number: int) -> dict:
    """Check the current status of a GitHub issue: open/closed, labels,
    and whether any linked pull request has been merged.

    Args:
        issue_number: The GitHub issue number to check.

    Returns:
        dict with state, labels, and pr_status (none/open/merged) if a
        linked PR is found by convention (PR title/body references the issue).
    """
    if config.OFFLINE_GITHUB:
        issue = _mock_issues.get(issue_number)
        if not issue:
            return {"status": "error", "message": "mock issue not found"}
        return {
            "status": "success",
            "issue_state": issue["state"],
            "labels": issue["labels"],
            "pr_status": issue["pr_status"],
            "merged_pr_url": f"https://github.com/offline-mode/pull/{issue_number}" if issue["pr_status"] == "merged" else None,
        }

    try:
        repo = _repo()
        issue = repo.get_issue(issue_number)
        labels = [l.name for l in issue.labels]

        pr_status = "none"
        merged_pr_url = None
        # Simple convention: look for open/closed PRs referencing #issue_number
        for pr in repo.get_pulls(state="all"):
            ref = f"#{issue_number}"
            if ref in (pr.title or "") or ref in (pr.body or ""):
                if pr.merged:
                    pr_status = "merged"
                    merged_pr_url = pr.html_url
                    break
                elif pr.state == "open":
                    pr_status = "open"

        return {
            "status": "success",
            "issue_state": issue.state,
            "labels": labels,
            "pr_status": pr_status,
            "merged_pr_url": merged_pr_url,
        }
    except GithubException as e:
        return {"status": "error", "message": str(e)}


def add_label(issue_number: int, label: str) -> dict:
    """Add a label to a GitHub issue (e.g. 'blocked', 'escalated').

    Args:
        issue_number: The GitHub issue number.
        label: Label name to add. Will be created on the repo if it
            doesn't already exist.
    """
    if config.OFFLINE_GITHUB:
        issue = _mock_issues.get(issue_number)
        if not issue:
            return {"status": "error", "message": "mock issue not found"}
        if label not in issue["labels"]:
            issue["labels"].append(label)
        return {"status": "success"}

    try:
        repo = _repo()
        try:
            repo.get_label(label)
        except GithubException:
            repo.create_label(name=label, color="d73a4a")
        issue = repo.get_issue(issue_number)
        issue.add_to_labels(label)
        return {"status": "success"}
    except GithubException as e:
        return {"status": "error", "message": str(e)}


def remove_label(issue_number: int, label: str) -> dict:
    """Remove a label from a GitHub issue (e.g. clearing 'blocked' once the
    work has been unblocked).

    Args:
        issue_number: The GitHub issue number.
        label: Label name to remove. Removing a label that is not present
            is treated as success, so this is safe to call repeatedly.
    """
    if config.OFFLINE_GITHUB:
        issue = _mock_issues.get(issue_number)
        if not issue:
            return {"status": "error", "message": "mock issue not found"}
        if label in issue["labels"]:
            issue["labels"].remove(label)
        return {"status": "success"}

    try:
        issue = _repo().get_issue(issue_number)
        if any(l.name == label for l in issue.labels):
            issue.remove_from_labels(label)
        return {"status": "success"}
    except GithubException as e:
        return {"status": "error", "message": str(e)}


def comment_on_issue(issue_number: int, comment: str) -> dict:
    """Post a comment on a GitHub issue (used for agent status updates).

    Args:
        issue_number: The GitHub issue number.
        comment: The comment text to post.
    """
    if config.OFFLINE_GITHUB:
        return {"status": "success"}

    try:
        repo = _repo()
        issue = repo.get_issue(issue_number)
        issue.create_comment(comment)
        return {"status": "success"}
    except GithubException as e:
        return {"status": "error", "message": str(e)}


def mock_merge_pr(issue_number: int) -> dict:
    """OFFLINE MODE ONLY: simulate a PR merge for a mock issue, so the
    monitoring/verification flow can be tested without real GitHub."""
    if not config.OFFLINE_GITHUB:
        return {"status": "error", "message": "only available in OFFLINE_MODE"}
    issue = _mock_issues.get(issue_number)
    if not issue:
        return {"status": "error", "message": "mock issue not found"}
    issue["pr_status"] = "merged"
    return {"status": "success"}
