"""
Entry point. The hosting panel should run this file.

It starts the bot using long polling — no webhook/port needed,
which fits panels that just run a plain Python process.
"""

import access
from bot import build_app


def main():
    access.init()
    app = build_app()
    print("Sian bot starting (polling)...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
