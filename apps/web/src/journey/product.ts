import {request, type FileVersion, type Material, type Round, type RuleCandidate, type RuleDetail, type RulesList, type RuleBody} from '../api'
import {clauseSourceReady, isRequirementBasis, localBasis, type RequirementBasis} from './requirements-state'
import type {RequirementCollection, RequirementVersion} from './requirement-library-state'
import {sourceContent} from './requirement-library-state'
import type {DecisionEvidence, DecisionInput} from './decision'

export function parsedBasis(basis: RequirementBasis, candidate: RuleCandidate): RequirementBasis {
  return {...basis, confirmed: null, source: {...basis.source, title: candidate.title, version: basis.source.version || 'v1', state: 'ready'},
    server: {...basis.server!, body: candidate.body},
    clauses: candidate.body.rules.map(row => ({id: row.id, title: row.rule_to_check.split('\n')[0].slice(0, 60), text: row.rule_to_check,
      original: row.rule_to_check, source: basis.source.filename, location: `条款 ${row.id}（原件位置待核对）`, checked: false, applicability: 'pending', reason: ''}))}
}
export function basisRuleBody(basis: RequirementBasis): RuleBody {
  if (!basis.server) throw new Error('审核要求尚未连接到规则版本。')
  return {...basis.server.body,
    customers: basis.server.body.customers.map(value => value.trim()),
    models: basis.server.body.models.map(value => value.trim()),
    parts: basis.server.body.parts.map(value => value.trim()),
    rules: basis.clauses.filter(row => row.applicability === 'apply').map(row => {
    const original = basis.server!.body.rules.find(item => item.id === row.id)
    return {id: row.id.trim(), category: row.title.trim(), priority: original?.priority ?? null, rule_to_check: row.text.trim(), type_of_rule: original?.type_of_rule.trim() || 'text'}
  })}
}
export const canonical = (value: unknown): string => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item) ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item)
export async function loadRuleCollections(): Promise<RequirementCollection[]> {
  const listed = await request<RulesList>('/rules')
  const versions = await readRuleVersions(listed.rules.filter(row => row.kind === 'engine').map(row => row.id), new AbortController().signal)
  const collections: RequirementCollection[] = versions.map(version => ({id: version.id, draft: null, versions: [version]}))
  const byId = new Map(collections.map(collection => [collection.id, collection.versions[0]]))
  const grouped = new Map<string, RequirementCollection>()
  for (const collection of collections) {
    const version = collection.versions[0], seen = new Set<string>()
    let root = version
    while (root.baseId && byId.has(root.baseId) && !seen.has(root.baseId)) {seen.add(root.id); root = byId.get(root.baseId)!}
    const family = grouped.get(root.id) ?? {id: root.id, draft: null, versions: []}
    family.versions.push(version); grouped.set(root.id, family)
  }
  return [...grouped.values()].map(collection => ({...collection, versions: collection.versions.sort((a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id))}))
}

