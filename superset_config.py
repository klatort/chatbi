"""
superset_config.py — ChatBI Native Extension Integration
=========================================================
Drop this file into your Superset config directory (or merge it into
your existing superset_config.py) to register the ChatBI extension with
Superset's Flask backend.

Quick start:
  1. Install the backend package:
       pip install -e /path/to/chatbi-native/backend/

  2. Build the frontend:
       cd /path/to/chatbi-native/frontend && npm run build

  3. Serve frontend/dist/ via nginx or a static file server on port 3099
     (or set CHATBI_REMOTE_ENTRY_URL to the correct URL).

  4. Add this file to your Superset config:
       export SUPERSET_CONFIG_PATH=/path/to/superset_config.py

  5. Restart Superset.

  6. The ChatBI FAB appears in the bottom-right corner of every page.
"""

import os

# ── 0. Required Superset Configs ─────────────────────────────────────
# Flask session secret: Replace with a strong random key!
SECRET_KEY = "CHANGE_ME_TO_A_COMPLEX_RANDOM_SECRET"

# Disable Talisman (CSP) so the browser can load remoteEntry.js from
# an external port/host. In production, configure CSP headers instead.
TALISMAN_ENABLED = False

# ── 1. Register the Flask Blueprint ──────────────────────────────────
# Superset loads any blueprints listed in BLUEPRINTS.
# The blueprint also injects a <script> tag into every HTML page that
# loads the ChatBI frontend panel from remoteEntry.js.
from chatbi_native.api import blueprint as chatbi_blueprint

BLUEPRINTS = [chatbi_blueprint]

# ── 2. LLM / MCP environment ─────────────────────────────────────────
# These can also be set as shell env vars or in a .env file instead.
os.environ.setdefault("CHATBI_MCP_URL", "http://localhost:5008/mcp")
# os.environ.setdefault("OPENAI_API_KEY", "sk-...")
# os.environ.setdefault("CHATBI_OPENAI_MODEL", "gpt-4o")

