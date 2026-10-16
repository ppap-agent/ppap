import type {AuditTemplateAdapter} from '../src/audit/template-contract'
import {templateShareContent, templateShareReference, type AuditTemplateSharingAdapter, type TemplateShare, type TemplateShareScope} from '../src/audit/template-sharing-contract'
import {sameSnapshot} from '../src/audit/result-projection'
import {targetKey, type AuditSharingAdapter, type ShareTarget} from '../src/audit/sharing-contract'
import {shareRetryAllowed} from '../src/audit/share-actions'

/** UI fixture only. Neither sends messages nor grants any Drive permissions. */
export function fixtureTemplateSharing(templates: AuditTemplateAdapter, directory: Pick<AuditSharingAdapter, 'listTargets'>, scene: string): AuditTemplateSharingAdapter {
  const scopes = new Map<string, TemplateShareScope>(), jobs = new Map<string, TemplateShare>()
  const reads = new Map<string, number>()
  return {
    listTargets: directory.listTargets,
    readScope: async folder => {
      if (!templates.listTemplates) throw new Error('preview_template_files_unavailable')
      const catalog = await templates.readCatalog(), items: TemplateShareScope['templates'] = []
      for (let page = 1; page <= Math.ceil(folder.folder_template_count / 10); page += 1) {
        const result = await templates.listTemplates({customer_id: folder.customer_id, search: '', page, page_size: 10})
        if (result.folder_id !== folder.folder_id || result.folder_revision !== folder.folder_revision) throw new Error('preview_folder_changed')
        items.push(...result.items)
      }
      const scope: TemplateShareScope = {customer_id: folder.customer_id, customer_name: catalog.customers.find(row => row.customer_id === folder.customer_id)?.name || '', folder_id: folder.folder_id, folder_revision: folder.folder_revision, templates: items, link_access: {state: 'allowed', reason: null}, zip_access: {state: 'allowed', reason: null}}
      scopes.set(JSON.stringify(templateShareReference(scope)), structuredClone(scope))
      return scope
    },
    send: async request => {
      const existing = jobs.get(request.request_id)
      if (existing) return structuredClone(existing)
      const scope = scopes.get(JSON.stringify(request.scope))
      if (!scope || !sameSnapshot(templateShareContent(scope, request.content.kind, request.content.package), request.content)) throw new Error('invalid_template_share_scope')
      const all: ShareTarget[] = []
      for (const kind of ['user', 'group'] as const) {
        let token: string | undefined
        const seen = new Set<string>()
        do {
          const page = await directory.listTargets('', kind, token)
          all.push(...page.targets)
          token = page.next_page_token || undefined
          if (token && seen.has(token)) throw new Error('preview_directory_cycle')
          if (token) seen.add(token)
        } while (token)
      }
      const targets = request.targets.map(ref => all.find(row => targetKey(ref) === targetKey(row)))
      if (!targets.length || targets.some(target => !target || target.organization_id !== request.organization_id || target.eligibility !== 'eligible')) throw new Error('invalid_template_share_targets')
      const job: TemplateShare = {request: structuredClone(request), receipt: {request_id: request.request_id, state: 'accepted', reason: null}, deliveries: targets.map(target => ({target: structuredClone(target!), attempt_id: request.request_id, state: 'sent', reason: null, retry_allowed: false}))}
      if (scene === 'templates-share-partial') job.deliveries = job.deliveries.map((row, index) => index === 1 ? {...row, state: 'failed', retry_allowed: true, reason: '投递失败，可重试。'} : index === 2 ? {...row, state: 'unknown', reason: '发送结果待确认。'} : row)
      if (scene === 'templates-share-revoked') {job.receipt = {...job.receipt, state: 'rejected', reason: '分享权限已撤销，未发送。'}; job.deliveries = job.deliveries.map(row => ({...row, state: 'failed', retry_allowed: false, reason: job.receipt.reason}))}
      jobs.set(request.request_id, job)
      if (scene === 'templates-share-contradictory') return {...structuredClone(job), receipt: {...job.receipt, state: 'rejected'}}
      if (scene === 'templates-share-unknown') throw new Error('preview_template_share_response_lost')
      return structuredClone(job)
    },
    read: async input => {
      const job = jobs.get(input.request_id)
      if (!job || input.attempts.length !== job.deliveries.length || input.attempts.some(attempt => !job.deliveries.some(row => targetKey(row.target) === targetKey(attempt.target) && row.attempt_id === attempt.attempt_id))) throw new Error('unknown_template_share_attempt')
      const count = (reads.get(input.request_id) || 0) + 1; reads.set(input.request_id, count)
      if (scene === 'templates-share-unknown' && count === 1) return {...structuredClone(job), receipt: {...job.receipt, state: 'pending'}, deliveries: job.deliveries.map(row => ({...row, state: 'unknown', retry_allowed: false, reason: '发送结果待确认。'}))}
      if (scene === 'templates-share-partial') job.deliveries = job.deliveries.map(row => row.state === 'unknown' ? {...row, state: 'sent', reason: null} : row)
      return structuredClone(job)
    },
    retryFailed: async (requestId, target, attemptId) => {
      const job = jobs.get(requestId), delivery = job?.deliveries.find(row => targetKey(row.target) === targetKey(target))
      if (!job || !delivery || !shareRetryAllowed(job, delivery)) throw new Error('no_retryable_preview_delivery')
      const next: TemplateShare = {...job, deliveries: job.deliveries.map(row => row === delivery ? {...row, attempt_id: attemptId, state: 'sent', retry_allowed: false, reason: null} : row)}
      jobs.set(requestId, next); return structuredClone(next)
    },
  }
}
