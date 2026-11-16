/**
 * Plugin main entry point. The plugin runtime loads this file and calls `main`.
 */
import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom';
import { createRoot } from 'react-dom/client';

const App = lazy(() => import('./App'));

export default async function main() {
  await window.JSSDK.shared.setSharedModules({ React, ReactDOM });
  const container = document.createElement('div');
  container.id = 'app';
  document.body.appendChild(container);
  createRoot(container).render(
    <Suspense fallback={<p className="muted">正在加载…</p>}>
      <App />
    </Suspense>,
  );
}
