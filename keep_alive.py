from flask import Flask
from threading import Thread
import os
import logging

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running! Status: Online"

@app.route('/health')
def health():
    return "OK", 200

def run():
    port = int(os.environ.get('PORT', 8080))
    logging.info(f"Starting Flask server on port {port}")
    try:
        app.run(host='0.0.0.0', port=port, debug=False)
    except Exception as e:
        logging.error(f"Flask server failed: {e}")

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
    logging.info("Flask thread started")
