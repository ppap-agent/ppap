// The page a rule-clarification round is confirmed on (#351): its own full page, not a drawer, with the confirmation at
// the top. Freezing the next revision and putting cards before the customer are separate regions with their own checks;
// a step the customer sees starts unchecked. Everything shown is the plan as the server describes it now.
import {useEffect, useMemo, useState, type ReactNode} from 'react'
import {cn} from '@/lib/utils'
import type {components} from '../generated/openapi'
import {request} from '../api'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Textarea} from '../components/ui/textarea'
import {toggle} from './plan-selection'

type Plan = components['schemas']['IntentPlan']
type Step = Plan['steps'][number]
type Detail = components['schemas']['IntentDetail']
type Source = {question_key?: string | null; answerers?: string[] | null; ruling?: string | null}
type Change = {kind: 'same' | 'changed' | 'new'; note: string; sources?: Source[]}
type Clause = {id: string; category: string; text: string}
type Document = {key: string; title: string; clauses: Clause[]}
type RevisionBody = {version: string; revision: number; base?: {version: string; revision: number} | null; documents: Document[]; changes: Record<string, Change>}
type Card = {key: string; clauses: string[]; question: string; why?: string; options?: {label: string}[]}
type Base = {documents: {clauses: {id: string; text: string}[]}[]}

const KIND: Record<Change['kind'], string> = {same: '意思不变', changed: '意思变了', new: '新增'}
const PAGE = 20

/** Paragraphs of the new wording that the old wording does not have: what the rewrite added. */
function added(before: string, after: string): Set<number> {
  const old = new Set(before.split(/\n{2,}/).map(part => part.trim()))
  return new Set(after.split(/\n{2,}/).flatMap((part, index) => (old.has(part.trim()) ? [] : [index])))
}

function grounds(change: Change): string[] {
  const kinds = new Set((change.sources ?? []).map(source => (source.ruling ? '平台裁定' : '客户答复')))
  return [...kinds]
}

function Section({step, chosen, onCheck, steps, tone, stat, children}: {step: Step; chosen: boolean; onCheck: (on: boolean) => void; steps: Step[]; tone?: 'external'; stat?: ReactNode; children: ReactNode}) {
  const id = `plan-step-${step.id}`
  const needs = step.depends_on.map(need => steps.find(other => other.id === need)?.title).filter(Boolean)
  return <section aria-labelledby={id} className={cn('rounded-xl border bg-card', tone === 'external' && 'border-l-4 border-l-warning')}>
    <div className="space-y-1 px-5 pt-4 pb-3">
      <div className="flex items-center gap-3">
        <Checkbox id={`${id}-check`} checked={chosen} onCheckedChange={value => onCheck(value === true)} aria-labelledby={id}/>
        <h2 id={id} className="text-base font-semibold">{step.title}</h2>
        {step.external ? <Badge variant="warning">会发给客户</Badge> : <Badge variant="outline">不发给客户</Badge>}
      </div>
      <div className="space-y-1 pl-7 text-sm text-muted-foreground">
        {step.summary.map((line, index) => <p key={index}>{line}</p>)}
        {needs.length > 0 && <p>依赖「{needs.join('」「')}」：取消它，这一步也会取消。</p>}
        {stat}
      </div>
    </div>
    {children}
  </section>
}

function Block({block}: {block: Detail}) {
  if (block.kind === 'facts') return <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">{(block.rows ?? []).map(([label, value], index) => <div key={index} className="contents"><dt className="text-muted-foreground">{String(label)}</dt><dd className="break-words">{value == null ? '—' : String(value)}</dd></div>)}</dl>
  if (block.kind === 'text') return <div className="space-y-1">{block.title && <h3 className="text-sm font-medium">{block.title}</h3>}<p className="whitespace-pre-wrap rounded-md bg-muted px-3 py-2 text-sm">{block.text}</p></div>
  if (block.kind === 'note') return <p className="text-xs text-muted-foreground">{block.text}</p>
  return null
}

