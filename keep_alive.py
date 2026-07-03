import os
import logging
import threading
import asyncio
from flask import Flask

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("keep_alive")

app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    """Run Flask keep-alive server."""
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=False)

if __name__ == "__main__":
    # Start Flask in a background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run Telegram bot in the MAIN thread (required by python-telegram-bot)
    from main import start_bot
    logger.info("Starting trading bot in main thread...")
    start_bot()