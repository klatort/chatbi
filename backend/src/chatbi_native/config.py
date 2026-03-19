"""
Runtime configuration loaded from environment variables.

Copy `.env.example` → `.env` and fill in your values before running.
"""

from __future__ import annotations

import os


class Config:
    # ── MCP ──────────────────────────────────────────────────────────
    MCP_SERVER_URL: str = os.getenv("CHATBI_MCP_URL", "http://localhost:5008/mcp")

    # ── LLM ──────────────────────────────────────────────────────────
    # Which provider to use: "openai" | "anthropic" | "google"
    LLM_PROVIDER: str = os.getenv("CHATBI_LLM_PROVIDER", "openai")

    # OpenAI / Azure OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("CHATBI_OPENAI_MODEL", "gpt-4o")
    OPENAI_API_BASE: str = os.getenv("OPENAI_API_BASE", "")

    # ── Server ───────────────────────────────────────────────────────
    FLASK_DEBUG: bool = os.getenv("FLASK_DEBUG", "0") == "1"

    @classmethod
    def validate(cls) -> None:
        """Raise at startup if critical env vars are missing."""
        if cls.LLM_PROVIDER == "openai" and not cls.OPENAI_API_KEY:
            raise EnvironmentError(
                "OPENAI_API_KEY is not set. "
                "Set it in your environment or a .env file."
            )
