"""
Entry point. The hosting panel should run this file.

It starts the bot using long polling — no webhook/port needed,
which fits panels that just run a plain Python process.
"""

import access
from telegram import Update
from bot import build_app


def main():
    access.init()
    app = build_app()
    print("Sian bot starting (polling)...")
    # allowed_updates=Update.ALL_TYPES is required to receive chat_member
    # updates (admin promote/demote via Telegram's own UI) - Telegram
    # doesn't send those by default unless explicitly requested.
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
