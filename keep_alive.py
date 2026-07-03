"""
Render entry point.

Architecture:
  - Main thread:  asyncio event loop (Telegram bot + APScheduler)
  - Daemon thread: Flask (health-check only, fully synchronous)

No asyncio code runs in threads. No deprecated patterns.
"""

import os
import logging
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("keep_alive")

# ── Flask health-check server (runs in daemon thread) ──
from flask import Flask

app = Flask("")

@app.route("/")
def health_check():
    return "Bot is running!"


def _run_flask():
    """Run Flask in a daemon thread — only serves Render health checks."""
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        use_reloader=False,
        threaded=False,
    )


# ── WSGI entry point (used by gunicorn/Render) ──
if __name__ != "__main__":
    # When gunicorn imports this module, it only sees the Flask app.
    # The bot is started via the daemon thread below.
    _flask_thread = threading.Thread(target=_run_flask, daemon=True, name="flask-health")
    _flask_thread.start()

    # Start the actual bot in a separate daemon process to isolate
    # the asyncio event loop from Flask/gunicorn completely.
    import multiprocessing
    _bot_process = multiprocessing.Process(
        target=_run_bot_process,
        name="telegram-bot",
        daemon=True,
    )
    _bot_process.start()
    logger.info(f"Bot process started (pid={_bot_process.pid})")


def _run_bot_process():
    """Run the bot in its own process with its own event loop."""
    from main import start_bot
    start_bot()


# ── Direct execution (python keep_alive.py) ──
if __name__ == "__main__":
    # Flask in background
    _flask_thread = threading.Thread(target=_run_flask, daemon=True, name="flask-health")
    _flask_thread.start()

    # Bot in main thread (event loop)
    from main import start_bot
    logger.info("Starting bot in main thread...")
    start_bot()