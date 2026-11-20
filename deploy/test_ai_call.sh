#!/usr/bin/env bash
# ==============================================================================
# GetAiPilot Voice Pilot: AI Voice Agent Live Outbound Call Tester
# Triggers real Sarvam AI voice call to customer phone & monitors media path
# ==============================================================================
set -euo pipefail

TARGET_NUMBER="${1:-+919343418163}"
WORKSPACE_ID="a2cb5823-d459-47c6-b7b3-f34713f3a403"
AGENT_ID="3c57e938-e485-4848-96d5-ff571d63a070"
API_URL="http://127.0.0.1:8010/api/v1/workspaces/${WORKSPACE_ID}/agents/${AGENT_ID}/test-call"

echo "======================================================================"
echo "  GetAiPilot Voice Pilot: Real AI Voice Agent Call Tester"
echo "======================================================================"
echo "Target Phone:  $TARGET_NUMBER"
echo "Workspace ID:  $WORKSPACE_ID"
echo "Agent ID:      $AGENT_ID"
echo "API Endpoint:  $API_URL"
echo "======================================================================"

echo ""
echo "[1/4] Ensuring Asterisk WAV Test Tone Exists:"
echo "------------------------------------------------------"
python3 -c "
import wave, struct, math, os
os.makedirs('/var/lib/asterisk/sounds', exist_ok=True)
tone_path = '/var/lib/asterisk/sounds/test-tone.wav'
with wave.open(tone_path, 'w') as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(8000)
    for i in range(8000 * 6):
        val = int(18000 * math.sin(2 * math.pi * 440 * i / 8000))
        wav.writeframes(struct.pack('<h', val))
print('  [OK] Generated 6s 440Hz test tone at ' + tone_path)
"

echo ""
echo "[2/4] Syncing Asterisk Configurations & Reloading:"
echo "------------------------------------------------------"
if [ -f "/root/asterisk/deploy/asterisk/pjsip.conf" ]; then
    cp /root/asterisk/deploy/asterisk/pjsip.conf /etc/asterisk/pjsip.conf
    asterisk -rx "pjsip reload"
    echo "  [OK] PJSIP reloaded (allow=ulaw,alaw restored for 24IT Hub)."
fi
if [ -f "/root/asterisk/deploy/asterisk/extensions.conf" ]; then
    cp /root/asterisk/deploy/asterisk/extensions.conf /etc/asterisk/extensions.conf
    asterisk -rx "dialplan reload"
    echo "  [OK] Dialplan reloaded."
fi

echo ""
echo "[3/4] Generating Workspace Admin Auth Token & Triggering Call:"
echo "------------------------------------------------------"
AUTH_TOKEN=$(python3 -c "
import jwt, time, base64, os
env_file = '/root/asterisk/backend/.env'
secret_raw = ''
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            if line.startswith('SUPABASE_JWT_SECRET='):
                secret_raw = line.split('=', 1)[1].strip().strip('\"').strip('\'')
try:
    secret = base64.b64decode(secret_raw)
except Exception:
    secret = secret_raw

payload = {
    'sub': '9dfaaf1d-02c9-4f2e-aec9-e3d1f5154ae7',
    'role': 'authenticated',
    'aud': 'authenticated',
    'exp': int(time.time()) + 7200
}
token = jwt.encode(payload, secret, algorithm='HS256')
print(token)
")

RESP=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "$API_URL" \
  -H "Authorization: Bearer ${AUTH_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"to_number\": \"${TARGET_NUMBER}\", \"confirm_real_dialing\": true}")

HTTP_CODE=$(echo "$RESP" | grep "HTTP_STATUS" | cut -d':' -f2)
BODY=$(echo "$RESP" | grep -v "HTTP_STATUS")

echo "Backend Response (HTTP $HTTP_CODE):"
echo "$BODY"

if [ "$HTTP_CODE" != "200" ]; then
    echo ""
    echo "[ERROR] Backend call origination failed with HTTP $HTTP_CODE!"
    exit 1
fi

echo ""
echo "[4/4] CALL ORIGINATED! YOUR PHONE SHOULD RING NOW: $TARGET_NUMBER"
echo "======================================================================"
echo ">>> PLEASE ANSWER THE CALL ON YOUR PHONE."
echo ">>> Sarvam AI (Shreya) will speak the greeting as soon as you answer."
echo ">>> Streaming live backend logs below (Press Ctrl+C to exit)..."
echo "======================================================================"

# Tail the PM2 backend log live to see AudioSocket & Sarvam TTS activity
timeout 30 pm2 logs asterisk-vp-backend --lines 0 || true
