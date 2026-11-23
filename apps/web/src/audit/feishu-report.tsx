// The audit as a report document in the person's own Feishu space (#348): a signing moment, so the document written is
// exactly the one confirmed here (`if_match`); a report that changed meanwhile is shown as changed, not sent.
import {useState} from 'react'
import type {components} from '../generated/openapi'
import {request} from '../api'
import {Button} from '../components/ui/button'
import {Alert, AlertDescription} from '../components/ui/alert'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'

type Envelope = components['schemas']['ReportEnvelope']

export function FeishuReportDocument({roundId}: {roundId: string}) {
  const path = `/integrations/feishu/rounds/${encodeURIComponent(roundId)}/report`
  const [current, setCurrent] = useState<Envelope | null>(null)
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [problem, setProblem] = useState('')
  async function read() {
    try {setCurrent(await request<Envelope>(path))}
    catch (cause) {setProblem(cause instanceof Error ? cause.message : '暂时无法读取报告文档状态。')}
  }
  async function write() {
    if (!current?.content_fingerprint) return
    setBusy(true); setProblem('')
    try {setCurrent(await request<Envelope>(path, 'POST', {if_match: current.content_fingerprint}))}
    catch (cause) {
      setProblem(cause instanceof Error ? cause.message : '没有生成，请稍后重试。')
      if ((cause as {code?: string}).code === 'content_changed') await read()
    } finally {setBusy(false)}
  }
  const report = current?.report
  const documentUrl = report?.status === 'ready' && report.document_id ? `https://feishu.cn/docx/${encodeURIComponent(report.document_id)}` : ''
  const pending = report && ['creating', 'writing', 'unknown'].includes(report.status)
  return <>
    <Button size="sm" variant="outline" onClick={() => {setProblem(''); setOpen(true); void read()}}>在飞书里生成报告文档</Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>在飞书里生成报告文档</DialogTitle>
          <DialogDescription>在你的飞书云空间新建一份文档，写入本次审核的结论和逐项结果，内容与本页一致。之后可以在飞书里分享给同事。</DialogDescription>
        </DialogHeader>
        {documentUrl ? <p className="text-sm">这份报告已经生成过。<a className="text-primary underline-offset-4 hover:underline" href={documentUrl} target="_blank" rel="noreferrer">在飞书里打开</a></p>
          : pending ? <p className="text-sm text-muted-foreground">{report?.status === 'unknown' ? '上次生成的结果还不确定，文档可能已在你的飞书云空间里。请先在飞书里查看，系统不会重复生成。' : '报告文档正在生成，请稍后再看。'}</p> : null}
        {problem && <Alert variant="destructive"><AlertDescription>{problem}</AlertDescription></Alert>}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>{documentUrl ? '关闭' : '取消'}</Button>
          {!documentUrl && !pending && <Button disabled={busy || !current?.content_fingerprint} onClick={() => void write()}>生成</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </>
}
