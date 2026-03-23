/**
 * Zustand store for the ChatBI panel.
 *
 * Responsibilities:
 *  - Track isolated multi-tenant chat sessions synchronized with the backend
 *  - Stream assistant responses from the backend SSE endpoint,
 *    accumulating tokens into the active session block
 */

import { create } from 'zustand';
import type { ChatMessage, ChatStore, SSEEvent, ToolCallEvent, ChatSession } from './types';

// ── Helpers ───────────────────────────────────────────────────────────

const uuid = (): string =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : Date.now().toString(36) + Math.random().toString(36).slice(2);

const BACKEND_URL =
  (typeof window !== 'undefined' && (window as any).__CHATBI_BACKEND_URL__) ||
  '';

// ── Store ─────────────────────────────────────────────────────────────

export const useChatStore = create<ChatStore>((set, get) => ({
  sessions: {},
  activeSessionId: null,
  isOpen: false,
  isStreaming: false,
  backendUrl: BACKEND_URL,

  // ── Panel controls ─────────────────────────────────────────────────
  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),

  setBackendUrl: (url: string) => set({ backendUrl: url }),

  clearHistory: () => {
    const { activeSessionId } = get();
    if (activeSessionId) {
      set(s => ({
        sessions: {
          ...s.sessions,
          [activeSessionId]: { ...s.sessions[activeSessionId], messages: [], updatedAt: Date.now() }
        }
      }));
      get().syncSession(activeSessionId);
    }
  },

  // ── Session Management ─────────────────────────────────────────────

  createNewSession: () => {
    const id = uuid();
    const newSession: ChatSession = { id, title: 'New Chat', messages: [], updatedAt: Date.now() };
    set(s => ({
      sessions: { ...s.sessions, [id]: newSession },
      activeSessionId: id
    }));
  },

  switchSession: (sessionId: string) => {
    set({ activeSessionId: sessionId });
  },

  deleteSession: async (sessionId: string) => {
    const { backendUrl, activeSessionId } = get();
    
    // Optimistic UI updates
    set(s => {
      const { [sessionId]: _, ...rest } = s.sessions;
      const remainingIds = Object.keys(rest).sort((a,b) => rest[b].updatedAt - rest[a].updatedAt);
      
      return { 
        sessions: rest,
        activeSessionId: activeSessionId === sessionId ? (remainingIds[0] || null) : activeSessionId
      };
    });
    
    try {
      let csrfToken = '';
      if (typeof window !== 'undefined') {
        const csrfEl = document.getElementById('csrf_token') as HTMLInputElement;
        if (csrfEl) csrfToken = csrfEl.value;
      }
      await fetch(`${backendUrl}/extensions/chatbi-native/sessions/${sessionId}`, { 
        method: 'DELETE',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken }
      });
    } catch (e) {
      console.error('[ChatBI] Failed to delete session', e);
    }
  },

  fetchSessions: async () => {
    const { backendUrl } = get();
    try {
      const res = await fetch(`${backendUrl}/extensions/chatbi-native/sessions`, {
          credentials: 'same-origin',
          headers: { 'Cache-Control': 'no-cache' }
      });
      if (res.ok) {
        const data: ChatSession[] = await res.json();
        const map: Record<string, ChatSession> = {};
        data.forEach(d => map[d.id] = d);
        
        set(s => ({
          sessions: map,
          // Select latest or spawn new if everything empty
          activeSessionId: s.activeSessionId && map[s.activeSessionId] 
            ? s.activeSessionId 
            : (data.length > 0 ? data[0].id : null)
        }));
      }
    } catch (e) {
      console.error('[ChatBI] Failed to fetch sessions', e);
    }
  },

  syncSession: async (sessionId: string) => {
    const { backendUrl, sessions } = get();
    const session = sessions[sessionId];
    if (!session) return;
    
    // Auto-generate title if it's "New Chat" and has messages
    if (session.title === 'New Chat' && session.messages.length > 0) {
      const firstUser = session.messages.find(m => m.role === 'user');
      if (firstUser) {
        session.title = firstUser.content.slice(0, 30) + (firstUser.content.length > 30 ? '...' : '');
      }
    }

    try {
      let csrfToken = '';
      if (typeof window !== 'undefined') {
        const csrfEl = document.getElementById('csrf_token') as HTMLInputElement;
        if (csrfEl) csrfToken = csrfEl.value;
      }

      await fetch(`${backendUrl}/extensions/chatbi-native/sessions/sync`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken
        },
        body: JSON.stringify(session)
      });
    } catch (e) {
      console.error('[ChatBI] Failed to sync session', e);
    }
  },

  // ── Send message & stream response ────────────────────────────────
  sendMessage: async (text: string) => {
    const { backendUrl, isStreaming } = get();
    if (!text.trim() || isStreaming) return;

    let { activeSessionId, sessions } = get();
    
    // Auto-spawn session if user is engaging an empty store
    if (!activeSessionId) {
      get().createNewSession();
      activeSessionId = get().activeSessionId!;
    }

    const currentMessages = get().sessions[activeSessionId]?.messages || [];

    // 1. Append the user message
    const userMsg: ChatMessage = {
      id: uuid(),
      role: 'user',
      content: text.trim(),
      timestamp: Date.now(),
    };

    // 2. Create a streaming assistant placeholder
    const assistantId = uuid();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      blocks: [],
      toolCalls: [],
      streaming: true,
      timestamp: Date.now(),
    };

    // 3. Inject eagerly into Active Session
    set(s => {
      const target = s.sessions[activeSessionId!];
      return {
        isStreaming: true,
        sessions: {
          ...s.sessions,
          [activeSessionId!]: {
            ...target,
            messages: [...currentMessages, userMsg, assistantMsg],
            updatedAt: Date.now()
          }
        }
      };
    });

    // Helper to patch the last assistant message strictly within the active session
    const patchAssistant = (updater: (m: ChatMessage) => Partial<ChatMessage>) => {
      set((state) => {
        const tempTarget = state.sessions[activeSessionId!];
        if (!tempTarget) return state;
        return {
          sessions: {
            ...state.sessions,
            [activeSessionId!]: {
              ...tempTarget,
              messages: tempTarget.messages.map((m) =>
                m.id === assistantId ? { ...m, ...updater(m) } : m,
              ),
              updatedAt: Date.now() // bump timestamp on every chunk
            }
          }
        };
      });
    };

    try {
      // Build conversation history for the backend (excluding the placeholder)
      const executionMessages = [...currentMessages, userMsg];
      const history = executionMessages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      // Extract Superset context from URL to help the agent
      let contextStr = '';
      if (typeof window !== 'undefined') {
        const path = window.location.pathname;
        const search = window.location.search;
        console.log('[ChatBI] Context Parse -> Path:', path, 'Search:', search);
        if (path.includes('/superset/dashboard/')) {
          const dashMatch = path.match(/\/dashboard\/([a-zA-Z0-9_\.-]+)/);
          if (dashMatch) {
            contextStr = `\n\n[System Context: User is viewing Dashboard ID/Slug: ${dashMatch[1]}]`;
          }
        } else if (path.includes('/superset/explore/')) {
          const params = new URLSearchParams(search);
          const sliceId = params.get('slice_id') || params.get('form_data');
          if (sliceId) {
            contextStr = `\n\n[System Context: User is exploring Chart/Slice ID: ${sliceId}]`;
          }
        }
      }

      let csrfToken = '';
      if (typeof window !== 'undefined') {
        const csrfEl = document.getElementById('csrf_token') as HTMLInputElement;
        if (csrfEl) csrfToken = csrfEl.value;
      }

      const response = await fetch(
        `${backendUrl}/extensions/chatbi-native/chat`,
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
          },
          body: JSON.stringify({ message: text.trim() + contextStr, history }),
        },
      );

      if (!response.ok || !response.body) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      // 4. Stream SSE chunks
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE lines
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;

          let event: SSEEvent;
          try {
            event = JSON.parse(raw) as SSEEvent;
          } catch {
            continue;
          }

          switch (event.type) {
            case 'token': {
              const text = event.content;
              if (text) {
                patchAssistant((m) => {
                  const blocks = [...(m.blocks ?? [])];
                  if (blocks.length > 0 && blocks[blocks.length - 1].type === 'text') {
                    // Append to last text block
                    const last = { ...blocks[blocks.length - 1] } as { type: 'text', content: string };
                    last.content += text;
                    blocks[blocks.length - 1] = last;
                  } else {
                    // Create new text block
                    blocks.push({ type: 'text', content: text });
                  }
                  return { content: m.content + text, blocks };
                });
              }
              break;
            }

            case 'tool_call': {
              if (!event.id && !event.name) break; // Ignore completely blank ghost chunks

              const tc: ToolCallEvent = {
                id: event.id || event.name || uuid(),
                name: event.name || 'unknown',
                args: event.args || {},
              };
              patchAssistant((m) => {
                const existingIndex = (m.toolCalls ?? []).findIndex(e => e.id === tc.id);
                if (existingIndex >= 0) {
                  // Merge late-arriving chunk args
                  const toolCalls = [...(m.toolCalls ?? [])];
                  toolCalls[existingIndex] = { 
                    ...toolCalls[existingIndex], 
                    args: Object.keys(tc.args).length > 0 ? tc.args : toolCalls[existingIndex].args 
                  };
                  
                  const blocks = [...(m.blocks ?? [])];
                  const blockIndex = blocks.findIndex(b => b.type === 'tool' && b.toolCall.id === tc.id);
                  if (blockIndex >= 0) {
                    blocks[blockIndex] = { type: 'tool', toolCall: toolCalls[existingIndex] };
                  }
                  return { toolCalls, blocks };
                }

                console.log(`[ChatBI Agent] 🛠️ Calling MCP Tool: ${tc.name}`, tc.args);
                return {
                  toolCalls: [...(m.toolCalls ?? []), tc],
                  blocks: [...(m.blocks ?? []), { type: 'tool', toolCall: tc }]
                };
              });
              break;
            }

            case 'tool_result': {
              const resultId = event.id;
              if (resultId) {
                const isError = event.content?.startsWith('Tool error:') || event.content?.startsWith('Error calling');
                if (isError) {
                  console.error(`[ChatBI Agent] ❌ Tool '${event.name}' Failed!`, event.content);
                } else {
                  console.log(`[ChatBI Agent] ✅ Tool '${event.name}' Returned data (len=${event.content?.length})`);
                }

                patchAssistant((m) => {
                  const toolCalls = (m.toolCalls ?? []).map((tc) =>
                    tc.id === resultId ? { ...tc, result: event.content ?? '' } : tc,
                  );
                  const blocks = (m.blocks ?? []).map((b) => {
                    if (b.type === 'tool' && b.toolCall.id === resultId) {
                      return { ...b, toolCall: { ...b.toolCall, result: event.content ?? '' } };
                    }
                    return b;
                  });
                  return { toolCalls, blocks };
                });
              }
              break;
            }

            case 'error':
              console.error(`[ChatBI Agent] 🚨 Critical Backend Error:`, event.content);
              patchAssistant(() => ({
                error: event.content ?? 'Unknown error',
                streaming: false,
              }));
              break;

            case 'done':
              console.log(`[ChatBI Agent] ✨ Stream Finished.`);
              patchAssistant(() => ({ streaming: false }));
              break;
          }
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      patchAssistant(() => ({
        error: msg,
        streaming: false,
        content: '',
      }));
    } finally {
      set({ isStreaming: false });
      patchAssistant(() => ({ streaming: false }));
      // 💥 Fire sync event to backup the LLM generation block mapping securely into SQLite
      get().syncSession(activeSessionId!);
    }
  },
}));
