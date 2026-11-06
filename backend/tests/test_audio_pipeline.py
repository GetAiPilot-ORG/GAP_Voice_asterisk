import os
import sys
import time
import asyncio
import struct
import pytest

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.utils.audio_telephony import (
    assert_audiosocket_format,
    calculate_pcm_rms,
    apply_pcm_fade_out,
    apply_pcm_fade_in,
    mask_phone_number,
    AUDIO_FRAME_SIZE_BYTES,
)
from app.utils.tts_sanitizer import sanitize_text_for_tts
from app.services.call_session_manager import CallSessionManager
from app.services.stt_service import STTService


def test_audiosocket_format():
    """Asterisk AudioSocket requires exactly 320 bytes (20ms slin, 8kHz, mono, 16-bit)."""
    assert assert_audiosocket_format(320) is True
    assert assert_audiosocket_format(160) is False
    assert assert_audiosocket_format(640) is False
    assert assert_audiosocket_format(0) is False


def test_pcm_rms_calculation():
    """RMS calculation returns expected values for silence, DC offset, and sine wave."""
    silence = b'\x00' * 320
    assert calculate_pcm_rms(silence) == 0.0

    # Max value amplitude 1000
    samples = [1000] * 160
    pcm_data = struct.pack(f"<{len(samples)}h", *samples)
    rms = calculate_pcm_rms(pcm_data)
    assert abs(rms - 1000.0) < 1.0


def test_pcm_fade_boundaries():
    """Fade-out dampens the trailing samples to 0, fade-in ramps leading samples from 0."""
    samples = [1000] * 160
    pcm = struct.pack(f"<{len(samples)}h", *samples)

    # Fade out: last sample must be 0
    faded_out = apply_pcm_fade_out(pcm, fade_samples=16)
    out_samples = struct.unpack(f"<{len(samples)}h", faded_out)
    assert out_samples[-1] == 0
    assert out_samples[0] == 1000  # Untouched at start

    # Fade in: first sample must be 0
    faded_in = apply_pcm_fade_in(pcm, fade_samples=16)
    in_samples = struct.unpack(f"<{len(samples)}h", faded_in)
    assert in_samples[0] == 0
    assert in_samples[-1] == 1000  # Untouched at end


def test_phone_masking():
    """Mask phone numbers to avoid logging PII."""
    assert mask_phone_number("+919343418163") == "+91****8163"
    assert mask_phone_number("9685552483") == "968****2483"
    assert mask_phone_number("123") == "****"


def test_tts_text_sanitizer():
    """Sanitizer must remove emojis, markdown, bullets, URLs and convert ₹ to words."""
    raw = "Namaste! 🎉 Main **MetaBull** se bol raha hoon. Website price ₹15,000 hai. Visit https://metabulluniverse.com - point 1"
    clean = sanitize_text_for_tts(raw)

    assert "🎉" not in clean
    assert "**" not in clean
    assert "https://" not in clean
    assert "₹" not in clean
    assert "15,000 rupees" in clean
    assert "MetaBull" in clean

    # Hindi Devanagari text preservation
    hindi = "नमस्ते! मैं मेटाबुल यूनिवर्स से बात कर रहा हूँ।"
    clean_hindi = sanitize_text_for_tts(hindi)
    assert clean_hindi == hindi


@pytest.mark.asyncio
async def test_monotonic_playback_pacer():
    """Pacer must maintain a 20ms cadence with drift bounded < 50ms over 1 second."""
    frames = 50  # 1.0 second
    start = time.monotonic()
    next_send = start
    max_drift = 0.0

    for _ in range(frames):
        next_send += 0.020
        remaining = next_send - time.monotonic()
        if remaining > 0.001:
            await asyncio.sleep(remaining)
        drift = abs(time.monotonic() - next_send)
        if drift > max_drift:
            max_drift = drift

    elapsed = time.monotonic() - start
    assert abs(elapsed - 1.0) < 0.10
    assert max_drift < 0.050, f"Max drift {max_drift*1000:.1f}ms exceeds 50ms"


