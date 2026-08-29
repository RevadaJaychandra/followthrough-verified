#!/bin/bash
# Full offline smoke test - proves the entire pipeline works with ZERO
# real credentials before you touch any API keys.
#
# smoke_test.py is the canonical test and runs anywhere Python does. This bash
# version is kept for Linux and CI, and needs curl plus a `python3` on PATH new
# enough to run the app (3.10+). The two are deliberately kept in step; if you
# change one, change the other.
#
# Run from backend/ after `pip install -r requirements.txt`:
#   bash smoke_test.sh
#
# What it proves, in order:
#   1. Server boots (Firestore/Pub/Sub/GitHub/Gmail all mocked in-memory)
#   2. Transcript -> commitments extraction works
#   3. Dependency-aware execution: independent commitment gets a GitHub
#      issue immediately, dependent one waits
#   4. Monitoring loop autonomously detects a blocker
#   5. Murph drafts an escalation and CANNOT send it: telling the agent
#      "yes send it" leaves the draft PENDING_APPROVAL. Only a human
#      approval through the API actually sends.
#   6. PR merge -> VERIFIED, and a waiting dependent task auto-unblocks
#      and gets its own GitHub issue in the same monitoring pass
#   7. A BLOCKED/ESCALATED commitment is not a dead end: clearing the
#      'blocked' label returns it to IN_PROGRESS and it can still verify
#   8. Spoken deadline phrases resolve to real dates, and ambiguous ones
#      resolve to nothing rather than a wrong guess
#
# If every step below prints "PASS", your core architecture is sound -
# the only remaining work is plugging in real credentials (see README.md).
set -e
cd "$(dirname "$0")"

export OFFLINE_MODE=true
export GCP_PROJECT_ID=smoke-test

SMOKE_LOG=$(mktemp -t followthrough_smoke.XXXXXX.log)

echo "Starting server in OFFLINE_MODE..."
uvicorn main:app --host 127.0.0.1 --port 8080 --log-level warning > "$SMOKE_LOG" 2>&1 &
PID=$!
trap "kill $PID 2>/dev/null" EXIT

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; echo "--- server log ---"; cat "$SMOKE_LOG"; exit 1; }

API=http://127.0.0.1:8080
jq_py() { python3 -c "import json,sys; $1"; }

echo "[1/8] Health check"
for _ in $(seq 1 20); do
  curl -sf $API/health > /dev/null && break
  sleep 0.5
done
curl -sf $API/health > /dev/null && pass "server responds" || fail "server not responding"

echo "[2/8] Upload meeting + extraction"
RESP=$(curl -sf -X POST $API/meetings -H "Content-Type: application/json" \
  -d '{"title": "Standup", "transcript": "Kartikeya: I will finish the authentication API by Friday.\nRahul: I will review it once it is ready."}')
COUNT=$(echo "$RESP" | jq_py "print(json.load(sys.stdin)['commitments_extracted'])")
[ "$COUNT" = "2" ] && pass "extracted 2 commitments" || fail "expected 2 commitments, got $COUNT"

echo "[3/8] Dependency-aware execution"
# The Pub/Sub subscriber runs execution on a background thread.
for _ in $(seq 1 20); do
  COMMITMENTS=$(curl -sf $API/commitments)
  IN_PROGRESS=$(echo "$COMMITMENTS" | jq_py "print(sum(1 for c in json.load(sys.stdin) if c['state']=='IN_PROGRESS'))")
  WAITING=$(echo "$COMMITMENTS" | jq_py "print(sum(1 for c in json.load(sys.stdin) if c['state']=='WAITING'))")
  [ "$IN_PROGRESS" = "1" ] && [ "$WAITING" = "1" ] && break
  sleep 0.5
done
[ "$IN_PROGRESS" = "1" ] && [ "$WAITING" = "1" ] && pass "1 executing immediately, 1 correctly waiting on dependency" \
  || fail "expected 1 IN_PROGRESS + 1 WAITING, got $IN_PROGRESS + $WAITING"

CMT_ID=$(echo "$COMMITMENTS" | jq_py "d=json.load(sys.stdin); print([c['id'] for c in d if c['state']=='IN_PROGRESS'][0])")

echo "[4/8] Blocker detection (polling up to 12s for the monitoring loop)..."
curl -sf -X POST $API/commitments/$CMT_ID/label -H "Content-Type: application/json" -d '{"label": "blocked"}' > /dev/null
# The monitoring loop runs every 5s (see _monitoring_loop in main.py).
STATE=""
for _ in $(seq 1 12); do
  STATE=$(curl -sf $API/commitments | jq_py "d=json.load(sys.stdin); print([c['state'] for c in d if c['id']=='$CMT_ID'][0])")
  [ "$STATE" = "BLOCKED" ] && break
  sleep 1
