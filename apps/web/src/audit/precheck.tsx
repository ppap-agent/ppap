import {lazy, Suspense, useEffect, useRef, useState} from 'react'
import {faCircleNotch, faDownload, faEye} from '@fortawesome/free-solid-svg-icons'
import {Icon} from '../components/ui/icon'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {ReadSkeleton} from '../components/read-skeleton'
import {precheckReportChapters} from './report-chapters'

const ReportReader = lazy(() => import('./report-reader').then(module => ({default: module.ReportReader})))

export type Precheck = components['schemas']['AuditTaskPrecheck']
type Task = components['schemas']['AuditTask']
export type PrecheckAdapter = {read: (taskId: string, signal?: AbortSignal) => Promise<Precheck>; report: (taskId: string) => Promise<Blob>}
/** The delivered file name (XL-26 「{任务编号}-预检报告.pdf」) when the report carries one. */
export const precheckFileName = (blob: Blob) => blob instanceof File && blob.name ? blob.name : '预检报告.pdf'

/** The precheck follows the recognized task; a changed file set reads it again. */
export function usePrecheck(adapter: PrecheckAdapter | undefined, task: Task | null | undefined, enabled: boolean) {
  const [state, setState] = useState<{key: string; result: Precheck | null; error: string}>({key: '', result: null, error: ''})
  const key = task ? `${task.id}:${task.state}:${task.files.map(row => `${row.file.id}.${row.selection}.${row.match?.state ?? ''}.${row.replacement_of_file_version_id ?? ''}`).join('|')}` : ''
  useEffect(() => {
    if (!adapter || !task || !enabled) return
    const controller = new AbortController()
    adapter.read(task.id, controller.signal).then(result => setState({key, result, error: ''})).catch(() => {if (!controller.signal.aborted) setState({key, result: null, error: '预检结果暂时无法读取，不影响识别与审核。'})})
    return () => controller.abort()
  }, [adapter, key, enabled]) // the key captures every task change the precheck depends on
  return state.key === key ? state : {key, result: null, error: ''}
}

/** PRD §2.11 四, as the report's 总评: a pass with warnings is 有条件通过 and always names their count; 未执行 has no rating. */
export function precheckRating(result: Pick<Precheck, 'conclusion' | 'warnings' | 'files'>): string {
  if (result.conclusion === 'fail') return '不通过：存在阻断项'
  if (result.conclusion === 'not_run') return result.files.length ? '未执行：识别尚未完成' : '未执行：没有送审文件'
  return result.warnings.length ? `有条件通过 · 含警告 ${result.warnings.length} 项：可以进入正式审核` : '通过：可以进入正式审核'
}

/** What the precheck found for one file, under its name in the matching list. */
export function PrecheckNotes({result, fileVersionId}: {result: Precheck | null; fileVersionId: string}) {
  if (!result) return null
  const items = [...result.blocks, ...result.warnings].filter(item => item.file_version_id === fileVersionId)
  // A warning never holds the start; a file-level block does until the file is removed; a content-level block goes to the formal audit.
  const effect = (item: Precheck['blocks'][number]) => item.level === 'warn' ? '仍可勾选送审' : item.scope === 'file' ? '移除后才能开始审核' : '不阻止开始审核，正式审核将核对'
  return items.length > 0 && <ul aria-label="预检发现" className="space-y-1.5 rounded-md border bg-card p-2.5 text-xs">{items.map(item => <li key={item.number} className="flex items-start gap-2"><span className={`shrink-0 rounded-sm px-1.5 text-2xs leading-4 font-semibold text-primary-foreground ${item.level === 'block' ? 'bg-destructive' : 'bg-warning'}`}>{item.level === 'block' ? '阻断' : '警告'}</span><span className="min-w-0"><span className="font-medium">{item.code} {item.title}</span>：{item.problem}（{effect(item)}）<span className="text-muted-foreground"> 整改：{item.action}</span></span></li>)}</ul>
}

/** The cover-and-ten-chapter report of this precheck: read from the same precheck the page shows, never billed, never filed with audit results. */
export function PrecheckReport({adapter, taskId, result}: {adapter: PrecheckAdapter; taskId: string; result: Precheck}) {
  // One PDF per precheck result: preview and download read the same bytes; a new result drops it.
  const key = JSON.stringify([taskId, result])
  const [pdf, setPdf] = useState<{key: string; url: string; name: string} | null>(null), names = useRef(new Map<string, string>()), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [target, setTarget] = useState<{index: number; nonce: number} | null>(null)
  const urls = useRef<string[]>([])
  useEffect(() => () => urls.current.forEach(url => URL.revokeObjectURL(url)), [])
  const current = pdf?.key === key ? pdf.url : ''
  async function read(): Promise<string> {
    if (current) return current
    setBusy(true); setError('')
    try {
      const blob = await adapter.report(taskId)
      const url = URL.createObjectURL(blob)
      names.current.set(url, precheckFileName(blob)); urls.current.push(url); setPdf({key, url, name: precheckFileName(blob)})
      return url
    } catch {setError('预检报告暂时无法生成，预检结果不受影响，可以重试。'); return ''} finally {setBusy(false)}
  }
  async function open(index: number) {if (await read()) setTarget({index, nonce: Date.now()})}
  async function download() {
    const url = await read()
    if (!url) return
    const link = document.createElement('a'); link.href = url; link.download = names.current.get(url) || '预检报告.pdf'; link.click()
  }
  const conclusion = precheckRating(result)
  // A card after the file sections: the conclusion with preview and download, then the chapters, each opening the reader at its page.
  return <section aria-label="预检报告" className="space-y-3 rounded-lg border bg-card p-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2 className={`text-sm font-semibold ${result.conclusion === 'fail' ? 'text-destructive' : ''}`}>预检报告 · {conclusion}</h2>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void open(0)}>{busy ? <Icon icon={faCircleNotch} className="animate-spin motion-reduce:animate-none"/> : <Icon icon={faEye}/>}预览预检报告</Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void download()}><Icon icon={faDownload}/>下载预检报告(PDF)</Button>
      </div></div>
    <ol aria-label="预检报告目录" className="flex flex-wrap gap-1.5">{precheckReportChapters.map((title, index) => <li key={title} className="min-w-0"><button type="button" disabled={busy} title={title} className="max-w-full truncate rounded-md border bg-header px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-ring disabled:opacity-60" onClick={() => void open(index)}>{title}</button></li>)}</ol>
    {busy && <p role="status" className="text-xs text-muted-foreground">正在生成预检报告…</p>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    <p className="text-xs text-muted-foreground">预览和下载不会开始审核，也不收费。</p>
    <Dialog open={Boolean(target && current)} onOpenChange={value => {if (!value) setTarget(null)}}>
      <DialogContent className="flex h-[88dvh] flex-col overflow-hidden sm:max-w-6xl">
        <DialogHeader><DialogTitle>{pdf?.name || '预检报告.pdf'}</DialogTitle><DialogDescription>预检报告 · 生成时间 {result.generated_at} · {conclusion}</DialogDescription></DialogHeader>
        <div className="min-h-0 flex-1 overflow-hidden rounded-sm border">{current && <Suspense fallback={<ReadSkeleton label="正在加载报告阅读器…" kind="document"/>}><ReportReader url={current} name={pdf?.name || '预检报告.pdf'} kind="预检报告" target={target}/></Suspense>}</div>
      </DialogContent>
    </Dialog>
  </section>
}
