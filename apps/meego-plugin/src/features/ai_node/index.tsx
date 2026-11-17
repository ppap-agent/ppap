/**
 * AI node card entry, built for the AI node app (see apps/meego-ai-node). The platform loads this file and calls `main`.
 */
import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom';
import { createRoot } from 'react-dom/client';
import { setHostApp } from '../ppap_tab/ppap';

const App = lazy(() => import('./App'));

export default async function main() {
  // Auth codes on this page are issued to the AI node app; PPAP finds the same person's connection.
  setHostApp('ai_node');
  await window.JSSDK.shared.setSharedModules({ React, ReactDOM });
  await window.JSSDK.utils.overwriteThemeForSemiUI().catch(() => undefined);
  await window.JSSDK.Context.load().then(
    context => {
      const apply = (scheme: string) => (scheme === 'dark' ? document.body.setAttribute('theme-mode', 'dark') : document.body.removeAttribute('theme-mode'));
      apply(context.colorScheme);
      context.watch(next => apply(next.colorScheme));
    },
    () => undefined,
  );
  const container = document.createElement('div');
  document.body.appendChild(container);
  createRoot(container).render(
    <Suspense fallback={null}>
      <App />
    </Suspense>,
  );
}