done
[ "$STATE" = "BLOCKED" ] && pass "monitoring loop autonomously detected the blocker" || fail "expected BLOCKED, got $STATE"

echo "[5/8] Murph: status + human-approval-gated escalation"
MURPH1=$(curl -sf -X POST $API/murph -H "Content-Type: application/json" -d '{"query": "whats blocked"}' | jq_py "print(json.load(sys.stdin)['reply'])")
echo "$MURPH1" | grep -qi "blocked" && pass "Murph correctly reports the blocker" || fail "Murph reply didn't mention blocked: $MURPH1"

MURPH2=$(curl -sf -X POST $API/murph -H "Content-Type: application/json" -d '{"query": "ask about it"}' | jq_py "print(json.load(sys.stdin)['reply'])")
echo "$MURPH2" | grep -qi "drafted" && pass "Murph drafted an escalation" || fail "Murph didn't draft escalation: $MURPH2"

PENDING_COUNT=$(curl -sf "$API/escalations?status=PENDING_APPROVAL" | jq_py "print(len(json.load(sys.stdin)))")
[ "$PENDING_COUNT" = "1" ] && pass "draft is queued as PENDING_APPROVAL, not sent" \
  || fail "expected 1 pending escalation, got $PENDING_COUNT"
ESC_ID=$(curl -sf "$API/escalations?status=PENDING_APPROVAL" | jq_py "print(json.load(sys.stdin)[0]['id'])")

# The safety property: telling the agent to send it is not approval.
curl -sf -X POST $API/murph -H "Content-Type: application/json" -d '{"query": "yes send it now"}' > /dev/null
STILL=$(curl -sf $API/escalations | jq_py "print(json.load(sys.stdin)[0]['status'])")
[ "$STILL" = "PENDING_APPROVAL" ] && pass "agent cannot self-approve: still PENDING_APPROVAL after 'yes send it'" \
  || fail "approval gate leaked - status became $STILL without human approval"

SENT=$(curl -sf -X POST $API/escalations/$ESC_ID/approve -H "Content-Type: application/json" \
  -d '{"approved_by": "smoke-test-human"}' | jq_py "print(json.load(sys.stdin)['escalation']['status'])")
[ "$SENT" = "SENT" ] && pass "human approval sends it, commitment state updated" \
  || fail "approved escalation not sent, status is $SENT"

echo "[6/8] Completion + dependency auto-unblock (in-process, no server needed)"
echo "[7/8] Blocked commitment recovers (regression: BLOCKED was a dead end)"
SMOKE67=$(python3 - <<'PYEOF'
import asyncio, sys, os
sys.path.insert(0, '.')
os.environ['OFFLINE_MODE'] = 'true'
os.environ['GCP_PROJECT_ID'] = 'smoke-test'
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

    mock_merge_pr(store.get_commitment(c1)['github_issue_number'])
    await check_all_commitments()

    after = states(mid)
    assert after[c1] == 'VERIFIED', f"expected c1 VERIFIED after merge, got {after[c1]}"
    assert after[c2] == 'IN_PROGRESS', f"expected c2 to auto-unblock, got {after[c2]}"
    print("STEP6_OK")

    issue2 = store.get_commitment(c2)['github_issue_number']
    add_label(issue2, 'blocked')
    await check_all_commitments()
    assert states(mid)[c2] == 'BLOCKED', f"expected c2 BLOCKED, got {states(mid)[c2]}"

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
PYEOF
)
echo "$SMOKE67" | grep -q "STEP6_OK" && pass "PR merge -> VERIFIED, dependent task auto-unblocks to IN_PROGRESS" \
  || fail "dependency/verification chain broken: $SMOKE67"
echo "$SMOKE67" | grep -q "STEP7_OK" && pass "blocked -> label cleared -> IN_PROGRESS -> merged -> VERIFIED" \
  || fail "blocked commitment could not recover: $SMOKE67"

echo "[8/8] Deadline parsing"
python3 test_deadlines.py > /dev/null && pass "spoken deadline phrases resolve correctly, ambiguous ones stay None" \
  || fail "deadline parsing broken"

echo ""
echo "ALL PASSED - full pipeline verified with zero real credentials."
echo "Next: fill in backend/.env with real GITHUB_TOKEN, GCP_PROJECT_ID,"
echo "and Gmail creds, set OFFLINE_MODE=false, and re-run against the real world."
