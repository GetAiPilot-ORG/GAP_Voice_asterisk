#!/usr/bin/env bash
# ==============================================================================
# GetAiPilot Voice Pilot: Real AI Voice Agent Test Caller
# Triggers an outbound AI call directly to your phone and streams live logs
# ==============================================================================
set -euo pipefail

WORKSPACE_ID="a2cb5823-d459-47c6-b7b3-f34713f3a403"
AGENT_ID="3c57e938-e485-4848-96d5-ff571d63a070"
TARGET_NUMBER="${1:-+919343418163}"
BACKEND_PORT="8010"

echo "======================================================================"
echo "  GetAiPilot Voice Pilot: AI Agent Live Outbound Call"
echo "======================================================================"
echo "Target Number: $TARGET_NUMBER"
echo "Workspace:     $WORKSPACE_ID"
echo "Agent:         $AGENT_ID"
echo "Time:          $(date)"
echo "======================================================================"

echo ""
echo "[1/4] Checking Backend & AudioSocket Health:"
echo "------------------------------------------------------"
if ! curl -s "http://127.0.0.1:${BACKEND_PORT}/health" >/dev/null 2>&1; then
    echo "[ERROR] FastAPI backend is not responding on port ${BACKEND_PORT}!"
    exit 1
fi
echo "[OK] Backend HTTP API is responding on port ${BACKEND_PORT}."

if ! ss -tulpn | grep -q ":9095 "; then
    echo "[ERROR] AudioSocket is not listening on port 9095!"
    exit 1
fi
echo "[OK] AudioSocket TCP server is listening on port 9095."

echo ""
echo "[2/4] Triggering Outbound AI Call via Backend API:"
echo "------------------------------------------------------"
RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST \
  "http://127.0.0.1:${BACKEND_PORT}/api/v1/workspaces/${WORKSPACE_ID}/agents/${AGENT_ID}/test-call" \
  -H "Content-Type: application/json" \
  -d "{\"to_number\": \"${TARGET_NUMBER}\", \"confirm_real_dialing\": true}")

HTTP_STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS:" | cut -d':' -f2)
BODY=$(echo "$RESPONSE" | grep -v "HTTP_STATUS:")

echo "Response Status: $HTTP_STATUS"
echo "Response Body:   $BODY"

if [ "$HTTP_STATUS" -ne 200 ]; then
    echo ""
    echo "[ERROR] Call trigger failed with HTTP $HTTP_STATUS!"
    echo "Check backend logs: tail -n 30 /root/.pm2/logs/asterisk-vp-backend-error.log"
    exit 1
fi

echo ""
echo "[3/4] CALL PLACED SUCCESSFULLY!"
echo "------------------------------------------------------"
echo ">>> YOUR PHONE SHOULD BE RINGING RIGHT NOW: $TARGET_NUMBER"
echo ">>> PLEASE ANSWER YOUR PHONE AND SPEAK TO THE AI AGENT."
echo ""
echo "[4/4] Streaming Real-Time AudioSocket & AI Logs (20 seconds):"
echo "------------------------------------------------------"
tail -f -n 0 /root/.pm2/logs/asterisk-vp-backend-out.log &
TAIL_PID=$!

# Trap ctrl-c to cleanly kill tail
trap "kill $TAIL_PID 2>/dev/null || true; exit 0" INT TERM

for i in $(seq 20 -1 1); do
    sleep 1
done

kill "$TAIL_PID" 2>/dev/null || true
echo ""
echo "======================================================================"
echo "  Call session monitoring completed."
echo "======================================================================"
