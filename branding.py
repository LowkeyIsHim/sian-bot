"""
Shared aesthetic building blocks. Change the look here and it updates
everywhere the bot speaks - one place to keep the visual identity consistent.

Uses only widely-supported characters (plain lines, a four-point star,
middots) rather than emoji or rare symbols that render inconsistently
across devices.
"""

BOT_NAME = "𓆩𝐆𝐎𝐃𝐃𝐄𝐒𝐒 𓆪"
LINE = "──────────"
DOT_DIVIDER = "· · ·"


def header(subtitle: str | None = None) -> str:
    """A bordered header block, e.g.:
    ✦───────────✦
        𝐆𝐎𝐃𝐃𝐄𝐒𝐒
         _menu_
    ✦───────────✦
    """
    lines = [f"✦{LINE}✦", BOT_NAME]
    if subtitle:
        lines.append(f"_{subtitle}_")
    lines.append(f"✦{LINE}✦")
    return "\n".join(lines)
