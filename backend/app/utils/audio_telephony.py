import math
import struct
from typing import Tuple

AUDIO_FORMAT_SLIN_SAMPLE_RATE = 8000
AUDIO_FORMAT_CHANNELS = 1
AUDIO_FORMAT_SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
AUDIO_FRAME_DURATION_MS = 20
AUDIO_FRAME_SIZE_BYTES = 320   # 8000 * 1 * 2 * 0.020 = 320 bytes

def assert_audiosocket_format(data_length: int) -> bool:
    """
    Validates that a frame matches the exact Asterisk AudioSocket slin standard (320 bytes).
    """
    return data_length == AUDIO_FRAME_SIZE_BYTES

def calculate_pcm_rms(pcm_bytes: bytes) -> float:
    """
    Computes RMS (Root Mean Square) energy of 16-bit signed linear PCM.
    Returns value normalized from 0.0 to 32768.0.
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return 0.0
    
    num_samples = len(pcm_bytes) // 2
    samples = struct.unpack(f"<{num_samples}h", pcm_bytes[:num_samples*2])
    sum_squares = sum(s * s for s in samples)
    mean_square = sum_squares / num_samples
    return math.sqrt(mean_square)

def apply_pcm_fade_out(pcm_bytes: bytes, fade_samples: int = 16) -> bytes:
    """
    Applies a smooth linear fade-out to the final N samples (16 samples = 2ms at 8kHz)
    to eliminate DC step clicks at underflow boundaries.
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return pcm_bytes
    
    num_samples = len(pcm_bytes) // 2
    samples = list(struct.unpack(f"<{num_samples}h", pcm_bytes[:num_samples*2]))
    
    fade_len = min(fade_samples, num_samples)
    for i in range(fade_len):
        idx = num_samples - fade_len + i
        factor = (fade_len - 1 - i) / max(1, fade_len)
        samples[idx] = int(samples[idx] * factor)
        
    return struct.pack(f"<{num_samples}h", *samples)

def apply_pcm_fade_in(pcm_bytes: bytes, fade_samples: int = 16) -> bytes:
    """
    Applies a smooth linear fade-in to the initial N samples (16 samples = 2ms at 8kHz)
    to prevent clicks when resuming from comfort silence.
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return pcm_bytes
    
    num_samples = len(pcm_bytes) // 2
    samples = list(struct.unpack(f"<{num_samples}h", pcm_bytes[:num_samples*2]))
    
    fade_len = min(fade_samples, num_samples)
    for i in range(fade_len):
        factor = i / max(1, fade_len)
        samples[i] = int(samples[i] * factor)
        
    return struct.pack(f"<{num_samples}h", *samples)

def mask_phone_number(phone: str) -> str:
    """
    Masks sensitive telephone numbers for secure logging (e.g. +919343418163 -> +91****8163).
    """
    if not phone or len(phone) < 6:
        return "****"
    cleaned = phone.strip()
    return f"{cleaned[:3]}****{cleaned[-4:]}"

