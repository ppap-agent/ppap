// A signing action proposed through MCP (#348). Nothing signs for the person: they complete it on the page the link
// opened, with that page's own control and current content, or decline it. The banner says what the person does, never
// which tool asked (#356): the tool is the person's, the authority is their own act.
// A rule-clarification plan has no page control of its own: it opens its own confirmation page (rule-plan.tsx), where
// internal steps start checked and a step the customer sees is checked only by the person (#351).
import {useEffect, useRef, useState} from 'react'
import {initialSelection, planRequest} from './plan-selection'
import {RulePlanPage} from './rule-plan'
import type {components} from '../generated/openapi'
import {apiUrl, request, setActiveIntent} from '../api'
import {Button} from '../components/ui/button'
import {Textarea} from '../components/ui/textarea'
import {auditAddressChanged, openInPlace, receiptsAddress} from '../audit/routes'

type Intent = components['schemas']['Intent']

const settled = (intent: Intent) => intent.outcome.state === 'succeeded' || intent.outcome.state === 'declined'
const readId = () => new URL(location.href).searchParams.get('intent')

/** What the proposal does, and where on the page it is completed (a plan is completed in this panel). */
function describe(intent: Intent): [string, string] {
  const path = intent.path
  if (path.endsWith('/plan')) return ['确认规则澄清的这一轮', '']
  if (path.endsWith('/confirm')) return ['开始审核', '核对本页的文件、规则和预计点数后，点「开始审核」完成。']
  if (path.endsWith('/report')) return ['在飞书里生成报告文档', '核对本页的审核结果后，在「报告」里点「在飞书里生成报告文档」完成。']
  if (path.endsWith('/regenerations')) return ['重新生成报告', '在「报告」里打开这份报告，点「重生成同一结果」完成。']
  if (path.endsWith('/signoff')) return ['签发这一轮', '点本页的「签发这一轮」，在确认框里核对文件与结论后完成。']
  if (path.endsWith('/signoff/withdraw')) return ['撤回签发', '在本页点「撤回签发」，写明原因后完成。']
  if (path.endsWith('/cards')) return ['把问题卡片发给客户', '']
  if (path.endsWith('/clarified')) return ['标记这一修订已澄清', '']
  if (path.endsWith('/readings')) return ['发布条款解读', '']
  return ['确认规则修订', '']
}

