#!/usr/bin/env bash
# validate-integration.sh — Verify AgentProof stack is running and capturing events.
#
# Sends a test completion through the LiteLLM proxy and confirms the event
# appears in the AgentProof API. Exits 0 if all checks pass, 1 otherwise.

set -uo pipefail

LITELLM_URL="${LITELLM_URL:-http://localhost:4000}"
LITELLM_KEY="${LITELLM_KEY:-sk-local-dev-key}"
API_URL="${API_URL:-http://localhost:8100}"

PASS=0
FAIL=0

check() {
    local name="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        printf "  %-45s PASS\n" "$name"
        ((PASS++))
    else
        printf "  %-45s FAIL\n" "$name"
        ((FAIL++))
    fi
}

echo ""
echo "AgentProof Integration Validation"
echo "=================================="
echo ""

# ── 1. Docker ────────────────────────────────────────────────
echo "[1/5] Docker"
check "Docker daemon running" docker info

# ── 2. AgentProof API ────────────────────────────────────────
echo "[2/5] AgentProof API"
check "API health (${API_URL})" curl -sf "${API_URL}/health"

# ── 3. LiteLLM proxy ────────────────────────────────────────
echo "[3/5] LiteLLM proxy"
check "Proxy health (${LITELLM_URL})" curl -sf "${LITELLM_URL}/health"

# ── 4. Test completion ───────────────────────────────────────
echo "[4/5] Send test completion through proxy"

# Grab event count before the test request
BEFORE=$(curl -sf "${API_URL}/api/v1/events" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('count', len(d)) if isinstance(d, dict) else len(d))" 2>/dev/null || echo "0")

COMPLETION_STATUS=0
RESPONSE=$(curl -sf -X POST "${LITELLM_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${LITELLM_KEY}" \
    -d '{
        "model": "claude-haiku",
        "messages": [{"role": "user", "content": "Say OK"}],
        "max_tokens": 5
    }' 2>&1) || COMPLETION_STATUS=$?

if [ "$COMPLETION_STATUS" -eq 0 ]; then
    printf "  %-45s PASS\n" "Proxy returned completion"
    ((PASS++))
else
    printf "  %-45s FAIL\n" "Proxy returned completion"
    ((FAIL++))
    echo "    Error: ${RESPONSE}"
fi

# ── 5. Event captured ───────────────────────────────────────
echo "[5/5] Verify event captured"

# Give the async pipeline a moment to flush
sleep 2

AFTER=$(curl -sf "${API_URL}/api/v1/events" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('count', len(d)) if isinstance(d, dict) else len(d))" 2>/dev/null || echo "0")

if [ "$AFTER" -gt "$BEFORE" ]; then
    printf "  %-45s PASS\n" "New event recorded in API"
    ((PASS++))
else
    printf "  %-45s FAIL\n" "New event recorded in API"
    ((FAIL++))
    echo "    Before: ${BEFORE}, After: ${AFTER}"
    echo "    Check: docker compose logs litellm | grep -i callback"
fi

# ── Summary ──────────────────────────────────────────────────
echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
echo ""

if [ "$FAIL" -gt 0 ]; then
    echo "Some checks failed. Run 'docker compose logs' for details."
    exit 1
else
    echo "All checks passed. Integration is working."
    exit 0
fi
