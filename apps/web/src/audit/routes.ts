export type ProductSurface = 'audit' | 'archive' | 'requirements' | 'usage' | 'templates' | 'receipts'

const paths: Record<ProductSurface, string> = {
  audit: '/tasks/new', archive: '/files', requirements: '/requirements', usage: '/usage', templates: '/templates', receipts: '/receipts',
}

export const taskAddress = (id: string | null) => id ? `/tasks/${encodeURIComponent(id)}` : paths.audit
export const surfaceAddress = (surface: ProductSurface) => paths[surface]
export const auditAddressChanged = 'ppap-audit-address-changed'
/** The rules page moved between its list and a rule's page; only the page header follows it. */
export const ruleAddressChanged = 'ppap-rule-address-changed'
/** The rules page's own address keys; they never follow the person to another page. */
export const rulesAddressKeys = ['template', 'systemRule', 'ruleQuestions', 'rulebook']

/** My receipts (#361), or one of them highlighted; the address MCP replies link to. */
export const receiptsAddress = (receipt?: string) => receipt ? `${paths.receipts}?${new URLSearchParams({receipt})}` : paths.receipts
/** The usage records of one task's round, found by its task number. */
export const taskUsageAddress = (taskNumber: string) => `${paths.usage}?${new URLSearchParams({usageSearch: taskNumber, usageRange: 'all'})}`

/** A plain click on a link to another page of the product opens it in place, as the browser's back would; any other click
 * (a new tab, a download) is left to the browser. */
export function openInPlace(event: {button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; currentTarget: {href: string}; preventDefault: () => void}) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  history.pushState(history.state, '', event.currentTarget.href)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

/** Observe local address edits without treating them as browser back/forward or rereading a task. */
export function writeAuditAddress(url: URL | string, mode: 'push' | 'replace' = 'replace', state: unknown = history.state) {
  if (mode === 'push') history.pushState(state, '', url)
  else history.replaceState(state, '', url)
  window.dispatchEvent(new Event(auditAddressChanged))
}

/** A refresh must read the original command receipt, never create another command. */
export function requestAddress(url: URL, taskId: string, kind: 'recognitionRequest' | 'acceptRequest', requestId: string): string {
  const next = new URL(url)
  next.pathname = taskAddress(taskId); next.searchParams.delete('task')
  next.searchParams.set(kind, requestId)
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

/** A share is not a task grant; a Round link (Feishu write-back) opens the task that Round belongs to. */
export function canonicalAddress(url: URL): string {
  const next = new URL(url)
  if (next.searchParams.has('share')) return next.pathname + next.search + next.hash
  if (next.searchParams.has('round')) {next.searchParams.delete('task'); next.pathname = surfaceAddress('audit')}
  const {surface, taskId} = readAuditRoute(next)
  next.pathname = surface === 'audit' ? taskAddress(taskId) : surfaceAddress(surface)
  next.searchParams.delete('view')
  if (surface === 'audit') next.searchParams.delete('task')
  return next.pathname + next.search + next.hash
}
