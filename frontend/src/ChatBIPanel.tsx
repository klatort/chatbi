/**
 * ChatBIPanel — Full Floating Chat Panel (Phase 3)
 * ==================================================
 * A globally available, collapsible chat panel that connects to the
 * ChatBI LangGraph backend and streams Agentic BI responses.
 *
 * Features:
 *  - Floating Action Button (FAB) in bottom-right corner
 *  - Smooth slide-up animation
 *  - Streaming token responses with blinking cursor
 *  - MCP tool call cards (collapsible, with spinner)
 *  - Lightweight markdown rendering (bold, code, headings, bullets)
 *  - Full conversation history maintained in Zustand store
 *  - Keyboard: Enter to send, Shift+Enter for newline, Escape to close
 */

import React, { useEffect, useRef, useCallback } from 'react';
import { useChatStore } from './store';
import { MessageBubble } from './MessageBubble';
import './styles/globals.css';

// ── Icons ─────────────────────────────────────────────────────────────

const ChatIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
    <path d="M21 11.5a9.5 5.5 0 01-9.5 5.5A9.5 5.5 0 012 11.5 9.5 5.5 0 0111.5 6 9.5 5.5 0 0121 11.5z" stroke="currentColor" strokeWidth="0" fill="none"/>
    <path d="M21 11.5C21.003 12.82 20.695 14.122 20.1 15.3C19.394 16.712 18.31 17.899 16.967 18.729C15.625 19.559 14.078 19.999 12.5 20C11.18 20.003 9.878 19.695 8.7 19.1L3 21L4.9 15.3C4.305 14.122 3.997 12.82 4 11.5C4.001 9.922 4.441 8.375 5.271 7.033C6.101 5.69 7.288 4.606 8.7 3.9C9.878 3.305 11.18 2.997 12.5 3H13C15.084 3.115 17.053 3.995 18.529 5.471C20.005 6.947 20.885 8.916 21 11V11.5Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    <circle cx="8.5" cy="11.5" r="1" fill="currentColor"/>
    <circle cx="12.5" cy="11.5" r="1" fill="currentColor"/>
    <circle cx="16.5" cy="11.5" r="1" fill="currentColor"/>
  </svg>
);

const CloseIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
    <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"/>
  </svg>
);

const SendIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
    <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

const ClearIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
    <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
  </svg>
);

const SparkleIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2l2.09 6.26L20 9.27l-5.45 4.7 1.36 6.03L12 16.9l-3.91 3.1 1.36-6.03L4 9.27l5.91-1.01L12 2z"/>
  </svg>
);

// ── Prompt suggestions ─────────────────────────────────────────────────

const SUGGESTIONS = [
  'What datasets are available?',
  'Describe the schema of the sales table',
  'What are the top 10 products by revenue?',
  'Show me monthly trends for the last 6 months',
];

// ── Component ─────────────────────────────────────────────────────────

