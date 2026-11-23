#!/usr/bin/env bash
# ==============================================================================
# GetAiPilot Voice Pilot - VPS Production Deployment & Hardening Script
# Target: Ubuntu/Debian Linux on VPS 72.60.202.148
# ==============================================================================

set -euo pipefail

echo "================================================================="
echo "  GetAiPilot Voice Pilot: Asterisk & Backend Deployment Script"
echo "================================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

echo "[1/7] Backing up existing Asterisk configurations..."
mkdir -p /etc/asterisk/backups
for cfg in pjsip.conf extensions.conf rtp.conf modules.conf; do
    if [ -f "/etc/asterisk/$cfg" ]; then
        cp "/etc/asterisk/$cfg" "/etc/asterisk/backups/${cfg}.${TIMESTAMP}.bak"
        echo "  Backed up /etc/asterisk/$cfg -> /etc/asterisk/backups/${cfg}.${TIMESTAMP}.bak"
    fi
done

echo "[2/7] Installing production-hardened Asterisk configs..."
cp "$SCRIPT_DIR/asterisk/pjsip.conf" /etc/asterisk/pjsip.conf
cp "$SCRIPT_DIR/asterisk/extensions.conf" /etc/asterisk/extensions.conf
cp "$SCRIPT_DIR/asterisk/rtp.conf" /etc/asterisk/rtp.conf
cp "$SCRIPT_DIR/asterisk/modules.conf" /etc/asterisk/modules.conf

chown -R asterisk:asterisk /etc/asterisk/ || chown -R root:root /etc/asterisk/
chmod 640 /etc/asterisk/*.conf

echo "[3/7] Configuring UFW Firewall for SIP & RTP..."
if command -v ufw >/dev/null 2>&1; then
    # Allow 24IT Hub Signaling IP
    ufw allow from 159.65.156.202 to any port 5060 proto udp comment '24IT Hub SIP Signaling'
    # Allow 24IT Hub RTP Gateway IP
    ufw allow from 202.89.78.2 to any port 5060 proto udp comment '24IT Hub Secondary Signaling'
    # Allow RTP Range (10000 - 20000 UDP)
    ufw allow 10000:20000/udp comment 'Asterisk RTP Media Stream'
    # Allow Backend HTTP API
    ufw allow 8010/tcp comment 'Voice Pilot FastAPI Backend'
    ufw reload || true
    echo "  UFW firewall rules applied."
else
    echo "  UFW not active. Please ensure UDP 5060 and UDP 10000-20000 are open in your cloud firewall."
fi

echo "[4/7] Reloading Asterisk configuration..."
asterisk -rx "core reload" || true
asterisk -rx "pjsip reload"
asterisk -rx "dialplan reload"
asterisk -rx "module reload res_rtp_asterisk" || true

echo "[5/7] Installing FastAPI Backend systemd service..."
cp "$SCRIPT_DIR/systemd/fastapi-backend.service" /etc/systemd/system/fastapi-backend.service
systemctl daemon-reload
systemctl enable fastapi-backend.service
systemctl restart fastapi-backend.service

echo "[6/7] Waiting for backend to initialize..."
sleep 3

echo "[7/7] Running Verification Checks..."

echo "--- 1. PJSIP Endpoints ---"
asterisk -rx "pjsip show endpoints"

echo "--- 2. PJSIP Identifies ---"
asterisk -rx "pjsip show identifies"

echo "--- 3. Dialplan Outbound AI Answered ---"
asterisk -rx "dialplan show outbound-ai-answered"

echo "--- 4. Backend Health Check (Port 8010) ---"
if curl -sf http://127.0.0.1:8010/health; then
    echo "  FastAPI Backend is HEALTHY on port 8010!"
else
    echo "  WARNING: Backend did not respond on http://127.0.0.1:8010/health"
    systemctl status fastapi-backend.service --no-pager -n 20
fi

echo "--- 5. AudioSocket TCP Listener (Port 9095) ---"
if ss -tlnp | grep -q ":9095"; then
    echo "  AudioSocket TCP server is LISTENING on port 9095!"
else
    echo "  WARNING: Port 9095 not detected. Check backend logs."
fi

echo "================================================================="
echo "  DEPLOYMENT COMPLETE! Voice Pilot is production-hardened."
echo "================================================================="
