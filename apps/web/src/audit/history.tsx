import {displayTime} from '../helpers'
import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/bf7f2f7d5c4742a193c29da18e7688bf
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowUpRight} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {FileName} from '../journey/file-name'
import {resultKey, historyShareMatches, type ArchiveDocument, type AuditArchiveAdapter, type HistoryDetail, type HistoryEntry, type ResultReference, type readArchiveAddress} from './archive-contract'
import {AuditReviewContent, type OriginalRenderer} from './review-content'
import {HistoryReport} from './history-report'
import {FactQuestions} from './fact-questions'
import {factMachineFindings} from './fact-answers-contract'
import type {AuditReportAdapter} from './report-contract'
import type {AuditSharingAdapter} from './sharing-contract'
import {sameSnapshot} from './result-projection'
import {conclusionLabel, executionLabel as labels, resultTone} from './status-labels'

export function ArchiveHistory({document, documentId, initialAddress, onNavigate, adapter, renderOriginal, onBack, onOpenTask, reportAdapter, sharingAdapter, backLabel = '返回审核文件'}: {document: ArchiveDocument | null; documentId: string; initialAddress: ReturnType<typeof readArchiveAddress>; onNavigate: (reference: ResultReference, report: boolean, replace?: boolean) => void; adapter: AuditArchiveAdapter; renderOriginal?: OriginalRenderer; onBack: () => void; onOpenTask: (id: string) => void; reportAdapter?: AuditReportAdapter; sharingAdapter?: AuditSharingAdapter; backLabel?: string}) {
  const [history, setHistory] = useState<components['schemas']['AuditHistoryPage'] | null>(null)
  const [selected, setSelected] = useState<HistoryEntry | null>(null), [detail, setDetail] = useState<HistoryDetail | null>(null)
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [reload, setReload] = useState(0), [listReload, setListReload] = useState(0)
  const [reportView, setReportView] = useState(initialAddress.report)
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
    }).catch(failure => {if (current) {setError(failure instanceof Error && failure.name === 'TimeoutError' ? '读取审核文件超时，请重新读取。' : '历史审核清单暂不可用，未打开其他文档。'); setLoading(false)}})
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
      setError(failure instanceof Error && failure.name === 'TimeoutError' ? '读取审核文件超时，请重新读取。' : status === 401 ? '登录已失效。恢复登录后再读取这次记录。' : status === 403 ? '你没有查看这次审核的权限。' : status === 404 ? '这次审核记录已不可用。' : '这次审核暂时无法读取，请重试。')
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
  function choose(entry: HistoryEntry) {if (selected && sameSnapshot(selected.reference, entry.reference)) return; epoch.current += 1; setReportView(false); setDetail(null); setSelected(entry); onNavigate(entry.reference, false)}
  const isLatest = (entry: HistoryEntry) => sameSnapshot(entry.reference, history?.default_reference)
  const list = <nav aria-label="历次审核" className="grid gap-2 p-3">{history?.entries.map(entry => {const active = Boolean(selected && sameSnapshot(selected.reference, entry.reference)); return <div key={resultKey(entry.reference)} data-selected={active || undefined} className="rounded-lg border bg-card p-1 data-selected:border-primary data-selected:bg-primary/5"><Button variant="ghost" aria-label={`选择第 ${entry.sequence} 次审核`} aria-pressed={active} className="h-auto w-full justify-start whitespace-normal text-left" onClick={() => choose(entry)}><span className="grid min-w-0 flex-1 gap-1.5 py-1.5">
    {/* The prototype names each round as a version of the document; the latest completed one is marked. */}
    <span className="flex flex-wrap items-center justify-between gap-2 font-medium">版本{entry.sequence}{isLatest(entry) && '（最新）'}<Badge shape="square" variant={entry.execution === 'review_failed' ? 'destructive' : resultTone(entry.conclusion)}>{entry.execution === 'completed' && entry.conclusion ? conclusionLabel[entry.conclusion] ?? '结果未知' : `${labels[entry.execution]}${entry.conclusion ? ` · ${conclusionLabel[entry.conclusion] ?? '结果未知'}` : ''}`}</Badge></span>
    <time dateTime={entry.audited_at} className="text-xs text-muted-foreground">{displayTime(entry.audited_at)}</time>
    <span className="text-xs text-muted-foreground">{entry.initiator}{entry.source_revision && ` · 原件修订：${entry.source_revision}`}</span>
  </span></Button></div>})}</nav>
  // One kv grid names the selected audit; the clause table below is the record itself.
  const information = detail && <section aria-label="本次审核信息" className="border-b px-6 py-3"><dl className="grid gap-x-8 gap-y-1.5 text-sm grid-cols-2 [&>div]:flex [&>div]:min-w-0 [&>div]:gap-1 [&_dd]:min-w-0 [&_dd]:[overflow-wrap:anywhere] [&_dt]:shrink-0 [&_dt]:text-muted-foreground">
    <div><dt>审核版本：</dt><dd>第 {detail.entry.sequence} 次审核{isLatest(detail.entry) && '（最新完成）'}</dd></div>
    <div><dt>审核时间：</dt><dd><time dateTime={detail.entry.audited_at}>{displayTime(detail.entry.audited_at)}</time></dd></div>
    <div><dt>发起人：</dt><dd>{detail.entry.initiator}</dd></div>
    {/* A revision shows only when the file carries one; a placeholder says nothing. */}{detail.entry.source_revision && <div><dt>原件修订：</dt><dd>{detail.entry.source_revision}</dd></div>}
    <div><dt>来源任务：</dt><dd>{detail.source_task_access.state === 'allowed' ? <Button variant="link" size="xs" className="-ml-2" onClick={() => onOpenTask(detail.entry.reference.task_id)}>打开来源任务<ArrowUpRight/></Button> : <span className="text-muted-foreground">{detail.source_task_access.reason || '尚未取得来源任务读取许可。'}</span>}</dd></div>
  </dl></section>
  return <section aria-label="文档历史审核">{reportView && detail && <HistoryReport detail={detail} adapter={reportAdapter} sharingAdapter={sharingAdapter} onChange={next => setDetail(latest => latest && sameSnapshot(latest.entry.reference, next.entry.reference) ? next : latest)} onBack={() => {setReportView(false); onNavigate(detail.entry.reference, false)}} onRead={() => {setDetail(null); setReload(value => value + 1)}}/>}
    <div hidden={reportView && Boolean(detail)} className="-mx-6 min-w-0">
      <header className="flex flex-wrap items-center gap-3 border-b px-6 pb-2"><Button variant="ghost" size="icon" aria-label={backLabel} onClick={onBack}><ArrowLeft/></Button>{detail?.file.file.original_filename || history?.document.title || document?.title ? <><h1 className="min-w-0 text-lg font-semibold"><FileName name={detail?.file.file.original_filename || history?.document.title || document?.title || ''} colored/></h1>{history && <Badge shape="square" variant="outline">{history.entries.length} 个版本</Badge>}<span aria-hidden="true" className="flex-1"/></> : loading ? <div className="min-w-0 flex-1"><h1 className="sr-only">历史审核</h1><ReadSkeleton label="正在读取文档" kind="inline"/></div> : <h1 className="min-w-0 flex-1 text-lg font-semibold">历史审核</h1>}
      </header>
      <div className="flex min-w-0">
        <aside aria-label="历史版本" className="sticky top-0 max-h-[calc(100dvh-7rem)] w-60 shrink-0 self-start overflow-y-auto border-r"><h2 className="px-4 pt-4 text-sm font-semibold">历史版本{history && <span className="ml-1 font-normal text-muted-foreground">({history.entries.length})</span>}</h2>{history ? list : loading && <ReadSkeleton label="正在读取历史版本" kind="inline" className="p-4"/>}</aside>
        <div className="min-w-0 flex-1">
          {information}
          {loading && <ReadSkeleton label="正在读取这次审核…" kind={detail ? 'inline' : 'detail'} className="px-6 py-5"/>}
          {error && <div className="px-6 py-5"><Alert variant="destructive"><AlertTitle>这次记录读取未完成</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => history ? setReload(value => value + 1) : setListReload(value => value + 1)}>重新读取这次审核</Button></AlertDescription></Alert></div>}
          {detail && <FactQuestions key={`facts:${resultKey(detail.entry.reference)}`} roundId={detail.entry.reference.round_id} fileVersionId={detail.file.file.id} files={detail.originals.flatMap(row => row.access.state === 'allowed' && row.file ? [row.file] : [])} findings={factMachineFindings(detail.file.audit?.checks)}/>}
          {detail && <AuditReviewContent facts={[{label: '当前版本', value: `版本${detail.entry.sequence}${isLatest(detail.entry) ? '（最新）' : ''} · ${displayTime(detail.entry.audited_at)}`}]} key={resultKey(detail.entry.reference)} previous={previous?.detail.file.audit ? {label: `第 ${previous.sequence} 次审核`, checks: previous.detail.file.audit.checks} : null} file={detail.file} showFileTitle={false} roundId={detail.entry.reference.round_id} frozenFileVersionIds={detail.frozen_file_version_ids} originals={detail.originals} renderOriginal={renderOriginal} onReport={() => {setReportView(true); onNavigate(detail.entry.reference, true)}}/>}
          {history && !selected && !loading && <p className="px-6 py-5">尚无完成审核。可以选择已有记录查看进度或异常。</p>}
        </div>
      </div>
    </div>
  </section>
}
