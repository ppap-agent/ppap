import type {Draft, Finding, Round, RoundSummary, Rule, UsageEvent} from '../src/api'

type Scene = 'populated' | 'empty' | 'error' | 'long'
type Reply = {status: number; body: unknown}

const targetId = '11111111-1111-4111-8111-111111111111'
const referenceId = '22222222-2222-4222-8222-222222222222'
const roundId = '33333333-3333-4333-8333-333333333333'
const ruleId = '44444444-4444-4444-8444-444444444444'
const secondRuleId = '55555555-5555-4555-8555-555555555555'
const draftId = '66666666-6666-4666-8666-666666666666'
const timestamp = '2026-10-13T08:30:00+08:00'

const rules: Rule[] = [
  {id: ruleId, title: '尺寸检验报告 · 客户 A', type: '全功能检验报告', kind: 'engine', customers: ['客户 A'], models: null, parts: null, rule_profile: null, predecessor_id: null, created_at: timestamp},
  {id: secondRuleId, title: '控制计划 · 客户 B', type: '控制计划', kind: 'engine', customers: ['客户 B'], models: null, parts: null, rule_profile: null, predecessor_id: null, created_at: '2026-10-12T14:00:00+08:00'},
]
const ruleBody = {kind: 'engine' as const, type: '全功能检验报告', customers: ['客户 A'], models: [], parts: [], rule_profile: '', rules: [
  {id: '1', category: '完整性', priority: '高', type_of_rule: '内容检查', rule_to_check: '尺寸数据应覆盖图纸要求，并标明实际测量值。'},
  {id: '2', category: '一致性', priority: '中', type_of_rule: '内容检查', rule_to_check: '报告中的零件号应与图纸一致。'},
]}
const secondRuleBody = {kind: 'engine' as const, type: '控制计划', customers: ['客户 B'], models: [], parts: [], rule_profile: '', rules: [
  {id: '1', category: '流程一致性', priority: '高', type_of_rule: '内容检查', rule_to_check: '控制计划的工序号与过程流程图一致。'},
]}
const target = {file_id: targetId, file_version_id: targetId, original_filename: '尺寸检验报告-example.xlsm', role: 'target' as const, rule_version_id: ruleId, rule: {id: ruleId, title: rules[0].title, body: ruleBody}, status: 'ready'}
const reference = {file_id: referenceId, file_version_id: referenceId, original_filename: '零件图纸.pdf', role: 'reference' as const, rule_version_id: null, rule: null, status: 'ready'}
const findings: Finding[] = [
  {item_key: '1', item: ruleBody.rules[0].rule_to_check, status: 'NG', classification: '完整性', priority: '高', suggestions: '孔径测量值未覆盖图纸中的全部位置。', review: null, locator: {file_id: targetId, file_version_id: targetId, filename: target.original_filename, locator: '尺寸记录!B12', rule_item_id: '1', semantic: {clause_id: '1', rule_version_id: ruleId, rule_text: ruleBody.rules[0].rule_to_check, scope: {total: 3, checked: 3, unknown: 0}, sources: [{file_version_id: targetId, filename: target.original_filename, role: 'target', locator: {kind: 'xlsx_cell', sheet: '尺寸记录', cell: 'B12'}, excerpt: '孔径 10.0 ± 0.1；实测值缺少第 3 处。'}, {file_version_id: referenceId, filename: reference.original_filename, role: 'reference', locator: {kind: 'pdf_block', page: 2, block: 4}, excerpt: '孔径要求：3 处，10.0 ± 0.1。'}]}}},
  {item_key: '2', item: ruleBody.rules[1].rule_to_check, status: 'PASS', classification: '一致性', priority: '中', suggestions: '报告和图纸中的零件号一致。', review: null, locator: {file_id: targetId, file_version_id: targetId, filename: target.original_filename, locator: '尺寸记录!B2', rule_item_id: '2'}},
]
const round: Round = {id: roundId, analysis_mode: 'deep', execution: 'completed', created_at: timestamp, predecessor_id: null, source_draft_id: draftId, inputs: [target, reference], findings, failure: null, progress: null, result_attempt: 1, writeback: {available: false}, coverage: {checked: ['1', '2'], unchecked: [], rule_items: ruleBody.rules.map((item, index) => ({id: item.id, rule_to_check: item.rule_to_check, coverage: 'executed' as const, status: index ? 'PASS' as const : 'NG' as const, item_key: item.id, file_version_id: targetId}))}}
const draft: Draft = {id: draftId, analysis_mode: 'deep', items: [target, reference], predecessor_id: null, status: 'draft', updated_at: timestamp, writeback: {available: false}}

function summary(index: number): RoundSummary {
  return {id: index ? `${String(index).padStart(8, '0')}-3333-4333-8333-333333333333` : roundId, created_at: new Date(Date.parse(timestamp) - index * 3600000).toISOString(), analysis_mode: index % 2 ? 'standard' : 'deep', execution: 'completed', predecessor_id: null, inputs: [{file_version_id: targetId, original_filename: target.original_filename, role: 'target', rule: {id: rules[0].id, title: rules[0].title}}], result_counts: {NG: 1, PASS: 1}, reviewed_count: index % 4 === 0 ? 1 : 0}
}

