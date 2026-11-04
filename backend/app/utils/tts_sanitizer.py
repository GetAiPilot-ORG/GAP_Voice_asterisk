import re
import unicodedata

# Comprehensive regex to match emojis and symbols across Unicode blocks
_EMOJI_PATTERN = re.compile(
    r'[\U00010000-\U0010ffff]'  # Supplementary Multilingual Plane (emojis, symbols)
    r'|[\u2600-\u27bf]'          # Misc symbols & Dingbats
    r'|[\u2300-\u23ff]'          # Misc technical
    r'|[\u2b50\u2b55\u200d\ufe0f]' # Star, circle, zero-width joiner, variation selector
)

# Currency replacements for Indian spoken context
_CURRENCY_REPLACEMENTS = [
    (re.compile(r'₹\s*([0-9,]+)', re.IGNORECASE), r'\1 rupees'),
    (re.compile(r'rs\.?\s*([0-9,]+)', re.IGNORECASE), r'\1 rupees'),
    (re.compile(r'inr\s*([0-9,]+)', re.IGNORECASE), r'\1 rupees'),
]

# Markdown formatting characters
_MARKDOWN_PATTERN = re.compile(r'[*_#~`\[\]()]')

# Bullet points at line start or following punctuation
_BULLET_PATTERN = re.compile(r'(^|[\n\r])\s*[-*•\d+.]\s*')

# URLs
_URL_PATTERN = re.compile(r'https?://\S+|www\.\S+')


def sanitize_text_for_tts(text: str) -> str:
    """
    Sanitizes raw LLM output text for high-quality, glitch-free telephony TTS.
    - Strips emojis and decorative Unicode symbols
    - Normalizes Indian currency symbols to spoken 'rupees'
    - Strips markdown formatting (*, _, #, bullet points, brackets)
    - Strips URLs
    - Normalizes multiple spaces and trims
    - Preserves Devanagari Hindi characters and matras (U+0900 to U+097F)
    """
    if not text:
        return ""

    s = text

    # 1. Strip URLs
    s = _URL_PATTERN.sub(" ", s)

    # 2. Normalize currency
    for pattern, repl in _CURRENCY_REPLACEMENTS:
        s = pattern.sub(repl, s)
    s = s.replace("₹", " rupees ")

    # 3. Strip emojis
    s = _EMOJI_PATTERN.sub("", s)

    # 4. Strip bullet points and list numbering
    s = _BULLET_PATTERN.sub(" ", s)

    # 5. Strip markdown formatting
    s = _MARKDOWN_PATTERN.sub("", s)

    # 6. Normalize punctuation (replace dashes, underscores with comma or space)
    s = s.replace(" - ", ", ").replace(" -- ", ", ").replace("_", " ")

    # 7. Normalize whitespace
    s = re.sub(r'\s+', ' ', s).strip()

    return s