export function IntentBanner() {
  const [id, setId] = useState(readId)
  const [intent, setIntent] = useState<Intent | null>(null)
  const [problem, setProblem] = useState(''), [reauth, setReauth] = useState(false)
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false)
  const shownFor = useRef<string | null>(null)
  const [declining, setDeclining] = useState(false), [reason, setReason] = useState('')
  const [chosen, setChosen] = useState<Set<string>>(new Set()), [left, setLeft] = useState('')

  async function read(ident: string) {
    try {
      const found = await request<Intent>(`/intents/${encodeURIComponent(ident)}`)
      setIntent(found); setProblem('')
      // The checks start over whenever the plan is read again: a changed plan is decided on as it is now.
      setChosen(initialSelection(found.plan?.steps ?? []))
      // A plan opens its own page once on arrival; closing it leaves the banner, which opens it again.
      if (found.plan && !settled(found) && shownFor.current !== found.id) {shownFor.current = found.id; setOpen(true)}
      setActiveIntent(settled(found) ? null : {id: found.id, method: found.method, path: found.path})
    } catch (cause) {
      setIntent(null); setActiveIntent(null)
      setProblem((cause as {code?: string}).code === 'intent_not_found' ? '找不到这个请求，或它不是发给你的。' : cause instanceof Error ? cause.message : '暂时无法读取这个请求。')
    }
  }
  useEffect(() => {
    const follow = () => setId(readId())
    window.addEventListener('popstate', follow); window.addEventListener(auditAddressChanged, follow)
    return () => {window.removeEventListener('popstate', follow); window.removeEventListener(auditAddressChanged, follow)}
  }, [])
  useEffect(() => {
    if (!id) {setIntent(null); setActiveIntent(null); return}
    void read(id)
    const changed = (event: Event) => {
      const code = (event as CustomEvent<string>).detail
      if (code === 'reauthentication_required') setReauth(true)
      else {if (code === 'done') setReauth(false); void read(id)}
    }
    window.addEventListener('ppap-intent-changed', changed)
    return () => {window.removeEventListener('ppap-intent-changed', changed); setActiveIntent(null)}
  }, [id])

  function close() {
    const url = new URL(location.href); url.searchParams.delete('intent')
    history.replaceState(history.state, '', url); setId(null); setOpen(false)
  }
  async function act(run: () => Promise<unknown>) {
    setBusy(true); setProblem('')
    try {await run(); setOpen(false); setDeclining(false)}
    catch (cause) {setProblem(cause instanceof Error ? cause.message : '没有完成，请稍后重试。')}
    finally {setBusy(false); if (id) void read(id)}
  }
  if (!id) return null
  const [label, where] = intent ? describe(intent) : ['', '']
  const plan = intent?.plan
  const outcome = intent?.outcome
  const done = intent && settled(intent)
  const signIn = <Button asChild size="sm"><a href={`${apiUrl}/login/feishu?return_to=${encodeURIComponent(location.href)}`}>重新登录</a></Button>
  const decline = declining
    ? <div className="w-full space-y-2"><Textarea aria-label="拒绝原因（选填）" placeholder="可以写一句为什么拒绝（选填）" maxLength={500} value={reason} onChange={event => setReason(event.target.value)}/>
      <div className="flex justify-end gap-2"><Button size="sm" variant="ghost" onClick={() => setDeclining(false)}>取消</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => void act(() => request(`/intents/${encodeURIComponent(id)}/decline`, 'POST', reason.trim() ? {reason: reason.trim()} : {}))}>拒绝这个请求</Button></div></div>
    : <Button size="sm" variant="ghost" onClick={() => setDeclining(true)}>拒绝</Button>
  return <>
    <aside role="region" aria-label="待你完成的请求" className="fixed inset-x-0 bottom-4 z-40 mx-auto w-full max-w-2xl px-4">
      <div className="space-y-2 rounded-xl border bg-card p-4 text-sm shadow-lg">
        {!intent ? <><p>{problem || '正在读取这个请求…'}</p>{problem && <div className="flex justify-end"><Button size="sm" variant="ghost" onClick={close}>关闭</Button></div>}</> : <>
          <p className="font-medium">请你{label}</p>
          {done ? <><p className="text-muted-foreground">{outcome?.state === 'succeeded' ? <>已完成{intent.receipt && <>，回执 <span className="tabular-nums text-foreground">{intent.receipt.number}</span></>}。</> : '你已拒绝这个请求。'}</p><div className="flex items-center justify-end gap-2">{outcome?.state === 'succeeded' && intent.receipt && <Button size="sm" variant="link" asChild><a href={receiptsAddress(intent.receipt.id)} onClick={openInPlace}>查看回执</a></Button>}<Button size="sm" variant="ghost" onClick={close}>关闭</Button></div></>
            : intent.unavailable ? <><p className="text-muted-foreground">这一步已经变化，不能再按原样完成，请按最新状态重新发起。</p><div className="flex justify-end gap-2">{decline}<Button size="sm" variant="ghost" onClick={close}>关闭</Button></div></>
            : <>
              {plan ? <ul className="list-disc pl-5 text-muted-foreground">{plan.steps.map(step => <li key={step.id}>{step.summary[0] ?? step.title}</li>)}</ul> : <p className="text-muted-foreground">{where}</p>}
              {outcome?.state === 'failed' && outcome.error && <p className="text-destructive">上次没有完成，可以再试一次。</p>}
              {reauth && <p className="text-destructive">为确认是你本人在操作，请重新登录后再完成。</p>}
              {problem && <p className="text-destructive">{problem}</p>}
              <div className="flex flex-wrap items-center justify-end gap-2">{decline}{!declining && (reauth ? signIn : plan && <Button size="sm" onClick={() => setOpen(true)}>查看并确认</Button>)}</div>
            </>}
        </>}
      </div>
    </aside>
    {intent && plan && open && !done && <RulePlanPage plan={plan} title={`请你${label}`} chosen={chosen} setChosen={setChosen} busy={busy} reauth={reauth} signIn={signIn} problem={problem}
      onConfirm={left => void act(() => request(plan.path, plan.method, planRequest(plan.steps, chosen, left)))}
      onDecline={reason => void act(() => request(`/intents/${encodeURIComponent(intent.id)}/decline`, 'POST', reason.trim() ? {reason: reason.trim()} : {}))}
      onClose={() => setOpen(false)}/>}
  </>
}
