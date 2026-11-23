// Signing off a round (#357): the person who ran it stands by its conclusion, and may withdraw it with a reason. The page
// shows where the signoff stands beside what the round stands on; signing and withdrawing are the person's own signing
// moments, confirmed on one screen with the content as the server has it now.
import {useCallback, useEffect, useId, useRef, useState, type ReactNode} from 'react'
import {apiUrl, request} from '../api'
import {iconChevronRight} from '../components/icons'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Icon} from '../components/ui/icon'
import {Label} from '../components/ui/label'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Textarea} from '../components/ui/textarea'
import {FileName} from '../journey/file-name'
import {conclusionText, fileConclusion, machineFirstPass, signoffChanged, signoffEventText, signoffStatus, signoffSummary, WITHDRAW_REASON_MAX, type RoundSignoff, type SignoffContent} from './signoff-contract'
import {resultTone} from './status-labels'

const address = (roundId: string) => `/rounds/${encodeURIComponent(roundId)}/signoff`
const codeOf = (cause: unknown) => (cause as {code?: string} | null)?.code
const messageOf = (cause: unknown) => cause instanceof Error ? cause.message : '没有完成，请稍后重试。'
const tone = (value: string) => resultTone(value === 'ERROR' ? 'UNKNOWN' : value as 'PASS' | 'NG' | 'UNKNOWN')
// Completing a client's proposal after a while asks the person to sign in again; the way back is in the dialog itself.
const reauthenticate = (cause: unknown) => codeOf(cause) === 'reauthentication_required'
const SignInAgain = () => <Button asChild size="sm" variant="outline"><a href={`${apiUrl}/login/feishu?return_to=${encodeURIComponent(location.href)}`}>重新登录</a></Button>

export type SignoffRead = {value: RoundSignoff | null; error: string; reload: () => Promise<RoundSignoff | null>; apply: (value: RoundSignoff) => void}

/** The round's signoff as the server has it, read again when the round or its task's state changes, or when told. */
export function useRoundSignoff(roundId: string | null | undefined, revision?: unknown): SignoffRead {
  const [read, setRead] = useState<{round: string; value: RoundSignoff | null; error: string}>()
  const latest = useRef(roundId)
  latest.current = roundId
  const reload = useCallback(async () => {
    if (!roundId) return null
    try {
      const value = await request<RoundSignoff>(address(roundId))
      if (latest.current === roundId) setRead({round: roundId, value, error: ''})
      return value
    } catch {
      if (latest.current === roundId) setRead(current => ({round: roundId, value: current?.round === roundId ? current.value : null, error: '签发状态暂时无法读取'}))
      return null
    }
  }, [roundId])
  useEffect(() => {void reload()}, [reload, revision])
  useEffect(() => {
    const changed = (event: Event) => {if ((event as CustomEvent<string>).detail === roundId) void reload()}
    window.addEventListener(signoffChanged, changed)
    return () => window.removeEventListener(signoffChanged, changed)
  }, [roundId, reload])
  const shown = read && read.round === roundId ? read : undefined
  const apply = useCallback((value: RoundSignoff) => {if (roundId && value.round_id.toLowerCase() === roundId.toLowerCase()) setRead({round: roundId, value, error: ''})}, [roundId])
  return {value: shown?.value ?? null, error: shown?.error ?? '', reload, apply}
}

