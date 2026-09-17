"""
Shared aesthetic building blocks. Change the look here and it updates
everywhere the bot speaks - one place to keep the visual identity consistent.

Uses plain line-drawing characters (widely supported on every device) rather
than rare symbols that can render as blank boxes on some phones.
"""

BOT_NAME = "𓆩𝐆𝐎𝐃𝐃𝐄𝐒𝐒𓆪"
LINE = "───────────────"


def header(subtitle: str | None = None) -> str:
    """A bordered header block, e.g.:
    ───────────────
        𝐆𝐎𝐃𝐃𝐄𝐒𝐒
         menu
    ───────────────
    """
    title = BOT_NAME if subtitle is None else f"{BOT_NAME}\n{subtitle}"
    return f"{LINE}\n{title}\n{LINE}"