export async function readRuleVersions(ids: string[], signal: AbortSignal): Promise<RequirementVersion[]> {
  const details = await Promise.all([...new Set(ids)].map(async id => {
    const rule = await request<RuleDetail>(`/rules/${encodeURIComponent(id)}`, 'GET', undefined, signal)
    if (rule.id !== id) throw new Error('要求版本回执不一致，未显示其他版本。')
    return rule
  }))
  const files = new Map<string, Promise<FileVersion | null>>()
  return (await Promise.all(details.map(async rule => {
    if (rule.body.kind !== 'engine' || !rule.source_file_id || !rule.source_file_version_id) return []
    const path = `/files/${encodeURIComponent(rule.source_file_id)}/versions/${encodeURIComponent(rule.source_file_version_id)}`
    if (!files.has(path)) files.set(path, request<FileVersion>(path, 'GET', undefined, signal).catch(cause => {if (signal.aborted) throw cause; return null}))
    const file = await files.get(path)!
    if (file && (file.id !== rule.source_file_version_id || file.file_id !== rule.source_file_id)) throw new Error('要求原件回执不一致，未使用其他文件。')
    const material: Material = {file_id: rule.source_file_id, file_version_id: rule.source_file_version_id, original_filename: file?.original_filename || rule.title, role: 'reference', rule_version_id: null}
    const base = localBasis(rule.title)
    let basis = parsedBasis({...base, server: {body: rule.body, material, ruleId: rule.id}}, {title: rule.title, body: rule.body, source_file_version_id: rule.source_file_version_id})
    let scope: RequirementCollection['versions'][number]['scope'] = {customer: rule.body.customers[0] || '', part: rule.body.parts[0] || '', revision: '', allCustomers: rule.customer_scope === 'all', allParts: !rule.body.parts.length, allRevisions: true}
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
    basis.source.id = rule.id; basis.source.filename = material.original_filename
    if (!rule.case_context?.registration) basis.source.version = rule.created_at.slice(0, 16).replace('T', ' ')
    basis.server!.publishedContent = canonical({body: rule.body, title: basis.source.title, version: basis.source.version})
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
    if (!rule?.id || exactId && rule.id !== requestId || rule.title !== title || canonical(rule.body) !== content || rule.source_file_id !== server.material.file_id || rule.source_file_version_id !== server.material.file_version_id || (rule.predecessor_id ?? null) !== (server.ruleId ?? null) || canonical(rule.case_context) !== canonical(case_context)) throw new Error('保存回执与本次要求不一致。未采用该版本，请核对原请求。')
    return rule
  }
  async function find() {
    const direct = await request<RuleDetail>(`/rules/${requestId}`).catch(cause => {if ((cause as {status?: number}).status === 403 || (cause as {status?: number}).status === 404) return null; throw cause})
    if (direct) return verified(direct, true)
    const list = await request<RulesList>('/rules')
    for (const row of list.rules.filter(row => row.title === basis.source.title)) {
      const rule = await request<RuleDetail>(`/rules/${row.id}`)
      if (rule.source_file_version_id === server.material.file_version_id && canonical(rule.body) === content && canonical(rule.case_context) === canonical(case_context)) return verified(rule)
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
  const published = {...basis, source: {...basis.source, id: rule.id}, server: {...server, ruleId: rule.id, body, publishedContent: signature}}
  return {...published, server: {...published.server, verifiedContent: sourceContent(published)}}
}
export function sourceLocation(locator: Record<string, unknown>) {
  if (locator.kind === 'xlsx_cell') return `${locator.sheet} · ${locator.cell}`
  if (locator.kind === 'xlsx_range') return `${locator.sheet} · ${locator.start}:${locator.end}`
  if (locator.kind === 'pdf_block') return `第 ${locator.page} 页 · 区块 ${locator.block}`
  if (locator.kind === 'pdf_cell') return `第 ${locator.page} 页 · 表格行 ${locator.row} · 列 ${locator.column}`
  if (locator.kind === 'docx_paragraph') return `段落 ${locator.paragraph}`
  if (locator.kind === 'docx_cell') return `表 ${locator.table} · 行 ${locator.row} · 列 ${locator.column}`
  return '位置待核对'
}
export function roundEvidence(round: Round, basis: RequirementBasis | null): DecisionEvidence[] {
  const results = {PASS: '满足', NG: '不满足', ERR: '执行失败', UNABLE: '无法判断', NA: '不适用'} as const
  const rows: DecisionEvidence[] = round.findings.map(finding => {
    const semantic = finding.locator?.semantic
    const clause = basis?.clauses.find(row => row.id === (semantic?.clause_id || finding.locator?.rule_item_id))
    return {id: finding.item_key, displayId: semantic?.clause_id || finding.locator?.rule_item_id, title: clause?.title || finding.classification || finding.item,
      requirement: semantic?.rule_text || clause?.text || finding.classification,
      result: results[finding.status], explanation: [finding.item, finding.suggestions].filter(Boolean).join('\n'),
      sources: semantic?.sources.map(source => ({file: source.filename, fileVersionId: source.file_version_id, location: sourceLocation(source.locator as Record<string, unknown>), excerpt: source.excerpt})) ?? (finding.locator ? [{file: finding.locator.filename || '来源待核对', fileVersionId: finding.locator.file_version_id, location: finding.locator.locator || '位置待核对', excerpt: ''}] : [])}
  })
  for (const coverage of round.coverage.rule_items ?? []) {
    if (coverage.item_key && rows.some(row => row.id === coverage.item_key)) continue
    if (coverage.coverage === 'executed') continue
    rows.push({id: `coverage:${coverage.file_version_id}:${coverage.id}`, displayId: coverage.id, title: coverage.category || coverage.rule_to_check.slice(0, 48), requirement: coverage.rule_to_check, result: '未覆盖', explanation: coverage.reason === 'manual-only' ? '标准检查尚未确认此条款的执行映射，可由人工核对或选择深度审核。' : coverage.reason || '本条要求未完成自动检查。', sources: []})
  }
  if (!rows.length) rows.push({id: 'no-result', title: '本轮检查结果', requirement: basis?.source.title || '本轮要求', result: round.execution === 'failed' ? '执行失败' : '未覆盖', explanation: round.failure?.message || '本轮没有可核对的逐项结果。', sources: []})
  return rows
}
export function roundInput(round: Round, basis: RequirementBasis | null, scope: string, clarification: string | null): DecisionInput {
  return {receiptId: round.id, roundId: round.id, scope, basis, clarification,
    files: round.inputs.map(material => ({name: material.original_filename, revision: '已固定版本', sourceVersion: 1, material, roundId: round.id})), evidence: roundEvidence(round, basis)}
}
