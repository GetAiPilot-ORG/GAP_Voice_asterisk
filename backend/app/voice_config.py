from pydantic_settings import BaseSettings


class VoiceConfig(BaseSettings):
    # Feature flags
    ENABLE_EARLY_EOT_LLM: bool = False     # UNUSED: Early EOT speculation disabled in favour of turn state machine
    ENABLE_SPECULATIVE_INTERIM_LLM: bool = False
    ENABLE_FILLER_RESPONSE: bool = False
    ENABLE_BARGE_IN: bool = True

    # STT tuning
    STT_ENDPOINTING_MS: int = 300          # Fast 300ms baseline endpointing
    STT_UTTERANCE_END_MS: int = 1000       # Deepgram UtteranceEnd trigger threshold
    STT_MODEL: str = "nova-2"              # nova-2 | nova-3

    # Turn commit tuning
    TURN_COMMIT_CONNECTOR_WAIT_MS: int = 250   # Wait after trailing connectors before commit
    TURN_COMMIT_SAFETY_NET_MS: int = 1000      # Safety net commit when speech_final does not fire

    # LLM tuning
    OPENAI_MAX_OUTPUT_TOKENS: int = 80
    OPENAI_VOICE_MODEL: str = "gpt-4o-mini"
    VOICE_SYSTEM_PREFIX: str = (
        "Reply briefly and naturally for a phone/voice conversation. "
        "Keep the first sentence under 15 words."
    )

    # TTS chunking & playback
    TTS_PREBUFFER_MS: int = 200            # Prebuffer before starting audio playback (200ms)
    TTS_CHUNK_FIRST_WORD_MIN: int = 4      # Min words before fast first chunk release on pause punc
    TTS_CHUNK_FIRST_WORD_MAX: int = 6      # Max words before fast first chunk release without punc
    TTS_CHUNK_WORD_MIN: int = 8            # Min words before subsequent chunk release on pause punc
    TTS_CHUNK_TIMEOUT_MS: int = 300        # Max wait before releasing first chunk if >= 3 words

    model_config = {
        "env_prefix": "",
        "extra": "ignore",
        "env_file": ".env",
        "case_sensitive": False,
    }


voice_cfg = VoiceConfig()
