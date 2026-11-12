export type ProductSurface = 'audit' | 'archive' | 'requirements' | 'usage' | 'templates'

const paths: Record<ProductSurface, string> = {
  audit: '/tasks/new', archive: '/files', requirements: '/requirements', usage: '/usage', templates: '/templates',
}

export const taskAddress = (id: string | null) => id ? `/tasks/${encodeURIComponent(id)}` : paths.audit
export const surfaceAddress = (surface: ProductSurface) => paths[surface]
export const auditAddressChanged = 'ppap-audit-address-changed'

/** Observe local address edits without treating them as browser back/forward or rereading a task. */
export function writeAuditAddress(url: URL | string, mode: 'push' | 'replace' = 'replace', state: unknown = history.state) {
  if (mode === 'push') history.pushState(state, '', url)
  else history.replaceState(state, '', url)
  window.dispatchEvent(new Event(auditAddressChanged))
}

/** A refresh must read the original command receipt, never create another command. */
export function requestAddress(url: URL, taskId: string, kind: 'recognitionRequest' | 'acceptRequest', requestId: string): string {
  const next = new URL(url)
  if (next.pathname.startsWith('/preview/') || next.pathname === '/__preview') next.searchParams.set('task', taskId)
  else {next.pathname = taskAddress(taskId); next.searchParams.delete('task')}
  next.searchParams.delete('view'); next.searchParams.set(kind, requestId)
  return next.pathname + next.search + next.hash
}
export function rememberRequest(taskId: string, kind: 'recognitionRequest' | 'acceptRequest', requestId: string) {
  if (typeof window === 'undefined') return
  const url = new URL(location.href), route = readAuditRoute(url)
  if (route.surface === 'audit' && (!route.taskId || route.taskId === taskId)) writeAuditAddress(requestAddress(url, taskId, kind, requestId))
}

export function readAuditRoute(url: URL): {surface: ProductSurface; taskId: string | null} {
  const path = url.pathname.replace(/\/$/, '') || '/'
  const task = /^\/tasks\/([^/]+)$/.exec(path)?.[1]
  if (task) return {surface: 'audit', taskId: task === 'new' ? null : decodeURIComponent(task)}
  const natural = (Object.entries(paths) as [ProductSurface, string][]).find(([, value]) => value === path)
  if (natural) return {surface: natural[0], taskId: null}
  const view = url.searchParams.get('view')
  // PPAP Audit is the new-audit page; an address naming no other page lands there.
  const surface = view && Object.hasOwn(paths, view) ? view as ProductSurface : 'audit'
  return {surface, taskId: url.searchParams.get('task')}
}

/** Preserve old identities; a Round is not a Task and a share is not a task grant. */
export function canonicalAddress(url: URL): string {
  const next = new URL(url)
  if (next.searchParams.has('share')) return next.pathname + next.search + next.hash
  // First-generation addresses (drafts, cases) are no longer read; they land on PPAP Audit.
  // A Round link (Feishu write-back, review hints) opens the task that Round belongs to.
  const retired = ['case', 'draft'].some(key => next.searchParams.has(key))
  for (const key of ['case', 'draft', 'stage', 'resume']) next.searchParams.delete(key)
  if (next.searchParams.has('round')) next.searchParams.delete('task')
  if (next.pathname === '/rules') next.pathname = surfaceAddress('requirements')
  else if (next.pathname === '/dev' || retired || next.searchParams.has('round')) next.pathname = surfaceAddress('audit')
  const {surface, taskId} = readAuditRoute(next)
  next.pathname = surface === 'audit' ? taskAddress(taskId) : surfaceAddress(surface)
  next.searchParams.delete('view')
  if (surface === 'audit') next.searchParams.delete('task')
  return next.pathname + next.search + next.hash
}
