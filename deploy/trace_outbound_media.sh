#!/usr/bin/env bash
# ==============================================================================
# GetAiPilot Voice Pilot: Live Outbound Media & RTP Tracer
# Captures SIP signaling + RTP packets + firewall status during a live test call
# ==============================================================================
set -euo pipefail

TARGET_NUMBER="${1:-09343418163}"
ENDPOINT="provider-10ca861d-d002-4f52-8b2a-fbefea08a6de"
LOG_CAPTURE="/tmp/asterisk_media_live.log"
PCAP_CAPTURE="/tmp/media_trace.pcap"

echo "======================================================================"
echo "  GetAiPilot Voice Pilot: Outbound Media & RTP Live Trace"
echo "======================================================================"
echo "Target Number: $TARGET_NUMBER"
echo "SIP Endpoint:  $ENDPOINT"
echo "Time:          $(date)"
echo "======================================================================"

echo ""
echo "[1/6] Checking Linux Firewall (UFW / iptables):"
echo "------------------------------------------------------"
if command -v ufw >/dev/null 2>&1; then
    UFW_STATUS=$(ufw status | head -n 1)
    echo "UFW Status: $UFW_STATUS"
    if ufw status | grep -q "10000:20000"; then
        echo "  [OK] RTP Ports 10000:20000/udp explicitly allowed in UFW"
    else
        echo "  [WARN] RTP Ports 10000:20000/udp NOT found in UFW rules!"
        echo "         If UFW is active, incoming RTP from carrier may be dropped."
    fi
else
    echo "UFW not installed, checking iptables..."
fi

echo ""
echo "[2/6] Syncing PJSIP & Dialplan Configurations:"
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
" 2>/dev/null || true
if [ -f "/root/asterisk/deploy/asterisk/pjsip.conf" ]; then
    cp /root/asterisk/deploy/asterisk/pjsip.conf /etc/asterisk/pjsip.conf
    asterisk -rx "pjsip reload"
    echo "PJSIP reloaded with latest trunk settings."
fi
if [ -f "/root/asterisk/deploy/asterisk/extensions.conf" ]; then
    cp /root/asterisk/deploy/asterisk/extensions.conf /etc/asterisk/extensions.conf
    asterisk -rx "dialplan reload"
    echo "Dialplan reloaded with [outbound-test-playback]."
fi

echo ""
echo "[3/6] Starting Live Capture Engine (SIP + RTP):"
echo "------------------------------------------------------"
# Clean old capture
rm -f "$LOG_CAPTURE" "$PCAP_CAPTURE"

# Enable Asterisk debug
asterisk -rx "pjsip set logger on"
asterisk -rx "rtp set debug on"

# Start Asterisk console capture in background (with -n for batch non-TTY output)
timeout 22 asterisk -rvvvvv -n > "$LOG_CAPTURE" 2>&1 &
AST_PID=$!

# Start tcpdump if available
TCP_PID=""
if command -v tcpdump >/dev/null 2>&1; then
    tcpdump -i any -nn -s 0 -w "$PCAP_CAPTURE" "port 5060 or (udp and portrange 10000-20000)" >/dev/null 2>&1 &
    TCP_PID=$!
    echo "tcpdump started (PID: $TCP_PID) -> $PCAP_CAPTURE"
fi
echo "Asterisk console logger started (PID: $AST_PID) -> $LOG_CAPTURE"

sleep 1

echo ""
echo "[4/6] Originating Test Call to $TARGET_NUMBER:"
echo "------------------------------------------------------"
echo ">>> Executing Asterisk Originate..."
asterisk -rx "channel originate Local/$TARGET_NUMBER*$ENDPOINT*911168140253@outbound-dial-carrier/n extension test@outbound-test-playback" || true

echo ""
echo ">>> CALL INITIATED! YOUR PHONE SHOULD RING NOW: $TARGET_NUMBER"
echo ">>> PLEASE ANSWER THE CALL AND LISTEN CAREFULLY."
echo ">>> Capturing 15 seconds of media traffic..."
for i in $(seq 15 -1 1); do
    printf "\rWaiting for media capture... %2d seconds remaining" "$i"
    sleep 1
done
echo ""

