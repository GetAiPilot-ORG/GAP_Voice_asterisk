#!/usr/bin/env bash
# ==============================================================================
# GetAiPilot Voice Pilot: Phase 1 Controlled Experiments (E1 to E4)
# Proves root cause of outbound media silence before committing code changes.
# ==============================================================================
set -euo pipefail

TARGET_NUMBER="${2:-09343418163}"
TRUNK="provider-10ca861d-d002-4f52-8b2a-fbefea08a6de"
CALLER_ID="911168140253"
CARRIER_DOMAIN="cpbx.24ithub.com"
TEST_MODE="${1:-all}"

SPOOL_DIR="/var/spool/asterisk/outgoing"
LOG_DIR="/tmp/phase1_traces"
mkdir -p "$LOG_DIR"

cleanup() {
    asterisk -rx "pjsip set logger off" >/dev/null 2>&1 || true
    asterisk -rx "rtp set debug off" >/dev/null 2>&1 || true
    pkill -f "tcpdump.*phase1" >/dev/null 2>&1 || true
    pkill -f "asterisk.*-rvvvvv.*phase1" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "======================================================================"
echo "  GetAiPilot Voice Pilot: Phase 1 Telephony Diagnostic Engine"
echo "======================================================================"
echo "Target Phone:     $TARGET_NUMBER"
echo "Caller ID (DID):  $CALLER_ID"
echo "SIP Trunk:        $TRUNK"
echo "Carrier Domain:   $CARRIER_DOMAIN"
echo "Selected Mode:    $TEST_MODE (options: e1, e2, e3, e4, all)"
echo "Traces Directory: $LOG_DIR"
echo "======================================================================"

# Ensure standard audio files exist
ensure_audio_files() {
    python3 -c "
import wave, struct, math, os
os.makedirs('/var/lib/asterisk/sounds/en', exist_ok=True)
os.makedirs('/var/lib/asterisk/sounds', exist_ok=True)
for p in ['/var/lib/asterisk/sounds/test-tone.wav', '/var/lib/asterisk/sounds/en/test-tone.wav']:
    with wave.open(p, 'w') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        for i in range(8000 * 6):
            val = int(18000 * math.sin(2 * math.pi * 440 * i / 8000))
            wav.writeframes(struct.pack('<h', val))
" 2>/dev/null || true
}

analyze_pcap() {
    local pcap_file="$1"
    local test_name="$2"
    echo ""
    echo "------------------------------------------------------"
    echo "  [Analysis Report: $test_name]"
    echo "------------------------------------------------------"
    if [ ! -f "$pcap_file" ] || [ ! -s "$pcap_file" ]; then
        echo "  [WARN] PCAP capture file is missing or empty."
        return
    fi

    python3 -c "
with open('$pcap_file', 'rb') as f:
    data = f.read()

import re
print('1. SIP Signaling Headers:')
all_sip = re.findall(rb'((?:INVITE|SIP/2\.0 \d{3})[^\r\n]+.*?\r\n\r\n)', data, re.DOTALL)
for s in all_sip:
    text = s.decode(errors='ignore')
    first_line = text.split('\r\n')[0]
    if any(k in first_line for k in ['INVITE', '100', '180', '183', '200 OK', 'BYE']):
        print(f'   >>> {first_line}')
        for header in text.split('\r\n'):
            if any(h in header for h in ['From:', 'To:', 'P-Asserted-Identity:', 'Remote-Party-ID:', 'Privacy:', 'Contact:', 'User-Agent:']):
                print(f'       {header}')

print('\n2. 200 OK SDP Media Formats:')
sdp_matches = re.findall(rb'(SIP/2\.0 200 OK.*?\r\n\r\n.*?m=audio[^\r\n]*(?:\r\n[a-z]=[^\r\n]*)*)', data, re.DOTALL)
for sdp in sdp_matches[:1]:
    for line in sdp.decode(errors='ignore').split('\r\n'):
        if any(line.startswith(prefix) for prefix in ['m=audio', 'c=IN', 'a=rtpmap', 'a=sendrecv', 'a=fmtp']):
            print(f'       {line}')

print('\n3. RTP Packet Count & Payload Analysis:')
rtp_out = 0
rtp_in = 0
payload_types = set()
pos = 0
# VPS IP 72.60.202.148 (\x48\x3c\xca\x94), Carrier Gateway 202.89.78.2 (\xca\x59\x4e\x02)
while True:
    idx = data.find(b'\x48\x3c\xca\x94', pos)
    if idx == -1:
        break
    pos = idx + 4
    pkt = data[max(0, idx-14):idx+250]
    if b'\xca\x59\x4e\x02' in pkt or b'\x9f\x41\x9c\xca' in pkt: # 159.65.156.202
        rtp_idx = pkt.find(b'\x80')
        if rtp_idx != -1 and len(pkt) > rtp_idx + 12:
            pt = pkt[rtp_idx + 1] & 0x7F
            payload_types.add(pt)
            rtp_out += 1

pos = 0
while True:
    idx = data.find(b'\xca\x59\x4e\x02', pos)
    if idx == -1:
        idx = data.find(b'\x9f\x41\x9c\xca', pos)
        if idx == -1:
            break
    pos = idx + 4
    pkt = data[max(0, idx-14):idx+250]
    if b'\x48\x3c\xca\x94' in pkt:
        rtp_in += 1

print(f'   RTP Sent by Asterisk to Carrier:     {rtp_out} packets')
print(f'   RTP Received from Carrier by VPS:     {rtp_in} packets')
print(f'   RTP Payload Types Observed:           {payload_types}')
if 13 in payload_types and 0 not in payload_types and 8 not in payload_types:
    print('   [CRITICAL FINDING] Only Comfort Noise (Payload 13) was transmitted by Asterisk!')
elif 0 in payload_types or 8 in payload_types:
    print('   [CONFIRMED] Active audio PCM frames (Payload 0=PCMU / 8=PCMA) were successfully transmitted!')
"
}

run_e4_translation() {
    echo ""
    echo "======================================================================"
    echo "  EXPERIMENT E4: Codec & Audio Translation Matrix"
    echo "======================================================================"
    echo "Checking Asterisk 20 codec translation table (slin <-> ulaw / alaw):"
    asterisk -rx "core show translation" || true
    echo ""
    echo "Checking direct translation path for slin (AudioSocket format):"
    asterisk -rx "core show translation paths ulaw alaw slin slin16" || true
}

run_e1_direct_playback() {
    echo ""
    echo "======================================================================"
    echo "  EXPERIMENT E1: Direct PJSIP Originate with Native Playback"
    echo "  Channel:  PJSIP/${TARGET_NUMBER}@${TRUNK}"
    echo "  Target:   outbound-test-playback (Hello World & Congrats)"
    echo "======================================================================"
    ensure_audio_files
    local pcap="$LOG_DIR/e1_direct_playback.pcap"
    local ast_log="$LOG_DIR/e1_direct_playback.log"
    rm -f "$pcap" "$ast_log"

    asterisk -rx "pjsip set logger on" >/dev/null
    asterisk -rx "rtp set debug on" >/dev/null

    tcpdump -i any -nn -s 0 -w "$pcap" "port 5060 or (udp and portrange 10000-20000)" >/dev/null 2>&1 &
    local tcp_pid=$!
    timeout 25 asterisk -rvvvvv -n > "$ast_log" 2>&1 &
    local ast_pid=$!

    sleep 1

    # Create temporary call file
    local call_tmp="/tmp/e1_direct_$$.call"
    cat <<EOF > "$call_tmp"
Channel: PJSIP/${TARGET_NUMBER}@${TRUNK}
CallerID: "${CALLER_ID}" <${CALLER_ID}>
Context: outbound-test-playback
Extension: test
Priority: 1
MaxRetries: 0
RetryTime: 60
WaitTime: 30
Set: CALLERID(pres)=allowed
Set: PJSIP_HEADER(add,P-Asserted-Identity)=<sip:${CALLER_ID}@${CARRIER_DOMAIN}>
Set: PJSIP_HEADER(add,Remote-Party-ID)=<sip:${CALLER_ID}@${CARRIER_DOMAIN}>;party=calling;privacy=off;screen=no
EOF
    chmod 666 "$call_tmp"
    mv "$call_tmp" "$SPOOL_DIR/e1_direct.call"

    echo ">>> Direct PJSIP call file dropped into Asterisk spool."
    echo ">>> YOUR PHONE SHOULD RING NOW: $TARGET_NUMBER"
    echo ">>> PLEASE ANSWER THE CALL. You should hear 'Hello World'."
    echo ">>> Capturing 20 seconds of media..."
    for i in $(seq 20 -1 1); do
        printf "\rRemaining: %2d s" "$i"
        sleep 1
    done
    echo ""

    kill "$tcp_pid" 2>/dev/null || true
    kill "$ast_pid" 2>/dev/null || true
    cleanup

    analyze_pcap "$pcap" "E1: Direct PJSIP Originate with Native Playback"
}

run_e2_direct_audiosocket() {
    echo ""
    echo "======================================================================"
    echo "  EXPERIMENT E2: Direct PJSIP Originate with AudioSocket AI Greeting"
    echo "  Channel:  PJSIP/${TARGET_NUMBER}@${TRUNK}"
    echo "  Target:   outbound-ai-answered -> AudioSocket Port 9095"
    echo "======================================================================"
    ensure_audio_files
    local pcap="$LOG_DIR/e2_direct_audiosocket.pcap"
    local ast_log="$LOG_DIR/e2_direct_audiosocket.log"
    rm -f "$pcap" "$ast_log"

    # Pre-register call session in CallSessionManager via Python on VPS
    local call_uuid
    call_uuid=$(python3 -c "
import uuid, sys
from app.db.client import get_supabase_client
from app.services.call_session_manager import call_session_manager

u = str(uuid.uuid4())
db = get_supabase_client()
# Get active workspace and agent
ws_id = 'a2cb5823-d459-47c6-b7b3-f34713f3a403'
ag_id = '3c57e938-e485-4848-96d5-ff571d63a070'
did_res = db.table('did_numbers').select('id, phone_number').eq('agent_id', ag_id).execute()
did_id = did_res.data[0]['id'] if did_res.data else None

# Register in DB
db.table('calls').insert({
    'id': u,
    'call_uuid': u,
    'twilio_call_sid': u,
    'workspace_id': ws_id,
    'agent_id': ag_id,
    'caller_phone_number': '${CALLER_ID}',
    'caller_id': '${CALLER_ID}',
    'dialed_number': '${TARGET_NUMBER}',
    'direction': 'outbound',
    'status': 'ringing',
    'provider': 'asterisk',
    'metadata': {'provider': 'asterisk', 'is_test': True, 'experiment': 'E2'}
}).execute()

# Register in memory
call_session_manager.register_inbound_asterisk_call(
    call_uuid=u,
    caller_id='${CALLER_ID}',
    dialed_number='${TARGET_NUMBER}',
    workspace_id=ws_id,
    agent_id=ag_id,
    phone_number_id=did_id or '',
    direction='outbound'
)
print(u)
" 2>/dev/null || python3 -c "import uuid; print(str(uuid.uuid4()))")

    echo "Registered Test Call UUID: $call_uuid"

    asterisk -rx "pjsip set logger on" >/dev/null
    asterisk -rx "rtp set debug on" >/dev/null

    tcpdump -i any -nn -s 0 -w "$pcap" "port 5060 or port 9095 or (udp and portrange 10000-20000)" >/dev/null 2>&1 &
    local tcp_pid=$!
    timeout 25 asterisk -rvvvvv -n > "$ast_log" 2>&1 &
    local ast_pid=$!

    sleep 1

    local call_tmp="/tmp/e2_direct_$$.call"
    cat <<EOF > "$call_tmp"
Channel: PJSIP/${TARGET_NUMBER}@${TRUNK}
CallerID: "${CALLER_ID}" <${CALLER_ID}>
Context: outbound-ai-answered
Extension: ${call_uuid}
Priority: 1
MaxRetries: 0
RetryTime: 60
WaitTime: 30
Set: CALLERID(pres)=allowed
Set: PJSIP_HEADER(add,P-Asserted-Identity)=<sip:${CALLER_ID}@${CARRIER_DOMAIN}>
Set: PJSIP_HEADER(add,Remote-Party-ID)=<sip:${CALLER_ID}@${CARRIER_DOMAIN}>;party=calling;privacy=off;screen=no
EOF
    chmod 666 "$call_tmp"
    mv "$call_tmp" "$SPOOL_DIR/e2_direct.call"

    echo ">>> Direct AudioSocket call file dropped into spool for UUID: $call_uuid"
    echo ">>> YOUR PHONE SHOULD RING NOW: $TARGET_NUMBER"
    echo ">>> PLEASE ANSWER THE CALL. Sarvam AI should speak the greeting."
    echo ">>> Capturing 25 seconds of media..."
    for i in $(seq 25 -1 1); do
        printf "\rRemaining: %2d s" "$i"
        sleep 1
    done
    echo ""

    kill "$tcp_pid" 2>/dev/null || true
    kill "$ast_pid" 2>/dev/null || true
    cleanup

    analyze_pcap "$pcap" "E2: Direct PJSIP Originate with AudioSocket AI Greeting"
}

run_e3_local_playback() {
    echo ""
    echo "======================================================================"
    echo "  EXPERIMENT E3: Local Channel Originate with Native Playback"
    echo "  Channel:  Local/${TARGET_NUMBER}@outbound-dial-carrier"
    echo "  Target:   outbound-test-playback (Local channel bridge test)"
    echo "======================================================================"
    ensure_audio_files
    local pcap="$LOG_DIR/e3_local_playback.pcap"
    local ast_log="$LOG_DIR/e3_local_playback.log"
    rm -f "$pcap" "$ast_log"

    asterisk -rx "pjsip set logger on" >/dev/null
    asterisk -rx "rtp set debug on" >/dev/null

    tcpdump -i any -nn -s 0 -w "$pcap" "port 5060 or (udp and portrange 10000-20000)" >/dev/null 2>&1 &
    local tcp_pid=$!
    timeout 25 asterisk -rvvvvv -n > "$ast_log" 2>&1 &
    local ast_pid=$!

    sleep 1

    local call_tmp="/tmp/e3_local_$$.call"
    cat <<EOF > "$call_tmp"
Channel: Local/${TARGET_NUMBER}@outbound-dial-carrier
CallerID: "${CALLER_ID}" <${CALLER_ID}>
Context: outbound-test-playback
Extension: test
Priority: 1
MaxRetries: 0
RetryTime: 60
WaitTime: 30
EOF
    chmod 666 "$call_tmp"
    mv "$call_tmp" "$SPOOL_DIR/e3_local.call"

    echo ">>> Local Channel call file dropped into spool."
    echo ">>> YOUR PHONE SHOULD RING NOW: $TARGET_NUMBER"
    echo ">>> PLEASE ANSWER THE CALL."
    echo ">>> Capturing 20 seconds of media..."
    for i in $(seq 20 -1 1); do
        printf "\rRemaining: %2d s" "$i"
        sleep 1
    done
    echo ""

    kill "$tcp_pid" 2>/dev/null || true
    kill "$ast_pid" 2>/dev/null || true
    cleanup

    analyze_pcap "$pcap" "E3: Local Channel Originate with Native Playback"
}

case "$TEST_MODE" in
    e1)
        run_e1_direct_playback
        ;;
    e2)
        run_e2_direct_audiosocket
        ;;
    e3)
        run_e3_local_playback
        ;;
    e4)
        run_e4_translation
        ;;
    all)
        run_e4_translation
        echo ""
        read -p "Press [Enter] to run Experiment E1 (Direct Playback) or Ctrl+C to cancel..." || true
        run_e1_direct_playback
        echo ""
        read -p "Press [Enter] to run Experiment E2 (Direct AudioSocket AI Greeting) or Ctrl+C to cancel..." || true
        run_e2_direct_audiosocket
        ;;
    *)
        echo "Unknown mode: $TEST_MODE. Usage: $0 [e1|e2|e3|e4|all] [target_number]"
        exit 1
        ;;
esac

echo ""
echo "======================================================================"
echo "  Phase 1 Experiments Complete. Traces stored in $LOG_DIR"
echo "======================================================================"
