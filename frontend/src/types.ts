/**
 * ChatBI Type Definitions
 * ========================
 * Shared types for the chat store and UI components.
 */

/** SSE event types emitted by the backend streaming /chat endpoint */
export type SSEEventType = 'token' | 'tool_call' | 'tool_result' | 'done' | 'error';

export interface SSEEvent {
  type: SSEEventType;
  id?: string;
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

export type MessageBlock = 
  | { type: 'text'; content: string }
  | { type: 'tool'; toolCall: ToolCallEvent };

/** A message in the conversation */
export type MessageRole = 'user' | 'assistant';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  /** Text content — built up token by token for assistant messages */
  content: string;
  /** Ordered timeline of everything the AI emitted (text chunks and tool usages) */
  blocks?: MessageBlock[];
  /** Tool calls made during this assistant turn */
  toolCalls?: ToolCallEvent[];
  /** True while streaming is in progress */
  streaming?: boolean;
  /** Error text if the turn failed */
  error?: string;
  timestamp: number;
}

export interface ChatSession {
  id: string;
  title: string;
  messages: ChatMessage[];
  updatedAt: number;
}

/** Store shape */
export interface ChatStore {
  // ── Multi-session State ────────────────────────────────────────────
  sessions: Record<string, ChatSession>;
  activeSessionId: string | null;

  // ── Transient State ────────────────────────────────────────────────
  isOpen: boolean;
  isStreaming: boolean;
  backendUrl: string;

  // ── Actions ────────────────────────────────────────────────────────
  toggle: () => void;
  open: () => void;
  close: () => void;
  
  // Session Actions
  fetchSessions: () => Promise<void>;
  syncSession: (sessionId: string) => Promise<void>;
  createNewSession: () => void;
  switchSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => Promise<void>;
  clearHistory: () => void; // Clears active session
  sendMessage: (text: string) => Promise<void>;
  setBackendUrl: (url: string) => void;
}