echo ""
echo "[5/6] Stopping Capture & Disabling Debug:"
echo "------------------------------------------------------"
asterisk -rx "rtp set debug off" || true
asterisk -rx "pjsip set logger off" || true

if [ -n "$TCP_PID" ]; then
    kill "$TCP_PID" 2>/dev/null || true
fi
kill "$AST_PID" 2>/dev/null || true
wait "$AST_PID" 2>/dev/null || true

echo "Media capture stopped."

echo ""
echo "[6/6] DIAGNOSTIC MEDIA ANALYSIS & FINDINGS:"
echo "======================================================================"

if [ -f "$PCAP_CAPTURE" ] && command -v tcpdump >/dev/null 2>&1; then
    echo "--- A. SIP SDP Media Negotiation (from tcpdump pcap) ---"
    tcpdump -r "$PCAP_CAPTURE" -A -s 0 "port 5060" 2>/dev/null | grep -E "(c=IN|m=audio|a=rtpmap|a=sendrecv)" | head -n 20 || true
    echo ""
fi

if [ -f "$LOG_CAPTURE" ]; then
    echo "--- A. SIP INVITE (Outbound SDP Offer from Asterisk) ---"
    grep -A 16 "<--- Transmitting SIP request" "$LOG_CAPTURE" | head -n 25 || echo "  (INVITE not captured in snippet)"

    echo ""
    echo "--- B. SIP 200 OK (Inbound SDP Answer from 24IT Hub) ---"
    grep -A 16 "<--- Received SIP response (200 OK)" "$LOG_CAPTURE" | head -n 25 || echo "  (200 OK not captured in snippet)"

    echo ""
    echo "--- C. RTP Transmission Statistics ---"
    SENT_COUNT=$(grep -c "Sent RTP packet" "$LOG_CAPTURE" || true)
    RECV_COUNT=$(grep -c "Got RTP packet" "$LOG_CAPTURE" || true)
    DROP_COUNT=$(grep -c -E "dropping due to strict RTP|reject the frame" "$LOG_CAPTURE" || true)

    echo "  Total Sent RTP Packets from Asterisk:     $SENT_COUNT"
    echo "  Total Received RTP Packets from Carrier:  $RECV_COUNT"
    echo "  Total Packets Dropped by Strict RTP:      $DROP_COUNT"

    if [ "$SENT_COUNT" -gt 0 ]; then
        echo ""
        echo "Sample Sent RTP Destination:"
        grep "Sent RTP packet to" "$LOG_CAPTURE" | head -n 3
    else
        echo "  [CRITICAL] Asterisk did NOT send any RTP packets!"
    fi

    if [ "$RECV_COUNT" -gt 0 ]; then
        echo ""
        echo "Sample Received RTP Source:"
        grep "Got RTP packet from" "$LOG_CAPTURE" | head -n 3
    else
        echo "  [CRITICAL] Asterisk did NOT receive any RTP packets from carrier!"
    fi

    if [ "$DROP_COUNT" -gt 0 ]; then
        echo ""
        echo "  [CRITICAL] Strict RTP dropped packets! Evidence:"
        grep -E "dropping due to strict RTP|reject the frame" "$LOG_CAPTURE" | head -n 5
    fi

    echo ""
    echo "--- D. Dialplan Playback Execution ---"
    grep -E "Playtones|StopPlaytones|Playback|hello-world|tt-monkeys" "$LOG_CAPTURE" || echo "  (No dialplan playback logged)"
fi

if [ -f "$PCAP_CAPTURE" ] && command -v tcpdump >/dev/null 2>&1; then
    echo ""
    echo "--- E. Kernel Network Packet Summary (from tcpdump pcap) ---"
    tcpdump -r "$PCAP_CAPTURE" -nn -q | head -n 20 || true
    echo "... total packets in pcap: $(tcpdump -r "$PCAP_CAPTURE" -nn -q 2>/dev/null | wc -l)"
fi

echo ""
echo "======================================================================"
echo "  ANALYSIS COMPLETE"
echo "  Full log saved at: $LOG_CAPTURE"
if [ -f "$PCAP_CAPTURE" ]; then
    echo "  Full pcap saved at: $PCAP_CAPTURE"
fi
echo "======================================================================"
