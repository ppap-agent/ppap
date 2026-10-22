import {ArrowLeft, RefreshCw} from 'lucide-react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {historyReportReady, historyReportReference, type HistoryDetail} from './archive-contract'
import {ReportPreview} from './report-preview'
import {frozenResultError} from './result-projection'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {useReportGeneration} from './report-actions'
import {useShareActions, type ShareContentFor} from './share-actions'
import {ReportShareDialog} from './report-share'
import {sameSnapshot} from './result-projection'

export function HistoryReport({detail, adapter, sharingAdapter, onChange, onBack, onRead}: {detail: HistoryDetail; adapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; onChange: (detail: HistoryDetail) => void; onBack: () => void; onRead: () => void}) {
  const entry = detail.entry
  const issue = frozenResultError(entry.reference.round_id, detail.file, detail.frozen_file_version_ids)
  const current = useRef(detail); current.current = detail
  const reference = historyReportReference(entry), report = detail.file.audit?.report
  const [sharingOpen, setSharingOpen] = useState(false), trigger = useRef<HTMLButtonElement | null>(null)
  function save(next: HistoryDetail) {current.current = next; onChange(next)}
  const generation = useReportGeneration(reference && report && !issue && detail.report_access.state === 'allowed' ? [{reference, report}] : [], (ref, report) => {
    const latest = current.current
    if (!sameSnapshot(historyReportReference(latest.entry), ref) || !latest.file.audit) return
    save({...latest, entry: {...latest.entry, report}, file: {...latest.file, audit: {...latest.file.audit, report}}})
  }, adapter)
  const contentFor: ShareContentFor = (reports, mode) => {
    const latest = current.current, ref = historyReportReference(latest.entry)
    if (!historyReportReady(latest.entry) || latest.report_access.state !== 'allowed' || frozenResultError(latest.entry.reference.round_id, latest.file, latest.frozen_file_version_ids) || reports.length !== 1 || !sameSnapshot(reports[0], ref)) return null
    return mode === 'link' ? {kind: 'link', artifact: null, package_id: null} : {kind: 'pdf', artifact: structuredClone(latest.entry.report.artifact!), package_id: null}
  }
  const sharing = useShareActions(detail.report_share, share => save({...current.current, report_share: share}), input => sameSnapshot(contentFor(input.reports, input.content.kind === 'link' ? 'link' : 'file'), input.content), sharingAdapter)
  const shareButton = <Button variant="ghost" size="sm" disabled={!sharingAdapter || detail.report_access.state !== 'allowed' || !historyReportReady(entry) && !detail.report_share} onClick={event => {trigger.current = event.currentTarget; setSharingOpen(true)}}>分享</Button>
  const backButton = <Button variant="ghost" size="icon" aria-label="返回本次审核" title="返回本次审核" onClick={onBack}><ArrowLeft aria-hidden="true"/></Button>
  const readButton = <Button variant="ghost" size="icon" aria-label="读取当次报告状态" title="读取当次报告状态" onClick={onRead}><RefreshCw aria-hidden="true"/></Button>
  return <section className="space-y-3" aria-label="历史报告"><h1 className="sr-only">历史报告</h1>{detail.report_access.state !== 'allowed' && <header className="flex items-center justify-between gap-2">{backButton}<div className="flex items-center gap-1">{readButton}{shareButton}</div></header>}{detail.report_access.state !== 'allowed' ? <Alert><AlertTitle>本次报告不可访问</AlertTitle><AlertDescription>{detail.report_access.reason || '未取得读取许可，未改开最新报告。'}</AlertDescription></Alert> : <ReportPreview name={detail.file.file.original_filename} rule={detail.file.audit?.rule.title || '—'} ruleVersion={detail.file.audit?.rule.id} reference={historyReportReference(entry)} report={detail.file.audit?.report} ready={!issue && historyReportReady(entry)} issue={issue} adapter={adapter} navigation={<>{backButton}<span className="shrink-0 text-xs text-muted-foreground">第 {entry.sequence} 次审核</span></>} information={<><div><dt>历史审核</dt><dd>第 {entry.sequence} 次审核</dd></div><div><dt>原件修订</dt><dd>{entry.source_revision || '未提供'}</dd></div></>} actions={<>{readButton}{shareButton}</>}>{reference && report && <div className="space-y-2">{report.generation_request?.state === 'pending' ? <><p role="status" data-request-id={report.generation_request.request_id}>{report.generation_request.reason}</p><Button variant="outline" disabled={generation.busy.includes(reference.report_id)} onClick={() => void generation.read(reference)}>读取生成结果</Button></> : report.state === 'failed' && <><Button variant="outline" disabled={!adapter || !report.regenerate_allowed || generation.busy.includes(reference.report_id)} onClick={() => void generation.regenerate(reference)}>重生成同一结果</Button>{(report.regenerate_reason || !report.regenerate_allowed) && <p className="text-xs text-muted-foreground">{report.regenerate_reason || '暂不能重新生成报告。'}</p>}</>}</div>}</ReportPreview>}
    {sharingAdapter && <ReportShareDialog key={JSON.stringify(reference)} open={sharingOpen} share={detail.report_share} reports={reference ? [reference] : []} contentFor={contentFor} describe={ref => sameSnapshot(ref, reference) ? {name: detail.file.file.original_filename, conclusion: entry.conclusion || '结果未知', context: `第 ${entry.sequence} 次审核${entry.source_revision ? ` · ${entry.source_revision}` : ''}`} : {name: '原分享中的其他历史文件', conclusion: '范围待核对', context: `冻结文件：${ref.file_version_id}`}} adapter={sharingAdapter} actions={sharing} trigger={trigger.current} onClose={() => setSharingOpen(false)}/>}
  </section>
}
import {useRef, useState} from 'react'
