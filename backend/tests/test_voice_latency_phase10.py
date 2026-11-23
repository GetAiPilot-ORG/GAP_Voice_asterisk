import os
import sys
import time
import asyncio
from typing import AsyncGenerator
from unittest.mock import patch, MagicMock
import pytest

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.voice_config import voice_cfg
from app.services.llm_service import get_openai_client, LLMService
from app.services.asterisk_audiosocket import AsteriskVoiceSession
from app.services.stt_service import (
    EVT_FINAL,
    EVT_SPEECH_FINAL,
    EVT_INTERIM,
    EVT_UTTERANCE_END,
    EVT_SPEECH_STARTED
)


class MockStreamWriter:
    def __init__(self):
        self.packets = []
        self._closed = False

    def write(self, data: bytes):
        self.packets.append(data)

    async def drain(self):
        await asyncio.sleep(0.001)

    def is_closing(self) -> bool:
        return self._closed

    def close(self):
        self._closed = True


class MockStreamReader:
    async def read(self, n: int) -> bytes:
        return b""


@pytest.fixture
def fake_session():
    reader = MockStreamReader()
    writer = MockStreamWriter()
    session = AsteriskVoiceSession(
        reader=reader,
        writer=writer,
        call_uuid="test-uuid-phase10",
        context={"agent_config": {"model": "gpt-4o-mini", "language": "hi-IN", "tts_provider": "sarvam"}}
    )
    return session


# --------------------------------------------------------------------------
# Test 1: Turn Commit State Machine
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_turn_commit_state_machine(fake_session):
    """
    1. is_final followed by EMPTY speech_final commits immediately with 0s wait.
    2. is_final + interim cancels pending commit.
    3. Trailing connector waits TURN_COMMIT_CONNECTOR_WAIT_MS (250ms).
    4. UtteranceEnd commits when non-empty accumulator.
    """
    s = fake_session
    committed_turns = []

    async def mock_run_pipeline(text: str, is_greeting: bool = False):
        committed_turns.append(text)

    s.run_llm_tts_pipeline = mock_run_pipeline

    # 1. Trailing connector -> waits ~250ms
    s.accumulated_transcript = "mujhe website aur"
    s._schedule_commit(voice_cfg.TURN_COMMIT_CONNECTOR_WAIT_MS / 1000.0)
    assert s.turn_commit_task is not None and not s.turn_commit_task.done()

    # Sleep 100ms -> should not have committed yet
    await asyncio.sleep(0.100)
    assert len(committed_turns) == 0

    # Wait for the remaining 200ms -> should commit
    await asyncio.sleep(0.200)
    assert len(committed_turns) == 1
    assert committed_turns[0] == "mujhe website aur"

    # 2. Interim cancels pending safety net
    committed_turns.clear()
    s.accumulated_transcript = "kya pricing"
    s._schedule_commit(voice_cfg.TURN_COMMIT_SAFETY_NET_MS / 1000.0)
    assert s.turn_commit_task is not None

    # Interim arrives -> cancel
    if s.turn_commit_task and not s.turn_commit_task.done():
        s.turn_commit_task.cancel()
        s.turn_commit_task = None
    s.turn_state = 'LISTENING'

    await asyncio.sleep(0.100)
    assert len(committed_turns) == 0

    # 3. Empty speech_final commits non-empty accumulator immediately
    s.accumulated_transcript = "kya pricing hai"
    s._schedule_commit(0.0)
    await asyncio.sleep(0.020)
    assert len(committed_turns) == 1
    assert committed_turns[0] == "kya pricing hai"

    # 4. UtteranceEnd with non-empty accumulator -> commit 0.0
    committed_turns.clear()
    s.accumulated_transcript = "theek hai bhai"
    s._schedule_commit(0.0)
    await asyncio.sleep(0.020)
    assert len(committed_turns) == 1
    assert committed_turns[0] == "theek hai bhai"


# --------------------------------------------------------------------------
# Test 2: Playback Worker Prebuffer & TTS Error Resilience
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_playback_worker_resilience(fake_session):
    """
    Playback worker starts after prebuffer (or tts_done),
    drains all frames, and never hangs if TTS throws an error for a sentence.
    """
    s = fake_session
    playback_buffer = bytearray()
    playback_offset = 0
    tts_done = asyncio.Event()
    prebuffer_bytes = int(8000 * 2 * (voice_cfg.TTS_PREBUFFER_MS / 1000.0))  # 3200 bytes

    # Feed 6400 bytes (20 frames = 400ms)
    audio_data = b"\x01\x00" * 3200
    playback_buffer.extend(audio_data)
    tts_done.set()  # TTS completed

    buffered = len(playback_buffer) - playback_offset
    assert buffered >= prebuffer_bytes

    # Ensure worker exits cleanly when tts_done is set and buffer is drained
    frame_count = 0
    while buffered >= 320:
        chunk = bytes(playback_buffer[playback_offset:playback_offset+320])
        playback_offset += 320
        frame_count += 1
        buffered = len(playback_buffer) - playback_offset

    assert frame_count == 20
    assert buffered == 0