function ClauseRow({clause, document, change, before}: {clause: Clause; document: string; change: Change; before: string | undefined}) {
  const [open, setOpen] = useState(false)
  const id = `plan-clause-${clause.id}`
  const marks = before === undefined ? new Set<number>() : added(before, clause.text)
  return <li className="border-t">
    <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}
      className="grid w-full grid-cols-[6rem_9rem_minmax(0,1fr)_auto] items-center gap-4 px-5 py-2.5 text-left text-sm outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50">
      <span className="font-medium tabular-nums">{clause.id}</span>
      <span className="truncate text-muted-foreground" title={document}>{document}</span>
      <span className="truncate" title={change.note}>{change.note}</span>
      <span className="flex items-center gap-1.5">{grounds(change).map(kind => <Badge key={kind} variant="outline">{kind}</Badge>)}<Badge variant={change.kind === 'same' ? 'outline' : 'warning'}>{KIND[change.kind]}</Badge></span>
    </button>
    {open && <div id={id} className="space-y-3 bg-muted/40 px-5 py-4">
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="space-y-1.5"><h3 className="text-xs font-medium text-muted-foreground">现在的写法</h3>
          <p className="whitespace-pre-wrap rounded-md border bg-card px-3 py-2 text-sm">{before ?? '正在读取原文…'}</p></div>
        <div className="space-y-1.5"><h3 className="text-xs font-medium text-muted-foreground">确认后的写法</h3>
          <div className="space-y-2 rounded-md border border-primary-line bg-card px-3 py-2 text-sm">{clause.text.split(/\n{2,}/).map((part, index) =>
            <p key={index} className={cn('whitespace-pre-wrap', marks.has(index) && 'rounded-sm bg-primary-soft px-1.5 py-0.5')}>{part}</p>)}</div>
          {marks.size > 0 && <p className="text-xs text-muted-foreground">浅蓝底是新加的内容。</p>}</div>
      </div>
      {(change.sources ?? []).length > 0 && <div className="space-y-1.5 text-sm"><h3 className="text-xs font-medium text-muted-foreground">依据</h3>
        <ul className="space-y-1">{change.sources!.map((source, index) => <li key={index} className="rounded-md bg-card px-3 py-1.5">
          {source.ruling ? <>平台裁定：<span className="text-muted-foreground">{source.ruling}</span></> : <>{(source.answerers ?? []).join('、')} 的答复</>}</li>)}</ul></div>}
    </div>}
  </li>
}