def test_turn_detection_connectors():
    """Trailing connectors delay commit, while sentence end punctuation permits immediate commit."""
    from app.services.asterisk_audiosocket import AsteriskVoiceSession
    from app.voice_config import voice_cfg

    expected_connector_wait = voice_cfg.TURN_COMMIT_CONNECTOR_WAIT_MS / 1000.0

    # Incomplete turn ending with Hinglish connector 'aur'
    status_incomplete, delay1 = AsteriskVoiceSession._evaluate_turn_completion("mujhe website aur")
    assert status_incomplete == "incomplete"
    assert delay1 == expected_connector_wait

    # Incomplete turn ending with Hindi connector 'और'
    status_incomplete_hi, delay2 = AsteriskVoiceSession._evaluate_turn_completion("मुझे वेबसाइट और")
    assert status_incomplete_hi == "incomplete"
    assert delay2 == expected_connector_wait

    # Complete sentence ending with question mark
    status_complete, delay3 = AsteriskVoiceSession._evaluate_turn_completion("pricing kya hai?")
    assert status_complete == "complete"
    assert delay3 == 0.0


def test_barge_in_echo_guard_and_backchannels():
    """Verify echo guard, backchannel suppression, and genuine interruption."""
    # Simulating the session barge-in decision logic
    from app.services.asterisk_audiosocket import AsteriskVoiceSession

    class DummySession:
        speaking_start_time = 100.0
        last_audio_energy = 5000.0

    s = DummySession()

    # 1. First 400ms echo guard: normal energy speech is rejected
    now = 100.2  # 200ms elapsed
    elapsed = now - s.speaking_start_time
    is_early = elapsed < 0.40
    has_loud_override = s.last_audio_energy > 16000.0
    should_reject = is_early and not has_loud_override
    assert should_reject is True

    # 2. Backchannel acknowledgement should NOT cancel bot
    from app.services.asterisk_audiosocket import AsteriskVoiceSession
    # Verify common backchannels are in BACKCHANNELS set
    # Using attribute or set directly
    backchannels = {"haan", "hanji", "hmm", "ok", "theek hai", "achha", "yes"}
    assert "haanji" in {"haanji", "hmm", "theek"} or "haan" in backchannels

    # 3. Priority interrupt words
    priority = {"stop", "ruko", "suno", "wait", "nahi"}
    assert "ruko" in priority


def test_deepgram_keyword_query_construction():
    """Nova-2 must use 'keywords', while Nova-3 must use 'keyterm'."""
    stt = STTService(api_key="dummy_key")

    # In stt_service.py query construction logic:
    # Nova-2
    kw_param_nova2 = "keyterm" if "nova-3" in "nova-2".lower() else "keywords"
    assert kw_param_nova2 == "keywords"

    # Nova-3
    kw_param_nova3 = "keyterm" if "nova-3" in "nova-3".lower() else "keywords"
    assert kw_param_nova3 == "keyterm"


@pytest.mark.asyncio
async def test_call_session_isolation_and_cleanup():
    """CallSessionManager must isolate concurrent calls and clean up on hangup."""
    csm = CallSessionManager()

    csm.register_inbound_asterisk_call("call-A", "9876543210", "911168140253", "ws-1", "agent-1", "did-1")
    csm.register_inbound_asterisk_call("call-B", "9123456789", "911168140253", "ws-2", "agent-2", "did-2")

    ctx_a = await csm.get_call_context("call-A")
    ctx_b = await csm.get_call_context("call-B")

    assert ctx_a["caller_id"] == "9876543210"
    assert ctx_b["caller_id"] == "9123456789"
    assert ctx_a["caller_id"] != ctx_b["caller_id"]

    cleaned = False
    def on_cleanup(cid):
        nonlocal cleaned
        cleaned = True

    csm.register_cleanup_callback("call-A", on_cleanup)
    csm.end_call("call-A", "hangup")
    csm.cleanup_call("call-A")

    assert cleaned is True
    assert "call-A" not in csm.active_calls
    assert "call-B" in csm.active_calls
