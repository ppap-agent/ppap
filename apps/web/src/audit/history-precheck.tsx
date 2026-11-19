import {lazy, Suspense, useEffect, useId, useRef, useState} from 'react'
import {BookOpen, Download, Loader2} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {ReadSkeleton} from '../components/read-skeleton'
import {precheckFileName, type PrecheckAdapter} from './precheck'

const ReportReader = lazy(() => import('./report-reader').then(module => ({default: module.ReportReader})))

/** P10: the precheck report of one historical audit, read from the precheck frozen with that task's acceptance (IX-10). */
export function HistoryPrecheck({adapter, taskId, label}: {adapter?: PrecheckAdapter; taskId: string; label: string}) {
  const describedBy = useId()
  const [url, setUrl] = useState(''), [generatedAt, setGeneratedAt] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [open, setOpen] = useState(false)
  const created = useRef(''), name = useRef('预检报告.pdf')
  useEffect(() => () => {if (created.current) URL.revokeObjectURL(created.current)}, [])
  // The same task always reads the same frozen precheck, so preview and download share one PDF.
  async function read(): Promise<string> {
    if (url) return url
    if (!adapter) return ''
    setBusy(true); setError('')
    try {
      const [blob, result] = await Promise.all([adapter.report(taskId), adapter.read(taskId)])
      setGeneratedAt(result.generated_at)
      const next = URL.createObjectURL(blob)
      name.current = precheckFileName(blob); created.current = next; setUrl(next)
      return next
    } catch {setError('预检报告暂时无法读取，可以重试。'); return ''} finally {setBusy(false)}
  }
  async function preview() {if (await read()) setOpen(true)}
  async function download() {
    const target = await read()
    if (!target) return
    const link = document.createElement('a'); link.href = target; link.download = name.current; link.click()
  }
  // IX-10.05: with no precheck report to trace, the entries stay greyed with the reason on hover.
  const missing = adapter ? undefined : '暂无可追溯预检报告'
  return <div className="grid gap-1 px-2 pb-1">
    <span id={describedBy} className="sr-only">{label}</span>
    <div className="flex flex-wrap gap-1" title={missing}>
      <Button variant="ghost" size="xs" aria-describedby={describedBy} disabled={!adapter || busy} onClick={() => void preview()}>{busy ? <Loader2 className="animate-spin motion-reduce:animate-none"/> : <BookOpen/>}预览预检报告</Button>
      <Button variant="ghost" size="xs" aria-describedby={describedBy} disabled={!adapter || busy} onClick={() => void download()}><Download/>下载预检报告</Button>
    </div>
    {busy && <p role="status" className="text-xs text-muted-foreground">正在读取预检报告…</p>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    <Dialog open={open && Boolean(url)} onOpenChange={setOpen}>
      <DialogContent className="flex h-[88dvh] flex-col overflow-hidden sm:max-w-6xl">
        <div className="flex flex-wrap items-end justify-between gap-2 pr-8"><DialogHeader><DialogTitle>{name.current}</DialogTitle><DialogDescription>预检报告 · {label}{generatedAt ? ` · 生成时间 ${generatedAt}` : ''}</DialogDescription></DialogHeader><Button size="sm" variant="outline" onClick={() => void download()}><Download/>下载报告</Button></div>
        <div className="min-h-0 flex-1 overflow-hidden rounded-sm border">{url && <Suspense fallback={<ReadSkeleton label="正在加载报告阅读器…" kind="document"/>}><ReportReader url={url} name={name.current} kind="预检报告"/></Suspense>}</div>
      </DialogContent>
    </Dialog>
  </div>
}