function usage(index: number): UsageEvent {
  return {id: String(index + 1), created_at: new Date(Date.parse(timestamp) - index * 3600000).toISOString(), consumed_points: 500, is_free_trial: true, round_id: roundId}
}

function sceneFromCookie(cookie: string): Scene {
  const value = cookie.split(';').map(part => part.trim()).find(part => part.startsWith('ppap_preview_scene='))?.split('=')[1]
  return value === 'empty' || value === 'error' || value === 'long' ? value : 'populated'
}

function ok(body: unknown): Reply { return {status: 200, body} }
function failure(status: number, error: string): Reply { return {status, body: {error}} }

export function previewResponse(method: string, url: string, cookie: string): Reply | null {
  const parsed = new URL(url, 'http://127.0.0.1')
  const pathname = parsed.pathname
  const apiPath = /^\/(me|public-config|logout|drafts|rounds|rules|rule-types|usage|integrations|files|file-analyses|reports)(\/|$)/.test(pathname)
  if (!apiPath) return null
  if (method !== 'GET') return failure(403, 'preview_read_only')
  const scene = sceneFromCookie(cookie)
  if (pathname === '/public-config') return ok({api_url: '', profile: 'preview', current_access: 'preview', deep_offered: true, feishu_app_id: '', git_sha: 'local', login: 'preview', usage_charge_enabled: false})
  if (pathname === '/me') return ok({display_name: '预览用户', user_id: 'preview-user', tenant_id: 'preview-tenant', membership: 'member', identity: {provider: 'preview', subject: 'preview-user', app_id: '', tenant_key: ''}})
  if (scene === 'error') return failure(503, '模拟服务不可用')
  const empty = scene === 'empty'
  const count = scene === 'long' ? 18 : 4
  if (pathname === '/rules') return ok({rules: empty ? [] : rules})
  if (pathname === `/rules/${ruleId}` || pathname === `/rules/${secondRuleId}`) return ok({id: pathname.endsWith(secondRuleId) ? secondRuleId : ruleId, title: pathname.endsWith(secondRuleId) ? rules[1].title : rules[0].title, source_file_id: null, source_file_version_id: null, created_at: timestamp, body: pathname.endsWith(secondRuleId) ? secondRuleBody : ruleBody})
  if (pathname === '/rule-types') return ok({types: ['全功能检验报告', '控制计划', '过程流程图']})
  if (pathname === '/rounds') {
    const query = parsed.searchParams.get('q')?.trim().toLowerCase() || ''
    const execution = parsed.searchParams.get('execution') || ''
    const unreviewed = parsed.searchParams.get('unreviewed') === 'true'
    const rows = empty ? [] : Array.from({length: count}, (_, index) => summary(index))
    return ok({rounds: rows.filter(row => (!query || row.inputs.some(item => (item.original_filename + ' ' + item.rule?.title).toLowerCase().includes(query))) && (!execution || row.execution === execution) && (!unreviewed || row.reviewed_count < 2)), next_offset: null})
  }
  if (/^\/rounds\/[a-f0-9-]+$/.test(pathname)) {
    const index = pathname === `/rounds/${roundId}` ? 0 : Number(pathname.split('/')[2].slice(0, 8))
    if (!Number.isInteger(index) || index < 0 || index >= count) return failure(404, 'preview_not_available')
    const row = summary(index)
    return ok({...round, id: row.id, analysis_mode: row.analysis_mode, created_at: row.created_at, findings: empty ? [] : round.findings, coverage: empty ? {checked: [], unchecked: [], rule_items: []} : round.coverage})
  }
  if (pathname === '/drafts') return ok({drafts: empty ? [] : [draft], next_offset: null})
  if (pathname === `/drafts/${draftId}`) return ok(draft)
  if (pathname === '/usage') {
    const rows = empty ? [] : Array.from({length: count}, (_, index) => usage(index))
    const since = parsed.searchParams.get('since') || ''
    const until = parsed.searchParams.get('until') || ''
    return ok({events: rows.filter(row => (!since || row.created_at.slice(0, 10) >= since) && (!until || row.created_at.slice(0, 10) <= until)), next_offset: null})
  }
  if (pathname === '/integrations/feishu/usage/quota') return ok({enabled: true, remaining_trial_ai_points: 3000, estimated_points: 500, quota_sufficient: true, accept_allowed: false})
  if (/\/office$/.test(pathname)) return ok({available: false, notice: '预览台不连接 OnlyOffice。'})
  if (/\/preview$/.test(pathname)) return ok({available: true, notice: '', sheets: ['尺寸记录'], sheet: '尺寸记录', rows: [{number: 2, text: '零件号  CPO-45'}, {number: 12, text: '孔径 10.0 ± 0.1；实测值缺少第 3 处。'}], start_row: 1, end_row: 12, next_row: null})
  if (/\/analysis$/.test(pathname)) return ok({analysis: null})
  return failure(404, 'preview_not_available')
}
