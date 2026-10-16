import type {components} from '../src/generated/openapi'
import {shareContentFor, sameSnapshot} from '../src/audit/result-projection'
import {targetKey, type AuditSharingAdapter, type ShareTarget, type ReportShare} from '../src/audit/sharing-contract'
import {shareUnresolved} from '../src/audit/share-actions'
import type {AuditReportReference} from '../src/audit/report-contract'

type Task = components['schemas']['AuditTask']
const organization = {organization_id: 'preview-org', organization_name: '示例质量团队'}
const catalog: ShareTarget[] = [
  {id: 'preview-member-li', kind: 'user', display_name: '示例李工', organization_id: organization.organization_id, eligibility: 'eligible', reason: null},
  {id: 'preview-member-wang', kind: 'user', display_name: '示例王工', organization_id: organization.organization_id, eligibility: 'eligible', reason: null},
  {id: 'preview-member-li-colleague', kind: 'user', display_name: '示例李同事', organization_id: organization.organization_id, eligibility: 'eligible', reason: null},
  {id: 'preview-quality-group', kind: 'group', display_name: '内部质量群（示例）', organization_id: organization.organization_id, eligibility: 'eligible', reason: null},
  {id: 'preview-external-group', kind: 'group', display_name: '外部协作群（示例）', organization_id: organization.organization_id, eligibility: 'blocked', reason: '含组织外部成员，不能发送。'},
  {id: 'preview-unknown-group', kind: 'group', display_name: '成员待核对群（示例）', organization_id: organization.organization_id, eligibility: 'unknown', reason: '尚未核对群成员范围，不能发送。'},
]

