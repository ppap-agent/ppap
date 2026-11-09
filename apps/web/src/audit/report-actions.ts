import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {sameSnapshot, reportReference, packageOutcomeError, taskPackageScopeError, multipleDistinctReports} from './result-projection'
import type {AuditReportAdapter, AuditReportReference, AuditReportPackage} from './report-contract'

type Task = components['schemas']['AuditTask']
type Report = components['schemas']['AuditFileReport']
type ReportItem = {reference: AuditReportReference; report: Report}
const sameCollection = (left: AuditReportReference[], right: AuditReportReference[]) => left.length === right.length && left.every(reference => right.some(row => sameSnapshot(row, reference)))

/** Report operations accept fixed references, not ownership of a private task. */
export function useReportGeneration(items: ReportItem[], onChange: (reference: AuditReportReference, report: Report) => void, adapter?: AuditReportAdapter) {
  const current = useRef(items), alive = useRef(true), pending = useRef(new Set<string>())
  current.current = items
  const [busy, setBusy] = useState<string[]>([])
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  const locate = (reference: AuditReportReference) => current.current.find(item => sameSnapshot(item.reference, reference))?.report
  function commit(reference: AuditReportReference, report: Report) {
    if (!alive.current || !locate(reference)) return
    current.current = current.current.map(item => sameSnapshot(item.reference, reference) ? {...item, report} : item)
    onChange(reference, report)
  }
  async function run(reference: AuditReportReference, readOnly: boolean) {
    const previous = locate(reference)
    const command = readOnly ? adapter?.readRegeneration : adapter?.regenerate
    if (!command || !previous || pending.current.has(reference.report_id)) return
    if (readOnly ? previous.generation_request?.state !== 'pending' : previous.state !== 'failed' || !previous.regenerate_allowed || previous.generation_request?.state === 'pending') return
    const requestId = readOnly ? previous.generation_request!.request_id : crypto.randomUUID()
    pending.current.add(reference.report_id); setBusy([...pending.current])
    const observation = {...previous, generation_request: {request_id: requestId, state: 'pending' as const, reason: '未取得报告生成结果，只查看原请求，不重复生成。'}}
    commit(reference, observation)
    try {
      const update = await command(structuredClone(reference), requestId)
      const latest = locate(reference)
      if (!alive.current || latest !== observation) return
      if (!sameSnapshot(update.reference, reference) || update.receipt.request_id !== requestId || update.report && update.report.report_id !== reference.report_id) throw new Error('report_receipt_mismatch')
      if (update.receipt.state === 'accepted' && (!update.report || update.report.state === 'not_eligible')) throw new Error('invalid_report_receipt')
      commit(reference, {...(update.report || latest), generation_request: update.receipt})
    } catch {
      const latest = locate(reference)
      if (latest === observation) commit(reference, {...latest, generation_request: {request_id: requestId, state: 'pending', reason: '报告生成结果未知。只查看原请求，不重复生成或重审。'}})
    } finally {pending.current.delete(reference.report_id); if (alive.current) setBusy([...pending.current])}
  }
  return {busy, regenerate: (reference: AuditReportReference) => run(reference, false), read: (reference: AuditReportReference) => run(reference, true)}
}

