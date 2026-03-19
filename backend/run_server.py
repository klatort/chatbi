"""
Dev runner for the ChatBI backend.
Runs the Flask app in debug mode so the LangGraph agent and blueprint
can be tested standalone, without installing into Superset.

Usage:
    cd backend/
    .venv/bin/python run_server.py

The /chat endpoint will be available at:
    POST http://localhost:5009/extensions/chatbi-native/chat

Test with curl:
    curl -N -X POST http://localhost:5009/extensions/chatbi-native/chat \\
         -H "Content-Type: application/json" \\
         -d '{"message": "What datasets are available?"}'
"""

from __future__ import annotations

import os

from dotenv import load_dotenv, find_dotenv
from flask import Flask

# Load .env if present
load_dotenv(find_dotenv(usecwd=True))

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from chatbi_native.api import blueprint

app = Flask(__name__)
app.register_blueprint(blueprint)

if __name__ == "__main__":
    port = int(os.getenv("CHATBI_PORT", "5009"))
    print(f"ChatBI backend running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
