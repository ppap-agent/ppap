import {displayTime} from '../helpers'
import {useEffect, useRef, useState} from 'react'
import {ClipboardList} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Badge} from '../components/ui/badge'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import type {OriginalRenderer} from './review-content'
import {FileName} from '../journey/file-name'
import {checkerLabel, evidenceLocation, evidenceVersion, precheckFacts, precheckFiles, precheckIsCurrent, precheckOriginals, preparationAction, preparationLabels, type PrecheckFact, type PrecheckReceipt} from './precheck-state'
import {readAuditRoute, rememberRequest, writeAuditAddress} from './routes'

type Task = components['schemas']['AuditTask']
export type TaskPrecheckAdapter = {
  history: (taskId: string) => Promise<PrecheckReceipt[]>
  read: (taskId: string, snapshotId: string) => Promise<PrecheckReceipt>
  retry: (task: Task, requestId: string) => Promise<PrecheckReceipt>
  report: (taskId: string, snapshotId: string) => Promise<Blob>
}
const statusLabels: Record<PrecheckReceipt['status'], string> = {queued: '等待检查', leased: '检查中', unknown: '检查结果待确认', succeeded: '检查已完成', failed: '检查执行失败', cancelled: '检查已取消'}
const factLabels: Record<string, string> = {satisfied: '已核实', defect: '检查发现', unknown: '尚未核实', not_checked: '未执行', error: '读取失败', handoff: '留待正式审核'}

function Facts({facts, originals, onOriginal}: {facts: PrecheckFact[]; originals: components['schemas']['FileVersion'][]; onOriginal?: (file: components['schemas']['FileVersion'], source: PrecheckFact['evidence'][number]) => void}) {
  return <ul className="space-y-3 text-sm">{facts.map((fact, index) => <li key={`${fact.id}:${index}`} className="space-y-1"><p><span className="font-medium">{factLabels[fact.status] || '尚未核实'}</span> · {fact.message}</p>{fact.nextStep && fact.status !== 'satisfied' && <p className="text-muted-foreground">{fact.nextStep}</p>}{fact.evidence.length > 0 && <ul className="space-y-1 pl-3 text-xs text-muted-foreground">{fact.evidence.map((source, index) => {
    const original = originals.find(file => file.id === evidenceVersion(source.locator))
    return <li key={index} className="break-words">{original && <span className="block"><FileName name={original.original_filename}/></span>}{source.excerpt || '原件定位'}{source.locator && <span className="block">{evidenceLocation(source.locator)}</span>}{original && onOriginal && <Button variant="link" size="sm" onClick={() => onOriginal(original, source)}>查看证据原件</Button>}</li>
  })}</ul>}</li>)}</ul>
}