export const ChatBIPanel: React.FC = () => {
  const { isOpen, isStreaming, messages, toggle, close, sendMessage, clearHistory } =
    useChatStore();

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [inputValue, setInputValue] = React.useState('');

  // Auto-scroll to bottom as messages update
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) close();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [isOpen, close]);

  const handleSend = useCallback(() => {
    const text = inputValue.trim();
    if (!text || isStreaming) return;
    setInputValue('');
    sendMessage(text);
  }, [inputValue, isStreaming, sendMessage]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSuggestion = (text: string) => {
    sendMessage(text);
  };

  const hasMessages = messages.length > 0;

  return (
    <div
      id="chatbi-extension-root"
      style={{
        position: 'fixed',
        bottom: '24px',
        right: '24px',
        zIndex: 9999,
        fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
      }}
    >
      {/* ── Global animation keyframes ───────────────────────────────── */}
      <style>{`
        @keyframes chatbi-slideUp {
          from { opacity: 0; transform: translateY(20px) scale(0.96); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes chatbi-blink {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0; }
        }
        @keyframes chatbi-bounce {
          0%, 80%, 100% { transform: scale(0.8); opacity: 0.6; }
          40%           { transform: scale(1.2); opacity: 1; }
        }
        @keyframes chatbi-spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
        @keyframes chatbi-fab-pop {
          0%   { transform: scale(0.8); opacity: 0; }
          70%  { transform: scale(1.08); }
          100% { transform: scale(1); opacity: 1; }
        }
        #chatbi-fab { animation: chatbi-fab-pop 0.3s cubic-bezier(0.16, 1, 0.3, 1); }
      `}</style>

      {/* ── Floating Action Button ──────────────────────────────────── */}
      {!isOpen && (
        <button
          id="chatbi-fab"
          onClick={toggle}
          title="Open ChatBI"
          style={{
            width: '56px',
            height: '56px',
            borderRadius: '50%',
            border: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#FFFFFF',
            background: 'linear-gradient(135deg, #20A7C9 0%, #1A85A0 100%)',
            boxShadow: '0 4px 20px rgba(32, 167, 201, 0.45)',
            transition: 'transform 0.2s ease, box-shadow 0.2s ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.transform = 'scale(1.1)';
            e.currentTarget.style.boxShadow = '0 6px 24px rgba(32,167,201,0.6)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.transform = 'scale(1)';
            e.currentTarget.style.boxShadow = '0 4px 20px rgba(32,167,201,0.45)';
          }}
        >
          <ChatIcon />
          {/* Unread dot when there are messages */}
          {hasMessages && (
            <span style={{
              position: 'absolute',
              top: '8px', right: '8px',
              width: '10px', height: '10px',
              borderRadius: '50%',
              background: '#FCC700',
              border: '2px solid white',
            }} />
          )}
        </button>
      )}

      {/* ── Chat Panel ─────────────────────────────────────────────── */}
      {isOpen && (
        <div
          style={{
            width: '420px',
            height: '600px',
            borderRadius: '20px',
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            background: '#FFFFFF',
            boxShadow: '0 16px 48px rgba(0, 0, 0, 0.14), 0 2px 8px rgba(0,0,0,0.06)',
            animation: 'chatbi-slideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        >
          {/* ── Header ─────────────────────────────────────────────── */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '14px 18px',
              background: 'linear-gradient(135deg, #20A7C9 0%, #127D96 100%)',
              color: '#FFFFFF',
              flexShrink: 0,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div style={{
                width: '32px', height: '32px', borderRadius: '10px',
                background: 'rgba(255,255,255,0.2)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <SparkleIcon />
              </div>
              <div>
                <div style={{ fontSize: '15px', fontWeight: 700, letterSpacing: '-0.2px' }}>ChatBI</div>
                <div style={{ fontSize: '11px', opacity: 0.8, marginTop: '1px' }}>
                  {isStreaming ? (
                    <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#5AC189', display: 'inline-block', animation: 'chatbi-blink 1s step-end infinite' }} />
                      Thinking…
                    </span>
                  ) : 'Agentic BI Assistant'}
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: '6px' }}>
              {hasMessages && (
                <button
                  onClick={clearHistory}
                  title="Clear conversation"
                  style={{
                    background: 'rgba(255,255,255,0.15)', border: 'none',
                    borderRadius: '8px', width: '30px', height: '30px',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    cursor: 'pointer', color: '#FFFFFF',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.25)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.15)'; }}
                >
                  <ClearIcon />
                </button>
              )}
              <button
                onClick={close}
                title="Close"
                style={{
                  background: 'rgba(255,255,255,0.15)', border: 'none',
                  borderRadius: '8px', width: '30px', height: '30px',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  cursor: 'pointer', color: '#FFFFFF',
                  transition: 'background 0.15s',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.25)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.15)'; }}
              >
                <CloseIcon />
              </button>
            </div>
          </div>

          {/* ── Messages ───────────────────────────────────────────── */}
          <div
            className="chatbi-scrollbar"
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: '16px',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            {!hasMessages ? (
              /* ── Welcome / empty state ── */
              <div style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                textAlign: 'center',
                gap: '16px',
                color: '#6B7280',
              }}>
                <div style={{
                  width: '52px', height: '52px', borderRadius: '14px',
                  background: 'linear-gradient(135deg, #E0F4F8 0%, #C8E8F0 100%)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '24px',
                }}>
                  ✦
                </div>
                <div>
                  <div style={{ fontSize: '16px', fontWeight: 700, color: '#111827', marginBottom: '6px' }}>
                    Ask anything about your data
                  </div>
                  <div style={{ fontSize: '13px', lineHeight: '1.6', maxWidth: '300px' }}>
                    I'll explore your datasets, inspect schemas, run SQL queries, and help you understand your BI data.
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', width: '100%', marginTop: '4px' }}>
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => handleSuggestion(s)}
                      disabled={isStreaming}
                      style={{
                        padding: '10px 14px',
                        borderRadius: '10px',
                        border: '1px solid #E5E7EB',
                        background: '#FAFAFA',
                        fontSize: '13px',
                        color: '#374151',
                        cursor: 'pointer',
                        textAlign: 'left',
                        transition: 'all 0.15s ease',
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.borderColor = '#20A7C9';
                        e.currentTarget.style.background = '#F0FAFB';
                        e.currentTarget.style.color = '#20A7C9';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.borderColor = '#E5E7EB';
                        e.currentTarget.style.background = '#FAFAFA';
                        e.currentTarget.style.color = '#374151';
                      }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              /* ── Conversation messages ── */
              <>
                {messages.map((msg) => (
                  <MessageBubble key={msg.id} message={msg} />
                ))}
                <div ref={messagesEndRef} />
              </>
            )}
          </div>

          {/* ── Input bar ──────────────────────────────────────────── */}
          <div
            style={{
              padding: '12px 14px',
              borderTop: '1px solid #F3F4F6',
              background: '#FAFAFA',
              flexShrink: 0,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-end',
                gap: '8px',
                background: '#FFFFFF',
                borderRadius: '12px',
                border: '1.5px solid #E5E7EB',
                padding: '8px 10px 8px 14px',
                transition: 'border-color 0.15s',
              }}
              onFocusCapture={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = '#20A7C9';
              }}
              onBlurCapture={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = '#E5E7EB';
              }}
            >
              <textarea
                ref={inputRef}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={isStreaming ? 'Thinking…' : 'Ask about your data… (Enter to send)'}
                disabled={isStreaming}
                rows={1}
                style={{
                  flex: 1,
                  border: 'none',
                  outline: 'none',
                  resize: 'none',
                  fontSize: '14px',
                  lineHeight: '1.5',
                  color: '#1F2937',
                  background: 'transparent',
                  fontFamily: 'inherit',
                  overflowY: 'hidden',
                  maxHeight: '100px',
                }}
                onInput={(e) => {
                  // Auto-grow
                  const el = e.currentTarget;
                  el.style.height = 'auto';
                  el.style.height = Math.min(el.scrollHeight, 100) + 'px';
                  if (el.scrollHeight <= 100) el.style.overflowY = 'hidden';
                  else el.style.overflowY = 'auto';
                }}
              />
              <button
                onClick={handleSend}
                disabled={!inputValue.trim() || isStreaming}
                title="Send (Enter)"
                style={{
                  width: '34px',
                  height: '34px',
                  borderRadius: '8px',
                  border: 'none',
                  cursor: !inputValue.trim() || isStreaming ? 'not-allowed' : 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#FFFFFF',
                  background: !inputValue.trim() || isStreaming
                    ? '#D1D5DB'
                    : 'linear-gradient(135deg, #20A7C9 0%, #1A85A0 100%)',
                  transition: 'background 0.15s, opacity 0.15s',
                  flexShrink: 0,
                }}
              >
                {isStreaming ? (
                  <svg width="14" height="14" viewBox="0 0 24 24" style={{ animation: 'chatbi-spin 1s linear infinite' }}>
                    <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" strokeDasharray="32" strokeDashoffset="10"/>
                  </svg>
                ) : (
                  <SendIcon />
                )}
              </button>
            </div>
            <div style={{ fontSize: '11px', color: '#9CA3AF', textAlign: 'center', marginTop: '6px' }}>
              Shift+Enter for new line · Esc to close
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ChatBIPanel;
