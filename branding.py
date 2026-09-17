"""
Shared aesthetic building blocks. Change the look here and it updates
everywhere the bot speaks - one place to keep the visual identity consistent.
"""

BOT_NAME = "𝐆𝐎𝐃𝐃𝐄𝐒𝐒"
SIGIL_L = "𓆩"
SIGIL_R = "𓆪"
DIVIDER = "˚₊‧⋆ ⋆‧₊˚"


def framed(text: str) -> str:
    """Wraps text in the signature sigil frame: 𓆩 text 𓆪"""
    return f"{SIGIL_L} {text} {SIGIL_R}"