/** Free preparation stays beside the existing Task and its one formal action. */
export function TaskPrecheck({task, adapter, files: workingFiles = task.files, dirty = false, renderOriginal}: {task: Task; adapter: TaskPrecheckAdapter; files?: Task['files']; dirty?: boolean; renderOriginal?: OriginalRenderer}) {
  const [originalSource, setOriginalSource] = useState<PrecheckFact['evidence'][number] | null>(null)
  const [original, setOriginal] = useState<components['schemas']['FileVersion'] | null>(null)
  const originalTrigger = useRef<HTMLElement | null>(null)
  const [observed, setObserved] = useState<PrecheckReceipt | null>(null)
  const [history, setHistory] = useState<PrecheckReceipt[] | null>(null)
  const [selected, setSelected] = useState<PrecheckReceipt | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [pendingId, setPendingId] = useState(() => typeof window !== 'undefined' && readAuditRoute(new URL(location.href)).taskId === task.id ? new URL(location.href).searchParams.get('precheckRequest') : null)
  const pending = useRef<{id: string; revision: number | null} | null>(pendingId ? {id: pendingId, revision: null} : null)
  const current = observed && observed.task_id === task.id && observed.input_revision === (task.input_revision ?? 0) && (!task.precheck || observed.id === task.precheck.id || observed.created_at > task.precheck.created_at) ? observed : task.precheck
  const snapshot = selected || current
  const isCurrent = Boolean(snapshot && snapshot.id === current?.id && precheckIsCurrent(snapshot, task) && !dirty)
  useEffect(() => {
    if (!current || !current.current || !['queued', 'leased'].includes(current.status) || selected || dirty || busy) return
    const id = current.id
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    async function read() {
      if (!alive) return
      try {
        const receipt = await adapter.read(task.id, id)
        if (!alive) return
        if (receipt.task_id !== task.id || receipt.id !== id) throw new Error('scope')
        setObserved(receipt)
        if (['queued', 'leased'].includes(receipt.status)) timer = setTimeout(() => void read(), 3000)
      } catch {if (alive) setError('预检状态暂时无法读取。请查看原检查，不会重新开始审核。')}
    }
    timer = setTimeout(() => void read(), 3000)
    return () => {alive = false; clearTimeout(timer)}
  }, [adapter, task.id, current?.id, current?.status, selected?.id, dirty, busy])
  async function readHistory() {
    if (busy) return
    setBusy(true); setError('')
    try {
      const rows = await adapter.history(task.id)
      if (rows.some(row => row.task_id !== task.id)) throw new Error('scope')
      setHistory(rows)
    } catch {setError('预检历史读取失败。当前文件和审核结果仍保留。')}
    finally {setBusy(false)}
  }
  async function readOrRetry(retry = false) {
    if (busy) return
    setBusy(true); setError('')
    const command = pending.current
    try {
      let receipt: PrecheckReceipt
      if (command) receipt = await adapter.read(task.id, command.id)
      else if (retry) {
        const request = {id: crypto.randomUUID(), revision: task.input_revision ?? 0}
        pending.current = request; setPendingId(request.id)
        rememberRequest(task.id, 'precheckRequest', request.id)
        receipt = await adapter.retry(task, request.id)
      } else if (snapshot) receipt = await adapter.read(task.id, snapshot.id)
      else return
      if (receipt.task_id !== task.id || pending.current && (('request_id' in receipt ? receipt.request_id : null) !== pending.current.id || pending.current.revision !== null && receipt.input_revision !== pending.current.revision)) throw new Error('scope')
      pending.current = null; setPendingId(null)
      const url = new URL(location.href)
      if (readAuditRoute(url).taskId === task.id) {url.searchParams.delete('precheckRequest'); writeAuditAddress(url)}
      setObserved(receipt); setSelected(precheckIsCurrent(receipt, task) ? null : receipt)
    } catch {setError('预检结果尚未核对。请查看原检查，已有文件仍保留。')}
    finally {setBusy(false)}
  }
  async function download() {
    if (!snapshot || busy) return
    const id = snapshot.id
    setBusy(true); setError('')
    try {
      const blob = await adapter.report(task.id, id)
      const url = URL.createObjectURL(blob), link = document.createElement('a')
      link.href = url; link.download = `PPAP-预检-${id}.html`; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {setError('这份预检记录暂时无法导出，请重新读取同一记录。')}
    finally {setBusy(false)}
  }
  const originals = snapshot ? precheckOriginals(snapshot) : []
  const files = workingFiles.filter(file => file.selection === 'selected')
  const actionFor = (file: Task['files'][number]) => {
    if (dirty) return 'confirm'
    const facts = isCurrent ? precheckFacts(snapshot, file.file.id) : []
    return preparationAction(file, facts)
  }
  const groups = (['ready', 'process', 'confirm'] as const).map(action => ({action, files: files.filter(file => actionFor(file) === action)}))
  const content = <>
    <div className="flex flex-wrap items-center justify-between gap-3"><div>{!task.round_id && <h2 className="font-semibold">资料准备与免费预检</h2>}<p className="mt-1 text-sm text-muted-foreground">预检不扣点。正式审核按待审文件计费；比对资料不单独收费。</p></div>{snapshot && <Badge variant={snapshot.status === 'succeeded' ? 'success' : snapshot.status === 'failed' || snapshot.status === 'unknown' ? 'warning' : 'outline'}>{statusLabels[snapshot.status]}</Badge>}</div>
    {pendingId && <p role="status" className="text-sm">有预检请求待确认，请查看原检查。不会再次提交检查请求。</p>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {dirty && <p className="text-sm">资料或用途已改变，请重新识别；旧记录不代表本次资料。</p>}
    {snapshot && <p className="text-sm text-muted-foreground">输入修订 {snapshot.input_revision} · {isCurrent ? '当前资料' : '历史记录，不代表当前资料'}{snapshot.status === 'succeeded' ? ' · 部分要求可能尚未核实，检查完成不等于审核合格。' : ' · 尚无完整检查结论。'}</p>}
    {!selected && <div className="grid gap-4 md:grid-cols-3">{groups.map(group => <section key={group.action} aria-label={`${preparationLabels[group.action]}文件`} className="space-y-2"><h3 className="text-sm font-medium">{preparationLabels[group.action]} · {group.files.length}</h3>{group.files.map(file => <div key={file.file.id} className="space-y-1 text-sm"><FileName name={file.file.original_filename}/><p className="text-xs text-muted-foreground">{checkerLabel(file, isCurrent ? snapshot : null)}</p>{(!isCurrent || snapshot?.status !== 'succeeded' || precheckFacts(snapshot, file.file.id).some(fact => !['satisfied', 'handoff'].includes(fact.status))) && <p className="text-xs text-muted-foreground">部分要求未核实</p>}</div>)}</section>)}</div>}
    {snapshot && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">查看检查依据与未覆盖要求</Button></CollapsibleTrigger><CollapsibleContent><div className="space-y-5 pt-3">{precheckFiles(snapshot).map(file => <section key={file.version} className="space-y-2"><h3 className="font-medium"><FileName name={file.name}/></h3>{renderOriginal && originals.some(row => row.id === file.version) && <Button variant="outline" size="sm" onClick={event => {originalTrigger.current = event.currentTarget; setOriginalSource(null); setOriginal(originals.find(row => row.id === file.version)!)} }>查看此版本原件</Button>}{precheckFacts(snapshot, file.version).length ? <Facts facts={precheckFacts(snapshot, file.version)} originals={originals} onOriginal={renderOriginal ? (file, source) => {originalTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; setOriginalSource(source); setOriginal(file)} : undefined}/> : <p className="text-sm text-muted-foreground">尚未取得该原件的检查事实。</p>}</section>)}</div></CollapsibleContent></Collapsible>}
    <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={busy} onClick={() => void readHistory()}>读取预检历史</Button>{snapshot && <Button variant="outline" size="sm" disabled={busy} onClick={() => void readOrRetry()}>查看原检查</Button>}{!pendingId && current && ['failed', 'cancelled'].includes(current.status) && !dirty && !task.round_id && <Button variant="outline" size="sm" disabled={busy} onClick={() => void readOrRetry(true)}>重新检查当前资料</Button>}{snapshot && ['succeeded', 'failed', 'cancelled'].includes(snapshot.status) && <Button variant="outline" size="sm" disabled={busy} onClick={() => void download()}>导出此预检记录</Button>}</div>
    <Sheet open={Boolean(original)} onOpenChange={open => {if (!open) setOriginal(null)}}><SheetContent className="overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-6xl" onCloseAutoFocus={event => {event.preventDefault(); if (originalTrigger.current?.isConnected) originalTrigger.current.focus()}}><SheetHeader><SheetTitle>预检原件对照</SheetTitle><SheetDescription>查看这份预检记录引用的原件版本；预检不代替正式审核。</SheetDescription></SheetHeader>{original && <div key={original.id}><FileName name={original.original_filename}/>{originalSource && <p className="my-3 text-sm">{evidenceLocation(originalSource.locator)} · {originalSource.excerpt || '未提供摘录'}</p>}{renderOriginal?.(original, null)}</div>}</SheetContent></Sheet>
    {history && <ul aria-label="预检历史" className="space-y-2 text-sm">{history.length ? history.map(row => <li key={row.id}><Button variant="link" size="sm" onClick={() => setSelected(row)}>修订 {row.input_revision} · {Number.isFinite(Date.parse(row.created_at)) ? displayTime(row.created_at) : row.created_at} · {statusLabels[row.status]}</Button></li>) : <li>尚无预检历史。</li>}{selected && <li><Button variant="link" size="sm" onClick={() => setSelected(null)}>返回当前资料</Button></li>}</ul>}
  </>
  // Once the audit has started, the precheck is history: it stays one click away instead of pushing the results down.
  // Once the audit has started the precheck is history: it lives behind one quiet button instead of in front of the results.
  if (task.round_id) return <Sheet><SheetTrigger asChild><Button variant="ghost" size="sm"><ClipboardList aria-hidden="true"/>预检记录</Button></SheetTrigger><SheetContent className="overflow-y-auto data-[side=right]:w-full data-[side=right]:max-w-3xl"><SheetHeader><SheetTitle>资料准备与免费预检</SheetTitle><SheetDescription className="sr-only">本任务开始审核前的免费预检记录</SheetDescription></SheetHeader><section aria-label="免费内容预检" className="space-y-4 px-4 pb-6">{content}</section></SheetContent></Sheet>
  return <section aria-label="免费内容预检" className="space-y-4 border-t py-5">{content}</section>
}
