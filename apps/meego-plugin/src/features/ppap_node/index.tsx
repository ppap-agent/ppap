/**
 * Node-form control entry. The platform loads this file and calls `main`.
 */
import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom';
import { createRoot } from 'react-dom/client';

const Card = lazy(() => import('./Card'));

export default async function main() {
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
      <Card />
    </Suspense>,
  );
}
