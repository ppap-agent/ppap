import type {AuditArchiveAdapter} from '../src/audit/archive-contract'
import type {AuditUsageAdapter, UsageEntry, UsagePage} from '../src/audit/usage-contract'

const known = (value: number) => ({value, reason: null})
const unknown = {value: null, reason: '暂未取得完整数据。'}
/** Explicit read-only fixtures, not an account, allocation engine or shadow ledger. */
export function fixtureUsage(archive: AuditArchiveAdapter, scene: string): AuditUsageAdapter {
  let reads = 0
  let entries: UsageEntry[] | null = null
  return {readUsage: async query => {
    reads += 1
    if (scene === 'usage-error' && reads === 1 || scene === 'usage-refresh-error' && reads > 1) throw new Error('preview_usage_unavailable')
    if (scene === 'usage-denied' || scene === 'usage-revoked' && reads > 1) throw Object.assign(new Error('preview_usage_denied'), {status: 403})
    if (!entries) {
      const catalog = await archive.listDocuments({search: '', category_id: null, document_type: null, customer: null, initiator: null, from_date: null, through_date: null, page: 1, page_size: 10})
      const second = await archive.listDocuments({...catalog.query, page: 2})
      entries = await Promise.all([...catalog.documents, ...second.documents].map(async (document, index): Promise<UsageEntry> => {
        const history = await archive.listHistory(document.document_id), entry = index === 0 ? history.entries.find(row => row.sequence === 2)! : history.entries[0]
        const free = index === 0 ? 200 : index === 1 ? 500 : 0
        const settlement: UsageEntry['settlement'] = index === 1 ? {state: 'trial', reason: null} : index >= 2 && index <= 4 ? {state: index === 4 ? 'processing' : 'pending', reason: '等待结算结果。'} : {state: 'charged', reason: null}
        const unavailable = index === 4 ? {state: 'deleted' as const, reason: '这次审核记录已删除。'} : index === 3 ? {state: 'denied' as const, reason: '当前账号无权读取这次审核。'} : {state: 'allowed' as const, reason: null}
        return {usage_id: `示例消费-${index + 1}`, occurred_at: entry.audited_at, filename: document.title, audit_sequence: entry.sequence, initiator: index % 2 ? '示例王工' : '示例李工', points: 500, allocation: {free_points: free, paid_points: 500 - free, reason: null}, orders: settlement.state === 'charged' ? [{order_id: `示例飞书订单-${index + 1}`, confirmed_at: entry.audited_at}] : [], order_reason: index === 1 ? '全部使用免费额度，无飞书订单。' : '尚未取得飞书订单号。', settlement, reference: structuredClone(entry.reference), document_id: document.document_id, audit_access: unavailable}
      }))
    }
    const values = structuredClone(entries)
    const uncertain = scene === 'usage-unknown' || scene === 'usage-quota-unknown' || scene === 'usage-race' && reads === 1
    if (uncertain) values[2].settlement = {state: 'unknown', reason: '扣费结果待确认。'}
    if (scene === 'usage-unmapped') {values[0].document_id = null; values[0].audit_access = {state: 'unknown', reason: '尚未确认文档关联。'}}
    const from = query.range === '7d' ? '2026-10-10' : query.range === '30d' ? '2026-09-17' : query.range === '90d' ? '2026-07-19' : null
    const filtered = (scene === 'usage-empty' ? [] : values).filter(row => (!from || row.occurred_at.slice(0, 10) >= from) && `${row.filename} ${row.initiator}`.includes(query.search))
    const result: UsagePage = {organization_id: scene === 'usage-foreign' && reads > 1 ? 'other-org' : 'preview-org', organization_name: '示例质量团队', query: structuredClone(query), from_date: from, through_date: '2026-10-16', summary: {month: '2026-10', timezone: 'Asia/Shanghai', as_of: '2026-10-16T09:00:00+08:00', consumption_count: known(12), charged_points: uncertain ? unknown : known(4500), pending_points: uncertain ? unknown : known(1500), free_remaining: scene === 'usage-quota-unknown' ? unknown : known(300), free_total: scene === 'usage-quota-unknown' ? unknown : known(1000)}, entries: filtered.slice((query.page - 1) * 10, query.page * 10), total: filtered.length, notices: scene === 'usage-ai-disabled' ? ['ai_disabled'] : scene === 'usage-no-package' ? ['no_package'] : scene === 'usage-paid-insufficient' ? ['paid_insufficient'] : []}
    if (scene === 'usage-empty') result.summary = {...result.summary, consumption_count: known(0), charged_points: known(0), pending_points: known(0), free_remaining: known(1000)}
    if (scene === 'usage-invalid') result.entries = result.entries.map((row, index) => index === 1 ? structuredClone(result.entries[0]) : row)
    if (scene === 'usage-race' && reads > 1) {
      result.summary.charged_points = known(5000); result.summary.pending_points = known(1000)
      result.entries = result.entries.map(row => row.usage_id === '示例消费-3' ? {...row, settlement: {state: 'charged', reason: null}, orders: [{order_id: '示例飞书订单-3', confirmed_at: '2026-10-16T10:00:00+08:00'}], order_reason: null} : row)
    }
    return result
  }}
}
