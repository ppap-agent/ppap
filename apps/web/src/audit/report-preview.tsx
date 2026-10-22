import {lazy, Suspense, useEffect, useState, type ReactNode} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {readReportPdf, type AuditReportAdapter, type AuditReportReference} from './report-contract'

const PdfSource = lazy(() => import('../journey/pdf-source').then(module => ({default: module.PdfSource})))
const labels = {not_eligible: '无正式报告', pending: '待生成', generating: '生成中', ready: '就绪', failed: '生成失败'}

export function ReportTime({value}: {value: string}) {
  const instant = new Date(value)
  return Number.isNaN(instant.valueOf()) ? <span>待核对</span> : <time dateTime={value}>{instant.toLocaleString('zh-CN', {hour12: false, timeZoneName: 'short'})}</time>
}

/** The same fixed-artifact reader is used by P6 and P11. */
export function ReportPreview({name, rule, reference, report, ready, issue, adapter, children}: {name: string; rule: string; reference: AuditReportReference | null; report?: components['schemas']['AuditFileReport'] | null; ready: boolean; issue: string; adapter?: AuditReportAdapter; children?: ReactNode}) {
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
  return <section className="min-w-0 space-y-4" aria-label="当前报告" data-file-version={reference?.file_version_id} data-result-attempt={reference?.result_attempt}><div className="flex flex-wrap items-start justify-between gap-3"><h2 className="min-w-0 flex-1 break-all text-lg font-semibold">{name}-审核报告</h2><Button disabled={!currentPdf?.url} onClick={download}>下载当前报告</Button></div><Badge variant="outline">{issue ? '结果待核对' : !report ? '尚未取得结果' : report.state === 'ready' && !ready ? '产物待核对' : labels[report.state]}</Badge>{report?.generated_at && <p className="text-xs text-muted-foreground">生成时间：<ReportTime value={report.generated_at}/></p>}<p className="text-xs text-muted-foreground">审核规则：{rule}</p>{issue ? <Alert variant="destructive"><AlertTitle>不能展示正式报告</AlertTitle><AlertDescription>{issue}</AlertDescription></Alert> : report?.reason ? <p className="text-muted-foreground">{report.reason}</p> : null}{children}{ready && adapter ? currentPdf?.error ? <Alert variant="destructive"><AlertTitle>无法读取 PDF</AlertTitle><AlertDescription>{currentPdf.error}<Button variant="outline" onClick={() => {setPdf(null); setReload(value => value + 1)}}>重新读取 PDF</Button></AlertDescription></Alert> : currentPdf?.url ? <div className="h-[65dvh] min-h-96 overflow-hidden border"><Suspense fallback={<p role="status">正在加载 PDF 阅读器…</p>}><PdfSource key={currentPdf.url} url={currentPdf.url} name={report!.artifact!.filename}/></Suspense></div> : <p role="status">正在读取 PDF…</p> : ready ? <p>暂时无法读取报告。</p> : null}</section>
}
