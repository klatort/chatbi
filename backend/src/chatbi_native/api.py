"""
ChatBI Native — Flask Blueprint API  (Phase 2: fully wired)
============================================================
Endpoints:

  GET  /extensions/chatbi-native/health
       → liveness probe

  POST /extensions/chatbi-native/chat
       Body:  { "message": "...", "history": [...], "conversation_id": "..." }
       Returns: SSE stream of JSON-encoded event chunks

SSE payload schema:
  data: {"type": "token",       "content": "<text>"}
  data: {"type": "tool_call",   "name": "<tool>",   "args": {...}}
  data: {"type": "tool_result", "name": "<tool>",   "content": "<text>"}
  data: {"type": "done"}
  data: {"type": "error",       "content": "<message>"}
"""

from __future__ import annotations

import json
import logging
import os
from typing import Generator

from flask import Blueprint, Response, jsonify, request, stream_with_context

logger = logging.getLogger(__name__)

blueprint = Blueprint(
    "chatbi_native",
    __name__,
    url_prefix="/extensions/chatbi-native",
)


# ── Script Injection ─────────────────────────────────────────────────
# Superset does NOT have a built-in mechanism to load Module Federation
# remotes from config. We inject a small <script> into every HTML page
# that loads remoteEntry.js and calls mountComponent() to render the
# ChatBI floating panel.

_CHATBI_LOADER_TEMPLATE = """
<!-- ChatBI Native Extension Loader -->
<script>
(function() {{
  if (window.__chatbi_loaded) return;
  window.__chatbi_loaded = true;

  var remoteUrl = "{remote_entry_url}";
  var script = document.createElement("script");
  script.src = remoteUrl;
  script.async = true;
  script.onload = function() {{
    if (typeof chatbi_native === "undefined") return;
    try {{
      // Try to use Superset's existing share scope if it uses Module Federation,
      // otherwise provide an isolated empty scope so ChatBI downloads its own React.
      var scope = (window.__webpack_share_scopes__ || {{}}).default || {{}};
      chatbi_native.init(scope);
    }} catch (e) {{
      // Already initialized
    }}
    
    chatbi_native.get("./ChatBIPanel").then(function(factory) {{
      console.log("[ChatBI] Module fetched from container, evaluating factory...");
      var mod = factory();
      console.log("[ChatBI] Factory evaluated, attempting to call mountComponent...");
      if (mod && mod.default && typeof mod.default.mountComponent === "function") {{
        mod.default.mountComponent();
        console.log("[ChatBI] Successfully called mod.default.mountComponent()");
      }} else if (mod && typeof mod.mountComponent === "function") {{
        mod.mountComponent();
        console.log("[ChatBI] Successfully called mod.mountComponent()");
      }} else {{
        console.error("[ChatBI] mountComponent function not found in exported module:", mod);
      }}
    }}).catch(function(err) {{
      console.error("[ChatBI] Failed to mount:", err);
    }});
  }};
  script.onerror = function() {{
    console.error("[ChatBI] Failed to load remoteEntry.js from " + remoteUrl);
  }};
  document.head.appendChild(script);
}})();
</script>
"""


_REMOTE_URL = os.getenv("CHATBI_REMOTE_ENTRY_URL", "http://localhost:3099/remoteEntry.js")
_LOADER_BYTES = _CHATBI_LOADER_TEMPLATE.format(remote_entry_url=_REMOTE_URL).encode("utf-8")

import gzip

@blueprint.after_app_request
def inject_chatbi_loader(response: Response) -> Response:
    """
    App-level after_request hook that injects the ChatBI loader script
    into every HTML response served by Superset.
    """
    content_type = response.content_type or ""
    if "text/html" not in content_type:
        return response

    if response.is_streamed:
        return response

    try:
        data = response.get_data()
        is_gzipped = response.content_encoding == 'gzip'

        if is_gzipped:
            data = gzip.decompress(data)

        logger.info(
            "ChatBI: after_app_request HTML response, len=%d, has_body_tag=%s",
            len(data), b"</body>" in data
        )

        if b"</body>" in data and b"__chatbi_loaded" not in data:
            data = data.replace(b"</body>", _LOADER_BYTES + b"</body>")
            if is_gzipped:
                data = gzip.compress(data)
            response.set_data(data)
            logger.info("ChatBI: script injected successfully!")
            
    except Exception as exc:
        logger.error("ChatBI: injection failed: %s", exc)

    return response

