/**
 * MessageBubble
 * ==============
 * Renders a single chat message — user or assistant.
 *
 * Assistant messages:
 *  - Render text content with basic markdown-like formatting
 *  - Show collapsible ToolCallBadge cards for each MCP tool call
 *  - Show a blinking cursor while streaming
 *  - Show an error badge on failure
 */

import React from 'react';
import type { ChatMessage } from './types';
import { ToolCallBadge } from './ToolCallBadge';

/* ── Very lightweight markdown renderer ───────────────────────────────
   Handles: ** bold **, ` code `, ```blocks```, # headings, - bullets
   Full markdown would require a library; this covers BI responses well. */

function renderMarkdown(text: string): React.ReactNode[] {
  const lines = text.split('\n');
  const result: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];

  lines.forEach((line, i) => {
    if (line.startsWith('```')) {
      if (inCodeBlock) {
        result.push(
          <pre
            key={i}
            style={{
              background: '#1E1E2E',
              color: '#CDD6F4',
              borderRadius: '8px',
              padding: '10px 12px',
              overflowX: 'auto',
              fontSize: '12px',
              lineHeight: '1.6',
              margin: '6px 0',
            }}
          >
            <code>{codeLines.join('\n')}</code>
          </pre>,
        );
        codeLines = [];
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
      }
      return;
    }
    if (inCodeBlock) {
      codeLines.push(line);
      return;
    }

    // Heading
    if (/^#{1,3}\s/.test(line)) {
      const level = line.match(/^#+/)?.[0].length ?? 1;
      const hText = line.replace(/^#+\s/, '');
      const sizes = ['18px', '16px', '14px'];
      result.push(
        <div key={i} style={{ fontWeight: 700, fontSize: sizes[level - 1] ?? '14px', marginTop: '10px', marginBottom: '2px', color: '#F3F4F6' }}>
          {inlineMarkdown(hText)}
        </div>,
      );
      return;
    }

    // Bullet
    if (/^[-*]\s/.test(line)) {
      result.push(
        <div key={i} style={{ display: 'flex', gap: '6px', marginTop: '2px' }}>
          <span style={{ color: '#20A7C9', flexShrink: 0, marginTop: '1px' }}>•</span>
          <span>{inlineMarkdown(line.slice(2))}</span>
        </div>,
      );
      return;
    }

    // Horizontal rule
    if (/^---+$/.test(line.trim())) {
      result.push(<hr key={i} style={{ border: 'none', borderTop: '1px solid #404040', margin: '8px 0' }} />);
      return;
    }

    // Empty line
    if (!line.trim()) {
      result.push(<div key={i} style={{ height: '6px' }} />);
      return;
    }

    // Regular paragraph
    result.push(<span key={i} style={{ display: 'block' }}>{inlineMarkdown(line)}</span>);
  });

  return result;
}

/** Handle inline ** bold ** and `code` */
function inlineMarkdown(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  const regex = /(\*\*(.+?)\*\*|`([^`]+)`)/g;
  let last = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    if (match[0].startsWith('**')) {
      parts.push(<strong key={match.index}>{match[2]}</strong>);
    } else {
      parts.push(
        <code key={match.index} style={{ background: '#333333', borderRadius: '4px', padding: '1px 5px', fontSize: '12px', fontFamily: 'monospace', color: '#20A7C9' }}>
          {match[3]}
        </code>,
      );
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length === 1 ? parts[0] : <>{parts}</>;
}

// ── Component ─────────────────────────────────────────────────────────

interface MessageBubbleProps {
  message: ChatMessage;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  const isUser = message.role === 'user';

  if (isUser) {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '12px' }}>
        <div
          style={{
            maxWidth: '80%',
            padding: '10px 14px',
            borderRadius: '16px 16px 4px 16px',
            background: 'linear-gradient(135deg, #20A7C9 0%, #1A85A0 100%)',
            color: '#FFFFFF',
            fontSize: '14px',
            lineHeight: '1.5',
            wordBreak: 'break-word',
          }}
        >
          {message.content}
        </div>
      </div>
    );
  }

  // ── Assistant message ─────────────────────────────────────────────
  const hasContent = message.content.length > 0;
  const hasTools = (message.toolCalls?.length ?? 0) > 0;

  return (
    <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', alignItems: 'flex-start' }}>
      {/* Avatar */}
      <div
        style={{
          flexShrink: 0,
          width: '28px',
          height: '28px',
          borderRadius: '8px',
          background: 'linear-gradient(135deg, #1A3E48 0%, #144957 100%)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '14px',
          color: '#20A7C9',
        }}
      >
        ✦
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Render interleaved blocks */}
        {(message.blocks?.length ?? 0) > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {message.blocks!.map((block, idx) => {
              if (block.type === 'text') {
                return (
                  <div
                    key={`text-${idx}`}
                    style={{
                      background: '#2A2A2A',
                      border: '1px solid #3A3A3A',
                      borderRadius: '4px 16px 16px 16px',
                      padding: '10px 14px',
                      fontSize: '14px',
                      lineHeight: '1.6',
                      color: '#F3F4F6',
                      wordBreak: 'break-word',
                    }}
                  >
                    {renderMarkdown(block.content)}
                    {/* Blinking cursor if this is the last block and streaming */}
                    {message.streaming && idx === message.blocks!.length - 1 && (
                      <span
                        style={{
                          display: 'inline-block',
                          width: '2px',
                          height: '14px',
                          background: '#20A7C9',
                          marginLeft: '2px',
                          verticalAlign: 'middle',
                          animation: 'chatbi-blink 1s step-end infinite',
                        }}
                      />
                    )}
                  </div>
                );
              } else if (block.type === 'tool') {
                return <ToolCallBadge key={block.toolCall.id} tool={block.toolCall} />;
              }
              return null;
            })}
          </div>
        ) : (
          /* Thinking indicator (no content yet, still streaming) */
          !message.error && message.streaming && (
            <div
              style={{
                background: '#2A2A2A',
                border: '1px solid #3A3A3A',
                borderRadius: '4px 16px 16px 16px',
                padding: '10px 14px',
                display: 'flex',
                gap: '4px',
                alignItems: 'center',
              }}
            >
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  style={{
                    width: '6px',
                    height: '6px',
                    borderRadius: '50%',
                    background: '#20A7C9',
                    display: 'inline-block',
                    animation: `chatbi-bounce 1.2s ease-in-out ${i * 0.2}s infinite`,
                  }}
                />
              ))}
            </div>
          )
        )}

        {/* Error state */}
        {message.error && (
          <div
            style={{
              marginTop: '6px',
              padding: '8px 12px',
              borderRadius: '8px',
              background: '#450A0A',
              border: '1px solid #7F1D1D',
              color: '#FCA5A5',
              fontSize: '13px',
            }}
          >
            ⚠ {message.error}
          </div>
        )}
      </div>
    </div>
  );
};
