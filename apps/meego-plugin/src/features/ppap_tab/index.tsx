/**
 * Plugin main entry point. The plugin runtime loads this file and calls `main`.
 */
import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom';
import { createRoot } from 'react-dom/client';

const App = lazy(() => import('./App'));

export default async function main() {
  await window.JSSDK.shared.setSharedModules({ React, ReactDOM });
  // Something is on screen at once; the theme and the scheme are styles, applied as they arrive.
  const container = document.createElement('div');
  container.id = 'app';
  document.body.appendChild(container);
  createRoot(container).render(
    <Suspense fallback={<p className="muted" style={{ padding: 16 }}>正在加载 PPAP 审核…</p>}>
      <App />
    </Suspense>,
  );
  // Look like the rest of Feishu Project: its component theme, and its light or dark scheme.
  const applyScheme = (scheme: string) => (scheme === 'dark' ? document.body.setAttribute('theme-mode', 'dark') : document.body.removeAttribute('theme-mode'));
  await Promise.all([
    window.JSSDK.utils.overwriteThemeForSemiUI().catch(() => undefined),
    window.JSSDK.Context.load().then(
      context => {
        applyScheme(context.colorScheme);
        context.watch(next => applyScheme(next.colorScheme));
      },
      () => undefined,
    ),
  ]);
}