/** What a round stands on (`children`) and, beside it, where its signoff stands, with the person's one action on it. */
export function RoundSignoffStrip({roundId, signoff, children}: {roundId: string; signoff: SignoffRead; children: ReactNode}) {
  const {value, error} = signoff
  const [dialog, setDialog] = useState<'sign' | 'withdraw' | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const history = value?.history ?? []
  const status = value && signoffStatus(value)
  const statusId = useId(), countId = useId()
  return <div className="shrink-0 border-b bg-card px-6 py-2"><Collapsible open={historyOpen} onOpenChange={setHistoryOpen}>
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
      {children}
      <section aria-label="签发" className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <p className="text-muted-foreground">{machineFirstPass}</p>
        {!value ? <p role="status" className="text-muted-foreground">{error || '正在读取签发状态…'}</p> : <>
          <p id={statusId} role="status" className="flex items-center gap-1.5"><Badge shape="square" variant={value.state === 'signed' ? 'success' : 'outline'}>{status!.label}</Badge>{status!.detail && <span className="tabular-nums text-foreground" title="北京时间">{status!.detail}</span>}</p>
          {value.state === 'unsigned' && value.unavailable && <p className="text-muted-foreground">{value.unavailable.message}</p>}
          {history.length > 0 && <CollapsibleTrigger asChild><Button variant="quiet" size="xs" className="group" aria-label="签发记录" aria-describedby={countId}><Icon icon={iconChevronRight} aria-hidden="true" className="transition-transform group-data-[state=open]:rotate-90"/>签发记录 <span id={countId} className="tabular-nums">{history.length} 次</span></Button></CollapsibleTrigger>}
          {value.can_sign && <Button variant="soft" size="sm" aria-describedby={statusId} onClick={() => setDialog('sign')}>签发这一轮</Button>}
          {value.can_withdraw && <Button variant="outline" size="sm" aria-describedby={statusId} onClick={() => setDialog('withdraw')}>撤回签发</Button>}
        </>}
      </section>
    </div>
    <CollapsibleContent>
      <ol aria-label="签发记录" className="mt-2 space-y-1 border-t pt-2 text-xs">{[...history].reverse().map((event, index) => {
        const line = signoffEventText(event)
        return <li key={`${event.at}-${index}`} className="flex flex-wrap gap-x-2"><span className="font-medium">{line.action}</span><span className="text-muted-foreground">{line.who}</span><span className="tabular-nums text-muted-foreground" title="北京时间">{line.when}</span>{line.reason && <span className="min-w-0 break-words">原因：{line.reason}</span>}</li>
      })}</ol>
    </CollapsibleContent>
    <SignDialog open={dialog === 'sign'} roundId={roundId} signoff={signoff} onClose={() => setDialog(null)}/>
    <WithdrawDialog open={dialog === 'withdraw'} roundId={roundId} signoff={signoff} onClose={() => setDialog(null)}/>
  </Collapsible></div>
}

function SignContent({content}: {content: SignoffContent}) {
  const summary = signoffSummary(content)
  return <div className="space-y-3">
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1"><span className="flex items-center gap-1.5"><span className="text-muted-foreground">本轮结论</span><Badge shape="square" variant={tone(summary.conclusion)}>{conclusionText(summary.conclusion)}</Badge></span><span className="tabular-nums">{summary.line}</span></p>
    <Table aria-label="签发的文件">
      <TableHeader variant="muted"><TableRow interactive={false}><TableHead>文件</TableHead><TableHead className="w-24">结论</TableHead><TableHead className="w-20 text-right">人工确认</TableHead><TableHead className="w-28 text-right">按机器结论接受</TableHead><TableHead className="w-20 text-right">待审核</TableHead></TableRow></TableHeader>
      <TableBody>{content.files.map(file => {
        const conclusion = fileConclusion(file)
        return <TableRow key={file.file_version_id} interactive={false}>
          <TableCell className="w-full max-w-0"><FileName truncate name={file.filename}/></TableCell>
          <TableCell><Badge shape="square" variant={tone(conclusion)}>{conclusionText(conclusion)}</Badge></TableCell>
          <TableCell className="text-right"><span className="tabular-nums">{file.reviewed_count}</span></TableCell>
          <TableCell className="text-right"><span className="tabular-nums">{file.accepted_count}</span></TableCell>
          <TableCell className="text-right"><span className={file.pending_count ? 'font-medium tabular-nums text-warning' : 'tabular-nums text-muted-foreground'}>{file.pending_count}</span></TableCell>
        </TableRow>
      })}</TableBody>
    </Table>
    {summary.pending > 0 && <p className="text-xs text-muted-foreground">待审核的 {summary.pending} 项（无法判断或未覆盖）签发后仍是待审核，不算作符合。</p>}
  </div>
}

