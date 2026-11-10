import {VerificationDisclosure} from './verification-disclosure'
import {lazy, Suspense, useEffect, useState, type ReactNode} from 'react'
import {Download, Info} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger} from '../components/ui/sheet'
import {reportShortLabel as labels} from './status-labels'
import {readReportPdf, type AuditReportAdapter, type AuditReportReference} from './report-contract'
import {Disclosure} from '../components/disclosure'
import {displayTime} from '../helpers'

const PdfSource = lazy(() => import('../journey/pdf-source').then(module => ({default: module.PdfSource})))

export function ReportTime({value}: {value: string}) {
  const instant = new Date(value)
  return Number.isNaN(instant.valueOf()) ? <span>待确认</span> : <time dateTime={value}>{displayTime(instant)}</time>
}

/** The same fixed-artifact reader is used by P6 and P11. */
export function ReportPreview({name, rule, ruleVersion, reference, report, verification, ready, issue, adapter, navigation, information, actions, children}: {name: string; rule: string; ruleVersion?: string; verification?: components['schemas']['AuditCheckerVerification'] | null; reference: AuditReportReference | null; report?: components['schemas']['AuditFileReport'] | null; ready: boolean; issue: string; adapter?: AuditReportAdapter; navigation?: ReactNode; information?: ReactNode; actions?: ReactNode; children?: ReactNode}) {
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
  function download() {if (currentPdf?.url && report?.artifact) {const link = document.createElement('a'); link.href = currentPdf.url; link.download = report.artifact.filename; link.click()}}
  return <section className="flex h-full min-w-0 flex-col gap-3" aria-label="当前报告" data-file-version={reference?.file_version_id} data-result-attempt={reference?.result_attempt}>
    <header className="flex flex-wrap items-center gap-2">{navigation}<h2 className="min-w-0 flex-1 basis-48 [overflow-wrap:anywhere] text-base font-semibold">{name}</h2><div className="flex flex-wrap items-center gap-1">
      <Sheet><SheetTrigger asChild><Button variant="ghost" size="sm"><Info/>报告信息</Button></SheetTrigger><SheetContent><SheetHeader><SheetTitle>报告信息</SheetTitle><SheetDescription>查看本报告的来源与审核记录。</SheetDescription></SheetHeader>
        <div className="min-h-0 space-y-6 overflow-y-auto px-4 pb-6 text-sm">
          <div className="space-y-1 border-b pb-5"><p className="text-xs text-muted-foreground">原始文件</p><h3 className="[overflow-wrap:anywhere] text-base font-semibold">{name}</h3></div>
          <dl className="grid gap-4 [&>div]:grid [&>div]:grid-cols-[5rem_minmax(0,1fr)] [&>div]:gap-3 [&_dt]:text-muted-foreground [&_dd]:[overflow-wrap:anywhere]">
            {information}<div><dt>审核规则</dt><dd>{rule}</dd></div><div><dt>生成时间</dt><dd>{report?.generated_at ? <ReportTime value={report.generated_at}/> : report?.state === 'ready' ? '未记录' : '尚未生成'}</dd></div>
          </dl>
          {(reference || ruleVersion) && <Disclosure className="border-t pt-4" summary={<>追溯信息</>}><dl className="mt-4 grid gap-4 [&_dt]:mb-1 [&_dt]:text-xs [&_dt]:text-muted-foreground [&_dd]:[overflow-wrap:anywhere] [&_dd]:font-mono [&_dd]:text-xs">{ruleVersion && <div><dt>规则版本标识</dt><dd>{ruleVersion}</dd></div>}{reference && <><div><dt>任务标识</dt><dd>{reference.task_id}</dd></div><div><dt>审核轮次标识</dt><dd>{reference.round_id}</dd></div><div><dt>文件版本标识</dt><dd>{reference.file_version_id}</dd></div><div><dt>结果尝试</dt><dd>{reference.result_attempt}</dd></div><div><dt>报告标识</dt><dd>{reference.report_id}</dd></div></>}</dl></Disclosure>}
        </div>
      </SheetContent></Sheet>
      {actions}<Button size="sm" disabled={!currentPdf?.url} onClick={download}><Download/>下载当前报告</Button>
    </div></header>
    {!issue && <VerificationDisclosure verification={verification}/>}
    {(!ready || issue) && <Badge className="self-start" variant="outline">{issue ? '结果待确认' : !report ? '尚未取得结果' : report.state === 'ready' ? '产物待确认' : labels[report.state]}</Badge>}
    {issue ? <Alert variant="destructive"><AlertTitle>不能展示正式报告</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert> : report?.reason ? <p className="text-muted-foreground">{report.reason}</p> : null}{children}
    {ready && adapter ? currentPdf?.error ? <Alert variant="destructive"><AlertTitle>无法读取 PDF</AlertTitle><AlertDescription>{currentPdf.error}<Button variant="outline" onClick={() => {setPdf(null); setReload(value => value + 1)}}>重新读取 PDF</Button></AlertDescription></Alert> : currentPdf?.url ? <div className="h-[65dvh] min-h-96 min-w-0 flex-1 overflow-hidden rounded-md border"><Suspense fallback={<ReadSkeleton label="正在加载 PDF 阅读器…" kind="document"/>}><PdfSource key={currentPdf.url} url={currentPdf.url} name={report!.artifact!.filename}/></Suspense></div> : <ReadSkeleton label="正在读取 PDF…" kind="document" className="min-h-96 flex-1"/> : ready ? <p>暂时无法读取报告。</p> : null}
  </section>
}
