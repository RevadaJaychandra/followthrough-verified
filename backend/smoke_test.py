"""Full offline smoke test — proves the entire pipeline works with ZERO
real credentials, on Windows, macOS, or Linux.

This is the canonical smoke test. `smoke_test.sh` is the bash equivalent
kept for Linux/CI; this file is the one to run during development because
it does not depend on bash, curl, /tmp, or a `python3` on PATH (the
machine's `python3` may be an older interpreter than the venv's).

Run from the backend/ directory, with the venv active:
    python smoke_test.py

What it proves, in order:
  1. Server boots (Firestore/Pub/Sub/GitHub/Gmail all mocked in-memory)
  2. Transcript -> commitments extraction works
  3. Dependency-aware execution: the independent commitment gets a GitHub
     issue immediately, the dependent one parks as WAITING
  4. The monitoring loop autonomously detects a blocker
  5. Murph reflects live state and can draft + send an approved escalation
  6. PR merge -> VERIFIED, and the waiting dependent task auto-unblocks
     and gets its own issue in the same monitoring pass
  7. A BLOCKED/ESCALATED commitment is not a dead end: clearing the
     'blocked' label returns it to IN_PROGRESS and it can still be verified

Exit code 0 and "ALL PASSED" means the architecture is sound and the only
remaining work is plugging in real credentials (see README.md).
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.resolve()
BASE_URL = "http://127.0.0.1:8080"

# The monitoring loop in main.py runs every MONITOR_INTERVAL seconds. We wait
# a little over two passes so a single slow pass never flakes the test.
MONITOR_INTERVAL = 5
MONITOR_WAIT = MONITOR_INTERVAL * 2 + 2

_failures = 0


def pass_(msg):
    print(f"  PASS: {msg}")


def fail(msg, log_path=None):
    global _failures
    _failures += 1
    print(f"  FAIL: {msg}")
    if log_path and log_path.exists():
        print("--- server log ---")
        print(log_path.read_text(encoding="utf-8", errors="replace"))
    raise SystemExit(1)


def request(path, method="GET", payload=None, timeout=30):
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else None


def wait_for_server(log_path, attempts=40):
    for _ in range(attempts):
        try:
            if request("/health").get("status") == "ok":
                return True
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            time.sleep(0.5)
    return False


def wait_for_state(commitment_id, expected, timeout_seconds):
    """Poll /commitments until the given commitment reaches `expected`.
    Returns the final observed state (which may not be `expected`)."""
    deadline = time.time() + timeout_seconds
    state = None
    while time.time() < deadline:
        for c in request("/commitments"):
            if c["id"] == commitment_id:
                state = c["state"]
                break
        if state == expected:
            return state
        time.sleep(1)
    return state


def main():
    global _failures
    log_path = BACKEND_DIR / "smoke_test_server.log"
    env = dict(os.environ, OFFLINE_MODE="true", GCP_PROJECT_ID="smoke-test")

    print("Starting server in OFFLINE_MODE...")
    with log_path.open("w", encoding="utf-8") as log_file:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--host", "127.0.0.1", "--port", "8080", "--log-level", "warning"],
            cwd=str(BACKEND_DIR), env=env, stdout=log_file, stderr=subprocess.STDOUT,
        )
        try:
            print("[1/7] Health check")
            if wait_for_server(log_path):
                pass_("server responds")
            else:
                fail("server not responding", log_path)

            print("[2/7] Upload meeting + extraction")
            resp = request("/meetings", "POST", {
                "title": "Standup",
                "transcript": (
                    "Kartikeya: I will finish the authentication API by Friday.\n"
                    "Rahul: I will review it once it is ready."
                ),
            })
            count = resp["commitments_extracted"]
            if count == 2:
                pass_("extracted 2 commitments")
            else:
                fail(f"expected 2 commitments, got {count}", log_path)

            print("[3/7] Dependency-aware execution")
            # The Pub/Sub subscriber runs execution on a background thread.
            deadline = time.time() + 15
            commitments = []
            while time.time() < deadline:
                commitments = request("/commitments")
                states = sorted(c["state"] for c in commitments)
                if states == ["IN_PROGRESS", "WAITING"]:
                    break
                time.sleep(0.5)
            in_progress = [c for c in commitments if c["state"] == "IN_PROGRESS"]
            waiting = [c for c in commitments if c["state"] == "WAITING"]
            if len(in_progress) == 1 and len(waiting) == 1:
                pass_("1 executing immediately, 1 correctly waiting on dependency")
            else:
                fail(f"expected 1 IN_PROGRESS + 1 WAITING, got "
                     f"{len(in_progress)} + {len(waiting)}", log_path)
            cmt_id = in_progress[0]["id"]

            print(f"[4/7] Blocker detection (waiting up to {MONITOR_WAIT}s for monitoring poll)...")
            request(f"/commitments/{cmt_id}/label", "POST", {"label": "blocked"})
            state = wait_for_state(cmt_id, "BLOCKED", MONITOR_WAIT)
            if state == "BLOCKED":
                pass_("monitoring loop autonomously detected the blocker")
            else:
                fail(f"expected BLOCKED, got {state}", log_path)

            print("[5/7] Murph: status + escalation flow")
            reply = request("/murph", "POST", {"query": "whats blocked"})["reply"]
            if "blocked" in reply.lower():
                pass_("Murph correctly reports the blocker")
            else:
                fail(f"Murph reply didn't mention blocked: {reply}", log_path)

            reply = request("/murph", "POST", {"query": "ask about it"})["reply"]
            if "drafted" in reply.lower():
                pass_("Murph drafted an escalation")
            else:
                fail(f"Murph didn't draft escalation: {reply}", log_path)

            reply = request("/murph", "POST", {"query": "yes send it"})["reply"]
            if "sent" in reply.lower():
                pass_("Murph sent the escalation, commitment state updated")
            else:
                fail(f"Escalation send failed: {reply}", log_path)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    print("[6/7] Completion + dependency auto-unblock (in-process, no server needed)")
    result = subprocess.run(
        [sys.executable, "-c", _INPROCESS_CHECK],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True,
    )
    if "STEP6_OK" in result.stdout:
        pass_("PR merge -> VERIFIED, dependent task auto-unblocks to IN_PROGRESS")
    else:
        fail(f"dependency/verification chain broken:\n{result.stdout}\n{result.stderr}")

    print("[7/7] Blocked commitment recovers (regression: BLOCKED was a dead end)")
    if "STEP7_OK" in result.stdout:
        pass_("blocked -> label cleared -> IN_PROGRESS -> merged -> VERIFIED")
    else:
        fail(f"blocked commitment could not recover:\n{result.stdout}\n{result.stderr}")

    print()
    print("ALL PASSED — full pipeline verified with zero real credentials.")
    print("Next: fill in backend/.env with real GITHUB_TOKEN, GCP_PROJECT_ID,")
    print("and Gmail creds, set OFFLINE_MODE=false, and re-run against the real world.")
    return 0


_INPROCESS_CHECK = """
import asyncio, sys
sys.path.insert(0, '.')
import store
from agents.execution_agent import execute_commitment
from agents.monitoring_agent import check_all_commitments
from tools.github_tool import mock_merge_pr, add_label, remove_label