function SignDialog({open, roundId, signoff, onClose}: {open: boolean; roundId: string; signoff: SignoffRead; onClose: () => void}) {
  const [phase, setPhase] = useState<'reading' | 'ready' | 'sending' | 'signed'>('reading')
  const [notice, setNotice] = useState(''), [problem, setProblem] = useState(''), [signIn, setSignIn] = useState(false)
  // What was signed stays on screen while the dialog closes; the page beside it already says it is signed.
  const [signed, setSigned] = useState<SignoffContent | null>(null)
  const {value, reload, apply} = signoff
  // The confirmation shows the round as the server has it now; signing binds to exactly what was shown.
  useEffect(() => {
    if (!open) return
    setPhase('reading'); setNotice(''); setProblem(''); setSignIn(false); setSigned(null)
    void reload().then(found => {setPhase('ready'); if (!found) setProblem('暂时无法读取这一轮的内容，请稍后再试。')})
  }, [open, reload])
  const content = phase === 'signed' ? signed : value?.current
  const busy = phase === 'sending' || phase === 'signed'
  async function sign() {
    if (!content) return
    setPhase('sending'); setNotice(''); setProblem(''); setSignIn(false)
    try {
      const done = await request<RoundSignoff>(address(roundId), 'POST', {if_match: content.fingerprint})
      setSigned(content); setPhase('signed')
      apply(done)
      onClose()
    } catch (cause) {
      const changed = codeOf(cause) === 'content_changed'
      if (changed) setNotice('内容刚有变化，请再核对一次。'); else setProblem(messageOf(cause))
      setSignIn(reauthenticate(cause))
      await reload()
      setPhase('ready')
    }
  }
  const ready = content && (phase === 'signed' || phase !== 'reading' && value?.can_sign)
  return <Dialog open={open} onOpenChange={next => {if (!next && !busy) onClose()}}>
    <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader><DialogTitle>签发这一轮</DialogTitle><DialogDescription>签发表示你认可这一轮的结论，并为它负责。签发后要更正复核，须先撤回签发并写明原因。</DialogDescription></DialogHeader>
      {phase === 'reading' ? <p role="status" className="text-muted-foreground">正在读取这一轮的内容…</p>
        : ready ? <SignContent content={content}/>
        : value?.unavailable ? <p className="text-muted-foreground">{value.unavailable.message}</p>
        : value?.state === 'signed' ? <p className="text-muted-foreground">这一轮已经签发。</p> : null}
      {notice && <p role="alert" className="font-medium text-warning">{notice}</p>}
      {problem && <p role="alert" className="text-destructive">{problem}</p>}
      <DialogFooter>
        {signIn && <SignInAgain/>}
        <Button variant="outline" disabled={busy} onClick={onClose}>{ready ? '取消' : '关闭'}</Button>
        {ready && <Button disabled={busy} onClick={() => void sign()}>{busy ? '正在签发…' : '确认签发'}</Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>
}

function WithdrawDialog({open, roundId, signoff, onClose}: {open: boolean; roundId: string; signoff: SignoffRead; onClose: () => void}) {
  const [reason, setReason] = useState(''), [sending, setSending] = useState(false), [problem, setProblem] = useState(''), [signIn, setSignIn] = useState(false)
  const reasonId = useId(), countId = useId()
  useEffect(() => {if (open) {setReason(''); setProblem(''); setSignIn(false)}}, [open])
  const text = reason.trim()
  async function withdraw() {
    if (!text) {setProblem('请写明撤回原因。'); return}
    setSending(true); setProblem(''); setSignIn(false)
    try {
      signoff.apply(await request<RoundSignoff>(`${address(roundId)}/withdraw`, 'POST', {reason: text}))
      onClose()
    } catch (cause) {
      setProblem(messageOf(cause))
      setSignIn(reauthenticate(cause))
      if (!['withdraw_reason_required', 'withdraw_reason_too_long'].includes(codeOf(cause) ?? '')) void signoff.reload()
    } finally {setSending(false)}
  }
  return <Dialog open={open} onOpenChange={next => {if (!next && !sending) onClose()}}>
    <DialogContent className="sm:max-w-lg">
      <DialogHeader><DialogTitle>撤回签发</DialogTitle><DialogDescription>撤回后这一轮变为未签发，可以更正复核后再签发。已交付的报告和飞书文档不会改动。</DialogDescription></DialogHeader>
      <form className="space-y-2" onSubmit={event => {event.preventDefault(); void withdraw()}}>
        <Label htmlFor={reasonId}>撤回原因</Label>
        <Textarea id={reasonId} required aria-describedby={countId} maxLength={WITHDRAW_REASON_MAX} placeholder="写明为什么撤回，会留在签发记录里" value={reason} disabled={sending} onChange={event => setReason(event.target.value)}/>
        <p id={countId} className="text-right text-xs tabular-nums text-muted-foreground">{reason.length}/{WITHDRAW_REASON_MAX}</p>
        {problem && <p role="alert" className="text-destructive">{problem}</p>}
        <DialogFooter>
          {signIn && <SignInAgain/>}
          <Button type="button" variant="outline" disabled={sending} onClick={onClose}>取消</Button>
          <Button type="submit" disabled={sending || !text}>{sending ? '正在撤回…' : '确认撤回'}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>
}
