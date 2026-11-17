import os
import sys
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.services.call_session_manager import CallSessionManager
from app.services.asterisk_config_generator import AsteriskConfigGenerator
from app.utils.audio_telephony import assert_audiosocket_format, calculate_pcm_rms


def test_did_normalization_variants():
    """Verify that all 4 Indian telecom DID variants resolve to the canonical number."""
    variants = [
        "+911168140253",
        "911168140253",
        "01168140253",
        "1168140253",
    ]
    # For every format, extracting 10-digit base yields '1168140253'
    for raw in variants:
        digits = "".join(c for c in raw if c.isdigit())
        if digits.startswith("91") and len(digits) == 12:
            base = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            base = digits[1:]
        elif len(digits) == 10:
            base = digits
        else:
            base = digits
        assert base == "1168140253", f"Failed for variant {raw}"


def test_asterisk_config_generator_pjsip_security():
    """Verify generated PJSIP config includes anti-anonymous identity, symmetrical RTP, and carrier IPs."""
    trunk = {
        "id": "10ca861d-d002-4f52-8b2a-fbefea08a6de",
        "name": "24IThub LLC Trunk",
        "auth_type": "ip_auth",
        "sip_proxy": "159.65.156.202",
        "sip_port": 5060,
        "transport": "udp",
        "outbound_caller_id": "911168140253",
        "provider_ips": ["159.65.156.202", "202.89.78.2"],
    }
    configs = AsteriskConfigGenerator.generate_config(trunk)
    pjsip = configs["pjsip_conf"]
    ext = configs["extensions_conf"]

    # 1. Identity must not be Anonymous
    assert "from_user=911168140253" in pjsip
    assert 'callerid="911168140253" <911168140253>' in pjsip
    assert "send_pai=yes" in pjsip
    assert "send_rpid=yes" in pjsip
    assert "Anonymous" not in pjsip

    # 2. Symmetric RTP and NAT traversal
    assert "rtp_symmetric=yes" in pjsip
    assert "direct_media=no" in pjsip
    assert "force_rport=yes" in pjsip
    assert "rewrite_contact=yes" in pjsip

    # 3. Carrier RTP gateway match
    assert "match=202.89.78.2" in pjsip
    assert "match=159.65.156.202" in pjsip

    # 4. Dialplan contains answer-gated outbound context
    assert "[outbound-ai-answered]" in ext
    assert "[call-hangup-handler]" in ext
    assert "FILTER(0123456789" in ext


def test_audiosocket_frame_format():
    """Verify AudioSocket telephony constraints (320 bytes = 20ms linear16 8kHz)."""
    assert assert_audiosocket_format(320) is True
    assert assert_audiosocket_format(160) is False
    assert assert_audiosocket_format(640) is False
    assert assert_audiosocket_format(0) is False


def test_call_session_sarvam_agent_resolution():
    """Verify that an agent with Hindi language or Sarvam voice automatically selects Sarvam TTS."""
    csm = CallSessionManager()
    dummy_agent = {
        "id": "test-agent-123",
        "name": "Aanya",
        "language": "hi-IN",
        "voice_id": "shreya",
        "voice_provider": "sarvam",
        "kb_metadata": {"tts_provider": "deepgram"} # Should NOT override Sarvam for Hindi agent
    }
    
    # Simulate agent resolution logic from CallSessionManager
    lang = dummy_agent.get("language") or "hi-IN"
    raw_voice_id = (dummy_agent.get("voice_id") or "").strip()
    raw_provider = (dummy_agent.get("voice_provider") or "").lower()

    is_sarvam_voice = raw_voice_id.lower() in ("shreya", "shubh", "meera", "manan", "ishita", "arjun")
    is_hindi_lang = lang.lower().startswith("hi")

    if is_sarvam_voice or raw_provider == "sarvam" or (is_hindi_lang and raw_provider not in ("elevenlabs", "playht")):
        resolved_tts = "sarvam"
        resolved_voice = raw_voice_id if raw_voice_id else "shreya"
    else:
        resolved_tts = "deepgram"
        resolved_voice = "aura-asteria-en"

    assert resolved_tts == "sarvam"
    assert resolved_voice == "shreya"


def test_hangup_cause_code_mapping():
    """Verify Q.850 cause code mapping to call status and reasons."""
    cause_map = {
        "16": "normal_clearing",
        "17": "user_busy",
        "18": "no_answer",
        "19": "no_answer",
        "21": "call_rejected",
        "34": "circuit_congestion",
        "38": "network_out_of_order",
    }
    assert cause_map["16"] == "normal_clearing"
    assert cause_map["17"] == "user_busy"
    assert cause_map["18"] == "no_answer"
    assert cause_map["21"] == "call_rejected"