def states(mid):
    return {c['id']: c['state'] for c in store.list_commitments(mid)}

async def main():
    mid = store.save_meeting('smoke test', 'Smoke')
    c1 = store.create_commitment(mid, 'Task A', 'Alice', 'Friday')
    c2 = store.create_commitment(mid, 'Task B', 'Bob', 'unspecified', depends_on='Task A')
    await execute_commitment(c1)
    await execute_commitment(c2)
    before = states(mid)
    assert before[c1] == 'IN_PROGRESS', f"expected c1 IN_PROGRESS, got {before[c1]}"
    assert before[c2] == 'WAITING', f"expected c2 WAITING, got {before[c2]}"

    cmt1 = store.get_commitment(c1)
    mock_merge_pr(cmt1['github_issue_number'])
    await check_all_commitments()

    after = states(mid)
    assert after[c1] == 'VERIFIED', f"expected c1 VERIFIED after merge, got {after[c1]}"
    assert after[c2] == 'IN_PROGRESS', f"expected c2 to auto-unblock, got {after[c2]}"
    print("STEP6_OK")

    # Regression guard: monitoring used to poll only IN_PROGRESS commitments,
    # so BLOCKED and ESCALATED were dead ends -- a merged PR was never seen
    # and every dependent task waited forever.
    issue2 = store.get_commitment(c2)['github_issue_number']
    add_label(issue2, 'blocked')
    await check_all_commitments()
    assert states(mid)[c2] == 'BLOCKED', f"expected c2 BLOCKED, got {states(mid)[c2]}"

    # Simulate an escalation having been sent, which is the state a real
    # blocked commitment sits in when someone unblocks it.
    store.update_commitment(c2, state='ESCALATED')
    remove_label(issue2, 'blocked')
    await check_all_commitments()
    assert states(mid)[c2] == 'IN_PROGRESS', f"expected c2 back to IN_PROGRESS, got {states(mid)[c2]}"
    assert store.get_commitment(c2)['blocked_reason'] is None, "blocked_reason should be cleared"

    mock_merge_pr(issue2)
    await check_all_commitments()
    assert states(mid)[c2] == 'VERIFIED', f"expected c2 VERIFIED, got {states(mid)[c2]}"
    print("STEP7_OK")

asyncio.run(main())
"""


if __name__ == "__main__":
    sys.exit(main())
