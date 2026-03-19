/**
 * ToolCallBadge
 * ==============
 * Renders a compact, expandable card showing a single MCP tool invocation
 * and its result. Shows a spinner while the result is pending.
 */

import React, { useState } from 'react';
import type { ToolCallEvent } from './types';

const TOOL_ICONS: Record<string, string> = {
  list_datasets: '🗂',
  get_schema: '🔍',
  execute_sql: '⚡',
  get_chart_config: '📊',
};

interface ToolCallBadgeProps {
  tool: ToolCallEvent;
}

export const ToolCallBadge: React.FC<ToolCallBadgeProps> = ({ tool }) => {
  const [expanded, setExpanded] = useState(false);
  const isPending = tool.result === undefined;
  const icon = TOOL_ICONS[tool.name] ?? '🔧';

  return (
    <div
      style={{
        margin: '6px 0',
        borderRadius: '8px',
        border: '1px solid #E5E7EB',
        background: '#F9FAFB',
        overflow: 'hidden',
        fontSize: '12px',
      }}
    >
      {/* ── Header ── */}
      <button
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '6px 10px',
          width: '100%',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
          color: '#374151',
        }}
      >
        <span style={{ fontSize: '14px' }}>{icon}</span>
        <span style={{ fontFamily: 'monospace', fontWeight: 600, flex: 1 }}>
          {tool.name}
        </span>
        {isPending ? (
          /* spinner */
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            style={{ animation: 'chatbi-spin 1s linear infinite', color: '#20A7C9' }}
          >
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" strokeDasharray="32" strokeDashoffset="10" />
          </svg>
        ) : (
          <span style={{ color: '#5AC189', fontWeight: 600 }}>✓</span>
        )}
        <span style={{ color: '#9CA3AF', fontSize: '10px' }}>
          {expanded ? '▲' : '▼'}
        </span>
      </button>

      {/* ── Expanded detail ── */}
      {expanded && (
        <div style={{ borderTop: '1px solid #E5E7EB', padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {Object.keys(tool.args).length > 0 && (
            <div>
              <div style={{ color: '#6B7280', marginBottom: '2px', fontWeight: 600 }}>Args</div>
              <pre
                style={{
                  margin: 0,
                  padding: '6px 8px',
                  borderRadius: '6px',
                  background: '#F3F4F6',
                  overflowX: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  color: '#1F2937',
                  lineHeight: '1.4',
                }}
              >
                {JSON.stringify(tool.args, null, 2)}
              </pre>
            </div>
          )}
          {tool.result !== undefined && (
            <div>
              <div style={{ color: '#6B7280', marginBottom: '2px', fontWeight: 600 }}>Result</div>
              <pre
                style={{
                  margin: 0,
                  padding: '6px 8px',
                  borderRadius: '6px',
                  background: '#F0FDF4',
                  overflowX: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  color: '#14532D',
                  lineHeight: '1.4',
                  maxHeight: '200px',
                  overflowY: 'auto',
                }}
              >
                {tool.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