export function useReportPackage(job: AuditReportPackage | null | undefined, onChange: (job: AuditReportPackage) => void, eligible: (reference: AuditReportReference) => boolean, adapter?: AuditReportAdapter, scopeError = '') {
  const current = useRef(job), alive = useRef(true), pending = useRef(false)
  current.current = job
  const [packageBusy, setPackageBusy] = useState(false)
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  function commit(next: AuditReportPackage) {if (alive.current) {current.current = next; onChange(next)}}
  function canCreatePackage(reports: AuditReportReference[]) {
    if (scopeError || !adapter?.createPackage || !adapter.readPackage || !adapter.readPackageFile || pending.current || !multipleDistinctReports(reports) || reports.some(reference => !eligible(reference))) return false
    const previous = current.current
    if (!previous) return true
    if (packageOutcomeError(previous) || previous.receipt.state === 'pending' || previous.receipt.state === 'accepted' && ['pending', 'generating'].includes(previous.state)) return false
    return !(sameCollection(reports, previous.reports) && previous.state === 'failed' && previous.receipt.state !== 'rejected' && (!previous.retry_allowed || !adapter.retryPackage))
  }
  async function run(readOnly: boolean, reports?: AuditReportReference[]) {
    if (scopeError || !adapter || pending.current) return
    const previous = current.current
    if (readOnly ? !previous : !canCreatePackage(reports || [])) return
    const recovery = !readOnly && previous?.state === 'failed' && previous.receipt.state === 'accepted' && sameCollection(reports || [], previous.reports) ? previous : null
    const references = structuredClone(readOnly ? previous!.reports : recovery ? recovery.reports : reports || [])
    // Reading an existing job never depends on the current page or selection.
    if (!multipleDistinctReports(references)) return
    const requestId = readOnly ? previous!.receipt.request_id : crypto.randomUUID()
    const read = adapter.readPackage, create = adapter.createPackage, retry = adapter.retryPackage
    const command = readOnly ? read && (() => read(requestId)) : recovery ? retry && (() => retry(structuredClone(recovery), requestId)) : create && (() => create(references, requestId))
    if (!command) return
    pending.current = true; setPackageBusy(true)
    const observation: AuditReportPackage = readOnly ? previous! : {package_id: null, reports: references, receipt: {request_id: requestId, state: 'pending', reason: null}, state: 'pending', artifact: null, reason: 'ZIP 提交结果未知；只查看原打包请求。', retry_allowed: false}
    if (!readOnly) commit(observation)
    try {
      const next = await command()
      if (!alive.current || current.current !== observation) return
      if (next.receipt.request_id !== requestId || !sameSnapshot(next.reports, references)) throw new Error('package_receipt_mismatch')
      if (recovery && next.receipt.state === 'rejected') {commit({...recovery, reason: next.receipt.reason || next.reason, retry_allowed: false}); return}
      if (packageOutcomeError(next) || next.state === 'ready' && (!next.package_id || !next.artifact?.artifact_id || next.artifact.content_type !== 'application/zip' || next.artifact.byte_size <= 0)) throw new Error('package_receipt_mismatch')
      if (recovery && next.receipt.state === 'accepted' && next.package_id !== recovery.package_id) throw new Error('package_recovery_mismatch')
      commit(next)
    } catch {if (current.current === observation) commit({...observation, reason: 'ZIP 结果暂时无法确认。保留原集合与原请求，只读取状态，不重复打包。'})}
    finally {pending.current = false; if (alive.current) setPackageBusy(false)}
  }
  return {packageBusy, canCreatePackage, createPackage: (reports: AuditReportReference[]) => run(false, reports), readPackage: () => run(true)}
}

/** The task view updates only report projections; historical views use the same cores. */
export function useReportActions(task: Task, onChange: (task: Task) => void, adapter?: AuditReportAdapter) {
  const current = useRef(task)
  current.current = task
  const generation = useReportGeneration(task.files.flatMap(row => {const reference = reportReference(task, row); return reference && row.audit ? [{reference, report: row.audit.report}] : []}), (reference, report) => {
    const latest = current.current
    const next = {...latest, files: latest.files.map(row => sameSnapshot(reportReference(latest, row), reference) && row.audit ? {...row, audit: {...row.audit, report}} : row)}
    current.current = next; onChange(next)
  }, adapter)
  const packaging = useReportPackage(task.report_package, job => {const next = {...current.current, report_package: job}; current.current = next; onChange(next)}, reference => current.current.files.some(row => sameSnapshot(reportReference(current.current, row), reference)), adapter, taskPackageScopeError(task))
  return {...generation, ...packaging}
}
