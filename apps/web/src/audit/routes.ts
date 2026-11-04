export type ProductSurface = 'workbench' | 'audit' | 'archive' | 'requirements' | 'usage' | 'templates'

const paths: Record<ProductSurface, string> = {
  workbench: '/workbench', audit: '/tasks/new', archive: '/files', requirements: '/requirements', usage: '/usage', templates: '/templates',
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

/** Keep saved draft/stage consumers; a bare Round opens the shared historical reader. */
export function usesLegacyJourney(url: URL): boolean {
  if (url.searchParams.has('share')) return false
  if (['/dev', '/rules'].includes(url.pathname)) return true
  if (url.searchParams.has('round')) return ['stage', 'resume'].some(key => url.searchParams.has(key)) || url.pathname === '/' && url.searchParams.has('clause') && !url.searchParams.has('focus')
  return ['case', 'draft'].some(key => url.searchParams.has(key))
}

/** A refresh must read the original command receipt, never create another command. */
export function requestAddress(url: URL, taskId: string, kind: 'recognitionRequest' | 'acceptRequest' | 'precheckRequest', requestId: string): string {
  const next = new URL(url)
  if (next.pathname.startsWith('/preview/') || next.pathname === '/__preview') next.searchParams.set('task', taskId)
  else {next.pathname = taskAddress(taskId); next.searchParams.delete('task')}
  next.searchParams.delete('view'); next.searchParams.set(kind, requestId)
  return next.pathname + next.search + next.hash
}
export function rememberRequest(taskId: string, kind: 'recognitionRequest' | 'acceptRequest' | 'precheckRequest', requestId: string) {
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
  const surface = view && Object.hasOwn(paths, view) ? view as ProductSurface : url.searchParams.has('task') || url.searchParams.has('round') || ['/audit', '/__audit-native', '/audit.html', '/preview/audit.html'].includes(path) ? 'audit' : 'workbench'
  return {surface, taskId: url.searchParams.get('task')}
}

/** Preserve legacy identities; a Round is not a Task and a share is not a task grant. */
export function canonicalAddress(url: URL): string {
  const next = new URL(url)
  if (next.searchParams.has('round') && !next.searchParams.has('share') && !usesLegacyJourney(next)) {
    next.pathname = '/audit'
    return next.pathname + next.search + next.hash
  }
  if (['case', 'draft', 'round', 'share'].some(key => next.searchParams.has(key))) return next.pathname + next.search + next.hash
  const {surface, taskId} = readAuditRoute(next)
  next.pathname = surface === 'audit' ? taskAddress(taskId) : surfaceAddress(surface)
  next.searchParams.delete('view')
  if (surface === 'audit') next.searchParams.delete('task')
  return next.pathname + next.search + next.hash
}
