/**
 * index.tsx — Module Federation entry point
 *
 * This file must contain ONLY a dynamic import of bootstrap.tsx.
 * NO static imports, NO re-exports.
 *
 * Why: webpack resolves static `import`/`export` statements eagerly at
 * module evaluation time. Any static reference to React here would trigger
 * the "Shared module is not available for eager consumption" error because
 * the MF runtime hasn't had an async tick to negotiate the shared singleton yet.
 *
 * The dynamic import() gives the MF runtime that async tick.
 * All real app code lives in bootstrap.tsx.
 *
 * Note: the webpack.config.js `exposes` points directly at ./src/ChatBIPanel
 * (not at this file), so federation consumers are unaffected.
 */

import('./bootstrap');
