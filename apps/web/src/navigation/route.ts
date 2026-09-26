const ID = /^[a-f0-9-]+$/i

export type ReturnTarget =
  | {page: 'audit-new'; draftId?: string; fromRoundId?: string}
  | {page: 'audit-round'; roundId: string}

export type Route =
  | {page: 'audit-new'; draftId?: string; fromRoundId?: string}
  | {page: 'audit-round'; roundId: string; review?: 'next'}
  | {page: 'audit-history'}
  | {page: 'usage'}
  | {page: 'dev'}
  | {page: 'rule-library'; returnTo?: ReturnTarget}
  | {page: 'rule-detail'; ruleId: string; saved?: boolean; returnTo?: ReturnTarget}
  | {page: 'rule-import'; predecessorId?: string; parseId?: string; documentType?: string; returnTo?: ReturnTarget}

function id(value: string | null) {
  return value && ID.test(value) ? value : undefined
}

function defined<T extends Route>(value: T): T {
  const copy = {...value}
  for (const key of Object.keys(copy) as Array<keyof T>) {
    if (copy[key] === undefined) delete copy[key]
  }
  return copy
}

export function isSafeReturnTo(value: string) {
  return parseReturnTarget(value) !== undefined
}

export function parseReturnTarget(value: string | null): ReturnTarget | undefined {
  if (!value || value.includes('//') || value.includes('\\') || value.includes('@')) return undefined
  const match = value.match(/^\/\?(.+)$/)
  if (!match) return undefined
  const params = new URLSearchParams(match[1])
  const roundId = id(params.get('round'))
  const draftId = id(params.get('draft'))
  const fromRoundId = id(params.get('from'))
  const allowed = new Set(roundId ? ['round'] : ['draft', 'from'])
  for (const key of params.keys()) if (!allowed.has(key)) return undefined
  if (roundId && !draftId && !fromRoundId) return {page: 'audit-round', roundId}
  if (draftId && !roundId) {
    return fromRoundId ? {page: 'audit-new', draftId, fromRoundId} : {page: 'audit-new', draftId}
  }
  return undefined
}

export function returnLabel(target?: ReturnTarget) {
  if (!target) return '规则库'
  return target.page === 'audit-round' ? '返回审核结果' : '返回审核准备'
}

function encodeReturn(target?: ReturnTarget) {
  if (!target) return ''
  return productHref(target)
}

export function productHref(route: Route) {
  if (route.page === 'audit-history') return '/audit-files'
  if (route.page === 'usage') return '/usage'
  if (route.page === 'dev') return '/dev'
  if (route.page === 'audit-round') {
    const params = new URLSearchParams({round: route.roundId})
    if (route.review === 'next') params.set('review', 'next')
    return '/?' + params
  }
  if (route.page === 'audit-new') {
    const params = new URLSearchParams()
    if (route.draftId) params.set('draft', route.draftId)
    if (route.fromRoundId) params.set('from', route.fromRoundId)
    const query = params.toString()
    return query ? '/?' + query : '/'
  }
  const params = new URLSearchParams()
  if (route.page === 'rule-detail') {
    params.set('rule', route.ruleId)
    if (route.saved) params.set('saved', '1')
  } else if (route.page === 'rule-import') {
    if (route.parseId) params.set('parse', route.parseId)
    else params.set('new', '1')
    if (route.predecessorId) params.set('predecessor', route.predecessorId)
    if (route.documentType) params.set('type', route.documentType)
  }
  const returnTo = 'returnTo' in route ? encodeReturn(route.returnTo) : ''
  if (returnTo) params.set('return_to', returnTo)
  const query = params.toString()
  return query ? '/rules?' + query : '/rules'
}

export function parseProductRoute(pathname = '/', search = ''): Route {
  const path = pathname.replace(/\/$/, '') || '/'
  const params = new URLSearchParams(search)
  const returnTo = parseReturnTarget(params.get('return_to'))
  if (path === '/audit-files') return {page: 'audit-history'}
  if (path === '/usage') return {page: 'usage'}
  if (path === '/dev') return {page: 'dev'}
  if (path === '/rules') {
    const ruleId = id(params.get('rule'))
    const parseId = id(params.get('parse'))
    const predecessorId = id(params.get('predecessor'))
    const documentType = params.get('type') || undefined
    if (params.has('new') || parseId || (!ruleId && predecessorId)) {
      return defined({page: 'rule-import', predecessorId, parseId, documentType: documentType || undefined, returnTo})
    }
    if (ruleId) return defined({page: 'rule-detail', ruleId, saved: params.get('saved') === '1' ? true : undefined, returnTo})
    return defined({page: 'rule-library', returnTo})
  }
  const roundId = id(params.get('round'))
  if (path === '/' && roundId) {
    return defined({page: 'audit-round', roundId, review: params.get('review') === 'next' ? 'next' as const : undefined})
  }
  return defined({page: 'audit-new', draftId: id(params.get('draft')), fromRoundId: id(params.get('from'))})
}

export function isTenantDenied(search: string) {
  return new URLSearchParams(search).get('error') === 'tenant_not_allowed'
}

export function shellPath(route: Route, reviewing: boolean) {
  if (reviewing || route.page === 'audit-history') return '/audit-files'
  if (route.page === 'usage') return '/usage'
  if (route.page === 'rule-library' || route.page === 'rule-detail' || route.page === 'rule-import') return '/rules'
  return '/'
}
