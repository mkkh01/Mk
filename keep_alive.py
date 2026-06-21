# keep_alive.py
from flask import Flask
from threading import Thread
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 AI Crypto Bot is Alive and Running!"

def run():
    # Render يعطينا المنفذ عبر متغير البيئة PORT، وإلا نستخدم 8080
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    """تشغيل الخادم في مسار (Thread) منفصل لكي لا يعطل البوت"""
    t = Thread(target=run)
    t.start()
