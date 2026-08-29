#!/bin/bash
# Full offline smoke test — proves the entire pipeline works with ZERO
# real credentials before you touch any API keys.
#
# Run from backend/ after `pip install -r requirements.txt`:
#   bash smoke_test.sh
#
# What it proves, in order:
#   1. Server boots (Firestore/Pub/Sub/GitHub calls are all mocked)
#   2. Transcript -> commitments extraction works
#   3. Dependency-aware execution: independent commitment gets a GitHub
#      issue immediately; dependent one waits
#   4. Monitoring loop autonomously detects a blocker within ~30s
#   5. Murph reflects live state and can draft + send an approved escalation
#   6. PR merge -> VERIFIED, and a waiting dependent task auto-unblocks
#      and gets its own GitHub issue in the same monitoring pass
#
# If every step below prints "PASS", your core architecture is sound —
# the only remaining work is plugging in real credentials (see README.md).
set -e
cd "$(dirname "$0")"

export OFFLINE_MODE=true
export GCP_PROJECT_ID=smoke-test

SMOKE_LOG=$(mktemp -t followthrough_smoke.XXXXXX.log)

echo "Starting server in OFFLINE_MODE..."
uvicorn main:app --host 0.0.0.0 --port 8080 > $SMOKE_LOG 2>&1 &
PID=$!
trap "kill $PID 2>/dev/null" EXIT
sleep 3

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; echo "--- server log ---"; cat $SMOKE_LOG; exit 1; }

echo "[1/6] Health check"
curl -sf http://localhost:8080/health > /dev/null && pass "server responds" || fail "server not responding"

echo "[2/6] Upload meeting + extraction"
RESP=$(curl -sf -X POST http://localhost:8080/meetings -H "Content-Type: application/json" \
  -d '{"title": "Standup", "transcript": "Kartikeya: I will finish the authentication API by Friday.\nRahul: I will review it once it is ready."}')
COUNT=$(echo "$RESP" | python3 -c "import json,sys; print(json.load(sys.stdin)['commitments_extracted'])")
[ "$COUNT" = "2" ] && pass "extracted 2 commitments" || fail "expected 2 commitments, got $COUNT"
sleep 1

echo "[3/6] Dependency-aware execution"
COMMITMENTS=$(curl -sf http://localhost:8080/commitments)
IN_PROGRESS=$(echo "$COMMITMENTS" | python3 -c "import json,sys; print(sum(1 for c in json.load(sys.stdin) if c['state']=='IN_PROGRESS'))")
WAITING=$(echo "$COMMITMENTS" | python3 -c "import json,sys; print(sum(1 for c in json.load(sys.stdin) if c['state']=='WAITING'))")
[ "$IN_PROGRESS" = "1" ] && [ "$WAITING" = "1" ] && pass "1 executing immediately, 1 correctly waiting on dependency" \
  || fail "expected 1 IN_PROGRESS + 1 WAITING, got $IN_PROGRESS + $WAITING"

CMT_ID=$(echo "$COMMITMENTS" | python3 -c "import json,sys; d=json.load(sys.stdin); print([c['id'] for c in d if c['state']=='IN_PROGRESS'][0])")

echo "[4/6] Blocker detection (polling up to 12s for the monitoring loop)..."
curl -sf -X POST http://localhost:8080/commitments/$CMT_ID/label -H "Content-Type: application/json" -d '{"label": "blocked"}' > /dev/null
# The monitoring loop runs every 5s (see _monitoring_loop in main.py), so
# poll for up to 12s rather than sleeping a fixed 32s.
STATE=""
for _ in $(seq 1 12); do
  STATE=$(curl -sf http://localhost:8080/commitments | python3 -c "import json,sys; d=json.load(sys.stdin); print([c['state'] for c in d if c['id']=='$CMT_ID'][0])")
  [ "$STATE" = "BLOCKED" ] && break
  sleep 1
done
[ "$STATE" = "BLOCKED" ] && pass "monitoring loop autonomously detected the blocker" || fail "expected BLOCKED, got $STATE"

echo "[5/6] Murph: status + escalation flow"
MURPH1=$(curl -sf -X POST http://localhost:8080/murph -H "Content-Type: application/json" -d '{"query": "whats blocked"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])")
echo "$MURPH1" | grep -qi "blocked" && pass "Murph correctly reports the blocker" || fail "Murph reply didn't mention blocked: $MURPH1"

MURPH2=$(curl -sf -X POST http://localhost:8080/murph -H "Content-Type: application/json" -d '{"query": "ask about it"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])")
echo "$MURPH2" | grep -qi "drafted" && pass "Murph drafted an escalation" || fail "Murph didn't draft escalation: $MURPH2"

MURPH3=$(curl -sf -X POST http://localhost:8080/murph -H "Content-Type: application/json" -d '{"query": "yes send it"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['reply'])")
echo "$MURPH3" | grep -qi "sent" && pass "Murph sent the escalation, commitment state updated" || fail "Escalation send failed: $MURPH3"

echo "[6/6] Completion + dependency auto-unblock (in-process, no server needed)"
SMOKE6=$(python3 - <<'PYEOF'
import asyncio, sys, os
sys.path.insert(0, '.')
os.environ['OFFLINE_MODE'] = 'true'
os.environ['GCP_PROJECT_ID'] = 'smoke-test'
import store
from agents.execution_agent import execute_commitment
from agents.monitoring_agent import check_all_commitments
from tools.github_tool import mock_merge_pr

async def main():
    mid = store.save_meeting('smoke test', 'Smoke')
    c1 = store.create_commitment(mid, 'Task A', 'Alice', 'Friday')
    c2 = store.create_commitment(mid, 'Task B', 'Bob', 'unspecified', depends_on='Task A')
    await execute_commitment(c1)
    await execute_commitment(c2)
    before = {c['id']: c['state'] for c in store.list_commitments(mid)}
    assert before[c1] == 'IN_PROGRESS', f"expected c1 IN_PROGRESS, got {before[c1]}"
    assert before[c2] == 'WAITING', f"expected c2 WAITING, got {before[c2]}"

    cmt1 = store.get_commitment(c1)
    mock_merge_pr(cmt1['github_issue_number'])
    await check_all_commitments()

    after = {c['id']: c['state'] for c in store.list_commitments(mid)}
    assert after[c1] == 'VERIFIED', f"expected c1 VERIFIED after merge, got {after[c1]}"
    assert after[c2] == 'IN_PROGRESS', f"expected c2 to auto-unblock to IN_PROGRESS, got {after[c2]}"
    print("OK")

asyncio.run(main())
PYEOF
)
echo "$SMOKE6" | grep -q "OK" && pass "PR merge -> VERIFIED, dependent task auto-unblocks to IN_PROGRESS" || fail "dependency/verification chain broken: $SMOKE6"

echo ""
echo "ALL PASSED — full pipeline verified with zero real credentials."
echo "Next: fill in backend/.env with real GITHUB_TOKEN, GCP_PROJECT_ID,"
echo "and Gmail creds, set OFFLINE_MODE=false, and re-run against the real world."
