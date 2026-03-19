"""
superset_config.py — ChatBI Native Extension Integration
=========================================================
Drop this file into your Superset config directory (or merge it into
your existing superset_config.py) to register the ChatBI extension with
Superset's Flask backend and frontend plugin system.

Quick start:
  1. Install the backend package:
       pip install -e /path/to/chatbi-native/backend/

  2. Build the frontend:
       cd /path/to/chatbi-native/frontend && npm run build

  3. Add this file to your Superset config:
       export SUPERSET_CONFIG_PATH=/path/to/superset_config.py

  4. Restart Superset.

  5. The ChatBI FAB appears in the bottom-right corner of every
     Dashboard and Explore view.
"""

import os

# ── 1. Register the Flask Blueprint ──────────────────────────────────
# Superset loads any blueprints listed in BLUEPRINTS.
from chatbi_native.api import blueprint as chatbi_blueprint

BLUEPRINTS = [chatbi_blueprint]

# ── 2. Point the Module Federation loader at the built remoteEntry.js ─
# Replace the URL with wherever you serve the frontend dist/ folder.
# For local dev: run `npm run dev` (port 3099) or serve dist/ statically.
CHATBI_REMOTE_ENTRY_URL = os.getenv(
    "CHATBI_REMOTE_ENTRY_URL",
    "http://localhost:3099/remoteEntry.js",
)

# ── 3. Register the frontend remote in Superset's extension registry ──
# Superset reads FRONTEND_EXTENSIONS to load Module Federation remotes
# and call their mountComponent() on page load.
FRONTEND_EXTENSIONS = [
    {
        "id": "chatbi-native",
        "remote": CHATBI_REMOTE_ENTRY_URL,
        # The federation module name declared in webpack.config.js
        "scope": "chatbi_native",
        # The exposed component path from webpack exposes config
        "module": "./ChatBIPanel",
        # Tells Superset to call extensionConfig.mountComponent()
        "mountAs": "GLOBAL_OVERLAY",
    }
]

# ── 4. LLM / MCP environment ─────────────────────────────────────────
# These can also be set as shell env vars instead.
os.environ.setdefault("CHATBI_MCP_URL", "http://localhost:5008/mcp")
# os.environ.setdefault("OPENAI_API_KEY", "sk-...")
# os.environ.setdefault("CHATBI_OPENAI_MODEL", "gpt-4o")