logger.info("ChatBI: registered loader injection (remote=%s)", _REMOTE_URL)

@blueprint.route("/test-inject", methods=["GET"])
def test_inject() -> Response:
    """Diagnostic: returns a minimal HTML page to verify script injection."""
    return Response(
        "<html><head></head><body><h1>ChatBI Injection Test</h1></body></html>",
        content_type="text/html",
    )


# ── Health Check ──────────────────────────────────────────────────────
@blueprint.route("/health", methods=["GET"])
def health() -> Response:
    """Simple liveness probe — also validates env config."""
    try:
        from chatbi_native.config import Config
        Config.validate()
        status = "ok"
    except EnvironmentError as exc:
        status = f"misconfigured: {exc}"

    return jsonify({
        "status": status,
        "extension": "chatbi-native",
        "version": "0.3.0",
    })

# ── Multi-Session Routing ─────────────────────────────────────────────
def get_user_id() -> str:
    from flask import g
    if hasattr(g, 'user') and getattr(g.user, 'username', None):
        return str(g.user.username)
    return "anonymous"

@blueprint.route("/sessions", methods=["GET"])
def fetch_sessions() -> Response:
    from chatbi_native.db import get_user_sessions
    try:
        sessions = get_user_sessions(get_user_id())
        return jsonify(sessions)
    except Exception as e:
        logger.error(f"Failed to fetch sessions: {e}")
        return jsonify({"error": str(e)}), 500

@blueprint.route("/sessions/sync", methods=["POST"])
def sync_session() -> Response:
    from chatbi_native.db import save_session
    try:
        session_data = request.get_json(silent=True)
        if not session_data or 'id' not in session_data or 'title' not in session_data:
            return jsonify({"error": "Invalid session data schema"}), 400
        save_session(get_user_id(), session_data)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Failed to sync backend session: {e}")
        return jsonify({"error": str(e)}), 500

@blueprint.route("/sessions/<session_id>", methods=["DELETE"])
def remove_session(session_id: str) -> Response:
    from chatbi_native.db import delete_session
    try:
        delete_session(get_user_id(), session_id)
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Failed to delete backend session: {e}")
        return jsonify({"error": str(e)}), 500

# ── Chat Endpoint ─────────────────────────────────────────────────────
@blueprint.route("/chat", methods=["POST"])
def chat() -> Response:
    """
    Streaming chat endpoint powered by the LangGraph ReAct agent.

    The endpoint is intentionally simple — all LLM / MCP orchestration
    lives in ``chatbi_native.agent.stream_agent``.
    """
    body = request.get_json(silent=True) or {}
    user_message: str = body.get("message", "").strip()
    history: list[dict] = body.get("history", [])
    mcp_url: str | None = body.get("mcp_url")  # optional override

    if not user_message:
        return jsonify({"error": "Missing 'message' field"}), 400

    logger.info(
        "ChatBI /chat: message=%r history_len=%d", user_message[:80], len(history)
    )

    def generate() -> Generator[str, None, None]:
        try:
            from chatbi_native.agent import stream_agent

            # Get user ID for caching
            user_id = get_user_id()
            
            for chunk in stream_agent(user_message, history=history, mcp_url=mcp_url, user_id=user_id):
                yield f"data: {json.dumps(chunk)}\n\n"

        except ImportError as exc:
            # Agent deps not installed yet (Phase 2 setup incomplete)
            error_chunk = {
                "type": "error",
                "content": (
                    f"Agent dependencies not installed: {exc}. "
                    "Run: pip install -e .[dev] inside backend/"
                ),
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as exc:
            logger.exception("Unhandled error in /chat stream")
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@blueprint.after_request
def apply_cors(response: Response) -> Response:
    """Ensure all responses (including auto-generated OPTIONS) have CORS headers."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response
