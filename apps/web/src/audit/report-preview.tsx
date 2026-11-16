import {VerificationDisclosure} from './verification-disclosure'
import {lazy, Suspense, useEffect, useState, type ReactNode} from 'react'
import {Copy, Download, Info, Share2} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {reportShortLabel as labels} from './status-labels'
import {readReportPdf, type AuditReportAdapter, type AuditReportReference} from './report-contract'
import {auditReportFilename} from './result-projection'
import {showToast} from '../components/toast'
import {displayTime} from '../helpers'

const PdfSource = lazy(() => import('../journey/pdf-source').then(module => ({default: module.PdfSource})))

export function ReportTime({value}: {value: string}) {
  const instant = new Date(value)
  return Number.isNaN(instant.valueOf()) ? <span>待确认</span> : <time dateTime={value}>{displayTime(instant)}</time>
}

/** Sharing the report being read; it sits beside its download on every report page. */
export function ReportShareButton({disabled, onOpen}: {disabled: boolean; onOpen: (trigger: HTMLButtonElement) => void}) {
  return <Button size="sm" variant="outline" disabled={disabled} onClick={event => onOpen(event.currentTarget)}><Share2/>分享</Button>
}

/** The same fixed-artifact reader is used by P6 and P11; the current report's own actions (share, download) live only in its header. */
export function ReportPreview({name, rule, ruleVersion, reference, report, verification, ready, issue, adapter, navigation, information, actions, share, children}: {name: string; rule: string; ruleVersion?: string; verification?: components['schemas']['AuditCheckerVerification'] | null; reference: AuditReportReference | null; report?: components['schemas']['AuditFileReport'] | null; ready: boolean; issue: string; adapter?: AuditReportAdapter; navigation?: ReactNode; information?: ReactNode; actions?: ReactNode; share?: {disabled: boolean; onOpen: (trigger: HTMLButtonElement) => void}; children?: ReactNode}) {
  const [pdf, setPdf] = useState<{key: string; url: string; error: string} | null>(null), [reload, setReload] = useState(0)
  const pdfKey = ready && reference ? JSON.stringify([reference, report?.artifact]) : ''
  useEffect(() => {
    if (!pdfKey || !adapter) return
    let current = true, url = ''
    const [ref, artifact] = JSON.parse(pdfKey)
    void readReportPdf(adapter, ref, artifact).then(blob => {if (current) {url = URL.createObjectURL(blob); setPdf({key: pdfKey, url, error: ''})}}).catch(() => {if (current) setPdf({key: pdfKey, url: '', error: 'PDF 暂时无法读取，请重新读取。'})})
    return () => {current = false; if (url) URL.revokeObjectURL(url)}
  }, [pdfKey, adapter, reload])
  const currentPdf = pdf?.key === pdfKey ? pdf : null
  const generated = report?.generated_at ? <ReportTime value={report.generated_at}/> : report?.state === 'ready' ? '未记录' : '尚未生成'
  const trace = [ruleVersion && `规则版本标识：${ruleVersion}`, ...(reference ? [`任务标识：${reference.task_id}`, `审核轮次标识：${reference.round_id}`, `文件版本标识：${reference.file_version_id}`, `结果尝试：${reference.result_attempt}`, `报告标识：${reference.report_id}`] : [])].filter(Boolean).join('\n')
  const [copyIssue, setCopyIssue] = useState('')
  async function copyTrace() {
    try {await navigator.clipboard.writeText(trace); setCopyIssue(''); showToast('已复制追溯信息')}
    catch {setCopyIssue('浏览器未允许复制，请悬停在按钮上查看追溯信息。')}
  }
  function download() {if (currentPdf?.url && report?.artifact) {const link = document.createElement('a'); link.href = currentPdf.url; link.download = report.artifact.filename; link.click()}}
  return <section className="flex h-full min-w-0 flex-col overflow-hidden rounded-md border bg-card" aria-label="当前报告" data-file-version={reference?.file_version_id} data-result-attempt={reference?.result_attempt}>
    {/* One bar as in the prototype: what is being read, its name and when it was generated; the tools sit at its end. */}
    <header className="flex flex-wrap items-center gap-x-7 gap-y-2 border-b px-6 py-3 text-xs text-muted-foreground">{navigation}<p className="text-base font-semibold text-foreground">报告预览</p>
      <p className="flex min-w-0 gap-1"><span className="shrink-0">报告名称：</span><span className="[overflow-wrap:anywhere] text-foreground">{report?.artifact?.filename || auditReportFilename(name)}</span></p><h2 className="sr-only">{name}</h2>
      <p className="flex shrink-0 gap-1"><span>生成时间：</span><span className="text-foreground">{generated}</span></p>
      <div className="ml-auto flex flex-wrap items-center gap-1">
      <Sheet><SheetTrigger asChild><Button variant="ghost" size="sm"><Info/>报告信息</Button></SheetTrigger><SheetContent><SheetHeader><SheetTitle>报告信息</SheetTitle><SheetDescription>查看本报告的来源与审核记录。</SheetDescription></SheetHeader>
        <div className="min-h-0 space-y-6 overflow-y-auto px-4 pb-6 text-sm">
          <div className="space-y-1 border-b pb-5"><p className="text-xs text-muted-foreground">原始文件</p><h3 className="[overflow-wrap:anywhere] text-base font-semibold">{name}</h3></div>
          <dl className="grid gap-4 [&>div]:grid [&>div]:grid-cols-[5rem_minmax(0,1fr)] [&>div]:gap-3 [&_dt]:text-muted-foreground [&_dd]:[overflow-wrap:anywhere]">
            {information}<div><dt>审核规则</dt><dd>{rule}</dd></div><div><dt>生成时间</dt><dd>{generated}</dd></div>
          </dl>
          {trace && <div className="space-y-2 border-t pt-4">
            {/* Internal identifiers stay out of the page; support can still take them from the hover hint or the clipboard. */}
            <Button variant="outline" size="sm" title={trace} onClick={() => void copyTrace()}><Copy/>复制追溯信息</Button>
            {copyIssue && <p role="alert" className="text-sm text-destructive">{copyIssue}</p>}
          </div>}
        </div>
      </SheetContent></Sheet>
      {actions}{share && <ReportShareButton {...share}/>}<Button size="sm" variant="outline" disabled={!currentPdf?.url} onClick={download}><Download/>下载当前报告</Button>
    </div>
    </header>
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
    {!issue && <VerificationDisclosure verification={verification}/>}
    {(!ready || issue) && <Badge className="self-start" variant="outline">{issue ? '结果待确认' : !report ? '尚未取得结果' : report.state === 'ready' ? '产物待确认' : labels[report.state]}</Badge>}
    {issue ? <Alert variant="destructive"><AlertTitle>不能展示正式报告</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert> : report?.reason ? <p className="text-sm text-muted-foreground">{report.reason}</p> : null}{children}
    {ready && adapter ? currentPdf?.error ? <Alert variant="destructive"><AlertTitle>无法读取 PDF</AlertTitle><AlertDescription><p>{currentPdf.error}</p><Button variant="outline" size="sm" onClick={() => {setPdf(null); setReload(value => value + 1)}}>重新读取 PDF</Button></AlertDescription></Alert> : currentPdf?.url ? <div className="flex min-w-0 flex-1 justify-center rounded-sm bg-muted p-6">{/* The fixed PDF reads as one paper sheet over a muted desk. */}<div className="h-[65dvh] min-h-96 w-full max-w-3xl overflow-hidden bg-card shadow-md"><Suspense fallback={<ReadSkeleton label="正在加载 PDF 阅读器…" kind="document"/>}><PdfSource key={currentPdf.url} url={currentPdf.url} name={report!.artifact!.filename}/></Suspense></div></div> : <ReadSkeleton label="正在读取 PDF…" kind="document" className="min-h-96 flex-1"/> : ready ? <p className="text-sm text-muted-foreground">暂时无法读取报告。</p> : null}
    </div>
  </section>
}
