/**
 * ChatBI Type Definitions
 * ========================
 * Shared types for the chat store and UI components.
 */

/** SSE event types emitted by the backend streaming /chat endpoint */
export type SSEEventType = 'token' | 'tool_call' | 'tool_result' | 'done' | 'error';

export interface SSEEvent {
  type: SSEEventType;
  content?: string;
  name?: string;
  args?: Record<string, unknown>;
}

/** A single tool call with its corresponding result */
export interface ToolCallEvent {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: string;
}

/** A message in the conversation */
export type MessageRole = 'user' | 'assistant';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  /** Text content — built up token by token for assistant messages */
  content: string;
  /** Tool calls made during this assistant turn */
  toolCalls?: ToolCallEvent[];
  /** True while streaming is in progress */
  streaming?: boolean;
  /** Error text if the turn failed */
  error?: string;
  timestamp: number;
}

/** Store shape */
export interface ChatStore {
  // ── State ──────────────────────────────────────────────────────────
  messages: ChatMessage[];
  isOpen: boolean;
  isStreaming: boolean;
  /** Backend base URL — defaults to same origin, can be overridden */
  backendUrl: string;

  // ── Actions ────────────────────────────────────────────────────────
  toggle: () => void;
  open: () => void;
  close: () => void;
  sendMessage: (text: string) => Promise<void>;
  clearHistory: () => void;
  setBackendUrl: (url: string) => void;
}
