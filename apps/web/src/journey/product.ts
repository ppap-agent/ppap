import {request, type FileVersion, type Material, type RuleCandidate, type RuleDetail, type RulesList, type RuleBody} from '../api.ts'
import {clauseSourceReady, isRequirementBasis, localBasis, type RequirementBasis} from './requirements-state.ts'
import type {RequirementCollection, RequirementVersion} from './requirement-library-state.ts'
import {sourceContent} from './requirement-library-state.ts'

export function parsedBasis(basis: RequirementBasis, candidate: RuleCandidate): RequirementBasis {
  return {...basis, confirmed: null, source: {...basis.source, title: candidate.title, version: basis.source.version || 'v1', state: 'ready'},
    server: {...basis.server!, body: candidate.body},
    clauses: candidate.body.rules.map(row => ({id: row.id, title: row.rule_to_check.split('\n')[0].slice(0, 60), text: row.rule_to_check,
      original: row.rule_to_check, source: basis.source.filename, location: `条款 ${row.id}（原件位置待核对）`, checked: false, applicability: 'pending', reason: ''}))}
}
// These two fields are assigned by publication, not editable rule content. Keep
// every other field in the comparison so a changed clause or appendix still fails.
function publicationBody(body: RuleBody): RuleBody {
  const content = {...body}
  delete content.suite_key
  delete content.suite_revision_id
  return content
}
export function basisRuleBody(basis: RequirementBasis): RuleBody {
  if (!basis.server) throw new Error('审核规则尚未连接到规则版本。')
  return {...publicationBody(basis.server.body),
    customers: (basis.server.body.customers ?? []).map(value => value.trim()),
    models: (basis.server.body.models ?? []).map(value => value.trim()),
    parts: (basis.server.body.parts ?? []).map(value => value.trim()),
    rules: basis.clauses.filter(row => row.applicability === 'apply').map(row => {
    const original = basis.server!.body.rules.find(item => item.id === row.id)
    return {id: row.id.trim(), category: row.title.trim(), priority: original?.priority ?? null, rule_to_check: row.text.trim(), type_of_rule: original?.type_of_rule.trim() || 'text'}
  })}
}
export const canonical = (value: unknown): string => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item)
export function sourceFileLabel(filename: string, status?: string): string {
  if (status !== 'unreadable') return filename
  return filename === '原件不可读' ? filename : `${filename} · 原件不可读`
}
export async function readRuleVersions(ids: string[], signal: AbortSignal): Promise<RequirementVersion[]> {
  const details = await Promise.all([...new Set(ids)].map(async id => {
    const rule = await request<RuleDetail>(`/rules/${encodeURIComponent(id)}`, 'GET', undefined, signal)
    if (rule.id !== id) throw new Error('规则版本回执不一致，未显示其他版本。')
    return rule
  }))
  const files = new Map<string, Promise<{file: FileVersion | null; unavailable: boolean}>>()
  return (await Promise.all(details.map(async rule => {
    if (rule.body.kind !== 'engine' || !rule.source_file_id || !rule.source_file_version_id) return []
    const path = `/files/${encodeURIComponent(rule.source_file_id)}/versions/${encodeURIComponent(rule.source_file_version_id)}`
    if (!files.has(path)) files.set(path, request<FileVersion>(path, 'GET', undefined, signal).then(file => ({file, unavailable: false})).catch(cause => {if (signal.aborted) throw cause; return {file: null, unavailable: true}}))
    const read = await files.get(path)!
    if (read.file && (read.file.id !== rule.source_file_version_id || read.file.file_id !== rule.source_file_id)) throw new Error('规则原件回执不一致，未使用其他文件。')
    const material: Material = {file_id: rule.source_file_id, file_version_id: rule.source_file_version_id, original_filename: read.file?.original_filename || rule.title, role: 'reference', rule_version_id: null}
    const base = localBasis(rule.title)
    let basis = parsedBasis({...base, server: {body: rule.body, material, ruleId: rule.id}}, {title: rule.title, body: rule.body, source_file_version_id: rule.source_file_version_id})
    let scope: RequirementCollection['versions'][number]['scope'] = {customer: rule.body.customers?.[0] || '', part: rule.body.parts?.[0] || '', revision: '', allCustomers: rule.customer_scope === 'all', allParts: !rule.body.parts?.length, allRevisions: true}
    let reason = ''
    if (rule.case_context?.registration) {
      try {
        const stored: unknown = JSON.parse(rule.case_context.registration)
        if (stored && typeof stored === 'object' && 'basis' in stored && isRequirementBasis(stored.basis)) {
          basis = {...stored.basis, server: {body: rule.body, material, ruleId: rule.id}}
          if ('scope' in stored && stored.scope && typeof stored.scope === 'object' && ['customer', 'part', 'revision'].every(key => typeof (stored.scope as Record<string, unknown>)[key] === 'string')) scope = stored.scope as typeof scope
          if ('reason' in stored && typeof stored.reason === 'string') reason = stored.reason
        }
      } catch { /* Older published versions have no registration metadata. */ }
    }
    scope = {...scope, allCustomers: rule.customer_scope === 'all'}
    const ownName = basis.source.filename.trim()
    const readableName = read.file?.original_filename?.trim() || ''
    // Keep this rule's own file name. A denied original is unreadable, not the rule title and not another customer.
    const storedName = ownName && ownName !== rule.title ? ownName : ''
    material.original_filename = readableName || (read.unavailable ? storedName || '原件不可读' : rule.title)
    if (read.unavailable) material.status = 'unreadable'
    basis.source.id = rule.id; basis.source.filename = material.original_filename
    if (!rule.case_context?.registration) basis.source.version = rule.created_at.slice(0, 16).replace('T', ' ')
    basis.server!.publishedContent = canonical({body: publicationBody(rule.body), title: basis.source.title, version: basis.source.version})
    if (basis.clauses.every(clause => clause.checked && clauseSourceReady(clause, basis))) basis.server!.verifiedContent = sourceContent(basis)
    return [{id: rule.id, basis, scope, reason, at: rule.created_at, baseId: rule.predecessor_id || null}]
  }))).flat()
}
// Reuse an immutable version only after reading its full content. Unknown publication is reconciled by the same source and body.
export async function publishBasis(basis: RequirementBasis, declaration?: {scope: RequirementCollection['versions'][number]['scope']; reason: string}): Promise<RequirementBasis> {
  const body = basisRuleBody(basis), content = canonical(body), server = basis.server!
  const signature = canonical({body, title: basis.source.title, version: basis.source.version})
  if (!declaration && server.ruleId && server.publishedContent === signature && server.verifiedContent === sourceContent(basis)) return basis
  const title = basis.source.title.trim()
  const case_context = {registration: JSON.stringify({basis: {...basis, confirmed: null, server: undefined, library: undefined}, ...declaration})}
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical({title, body, source: server.material.file_version_id, case_context}))))
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128
  const hex = Array.from(bytes.slice(0, 16), value => value.toString(16).padStart(2, '0')).join('')
  const requestId = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`
  function verified(rule: RuleDetail, exactId = false) {
    if (!rule?.id || exactId && rule.id !== requestId || rule.title !== title || rule.body?.kind !== 'engine' || canonical(publicationBody(rule.body)) !== content || rule.source_file_id !== server.material.file_id || rule.source_file_version_id !== server.material.file_version_id || (rule.predecessor_id ?? null) !== (server.ruleId ?? null) || canonical(rule.case_context) !== canonical(case_context)) throw new Error('保存回执与本次要求不一致。未采用该版本，请核对原请求。')
    const {suite_key: key, suite_revision_id: revision} = rule.body
    // Older immutable receipts may lack Suite metadata. A present binding must
    // be complete and remain in the server-derived publication lineage.
    if (key !== undefined || revision !== undefined || server.body.suite_key !== undefined) {
      const expectedKey = server.ruleId ? server.body.suite_key : `custom:${rule.id}`
      if (typeof key !== 'string' || !key || key !== expectedKey || typeof revision !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(revision)) throw new Error('保存回执的检查版本关联不一致。未采用该版本，请核对原请求。')
    }
    return {...rule, body: rule.body}
  }
  async function find() {
    const direct = await request<RuleDetail>(`/rules/${requestId}`).catch(cause => {if ((cause as {status?: number}).status === 403 || (cause as {status?: number}).status === 404) return null; throw cause})
    if (direct) return verified(direct, true)
    const list = await request<RulesList>('/rules')
    for (const row of list.rules.filter(row => row.title === basis.source.title)) {
      const rule = await request<RuleDetail>(`/rules/${row.id}`)
      if (rule.source_file_version_id === server.material.file_version_id && rule.body.kind === 'engine' && canonical(publicationBody(rule.body)) === content && canonical(rule.case_context) === canonical(case_context)) return verified(rule)
    }
    return null
  }
  let rule = await find()
  if (!rule) {
    try {rule = verified(await request<RuleDetail>('/rules', 'POST', {request_id: requestId, title, body, source_file_version_id: server.material.file_version_id, predecessor_id: server.ruleId ?? null, case_context}), true)}
    catch (cause) {
      const found = await find().catch(() => null)
      if (!found) throw cause
      rule = found
    }
  }
  const published = {...basis, source: {...basis.source, id: rule.id}, server: {...server, ruleId: rule.id, body: rule.body, publishedContent: signature}}
  return {...published, server: {...published.server, verifiedContent: sourceContent(published)}}
}
export function sourceLocation(locator: Record<string, unknown>) {
  if (locator.kind === 'xlsx_cell') return `${locator.sheet} · ${locator.cell}`
  if (locator.kind === 'xlsx_range') return `${locator.sheet} · ${locator.start}:${locator.end}`
  if (locator.kind === 'pdf_block') return `第 ${locator.page} 页 · 区块 ${locator.block}`
  if (locator.kind === 'pdf_cell') return `第 ${locator.page} 页 · 表格行 ${locator.row} · 列 ${locator.column}`
  if (locator.kind === 'pdf_page') {
    const region = Array.isArray(locator.region) ? locator.region : null
    if (!region) return `第 ${locator.page} 页`
    const box = region.map(value => value == null ? '未定' : String(value)).join(', ')
    return `第 ${locator.page} 页 · 区域 ${box}`
  }
  if (locator.kind === 'docx_paragraph') return `段落 ${locator.paragraph}`
  if (locator.kind === 'docx_cell') return `表 ${locator.table} · 行 ${locator.row} · 列 ${locator.column}`
  return '位置待核对'
}