function RevisionList({body, customer}: {body: RevisionBody; customer: string}) {
  const [kind, setKind] = useState<'all' | Change['kind']>('all'), [type, setType] = useState('all'), [query, setQuery] = useState('')
  const [shown, setShown] = useState(PAGE), [base, setBase] = useState<Map<string, string> | null>(null)
  useEffect(() => {
    if (!body.base) return
    request<{revision: Base}>(`/rulebooks/${encodeURIComponent(customer)}/${encodeURIComponent(body.base.version)}/${body.base.revision}`)
      .then(found => setBase(new Map(found.revision.documents.flatMap(document => document.clauses.map(clause => [clause.id, clause.text] as const)))))
      .catch(() => setBase(new Map()))
  }, [customer, body.base])
  const rows = useMemo(() => body.documents.flatMap(document => document.clauses.filter(clause => body.changes[clause.id]).map(clause => ({clause, document: document.title, change: body.changes[clause.id]}))), [body])
  const counts = (Object.keys(KIND) as Change['kind'][]).map(key => [key, rows.filter(row => row.change.kind === key).length] as const).filter(([, n]) => n > 0)
  const types = [...new Set(rows.map(row => row.document))]
  const needle = query.trim().toLowerCase()
  const visible = rows.filter(row => (kind === 'all' || row.change.kind === kind) && (type === 'all' || row.document === type)
    && (!needle || row.clause.id.toLowerCase().includes(needle) || row.document.toLowerCase().includes(needle) || row.change.note.toLowerCase().includes(needle)))
  return <>
    <div className="flex flex-wrap items-center gap-3 border-t px-5 py-3">
      <div role="group" aria-label="按改动筛选" className="flex rounded-md bg-muted p-0.5 text-sm">{[['all', `全部 ${rows.length}`] as const, ...counts.map(([key, n]) => [key, `${KIND[key]} ${n}`] as const)].map(([key, label]) =>
        <button key={key} type="button" aria-pressed={kind === key} onClick={() => {setKind(key); setShown(PAGE)}}
          className={cn('rounded-sm px-3 py-1 outline-none focus-visible:ring-3 focus-visible:ring-ring/50', kind === key ? 'bg-card font-medium shadow-xs' : 'text-muted-foreground')}>{label}</button>)}</div>
      <Input aria-label="按条款编号、文件类型或改动筛选" placeholder="按条款编号、文件类型或改动筛选" value={query} onChange={event => {setQuery(event.target.value); setShown(PAGE)}} className="ml-auto w-64"/>
      <Select value={type} onValueChange={value => {setType(value); setShown(PAGE)}}><SelectTrigger aria-label="文件类型" className="w-48"><SelectValue/></SelectTrigger>
        <SelectContent><SelectItem value="all">全部文件类型</SelectItem>{types.map(name => <SelectItem key={name} value={name}>{name}</SelectItem>)}</SelectContent></Select>
    </div>
    <div className="grid grid-cols-[6rem_9rem_minmax(0,1fr)_auto] gap-4 border-t bg-muted/40 px-5 py-2 text-xs text-muted-foreground" aria-hidden="true"><span>条款</span><span>文件类型</span><span>改了什么</span><span>依据与改动</span></div>
    {visible.length === 0 ? <p className="border-t px-5 py-6 text-sm text-muted-foreground">没有符合筛选的条款。</p>
      : <ul aria-label="改动的条款">{visible.slice(0, shown).map(row => <ClauseRow key={row.clause.id} {...row} before={body.base ? base?.get(row.clause.id) : ''}/>)}</ul>}
    {visible.length > shown && <div className="border-t px-5 py-3"><Button variant="ghost" size="sm" onClick={() => setShown(shown + PAGE)}>再显示 {Math.min(PAGE, visible.length - shown)} 条（还有 {visible.length - shown} 条）</Button></div>}
  </>
}

function Cards({step}: {step: Step}) {
  const cards = ((step.body as {cards?: Card[]})?.cards ?? [])
  const notice = step.details.find(block => block.kind === 'facts')
  return <div className="space-y-3 border-t px-5 py-4">
    {notice && <Block block={notice}/>}
    <ul className="space-y-3">{cards.map(card => <li key={card.key} className="space-y-1.5 rounded-lg border px-4 py-3">
      {card.clauses[0] && <p className="text-xs text-muted-foreground">{card.clauses.join('、')}</p>}
      <p className="font-medium">{card.question}</p>
      {card.why && <p className="text-sm whitespace-pre-wrap text-muted-foreground">{card.why}</p>}
      {(card.options ?? []).length > 0 && <p className="text-sm">可选答案：{card.options!.map(option => option.label).join('；')}</p>}
    </li>)}</ul>
    {step.details.filter(block => block.kind === 'note').map((block, index) => <Block key={index} block={block}/>)}
  </div>
}

