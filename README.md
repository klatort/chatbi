# ChatBI Native — Superset Agentic BI Extension

A native Apache Superset UI extension providing a conversational Agentic BI interface. No iframes — a floating React chat panel powered by a Python LangGraph backend with MCP integration.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Apache Superset (host)                      │
│                                             │
│  remoteEntry.js ──▶ ChatBIPanel (React)     │
│  (Module Federation)    │                   │
│                         ▼ SSE stream        │
│  Flask Blueprint  ──▶  /extensions/         │
│                         │  chatbi-native/   │
└─────────────────────────┼───────────────────┘
                          ▼
              LangGraph ReAct Agent
                     │
     ┌───────────────┼──────────────┐
     ▼               ▼              ▼
list_datasets    get_schema    execute_sql
     └───────────────┴──────────────┘
              Superset MCP Server
              http://localhost:5008/mcp
```

## Quick Start

### Prerequisites

- [mise](https://mise.jdx.dev/) — manages Node 24 and Python 3.13
- An OpenAI API key (or change `CHATBI_LLM_PROVIDER` to another provider)
- Superset running with a FastMCP server on port 5008

### 1. Install dependencies

```bash
cd chatbi-native/
mise trust
mise run setup
```

### 2. Configure secrets

```bash
cd backend/
cp .env.example .env
# Edit .env and fill in OPENAI_API_KEY
```

### 3. Start dev servers (two terminals)

```bash
# Terminal 1 — LangGraph backend (port 5009)
mise run dev-backend

# Terminal 2 — React frontend dev server (port 3099)
mise run dev-frontend
```

Open **http://localhost:3099** — the teal ChatBI FAB appears in the bottom-right corner.

---

## Project Structure

```
chatbi-native/
├── .mise.toml              # Tool versions + dev tasks
├── extension.json          # Extension manifest
├── superset_config.py      # Drop into Superset config to register
│
├── frontend/
│   ├── src/
│   │   ├── index.tsx       # MF entry (async bootstrap only)
│   │   ├── bootstrap.tsx   # Real app mount + extensionConfig
│   │   ├── ChatBIPanel.tsx # Floating chat panel component
│   │   ├── MessageBubble.tsx  # Markdown renderer + streaming cursor
│   │   ├── ToolCallBadge.tsx  # MCP tool call expandable card
│   │   ├── store.ts        # Zustand store (SSE streaming)
│   │   └── types.ts        # Shared TypeScript types
│   └── webpack.config.js   # Module Federation config
│
└── backend/
    ├── run_server.py        # Standalone dev server (port 5009)
    └── src/chatbi_native/
        ├── api.py           # Flask Blueprint (/extensions/chatbi-native)
        ├── agent.py         # LangGraph ReAct StateGraph
        ├── mcp_client.py    # Async Superset MCP SSE client
        └── config.py        # Env-based config
```

## Mise Tasks

| Command | Description |
|---|---|
| `mise run setup` | Install all frontend and backend deps |
| `mise run dev-backend` | Start Flask backend on port 5009 |
| `mise run dev-frontend` | Start webpack dev server on port 3099 |
| `mise run build` | Production frontend bundle |
| `mise run build-dev` | Dev bundle (faster, with source maps) |

## Superset Integration

To load the extension inside a real Superset instance:

1. **Build the frontend:**
   ```bash
   mise run build
   ```

2. **Serve `frontend/dist/`** via a static file server or CDN.

3. **Register with Superset** by merging `superset_config.py` into your existing config:
   ```bash
   export SUPERSET_CONFIG_PATH=/path/to/chatbi-native/superset_config.py
   ```

4. **Restart Superset.** The ChatBI panel loads as a global overlay on every page.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CHATBI_MCP_URL` | `http://localhost:5008/mcp` | Superset FastMCP server URL |
| `CHATBI_LLM_PROVIDER` | `openai` | LLM provider (`openai`) |
| `OPENAI_API_KEY` | — | **Required** — your OpenAI key |
| `CHATBI_OPENAI_MODEL` | `gpt-4o` | Model to use |
| `CHATBI_PORT` | `5009` | Backend server port |
| `CHATBI_REMOTE_ENTRY_URL` | `http://localhost:3099/remoteEntry.js` | MF remote URL for Superset |

## SSE Event Schema

The `/extensions/chatbi-native/chat` endpoint streams newline-delimited SSE:

```
data: {"type": "token",       "content": "..."}      ← LLM token
data: {"type": "tool_call",   "name": "...", "args": {...}}
data: {"type": "tool_result", "content": "..."}
data: {"type": "done"}
data: {"type": "error",       "content": "..."}
```

## License

Apache 2.0