/** Controlled preview effects only; no Feishu IM API, permanent public URL or delivery claim. */
export function fixtureSharing(tasks: Map<string, Task>, scene: string): AuditSharingAdapter & {forReport: (reference: AuditReportReference) => ReportShare | null} {
  const jobs = new Map<string, ReportShare>()
  const reads = new Map<string, number>()
  const deniedAttempts = new Map<string, string>()
  const partial = scene.startsWith('reports-share-partial')
  let directoryReads = 0
  function observed(job: ReportShare): ReportShare {
    if (['reports-share-unknown', 'archive-share-unknown'].includes(scene) && (reads.get(job.request.request_id) || 0) < 2) return {...structuredClone(job), receipt: {...job.receipt, state: 'pending'}, deliveries: job.deliveries.map(row => ({...row, state: 'unknown', retry_allowed: false, reason: '发送结果待确认。'}))}
    return structuredClone(job)
  }
  function save(job: ReportShare) {
    jobs.set(job.request.request_id, structuredClone(job))
    const task = tasks.get(job.request.reports[0].task_id)!
    const related = [...jobs.values()].filter(row => row.request.reports.some(reference => reference.task_id === task.id)).map(observed)
    tasks.set(task.id, {...task, report_share: related.find(shareUnresolved) || related.at(-1)!})
  }
  return {
    forReport: reference => {
      const related = [...jobs.values()].filter(job => job.request.reports.length === 1 && sameSnapshot(job.request.reports[0], reference)).map(observed)
      return related.find(shareUnresolved) || related.at(-1) || null
    },
    listTargets: async (query, kind, pageToken) => {
      directoryReads += 1
      if (scene === 'reports-share-directory-denied' || scene === 'reports-share-directory-error' && directoryReads === 1) throw new Error('preview_directory_unavailable')
      const targets = catalog.filter(row => row.kind === kind && row.display_name.includes(query)).map(row => scene === 'reports-share-directory-change' && directoryReads > 1 && row.id === 'preview-member-li' ? {...row, eligibility: 'blocked' as const, reason: '此人员已失去分享资格。'} : row)
      if (scene === 'reports-share-directory-foreign') targets.push({id: 'preview-foreign-member', kind: 'user', display_name: '其他组织人员（示例）', organization_id: 'preview-other-org', eligibility: 'eligible', reason: null})
      if (scene.endsWith('share-directory-paging')) {
        if (pageToken && pageToken !== `next:${kind}:${query}`) throw new Error('preview_page_query_mismatch')
        return {...organization, targets: pageToken ? targets.slice(1) : targets.slice(0, 1), next_page_token: !pageToken && targets.length > 1 ? `next:${kind}:${query}` : null}
      }
      return {...organization, targets: scene === 'reports-share-directory-empty' ? [] : targets, next_page_token: null}
    },
    send: async request => {
      const task = tasks.get(request.reports[0]?.task_id)
      const targets = request.targets.map(ref => catalog.find(target => targetKey(target) === targetKey(ref)))
      if (!task || request.organization_id !== organization.organization_id || !targets.length || targets.some(target => !target || target.eligibility !== 'eligible') || !sameSnapshot(shareContentFor(task, request.reports, request.content.kind === 'link' ? 'link' : 'file'), request.content)) throw new Error('invalid_preview_share')
      const job: ReportShare = {request: structuredClone(request), receipt: {request_id: request.request_id, state: 'accepted', reason: null}, deliveries: targets.map((target, index) => ({
        target: structuredClone(target!), attempt_id: request.request_id,
        state: partial && index === 1 ? 'failed' : partial && index === 2 ? 'unknown' : 'sent',
        reason: partial && index === 1 ? '投递失败，可重试。' : partial && index === 2 ? '发送结果待确认。' : null,
        retry_allowed: partial && index === 1,
      }))}
      if (scene === 'reports-share-partial-target-blocked') job.deliveries[1] = {...job.deliveries[1], target: {...job.deliveries[1].target, eligibility: 'blocked'}, reason: '接收资格已变更，不能重试。'}
      if (scene === 'reports-share-revoked') {
        const reason = '分享权限已撤销，未发送。'
        job.receipt = {...job.receipt, state: 'rejected', reason}
        job.deliveries = job.deliveries.map(row => ({...row, target: {...row.target, eligibility: 'blocked'}, state: 'failed', reason, retry_allowed: false}))
        save(job)
        return structuredClone(job)
      }
      save(job)
      if (scene === 'reports-share-unknown' || scene === 'archive-share-unknown') throw new Error('preview_share_receipt_lost')
      return {...structuredClone(job), deliveries: job.deliveries.map(row => ({...row, state: 'pending', reason: '等待发送结果。'}))}
    },
    read: async ({request_id: requestId, attempts}) => {
      const job = jobs.get(requestId)
      if (!job) throw new Error('unknown_preview_share')
      const count = (reads.get(requestId) || 0) + 1; reads.set(requestId, count)
      for (const expected of attempts) {
        const delivery = job.deliveries.find(row => targetKey(row.target) === targetKey(expected.target))
        if (delivery && deniedAttempts.get(expected.attempt_id) === targetKey(delivery.target)) {
          delivery.attempt_id = expected.attempt_id
          delivery.state = 'failed'; delivery.retry_allowed = true
          delivery.reason = '本次未发送，可重试。'
          save(job)
        }
      }
      if (['reports-share-unknown', 'archive-share-unknown'].includes(scene) && count === 1) return observed(job)
      if (count > 1) {job.deliveries = job.deliveries.map(row => row.state === 'unknown' ? {...row, state: 'sent', reason: null} : row); save(job)}
      return structuredClone(job)
    },
    retryFailed: async (requestId, target, attemptId) => {
      const job = jobs.get(requestId), delivery = job?.deliveries.find(row => targetKey(row.target) === targetKey(target))
      if (!job || delivery?.state !== 'failed' || !delivery.retry_allowed || delivery.target.eligibility !== 'eligible' || delivery.target.organization_id !== job.request.organization_id) throw new Error('preview_retry_not_allowed')
      if (scene === 'reports-share-partial-retry-not-accepted' && !deniedAttempts.size) {deniedAttempts.set(attemptId, targetKey(target)); throw new Error('preview_attempt_not_accepted')}
      const next: ReportShare = {...job, deliveries: job.deliveries.map(row => row === delivery ? {...row, attempt_id: attemptId, state: 'sent', retry_allowed: false, reason: null} : row)}
      save(next)
      if (scene === 'reports-share-partial-retry-unknown') throw new Error('preview_retry_receipt_lost')
      return {...structuredClone(next), deliveries: next.deliveries.map(row => targetKey(row.target) === targetKey(target) ? {...row, state: 'pending', reason: '等待发送结果。'} : row)}
    },
  }
}