export function RulePlanPage({plan, title, chosen, setChosen, busy, reauth, signIn, problem, onConfirm, onDecline, onClose}: {
  plan: Plan; title: string; chosen: Set<string>; setChosen: (next: Set<string>) => void; busy: boolean; reauth: boolean; signIn: ReactNode; problem: string
  onConfirm: (left: string) => void; onDecline: (reason: string) => void; onClose: () => void
}) {
  const [left, setLeft] = useState(''), [declining, setDeclining] = useState(false), [reason, setReason] = useState('')
  const customer = decodeURIComponent(plan.path.split('/')[2] ?? '')
  const revision = plan.steps.find(step => step.id === 'revision')
  const name = (revision?.body as RevisionBody | undefined)?.version ?? ''
  const check = (step: Step) => (on: boolean) => setChosen(toggle(plan.steps, chosen, step.id, on))
  return <Dialog open onOpenChange={open => !open && onClose()}>
    <DialogContent layout="page" showCloseButton={false} aria-describedby="plan-intro">
      <div className="sticky top-0 z-10 border-b bg-canvas/95 backdrop-blur">
        <div className="mx-auto max-w-5xl px-6 py-4">
          <div className="flex flex-wrap items-start gap-4 rounded-xl border bg-card px-5 py-4">
            <div className="min-w-0 flex-1 space-y-1">
              <DialogTitle>{title}{name && `：规则库 ${name}`}</DialogTitle>
              <DialogDescription id="plan-intro">勾选的步骤确认后一起生效，没勾的留到下一次。审核仍用现在的规则，不会因此切换。</DialogDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-muted-foreground tabular-nums">已选 {chosen.size} / {plan.steps.length} 步</span>
              <Button variant="ghost" onClick={onClose}>稍后再看</Button>
              <Button variant="outline" onClick={() => setDeclining(!declining)} aria-expanded={declining}>拒绝</Button>
              {reauth ? signIn : <Button disabled={busy || chosen.size === 0} onClick={() => onConfirm(left)}>{busy ? '正在确认…' : '确认所选步骤'}</Button>}
            </div>
            {reauth && <p className="w-full text-sm text-destructive">为确认是你本人在操作，请重新登录后再完成。</p>}
            {problem && <p role="alert" className="w-full text-sm text-destructive">{problem}</p>}
            {declining && <div className="w-full space-y-2"><Textarea aria-label="拒绝原因（选填）" placeholder="可以写一句为什么拒绝（选填）" maxLength={500} value={reason} onChange={event => setReason(event.target.value)}/>
              <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setDeclining(false)}>取消</Button><Button variant="outline" disabled={busy} onClick={() => onDecline(reason)}>拒绝这一轮</Button></div></div>}
            {!declining && plan.steps.some(step => !chosen.has(step.id)) && <Textarea aria-label="没勾选的原因（选填）" placeholder="没勾的步骤留到下一次；可以写一句为什么（选填）" maxLength={500} value={left} onChange={event => setLeft(event.target.value)} className="w-full"/>}
          </div>
        </div>
      </div>
      <main className="mx-auto max-w-5xl space-y-5 px-6 pt-2 pb-10">
        {plan.steps.map(step => {
          if (step.id === 'revision') {
            const body = step.body as RevisionBody
            const changes = Object.values(body.changes ?? {})
            const basis = [...new Set(changes.flatMap(grounds))].join('、')
            const stat = changes.length > 0 && <p>{changes.length} 条改写{basis && ` · 依据：${basis}`}</p>
            return <Section key={step.id} step={step} steps={plan.steps} chosen={chosen.has(step.id)} onCheck={check(step)} stat={stat}>
              {changes.length > 0 ? <RevisionList body={body} customer={customer}/> : <div className="border-t px-5 py-4">{step.details.filter(block => block.kind !== 'note').map((block, index) => <Block key={index} block={block}/>)}</div>}
              {step.details.filter(block => block.kind === 'note').map((block, index) => <div key={index} className="border-t px-5 py-3"><Block block={block}/></div>)}
            </Section>
          }
          if (step.id === 'cards') return <Section key={step.id} step={step} steps={plan.steps} chosen={chosen.has(step.id)} onCheck={check(step)} tone="external"><Cards step={step}/></Section>
          return <Section key={step.id} step={step} steps={plan.steps} chosen={chosen.has(step.id)} onCheck={check(step)}>
            <div className="space-y-3 border-t px-5 py-4">{step.details.map((block, index) => <Block key={index} block={block}/>)}</div></Section>
        })}
      </main>
    </DialogContent>
  </Dialog>
}
