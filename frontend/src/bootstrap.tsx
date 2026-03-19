/**
 * bootstrap.tsx — Async bootstrap for Module Federation
 *
 * This file contains the real application bootstrap logic.
 * It is dynamically imported by index.tsx so that the Module
 * Federation runtime has time to resolve shared singletons
 * (react, react-dom) before any code tries to use them.
 *
 * Without this boundary, React/ReactDOM are not yet in scope
 * when index.tsx runs synchronously, causing a silent blank page.
 */

import React from 'react';
import { createRoot } from 'react-dom/client';
import { ChatBIPanel } from './ChatBIPanel';
import './styles/globals.css';

// ── Superset Extension Registry export ───────────────────────────────
export const extensionConfig = {
  id: 'chatbi-native',
  name: 'ChatBI',
  version: '0.2.0',
  type: 'GLOBAL_OVERLAY',
  component: ChatBIPanel,
  position: 'bottom-right',
  description: 'Conversational Agentic BI assistant powered by LangGraph + MCP',

  mountComponent(): void {
    const mountId = 'chatbi-native-mount';
    if (document.getElementById(mountId)) return;

    const container = document.createElement('div');
    container.id = mountId;
    document.body.appendChild(container);

    createRoot(container).render(
      <React.StrictMode>
        <ChatBIPanel />
      </React.StrictMode>,
    );
  },
};

// ── Standalone dev mount ──────────────────────────────────────────────
const devRoot = document.getElementById('chatbi-root');
if (devRoot) {
  createRoot(devRoot).render(
    <React.StrictMode>
      <ChatBIPanel />
    </React.StrictMode>,
  );
}

export { ChatBIPanel };
export default extensionConfig;
