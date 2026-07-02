import os
from flask import Flask
import threading
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("keep_alive")

app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run_bot():
    """Start the Telegram bot in a background thread."""
    from main import start_bot
    logger.info("Starting trading bot...")
    start_bot()

if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))