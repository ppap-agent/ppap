import {lazy, Suspense, useEffect, useState, type ReactNode} from 'react'
import {iconCircleInfo, iconDownload, iconShareNodes} from '../components/icons'
import {Icon} from '../components/ui/icon'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import {reportShortLabel as labels} from './status-labels'
import {readReportPdf, type AuditReportAdapter, type AuditReportReference} from './report-contract'
import {auditReportFilename} from './result-projection'
import {displayTime} from '../helpers'
import {host} from '../host'

const ReportReader = lazy(() => import('./report-reader').then(module => ({default: module.ReportReader})))

export function ReportTime({value}: {value: string}) {
  const instant = new Date(value)
  return Number.isNaN(instant.valueOf()) ? <span>待确认</span> : <time dateTime={value}>{displayTime(instant)}</time>
}

/** Sharing the report being read; it sits beside its download on every report page. */
export function ReportShareButton({disabled, onOpen}: {disabled: boolean; onOpen: (trigger: HTMLButtonElement) => void}) {
  return <Button size="sm" variant="outline" disabled={disabled} onClick={event => onOpen(event.currentTarget)}><Icon icon={iconShareNodes}/>分享</Button>
}

/** The same fixed-artifact reader is used by P6 and P11; the current report's own actions (share, download) live only in its header. */
export function ReportPreview({name, rule, reference, report, ready, issue, adapter, navigation, information, actions, share, children}: {name: string; rule: string; reference: AuditReportReference | null; report?: components['schemas']['AuditFileReport'] | null; ready: boolean; issue: string; adapter?: AuditReportAdapter; navigation?: ReactNode; information?: ReactNode; actions?: ReactNode; share?: {disabled: boolean; onOpen: (trigger: HTMLButtonElement) => void}; children?: ReactNode}) {
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
  function download() {if (currentPdf?.url && report?.artifact) {const link = document.createElement('a'); link.href = currentPdf.url; link.download = report.artifact.filename; link.click()}}
  return <section className="flex h-full min-w-0 flex-col overflow-hidden rounded-md border bg-card" aria-label="当前报告" data-file-version={reference?.file_version_id} data-result-attempt={reference?.result_attempt}>
    {/* One bar as in the prototype: what is being read, its name and when it was generated; the tools sit at its end. */}
    <header className="flex flex-wrap items-center gap-x-7 gap-y-2 border-b px-6 py-3 text-xs text-muted-foreground">{navigation}<p className="text-base font-semibold text-foreground">报告预览</p>
      <p className="flex min-w-0 gap-1"><span className="shrink-0">报告名称：</span><span className="[overflow-wrap:anywhere] text-foreground">{report?.artifact?.filename || auditReportFilename(name)}</span></p><h2 className="sr-only">{name}</h2>
      <p className="flex shrink-0 gap-1"><span>生成时间：</span><span className="text-foreground">{generated}</span></p>
      {ready && report?.fingerprint && <p className="flex shrink-0 gap-1" title="报告 PDF 的 SHA-256 前 12 位；飞书报告文档文首的指纹与此一致，即为同一份报告"><span>指纹：</span><span className="tabular-nums text-foreground">{report.fingerprint}</span></p>}
      <div className="ml-auto flex flex-wrap items-center gap-1">
      <Dialog><DialogTrigger asChild><Button variant="ghost" size="sm"><Icon icon={iconCircleInfo}/>报告信息</Button></DialogTrigger><DialogContent><DialogHeader><DialogTitle>报告信息</DialogTitle><DialogDescription>查看本报告的来源与审核记录。</DialogDescription></DialogHeader>
        <div className="min-h-0 space-y-6 overflow-y-auto px-4 pb-6 text-sm">
          {/* Only what the user needs about this report: its name, when it was made, the original file and the rule. */}
          <dl className="grid gap-4 [&>div]:grid [&>div]:grid-cols-[5rem_minmax(0,1fr)] [&>div]:gap-3 [&_dt]:text-muted-foreground [&_dd]:[overflow-wrap:anywhere]">
            <div><dt>报告名称</dt><dd>{report?.artifact?.filename || auditReportFilename(name)}</dd></div><div><dt>生成时间</dt><dd>{generated}</dd></div><div><dt>原始文件</dt><dd>{name}</dd></div><div><dt>审核规则</dt><dd>{rule}</dd></div>{information}
          </dl>
        </div>
      </DialogContent></Dialog>
      {actions}{share && <ReportShareButton {...share}/>}{host.browserDownload && <Button size="sm" variant="outline" disabled={!currentPdf?.url} onClick={download}><Icon icon={iconDownload}/>下载当前报告</Button>}
    </div>
    </header>
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
    {(!ready || issue) && <Badge className="self-start" variant="outline">{issue ? '结果待确认' : !report ? '尚未取得结果' : report.state === 'ready' ? '产物待确认' : labels[report.state]}</Badge>}
    {issue ? <Alert variant="destructive"><AlertTitle>不能展示正式报告</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert> : report?.reason ? <p className="text-sm text-muted-foreground">{report.reason}</p> : null}{children}
    {ready && adapter ? currentPdf?.error ? <Alert variant="destructive"><AlertTitle>无法读取 PDF</AlertTitle><AlertDescription><p>{currentPdf.error}</p><Button variant="outline" size="sm" onClick={() => {setPdf(null); setReload(value => value + 1)}}>重新读取 PDF</Button></AlertDescription></Alert> : currentPdf?.url ? <div className="h-[70dvh] min-h-96 overflow-hidden rounded-sm border">{/* Contents on the left, the fixed PDF on the right. */}<Suspense fallback={<ReadSkeleton label="正在加载报告阅读器…" kind="document"/>}><ReportReader key={currentPdf.url} url={currentPdf.url} name={report!.artifact!.filename} kind="审核报告"/></Suspense></div> : <ReadSkeleton label="正在读取 PDF…" kind="document" className="min-h-96 flex-1"/> : ready ? <p className="text-sm text-muted-foreground">暂时无法读取报告。</p> : null}
    </div>
  </section>
}
