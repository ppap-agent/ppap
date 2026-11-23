// The audit as a report document in the person's own Feishu space (#348): a signing moment, so the document written is
// exactly the one confirmed here (`if_match`); a report that changed meanwhile is shown as changed, not sent. The same
// delivery sets it read-only for others and attaches the frozen report PDFs (#359); a step that failed says so, and the
// person retries it here.
import {useState} from 'react'
import type {components} from '../generated/openapi'
import {apiUrl, request} from '../api'
import {Button} from '../components/ui/button'
import {Alert, AlertDescription} from '../components/ui/alert'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'

type Envelope = components['schemas']['ReportEnvelope']
type Export = components['schemas']['ReportExport']
type Step = NonNullable<Export['retry']>[number]

const readOnlyText: Record<components['schemas']['ReportReadOnly']['state'], string> = {
  set: '已设为只读：企业内的人凭链接可以阅读，不能编辑，也不对外分享。',
  working: '正在设为只读…',
  failed: '未能设为只读。',
  unknown: '未能确认是否已设为只读。',
}
const attachmentText: Record<components['schemas']['ReportAttachment']['state'], string> = {attached: '已附上', working: '正在附上', failed: '未能附上', unknown: '未能确认是否已附上'}

/** Signing in again for the report asks for what its steps need (#359); the person comes back to this page. */
const SignInForReport = () => <Button asChild size="sm" variant="outline"><a href={`${apiUrl}/login/feishu?${new URLSearchParams({purpose: 'report', return_to: location.href})}`}>重新登录飞书</a></Button>

function Delivery({report, busy, onRetry}: {report: Export; busy: boolean; onRetry: (step: Step) => void}) {
  const retry = report.retry ?? []
  return <div className="space-y-3 text-sm">
    {report.read_only && <div className="flex flex-wrap items-center gap-2"><p className={['failed', 'unknown'].includes(report.read_only.state) ? 'text-destructive' : 'text-muted-foreground'}>{readOnlyText[report.read_only.state]}</p>{retry.includes('read_only') && <Button size="sm" variant="outline" disabled={busy} onClick={() => onRetry('read_only')}>重试设置只读</Button>}</div>}
    {report.status === 'ready' && report.attachments && report.attachments.length > 0 && <div className="space-y-1">
      <p className="text-muted-foreground">文末附上的报告 PDF，指纹与 PPAP 报告页一致：</p>
      <ul className="space-y-1">{report.attachments.map(item => <li key={item.fingerprint + item.filename} className="flex flex-wrap gap-x-2"><span className="[overflow-wrap:anywhere]">{item.filename}</span><span className="tabular-nums text-muted-foreground">指纹 {item.fingerprint}</span><span className={['failed', 'unknown'].includes(item.state) ? 'text-destructive' : 'text-muted-foreground'}>{attachmentText[item.state]}</span></li>)}</ul>
      {retry.includes('attachments') && <Button size="sm" variant="outline" disabled={busy} onClick={() => onRetry('attachments')}>重试附上报告 PDF</Button>}
    </div>}
  </div>
}

export function FeishuReportDocument({roundId}: {roundId: string}) {
  const path = `/integrations/feishu/rounds/${encodeURIComponent(roundId)}/report`
  const [current, setCurrent] = useState<Envelope | null>(null)
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [problem, setProblem] = useState(''), [signIn, setSignIn] = useState(false)
  async function read() {
    try {setCurrent(await request<Envelope>(path))}
    catch (cause) {setProblem(cause instanceof Error ? cause.message : '暂时无法读取报告文档状态。')}
  }
  function failed(cause: unknown, fallback: string) {
    setProblem(cause instanceof Error ? cause.message : fallback)
    // No Feishu token, or a proposal completed after a while (#355): signing in for the report does both at once.
    setSignIn(['feishu_user_token_missing', 'reauthentication_required'].includes((cause as {code?: string}).code ?? ''))
  }
  async function write() {
    if (!current?.content_fingerprint) return
    setBusy(true); setProblem(''); setSignIn(false)
    try {setCurrent(await request<Envelope>(path, 'POST', {if_match: current.content_fingerprint}))}
    catch (cause) {
      failed(cause, '没有生成，请稍后重试。')
      if ((cause as {code?: string}).code === 'content_changed') await read()
    } finally {setBusy(false)}
  }
  async function retry(step: Step) {
    const id = current?.report?.id
    if (!id) return
    setBusy(true); setProblem(''); setSignIn(false)
    try {const done = await request<Envelope>(`${path}/retry`, 'POST', {report_id: id, step}); setCurrent(value => value && {...value, report: done.report})}
    catch (cause) {
      failed(cause, '没有完成，请稍后重试。')
      if ((cause as {code?: string}).code === 'report_retry_unavailable') await read()
    } finally {setBusy(false)}
  }
  const report = current?.report
  const documentUrl = report?.document_id && ['ready', 'unknown'].includes(report.status) ? `https://feishu.cn/docx/${encodeURIComponent(report.document_id)}` : ''
  const pending = report && ['creating', 'writing'].includes(report.status)
  return <>
    <Button size="sm" variant="outline" onClick={() => {setProblem(''); setSignIn(false); setOpen(true); void read()}}>在飞书里生成报告文档</Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>在飞书里生成报告文档</DialogTitle>
          <DialogDescription>在你的飞书云空间新建一份文档，写入本次审核的结论和逐项结果，内容与本页一致，并附上报告 PDF。文档设为企业内只读：同事凭链接可以阅读，不能编辑，也不对外分享。</DialogDescription>
        </DialogHeader>
        {report?.status === 'ready' && documentUrl ? <p className="text-sm">报告文档已在你的飞书云空间里。<a className="text-primary underline-offset-4 hover:underline" href={documentUrl} target="_blank" rel="noreferrer">在飞书里打开</a></p>
          : report?.status === 'unknown' ? <p className="text-sm text-muted-foreground">上次生成的结果还不确定，文档可能已在你的飞书云空间里。请先在飞书里查看，系统不会重复生成。{documentUrl && <a className="ml-1 text-primary underline-offset-4 hover:underline" href={documentUrl} target="_blank" rel="noreferrer">在飞书里打开</a>}</p>
          : pending ? <p className="text-sm text-muted-foreground">报告文档正在生成，请稍后再看。</p> : null}
        {report && documentUrl && <Delivery report={report} busy={busy} onRetry={step => void retry(step)}/>}
        {report?.reauthorize && <div className="flex flex-wrap items-center gap-2 text-sm"><p className="text-muted-foreground">飞书授权不足：重新登录飞书并同意报告文档所需的权限后，再重试。</p><SignInForReport/></div>}
        {problem && <Alert variant="destructive"><AlertDescription><p>{problem}</p>{signIn && <SignInForReport/>}</AlertDescription></Alert>}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>{documentUrl ? '关闭' : '取消'}</Button>
          {!documentUrl && !pending && report?.status !== 'unknown' && <Button disabled={busy || !current?.content_fingerprint} onClick={() => void write()}>生成</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </>
}
