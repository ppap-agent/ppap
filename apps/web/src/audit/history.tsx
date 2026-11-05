import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/bf7f2f7d5c4742a193c29da18e7688bf
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowUpRight, Check, ChevronDown, History} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {FileName} from '../journey/file-name'
import {resultKey, historyShareMatches, type ArchiveDocument, type AuditArchiveAdapter, type HistoryDetail, type HistoryEntry, type ResultReference, type readArchiveAddress} from './archive-contract'
import {AuditReviewContent, type OriginalRenderer} from './review-content'
import {HistoryReport} from './history-report'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {sameSnapshot} from './result-projection'
import {conclusionLabel, resultTone} from './status-labels'

const labels = {queued: '排队中', running: '审核中', completed: '审核完成', review_failed: '审核异常'}
export function ArchiveHistory({document, documentId, initialAddress, onNavigate, adapter, renderOriginal, onBack, onOpenTask, reportAdapter, sharingAdapter, backLabel = '返回文件档案'}: {document: ArchiveDocument | null; documentId: string; initialAddress: ReturnType<typeof readArchiveAddress>; onNavigate: (reference: ResultReference, report: boolean, replace?: boolean) => void; adapter: AuditArchiveAdapter; renderOriginal?: OriginalRenderer; onBack: () => void; onOpenTask: (id: string) => void; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; backLabel?: string}) {
  const [history, setHistory] = useState<components['schemas']['AuditHistoryPage'] | null>(null)
  const [selected, setSelected] = useState<HistoryEntry | null>(null), [detail, setDetail] = useState<HistoryDetail | null>(null)
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [reload, setReload] = useState(0), [listReload, setListReload] = useState(0)
  const [open, setOpen] = useState(false), [reportView, setReportView] = useState(initialAddress.report)
  const [previous, setPrevious] = useState<{sequence: number; detail: HistoryDetail} | null>(null)
  const epoch = useRef(0)
  const preliminary = useRef<{reference: ResultReference; read: Promise<HistoryDetail>} | null>(null)
  useEffect(() => {
    let current = true
    setLoading(true); setError('')
    const reference = !initialAddress.invalid ? initialAddress.reference || document?.latest_completed?.reference : null
    const early = reference ? {reference: structuredClone(reference), read: adapter.readHistory(documentId, structuredClone(reference))} : null
    // Selection still comes from the history list. Keep an early rejection handled until then.
    void early?.read.catch(() => {})
    preliminary.current = early
    void adapter.listHistory(documentId).then(result => {
      if (!current) return
      if (result.document.document_id !== documentId || result.entries.some(row => row.document_id !== documentId) || new Set(result.entries.map(row => resultKey(row.reference))).size !== result.entries.length) throw new Error('history_document_mismatch')
      if (initialAddress.invalid) throw new Error('history_invalid_address')
      const target = initialAddress.reference || result.default_reference
      const initial = target ? result.entries.find(row => sameSnapshot(row.reference, target)) : null
      if (initialAddress.reference && !initial) throw new Error('history_reference_missing')
      if (!initialAddress.reference && result.default_reference && (!initial || initial.execution !== 'completed')) throw new Error('history_default_mismatch')
      setHistory(result); setSelected(initial || null); setLoading(false)
      if (initial) onNavigate(initial.reference, initialAddress.report, true)
    }).catch(failure => {if (current) {setError(failure instanceof Error && failure.name === 'TimeoutError' ? '读取审核档案超时，请重新读取。' : '历史审核清单暂不可用，未打开其他文档。'); setLoading(false)}})
    return () => {current = false; if (preliminary.current === early) preliminary.current = null}
  }, [adapter, documentId, listReload])
  useEffect(() => {
    if (!selected) return
    const run = ++epoch.current
    setDetail(null); setLoading(true); setError('')
    const early = preliminary.current
    preliminary.current = null
    const read = early && sameSnapshot(early.reference, selected.reference) ? early.read : adapter.readHistory(documentId, structuredClone(selected.reference))
    void read.then(result => {
      if (epoch.current !== run) return
      const reference = selected.reference, audit = result.file.audit
      if (result.entry.document_id !== documentId || !sameSnapshot(result.entry.reference, reference) || result.entry.sequence !== selected.sequence || !historyShareMatches(result.entry, result.report_share) || result.file.file.id !== reference.file_version_id || result.file.selection !== 'selected' || !audit || result.entry.execution !== audit.execution || result.entry.conclusion !== audit.conclusion || !sameSnapshot(result.entry.report, audit.report) || !sameSnapshot(result.entry.report_access, result.report_access) || new Set(result.frozen_file_version_ids).size !== result.frozen_file_version_ids.length || new Set(result.originals.map(row => row.file_version_id)).size !== result.originals.length || audit.round_id !== reference.round_id || audit.result_attempt !== reference.result_attempt || audit.file_version_id !== reference.file_version_id || !result.frozen_file_version_ids.includes(reference.file_version_id) || result.originals.some(row => !result.frozen_file_version_ids.includes(row.file_version_id) || row.file && row.file.id !== row.file_version_id)) throw new Error('history_snapshot_mismatch')
      setDetail(result); setHistory(current => current ? {...current, entries: current.entries.map(row => sameSnapshot(row.reference, result.entry.reference) ? result.entry : row)} : current)
    }).catch(failure => {if (epoch.current === run) {
      const status = failure instanceof Error && 'status' in failure ? failure.status : null
      setError(failure instanceof Error && failure.name === 'TimeoutError' ? '读取审核档案超时，请重新读取。' : status === 401 ? '登录已失效。恢复登录后再读取这次记录。' : status === 403 ? '当前账号无权读取这次审核。未扩大来源任务或原件权限。' : status === 404 ? '这次审核记录不可用。未改开最新记录。' : '这次审核暂时无法读取。未用最新记录替换。')
    }}).finally(() => {if (epoch.current === run) setLoading(false)})
    return () => {epoch.current += 1}
  }, [adapter, documentId, selected, reload])
  // Compare with the nearest earlier completed audit; a failed read only omits the comparison.
  const earlier = selected && history?.entries.filter(row => row.sequence < selected.sequence && row.execution === 'completed').sort((a, b) => b.sequence - a.sequence)[0]
  const detailShown = Boolean(detail)
  useEffect(() => {
    setPrevious(null)
    if (!earlier || !detailShown) return
    let current = true
    void adapter.readHistory(documentId, structuredClone(earlier.reference)).then(result => {if (current) setPrevious({sequence: earlier.sequence, detail: result})}).catch(() => {})
    return () => {current = false}
  }, [adapter, documentId, detailShown, earlier?.reference.task_id, earlier?.reference.round_id, earlier?.reference.file_version_id, earlier?.reference.result_attempt])
  function choose(entry: HistoryEntry) {setOpen(false); if (selected && sameSnapshot(selected.reference, entry.reference)) return; epoch.current += 1; setReportView(false); setDetail(null); setSelected(entry); onNavigate(entry.reference, false)}
  const list = <nav aria-label="历次审核" className="grid divide-y">{history?.entries.map(entry => <Button key={resultKey(entry.reference)} variant="ghost" size="row" aria-label={`选择第 ${entry.sequence} 次审核`} aria-pressed={Boolean(selected && sameSnapshot(selected.reference, entry.reference))} className="items-start" onClick={() => choose(entry)}><span className="grid min-w-0 flex-1 gap-2"><span className="flex flex-wrap items-center gap-2 font-medium">第 {entry.sequence} 次审核{sameSnapshot(entry.reference, history.default_reference) && <Badge variant="outline">最新完成</Badge>}</span><span className="flex flex-wrap items-center gap-2 text-xs"><Badge shape="square" variant={entry.execution === 'review_failed' ? 'destructive' : resultTone(entry.conclusion)}>{labels[entry.execution]}{entry.conclusion ? ` · ${conclusionLabel[entry.conclusion] ?? '结果未知'}` : ''}</Badge><time dateTime={entry.audited_at} className="text-muted-foreground">{new Date(entry.audited_at).toLocaleString('zh-CN', {hour12: false})}</time></span><span className="text-xs text-muted-foreground">{entry.initiator} · 原件修订：{entry.source_revision || '未提供修订标识'}</span></span>{selected && sameSnapshot(selected.reference, entry.reference) && <Check className="mt-0.5 shrink-0"/>}</Button>)}</nav>
  return <section aria-label="文档历史审核">{reportView && detail && <HistoryReport detail={detail} adapter={reportAdapter} sharingAdapter={sharingAdapter} onChange={next => setDetail(latest => latest && sameSnapshot(latest.entry.reference, next.entry.reference) ? next : latest)} onBack={() => {setReportView(false); onNavigate(detail.entry.reference, false)}} onRead={() => {setDetail(null); setReload(value => value + 1)}}/>}
    <div hidden={reportView && Boolean(detail)} className="-mx-6 min-w-0 max-sm:-mx-4">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b px-6 pb-4 max-sm:px-4">
        <div className="grid min-w-0 flex-1 basis-96 grid-cols-[auto_minmax(0,1fr)] items-center gap-3 sm:flex sm:flex-wrap"><Button variant="ghost" size="icon" aria-label={backLabel} onClick={onBack}><ArrowLeft/></Button>{detail?.file.file.original_filename || history?.document.title || document?.title ? <h1 className="min-w-0 flex-1 text-lg font-semibold"><FileName name={detail?.file.file.original_filename || history?.document.title || document?.title || ''} colored/></h1> : loading ? <div className="min-w-0 flex-1"><h1 className="sr-only">历史审核</h1><ReadSkeleton label="正在读取文档" kind="inline"/></div> : <h1 className="min-w-0 flex-1 text-lg font-semibold">历史审核</h1>}
          <Sheet open={open} onOpenChange={setOpen}><SheetTrigger asChild><Button variant="outline" className="col-span-2 justify-self-start" aria-label="历史审核记录" aria-description={selected ? `当前第 ${selected.sequence} 次审核${sameSnapshot(selected.reference, history?.default_reference) ? '，最新完成' : ''}` : undefined} disabled={!history?.entries.length}><History/>{selected ? <>第 {selected.sequence} 次审核{sameSnapshot(selected.reference, history?.default_reference) && <span className="text-muted-foreground">· 最新完成</span>}</> : '历史审核记录'}<span className="text-muted-foreground">({history?.entries.length ?? 0})</span><ChevronDown/></Button></SheetTrigger><SheetContent side="left" className="data-[side=left]:w-full"><SheetHeader><SheetTitle>历史审核记录</SheetTitle><SheetDescription className="sr-only">选择需要查看的审核记录</SheetDescription></SheetHeader><div className="min-h-0 flex-1 overflow-y-auto">{list}</div></SheetContent></Sheet>
        </div>
        {detail && <div className="flex w-full flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground xl:w-auto"><span>审核时间：<time dateTime={detail.entry.audited_at}>{new Date(detail.entry.audited_at).toLocaleString('zh-CN', {hour12: false})}</time></span><span>发起人：{detail.entry.initiator}</span><span>原件修订：{detail.entry.source_revision || '未提供修订标识'}</span><Button variant="ghost" size="sm" className="ml-auto" disabled={detail.source_task_access.state !== 'allowed'} onClick={() => onOpenTask(detail.entry.reference.task_id)}>打开来源任务<ArrowUpRight/></Button></div>}
        {detail && detail.source_task_access.state !== 'allowed' && <p className="w-full text-xs text-muted-foreground">{detail.source_task_access.reason || '尚未取得来源任务读取许可。'}</p>}
      </header>
      {loading && <ReadSkeleton label="正在读取这次审核…" kind={detail ? 'inline' : 'detail'} className="px-6 py-5 max-sm:px-4"/>}
      {error && <div className="px-6 py-5 max-sm:px-4"><Alert variant="destructive"><AlertTitle>这次记录读取未完成</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => history ? setReload(value => value + 1) : setListReload(value => value + 1)}>重新读取这次审核</Button></AlertDescription></Alert></div>}
      {detail && <AuditReviewContent key={resultKey(detail.entry.reference)} previous={previous?.detail.file.audit ? {label: `第 ${previous.sequence} 次审核`, checks: previous.detail.file.audit.checks} : null} file={detail.file} showFileTitle={false} roundId={detail.entry.reference.round_id} frozenFileVersionIds={detail.frozen_file_version_ids} originals={detail.originals} renderOriginal={renderOriginal} onReport={() => {setReportView(true); onNavigate(detail.entry.reference, true)}}/>}
      {history && !selected && !loading && <p className="px-6 py-5 max-sm:px-4">尚无完成审核。可以选择已有记录查看进度或异常。</p>}
    </div>
  </section>
}
