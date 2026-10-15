import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {sameSnapshot, reportReference} from './result-projection'
import type {AuditReportAdapter, AuditReportReference, AuditReportPackage} from './report-contract'

type Task = components['schemas']['AuditTask']
type Report = components['schemas']['AuditFileReport']

/** Only report projections change here. Audit input, attempt and findings do not. */
export function useReportActions(task: Task, onChange: (task: Task) => void, adapter?: AuditReportAdapter) {
  const current = useRef(task), alive = useRef(true), pending = useRef(new Set<string>())
  current.current = task
  const [busy, setBusy] = useState<string[]>([])
  const packaging = useRef(false), [packageBusy, setPackageBusy] = useState(false)
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  function locate(reference: AuditReportReference) {
    const row = current.current.files.find(file => file.file.id === reference.file_version_id)
    return row && sameSnapshot(reportReference(current.current, row), reference) ? row : null
  }
  function commit(reference: AuditReportReference, report: Report) {
    const row = locate(reference)
    if (!alive.current || !row?.audit) return
    const next = {...current.current, files: current.current.files.map(file => file === row ? {...file, audit: {...row.audit!, report}} : file)}
    current.current = next; onChange(next)
  }
  async function run(reference: AuditReportReference, readOnly: boolean) {
    const row = locate(reference), previous = row?.audit?.report
    if (!adapter || !previous || pending.current.has(reference.report_id)) return
    if (readOnly ? previous.generation_request?.state !== 'pending' : previous.state !== 'failed' || !previous.regenerate_allowed || previous.generation_request?.state === 'pending') return
    const requestId = readOnly ? previous.generation_request!.request_id : crypto.randomUUID()
    pending.current.add(reference.report_id); setBusy([...pending.current])
    const observation = {...previous, generation_request: {request_id: requestId, state: 'pending' as const, reason: '未取得报告生成回执，只读取原请求，不重复生成。'}}
    commit(reference, observation)
    try {
      const update = await (readOnly ? adapter.readRegeneration(structuredClone(reference), requestId) : adapter.regenerate(structuredClone(reference), requestId))
      const latest = locate(reference)?.audit?.report
      if (!alive.current || latest !== observation) return
      if (!sameSnapshot(update.reference, reference) || update.receipt.request_id !== requestId || update.report && update.report.report_id !== reference.report_id) throw new Error('report_receipt_mismatch')
      if (update.receipt.state === 'accepted' && (!update.report || update.report.state === 'not_eligible')) throw new Error('invalid_report_receipt')
      commit(reference, {...(update.report || latest), generation_request: update.receipt})
    } catch {
      const latest = locate(reference)?.audit?.report
      if (latest === observation) commit(reference, {...latest, generation_request: {request_id: requestId, state: 'pending', reason: '报告生成结果未知。只读取原请求，不重复生成或重审。'}})
    } finally {
      pending.current.delete(reference.report_id)
      if (alive.current) setBusy([...pending.current])
    }
  }
  function commitPackage(job: AuditReportPackage) {
    if (!alive.current) return
    const next = {...current.current, report_package: job}
    current.current = next; onChange(next)
  }
  async function runPackage(readOnly: boolean, reports?: AuditReportReference[]) {
    if (!adapter || packaging.current) return
    const previous = current.current.report_package
    if (readOnly ? !previous : previous && (previous.receipt.state === 'pending' || previous.receipt.state === 'accepted' && ['pending', 'generating'].includes(previous.state))) return
    const references = structuredClone(readOnly ? previous!.reports : reports || [])
    if (references.length < 2 || references.some(reference => !locate(reference))) return
    if (!readOnly && previous?.state === 'failed' && previous.receipt.state !== 'rejected' && !previous.retry_allowed && sameSnapshot(references, previous.reports)) return
    const requestId = readOnly ? previous!.receipt.request_id : crypto.randomUUID()
    packaging.current = true; setPackageBusy(true)
    const observation: AuditReportPackage = readOnly ? previous! : {package_id: null, reports: references, receipt: {request_id: requestId, state: 'pending', reason: null}, state: 'pending', artifact: null, reason: 'ZIP 受理结果未知；只读取原打包请求。', retry_allowed: false}
    if (!readOnly) commitPackage(observation)
    try {
      const job = await (readOnly ? adapter.readPackage(requestId) : adapter.createPackage(references, requestId))
      const latest = current.current.report_package
      if (!alive.current || latest !== observation) return
      if (job.receipt.request_id !== requestId || !sameSnapshot(job.reports, references) || job.state === 'ready' && (!job.package_id || job.artifact?.content_type !== 'application/zip')) throw new Error('package_receipt_mismatch')
      commitPackage(job)
    } catch {
      const latest = current.current.report_package
      if (latest === observation) commitPackage({...latest, reason: 'ZIP 结果暂时无法确认。保留原集合与原请求，只读取状态，不重复打包。'})
    } finally {packaging.current = false; if (alive.current) setPackageBusy(false)}
  }
  return {busy, packageBusy, regenerate: (reference: AuditReportReference) => run(reference, false), read: (reference: AuditReportReference) => run(reference, true), createPackage: (reports: AuditReportReference[]) => runPackage(false, reports), readPackage: () => runPackage(true)}
}
