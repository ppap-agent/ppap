import type {components} from '../src/generated/openapi'
import {shareContentFor, sameSnapshot} from '../src/audit/result-projection'
import {targetKey, type AuditSharingAdapter, type ShareTarget, type ReportShare} from '../src/audit/sharing-contract'

type Task = components['schemas']['AuditTask']
const organization = {organization_id: 'preview-org', organization_name: '示例质量团队'}
const catalog: ShareTarget[] = [
  {id: 'preview-member-li', kind: 'user', display_name: '示例李工', organization_id: organization.organization_id, eligibility: 'eligible', reason: null},
  {id: 'preview-member-wang', kind: 'user', display_name: '示例王工', organization_id: organization.organization_id, eligibility: 'eligible', reason: null},
  {id: 'preview-member-li-colleague', kind: 'user', display_name: '示例李同事', organization_id: organization.organization_id, eligibility: 'eligible', reason: null},
  {id: 'preview-quality-group', kind: 'group', display_name: '内部质量群（示例）', organization_id: organization.organization_id, eligibility: 'eligible', reason: '示例服务已核对成员均在当前组织。'},
  {id: 'preview-external-group', kind: 'group', display_name: '外部协作群（示例）', organization_id: organization.organization_id, eligibility: 'blocked', reason: '含组织外部成员，不能发送。'},
  {id: 'preview-unknown-group', kind: 'group', display_name: '成员待核对群（示例）', organization_id: organization.organization_id, eligibility: 'unknown', reason: '尚未核对群成员范围，不能发送。'},
]

/** Controlled preview effects only; no Feishu IM API, permanent public URL or delivery claim. */
export function fixtureSharing(tasks: Map<string, Task>, scene: string): AuditSharingAdapter {
  const jobs = new Map<string, ReportShare>()
  const reads = new Map<string, number>()
  const deniedAttempts = new Map<string, string>()
  const partial = scene.startsWith('reports-share-partial')
  let directoryReads = 0
  function save(job: ReportShare) {
    jobs.set(job.request.request_id, structuredClone(job))
    const task = tasks.get(job.request.reports[0].task_id)!
    tasks.set(task.id, {...task, report_share: structuredClone(job)})
  }
  return {
    listTargets: async (query, kind, pageToken) => {
      directoryReads += 1
      if (scene === 'reports-share-directory-denied' || scene === 'reports-share-directory-error' && directoryReads === 1) throw new Error('preview_directory_unavailable')
      const targets = catalog.filter(row => row.kind === kind && row.display_name.includes(query)).map(row => scene === 'reports-share-directory-change' && directoryReads > 1 && row.id === 'preview-member-li' ? {...row, eligibility: 'blocked' as const, reason: '示例：此人员已失去分享资格。'} : row)
      if (scene === 'reports-share-directory-foreign') targets.push({id: 'preview-foreign-member', kind: 'user', display_name: '其他组织人员（示例）', organization_id: 'preview-other-org', eligibility: 'eligible', reason: null})
      if (scene === 'reports-share-directory-paging') {
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
        reason: partial && index === 1 ? '示例服务已确认目标拒收，未产生投递，可重试此目标。' : partial && index === 2 ? '尚未取得目标发送结果，不能重新发送。' : '示例：第 1 次发送已确认，未再次发送。',
        retry_allowed: partial && index === 1,
      }))}
      if (scene === 'reports-share-partial-target-blocked') job.deliveries[1] = {...job.deliveries[1], target: {...job.deliveries[1].target, eligibility: 'blocked'}, reason: '示例：目标资格已变更，不能重试。'}
      if (scene === 'reports-share-revoked') {
        const reason = '示例：分享权限已撤销，服务确认未发送。'
        job.receipt = {...job.receipt, state: 'rejected', reason}
        job.deliveries = job.deliveries.map(row => ({...row, target: {...row.target, eligibility: 'blocked'}, state: 'failed', reason, retry_allowed: false}))
        save(job)
        return structuredClone(job)
      }
      save(job)
      if (scene === 'reports-share-unknown') throw new Error('preview_share_receipt_lost')
      return {...structuredClone(job), deliveries: job.deliveries.map(row => ({...row, state: 'pending', reason: '示例：等待平台发送回执。'}))}
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
          delivery.reason = '示例：服务确认本次尝试未受理、未投递，可以重新尝试。'
          save(job)
        }
      }
      if (scene === 'reports-share-unknown' && count === 1) return {...structuredClone(job), receipt: {...job.receipt, state: 'pending'}, deliveries: job.deliveries.map(row => ({...row, state: 'unknown', retry_allowed: false, reason: '示例：原发送回执仍未知，不重发。'}))}
      if (count > 1) {job.deliveries = job.deliveries.map(row => row.state === 'unknown' ? {...row, state: 'sent', reason: '示例：读回原发送已成功，未再次发送。'} : row); save(job)}
      return structuredClone(job)
    },
    retryFailed: async (requestId, target, attemptId) => {
      const job = jobs.get(requestId), delivery = job?.deliveries.find(row => targetKey(row.target) === targetKey(target))
      if (!job || delivery?.state !== 'failed' || !delivery.retry_allowed || delivery.target.eligibility !== 'eligible' || delivery.target.organization_id !== job.request.organization_id) throw new Error('preview_retry_not_allowed')
      if (scene === 'reports-share-partial-retry-not-accepted' && !deniedAttempts.size) {deniedAttempts.set(attemptId, targetKey(target)); throw new Error('preview_attempt_not_accepted')}
      const next: ReportShare = {...job, deliveries: job.deliveries.map(row => row === delivery ? {...row, attempt_id: attemptId, state: 'sent', retry_allowed: false, reason: '示例：只重试此失败目标，未触碰其他目标。'} : row)}
      save(next)
      if (scene === 'reports-share-partial-retry-unknown') throw new Error('preview_retry_receipt_lost')
      return {...structuredClone(next), deliveries: next.deliveries.map(row => targetKey(row.target) === targetKey(target) ? {...row, state: 'pending', reason: '示例：本目标正在等待发送回执。'} : row)}
    },
  }
}