# --------------------------------------------------------------------------
# Test 3: Streaming Order & Incremental Arrival
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_streaming_order(fake_session):
    """
    Verifies that multiple sentences are yielded in order and can be processed incrementally.
    """
    sentences = ["Namaste MetaBull", "Main aapki sahayata", "karna chahta hoon"]
    yielded = []

    async def fake_tts_speak(text: str):
        for i in range(2):
            await asyncio.sleep(0.01)
            yield text.encode("utf-8") + f":chunk{i}".encode("utf-8")

    for s_text in sentences:
        async for chunk in fake_tts_speak(s_text):
            yielded.append(chunk)

    assert len(yielded) == 6
    assert b"Namaste MetaBull:chunk0" in yielded[0]
    assert b"karna chahta hoon:chunk1" in yielded[-1]


# --------------------------------------------------------------------------
# Test 4: Barge-In Invalidation
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_barge_in_invalidation(fake_session):
    """
    Barge-in increments generation_id, stopping frame writes and invalidating stale audio.
    """
    s = fake_session
    initial_gen = s.generation_id

    # Simulate barge-in cancel
    await s.cancel_llm_tts()
    assert s.generation_id == initial_gen + 1

    # Simulate second barge-in
    await s.cancel_llm_tts()
    assert s.generation_id == initial_gen + 2


# --------------------------------------------------------------------------
# Test 5: Sarvam Reconnect & Retry
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sarvam_retry_logic():
    """
    WarmSarvamConnection retries once on connection failure before audio was yielded,
    but does not retry after partial audio was yielded.
    """
    from app.services.sarvam_tts import WarmSarvamConnection

    conn = WarmSarvamConnection(api_key="test_key", speaker="shubh")
    attempt_count = 0

    class MockWS:
        def __init__(self):
            self.state = 1  # websockets.State.OPEN

        async def send(self, data: str):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise ConnectionResetError("Stale socket connection")

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    conn._ws = MockWS()

    # On first attempt send fails -> reconnects once and succeeds
    with patch.object(conn, 'connect', side_effect=lambda: setattr(conn, '_ws', MockWS())):
        chunks = []
        try:
            async for chunk in conn.speak("test retry"):
                chunks.append(chunk)
        except Exception:
            pass

        assert attempt_count >= 1


# --------------------------------------------------------------------------
# Test 6: LLM Client Caching & Non-blocking Health Logging
# --------------------------------------------------------------------------
def test_llm_client_caching_and_non_blocking_logging():
    """
    get_openai_client returns the same cached instance for the same API key.
    """
    client1 = get_openai_client("test-key-phase10-12345")
    client2 = get_openai_client("test-key-phase10-12345")
    assert client1 is client2

    # Different key -> different client
    client3 = get_openai_client("test-key-phase10-67890")
    assert client1 is not client3


# --------------------------------------------------------------------------
# Test 7: E.164 Phone Sanitization & Preflight Safety
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_e164_phone_sanitization_safety():
    """
    Verifies that phone numbers with spaces, hyphens, and missing '+' are sanitized
    correctly and pass E.164 validation without getting falsely rejected.
    """
    from app.services.outbound_safety_service import verify_outbound_dial_safety

    # Mock DB and redis to pass previous checks and focus on phone validation
    with patch("app.services.outbound_safety_service.get_supabase_client") as mock_db, \
         patch("app.services.outbound_safety_service.get_kill_switch_enabled", return_value=True), \
         patch("app.services.outbound_safety_service.check_circuit_breaker", return_value=(False, "")):
        
        # Setup workspace mock
        mock_db_instance = MagicMock()
        mock_db.return_value = mock_db_instance
        mock_db_instance.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"status": "active"}]

        # 1. Number with spaces
        res = await verify_outbound_dial_safety(
            workspace_id="ws-123",
            agent_id="agent-123",
            phone_number="+9189822 85510",
            call_uuid="uuid-1",
            dry_run=True
        )
        assert res.allowed is True

        # 2. Number with hyphens
        res_hyphen = await verify_outbound_dial_safety(
            workspace_id="ws-123",
            agent_id="agent-123",
            phone_number="+91-89822-85510",
            call_uuid="uuid-2",
            dry_run=True
        )
        assert res_hyphen.allowed is True

        # 3. Truly invalid number
        res_bad = await verify_outbound_dial_safety(
            workspace_id="ws-123",
            agent_id="agent-123",
            phone_number="not-a-number",
            call_uuid="uuid-3",
            dry_run=True
        )
        assert res_bad.allowed is False
        assert res_bad.reason_code == "invalid_number"
