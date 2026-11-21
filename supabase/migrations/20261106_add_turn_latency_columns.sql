-- Migration: Add per-turn latency observability columns to call_messages table
ALTER TABLE public.call_messages
  ADD COLUMN IF NOT EXISTS llm_first_token_ms INTEGER,
  ADD COLUMN IF NOT EXISTS tts_first_byte_ms INTEGER,
  ADD COLUMN IF NOT EXISTS first_audio_ms INTEGER,
  ADD COLUMN IF NOT EXISTS total_playback_ms INTEGER;

-- Index for latency analytics queries
CREATE INDEX IF NOT EXISTS idx_call_messages_first_audio_ms ON public.call_messages(first_audio_ms) WHERE first_audio_ms IS NOT NULL;
