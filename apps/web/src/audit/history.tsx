// Stitch: projects/13261964020862199279/screens/463e2efe783b487fa611435f6d9d28ab
import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {FileName} from '../journey/file-name'
import {resultKey, historyShareMatches, type ArchiveDocument, type AuditArchiveAdapter, type HistoryDetail, type HistoryEntry, type ResultReference, type readArchiveAddress} from './archive-contract'
import {AuditReviewContent, type OriginalRenderer} from './review-content'
import {HistoryReport} from './history-report'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {sameSnapshot} from './result-projection'

const labels = {queued: '排队中', running: '审核中', completed: '审核完成', review_failed: '审核异常'}
export function ArchiveHistory({document, documentId, initialAddress, onNavigate, adapter, renderOriginal, onBack, onOpenTask, reportAdapter, sharingAdapter, backLabel = '返回文件档案'}: {document: ArchiveDocument | null; documentId: string; initialAddress: ReturnType<typeof readArchiveAddress>; onNavigate: (reference: ResultReference, report: boolean, replace?: boolean) => void; adapter: AuditArchiveAdapter; renderOriginal?: OriginalRenderer; onBack: () => void; onOpenTask: (id: string) => void; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; backLabel?: string}) {
  const [history, setHistory] = useState<components['schemas']['AuditHistoryPage'] | null>(null)
  const [selected, setSelected] = useState<HistoryEntry | null>(null), [detail, setDetail] = useState<HistoryDetail | null>(null)
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [reload, setReload] = useState(0), [listReload, setListReload] = useState(0)
  const [open, setOpen] = useState(false), [reportView, setReportView] = useState(initialAddress.report)
  const epoch = useRef(0)
  useEffect(() => {
    let current = true
    setLoading(true); setError('')
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
    }).catch(() => {if (current) {setError('历史审核清单暂不可用，未打开其他文档。'); setLoading(false)}})
    return () => {current = false}
  }, [adapter, documentId, listReload])
  useEffect(() => {
    if (!selected) return
    const run = ++epoch.current
    setDetail(null); setLoading(true); setError('')
    void adapter.readHistory(documentId, structuredClone(selected.reference)).then(result => {
      if (epoch.current !== run) return
      const reference = selected.reference, audit = result.file.audit
      if (result.entry.document_id !== documentId || !sameSnapshot(result.entry.reference, reference) || result.entry.sequence !== selected.sequence || !historyShareMatches(result.entry, result.report_share) || result.file.file.id !== reference.file_version_id || result.file.selection !== 'selected' || !audit || result.entry.execution !== audit.execution || result.entry.conclusion !== audit.conclusion || !sameSnapshot(result.entry.report, audit.report) || !sameSnapshot(result.entry.report_access, result.report_access) || new Set(result.frozen_file_version_ids).size !== result.frozen_file_version_ids.length || new Set(result.originals.map(row => row.file_version_id)).size !== result.originals.length || audit.round_id !== reference.round_id || audit.result_attempt !== reference.result_attempt || audit.file_version_id !== reference.file_version_id || !result.frozen_file_version_ids.includes(reference.file_version_id) || result.originals.some(row => !result.frozen_file_version_ids.includes(row.file_version_id) || row.file && row.file.id !== row.file_version_id)) throw new Error('history_snapshot_mismatch')
      setDetail(result); setHistory(current => current ? {...current, entries: current.entries.map(row => sameSnapshot(row.reference, result.entry.reference) ? result.entry : row)} : current)
    }).catch(failure => {if (epoch.current === run) {
      const status = failure instanceof Error && 'status' in failure ? failure.status : null
      setError(status === 401 ? '登录已失效。恢复登录后再读取这次记录。' : status === 403 ? '当前账号无权读取这次审核。未扩大来源任务或原件权限。' : status === 404 ? '这次审核记录不可用。未改开最新记录。' : '这次审核暂时无法读取。未用最新记录替换。')
    }}).finally(() => {if (epoch.current === run) setLoading(false)})
    return () => {epoch.current += 1}
  }, [adapter, documentId, selected, reload])
  function choose(entry: HistoryEntry) {setOpen(false); if (selected && sameSnapshot(selected.reference, entry.reference)) return; epoch.current += 1; setReportView(false); setDetail(null); setSelected(entry); onNavigate(entry.reference, false)}
  const list = <ItemGroup aria-label="历次审核">{history?.entries.map(entry => <Item role="listitem" size="xs" key={resultKey(entry.reference)}><ItemContent><Button variant={selected && sameSnapshot(selected.reference, entry.reference) ? 'secondary' : 'ghost'} aria-label={`选择第 ${entry.sequence} 次审核`} aria-pressed={Boolean(selected && sameSnapshot(selected.reference, entry.reference))} className="h-auto justify-start whitespace-normal text-left" onClick={() => choose(entry)}><span className="grid gap-2"><span>第 {entry.sequence} 次审核{sameSnapshot(entry.reference, history.default_reference) ? ' · 最新完成' : ''}</span><span className="text-xs">{labels[entry.execution]}{entry.conclusion ? ` · ${entry.conclusion}` : ''}</span><span className="text-xs">{entry.audited_at.slice(0, 10)}</span></span></Button></ItemContent></Item>)}</ItemGroup>
  return <section aria-label="文档历史审核">{reportView && detail && <HistoryReport detail={detail} adapter={reportAdapter} sharingAdapter={sharingAdapter} onChange={next => setDetail(latest => latest && sameSnapshot(latest.entry.reference, next.entry.reference) ? next : latest)} onBack={() => {setReportView(false); onNavigate(detail.entry.reference, false)}} onRead={() => {setDetail(null); setReload(value => value + 1)}}/>}<div hidden={reportView && Boolean(detail)} className="space-y-5"><header className="space-y-3"><Button variant="ghost" aria-label={backLabel} onClick={onBack}>← {backLabel}</Button><h1 className="text-2xl font-semibold">历史审核</h1><h2 className="font-medium"><FileName name={history?.document.title || document?.title || '正在读取文档'}/></h2><p className="text-sm text-muted-foreground">{[history?.document.metadata.customer || document?.metadata.customer, history?.document.metadata.model || document?.metadata.model, history?.document.metadata.part || document?.metadata.part].filter(Boolean).join(' · ') || '其他'} · 档案 {documentId.split('-').at(-1)}</p></header>
    <Sheet open={open} onOpenChange={setOpen}><SheetTrigger asChild><Button variant="outline" className="md:hidden">历史审核记录</Button></SheetTrigger><SheetContent side="left"><SheetHeader><SheetTitle>历史审核记录</SheetTitle><SheetDescription>第 N 次审核是审核历史，不是文件修订版本。</SheetDescription></SheetHeader><div className="overflow-y-auto p-4">{list}</div></SheetContent></Sheet>
    <div className="grid min-w-0 gap-6 md:grid-cols-[12rem_minmax(0,1fr)]"><aside className="min-w-0 border-r pr-4 max-md:hidden">{list}</aside><div className="min-w-0 space-y-4">
      {loading && <p role="status">正在读取当次冻结审核…</p>}{error && <Alert variant="destructive"><AlertTitle>这次记录读取未完成</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => history ? setReload(value => value + 1) : setListReload(value => value + 1)}>重新读取这次审核</Button></AlertDescription></Alert>}
      {detail && <><header className="space-y-3 border-b pb-4"><div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-semibold">第 {detail.entry.sequence} 次审核</h2><Badge variant="outline">{labels[detail.entry.execution]}</Badge></div><p className="text-xs text-muted-foreground">{detail.entry.audited_at} · 发起人：{detail.entry.initiator}</p><p>原件修订：{detail.entry.source_revision || '未提供修订标识'}</p><p className="break-all text-xs text-muted-foreground">来源任务：{detail.entry.reference.task_id}</p><Button variant="outline" disabled={detail.source_task_access.state !== 'allowed'} onClick={() => onOpenTask(detail.entry.reference.task_id)}>打开来源任务</Button>{detail.source_task_access.state !== 'allowed' && <p className="text-xs text-muted-foreground">{detail.source_task_access.reason || '尚未取得来源任务读取许可。'}</p>}</header><AuditReviewContent key={resultKey(detail.entry.reference)} file={detail.file} roundId={detail.entry.reference.round_id} frozenFileVersionIds={detail.frozen_file_version_ids} originals={detail.originals} renderOriginal={renderOriginal} onReport={() => {setReportView(true); onNavigate(detail.entry.reference, true)}}/></>}
      {history && !selected && !loading && <p>尚无完成审核。可以选择已有记录查看进度或异常。</p>}
    </div></div>
  </div></section>
}
