#!/usr/bin/env bash
# ==============================================================================
# GetAiPilot Voice Pilot - Outbound Audio Root Cause Diagnostic Tool
# ==============================================================================
set -euo pipefail

echo "======================================================================"
echo "  GetAiPilot Voice Pilot: Outbound Media Investigation & Trace"
echo "======================================================================"

UUID1="daf1034f-b287-4592-9a52-b06293c9aa3b"
UUID2="f8babc77-4019-4bb1-ad2c-c037476f274b"

echo ""
echo "[PHASE 1] Historical Log Correlation for Failed UUIDs:"
echo "------------------------------------------------------"
for logfile in /root/.pm2/logs/asterisk-vp-backend-out.log /root/.pm2/logs/asterisk-vp-backend-error.log /root/asterisk/backend/logs/app.log /var/log/asterisk/messages /var/log/asterisk/full; do
    if [ -f "$logfile" ]; then
        echo ">>> Checking $logfile for $UUID1 and $UUID2..."
        grep -E "daf1034f|f8babc77" "$logfile" || echo "    (No match in $logfile)"
    fi
done

echo ""
echo "[PHASE 2] Checking Active Port Ownership & Listeners:"
echo "------------------------------------------------------"
echo "Port 8010 (FastAPI):"
ss -tulpn | grep 8010 || echo "WARNING: Nothing listening on 8010"
echo "Port 9095 (AudioSocket TCP):"
ss -tulpn | grep 9095 || echo "WARNING: Nothing listening on 9095"

echo ""
echo "[PHASE 3] Checking PM2 and Asterisk Status:"
echo "------------------------------------------------------"
pm2 ls
asterisk -rx "core show version"
asterisk -rx "pjsip show endpoints"

echo ""
echo "======================================================================"
echo "  DIAGNOSTIC TRACE READY"
echo "======================================================================"
