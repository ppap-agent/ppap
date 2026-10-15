import type {components} from '../generated/openapi'
import {sameSnapshot} from './result-projection.ts'

export type UsageQuery = components['schemas']['AuditUsageQuery']
export type UsagePage = components['schemas']['AuditUsagePage']
export type UsageEntry = components['schemas']['AuditUsageEntry']
export type UsageMetric = components['schemas']['AuditUsageMetric']
/** Read projection only. No reporting, charging, quota reset or local ledger. */
export type AuditUsageAdapter = {readUsage: (query: UsageQuery) => Promise<UsagePage>}
export const usageNotices = {
  ai_disabled: '飞书AI包开关已关闭，请联系管理员在飞书管理后台启用或联系我们获取免费额度',
  no_package: '提示：试用额度不足，且未采购飞书AI包，请联系管理员在飞书管理后台进行充值或联系我们获取免费额度',
  paid_insufficient: '提示：飞书AI包额度不足，本次消耗将于充值后自动扣费，请联系管理员或联系我们获取免费额度',
}
const count = (value: number) => Number.isSafeInteger(value) && value >= 0
const metricValid = (metric: UsageMetric) => metric.value === null ? Boolean(metric.reason) : count(metric.value)
const validTime = (value: string) => Number.isFinite(Date.parse(value))
function localDate(value: string, timezone: string) {
  if (!validTime(value)) return ''
  const parts = new Intl.DateTimeFormat('en', {timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(new Date(value))
  return ['year', 'month', 'day'].map(type => parts.find(part => part.type === type)?.value).join('-')
}
export function validUsagePage(page: UsagePage, query: UsageQuery, organization?: string) {
  if (!page.organization_id || !page.organization_name || organization && page.organization_id !== organization || !sameSnapshot(page.query, query) || !count(page.total) || page.entries.length !== Math.min(10, Math.max(0, page.total - (query.page - 1) * 10))) return false
  const summary = page.summary
  try {
    const parts = new Intl.DateTimeFormat('en', {timeZone: summary.timezone, year: 'numeric', month: '2-digit'}).formatToParts(new Date(summary.as_of))
    if (summary.month !== `${parts.find(row => row.type === 'year')?.value}-${parts.find(row => row.type === 'month')?.value}`) return false
  } catch {return false}
  if (![summary.consumption_count, summary.charged_points, summary.pending_points, summary.free_remaining, summary.free_total].every(metricValid) || summary.free_remaining.value !== null && summary.free_total.value !== null && summary.free_remaining.value > summary.free_total.value) return false
  if (!/^\d{4}-\d{2}-\d{2}$/.test(page.through_date) || page.from_date !== null && (!/^\d{4}-\d{2}-\d{2}$/.test(page.from_date) || page.from_date > page.through_date) || page.notices.some(notice => !(notice in usageNotices))) return false
  if (new Set(page.entries.map(row => row.usage_id)).size !== page.entries.length || new Set(page.entries.map(row => JSON.stringify([row.reference.round_id, row.reference.file_version_id]))).size !== page.entries.length) return false
  return page.entries.every(row => {
    const reference = row.reference, allocation = row.allocation
    const date = localDate(row.occurred_at, summary.timezone)
    if (!date || date > page.through_date || page.from_date && date < page.from_date || !['charged', 'pending', 'processing', 'unknown', 'trial'].includes(row.settlement.state) || !['allowed', 'denied', 'deleted', 'unknown'].includes(row.audit_access.state)) return false
    if (row.settlement.state === 'trial' && (allocation.free_points !== 500 || allocation.paid_points !== 0)) return false
    return row.usage_id && row.filename && row.initiator && validTime(row.occurred_at) && count(row.audit_sequence) && row.audit_sequence > 0 && row.points === 500 && reference.task_id && reference.round_id && reference.file_version_id && (reference.result_attempt === null || count(reference.result_attempt) && reference.result_attempt > 0) && (row.audit_access.state !== 'allowed' || Boolean(row.document_id)) && (allocation.free_points === null || count(allocation.free_points)) && (allocation.paid_points === null || count(allocation.paid_points)) && (allocation.free_points === null || allocation.paid_points === null ? Boolean(allocation.reason) : allocation.free_points + allocation.paid_points === row.points) && new Set(row.orders.map(order => order.order_id)).size === row.orders.length && row.orders.every(order => order.order_id && validTime(order.confirmed_at)) && (row.orders.length || row.order_reason) && (row.settlement.state !== 'unknown' || row.settlement.reason)
  })
}
export function readUsageQuery(search: string): UsageQuery {
  const params = new URLSearchParams(search), range = params.get('usageRange'), page = Number(params.get('usagePage'))
  return {range: range === '7d' || range === '90d' || range === 'all' ? range : '30d', search: params.get('usageSearch') || '', page: Number.isSafeInteger(page) && page > 0 ? page : 1, page_size: 10}
}
export function writeUsageQuery(query: UsageQuery) {
  const url = new URL(location.href)
  url.searchParams.set('usageRange', query.range); url.searchParams.set('usageSearch', query.search); url.searchParams.set('usagePage', String(query.page))
  history.replaceState(null, '', url)
  if (window.parent !== window) window.parent.postMessage({type: 'ppap-audit-view', view: 'usage', usageRange: query.range, usageSearch: query.search, usagePage: String(query.page), archiveDoc: url.searchParams.get('archiveDoc'), auditRef: url.searchParams.get('auditRef'), archiveReport: url.searchParams.get('archiveReport')}, location.origin)
}
