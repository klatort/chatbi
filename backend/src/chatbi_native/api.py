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
from typing import Generator

from flask import Blueprint, Response, jsonify, request, stream_with_context

logger = logging.getLogger(__name__)

blueprint = Blueprint(
    "chatbi_native",
    __name__,
    url_prefix="/extensions/chatbi-native",
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
        "version": "0.2.0",
    })


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

            for chunk in stream_agent(user_message, history=history, mcp_url=mcp_url):
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
