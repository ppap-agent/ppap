import {useCallback, useEffect, useId, useRef, useState} from 'react'
import {request} from '../api'
import {displayTime} from '../helpers'
import {iconChevronRight} from '../components/icons'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Icon} from '../components/ui/icon'
import {Item, ItemActions, ItemContent, ItemDescription, ItemTitle} from '../components/ui/item'
import {Label} from '../components/ui/label'
import {RadioGroup, RadioGroupItem} from '../components/ui/radio-group'
import {Textarea} from '../components/ui/textarea'
import {ReadSkeleton} from '../components/read-skeleton'
import {message} from '../journey/remote'
import {followRulesLink, rulesAddress} from './rule-templates-view'
import {applyReceipt, checkedReceipt, choiceLabel, groupQuestions, questionnaireVersionLabel, RELAYED, type AnswerReceipt, type AnswerRequest, type Question, type QuestionAnswer, type QuestionnaireList, type QuestionnaireSummary, type QuestionnaireView} from './rule-questions-contract'

const progress = (row: Pick<QuestionnaireSummary, 'answered' | 'total'>) => `已答 ${row.answered} / ${row.total} 题`

/** On the rules page, under a customer: each version of its rules that has questions waiting for the customer. */
export function QuestionnaireEntries({list, customerId}: {list: QuestionnaireList | null; customerId: string}) {
  const rows = list?.questionnaires.filter(row => row.customer_id === customerId) ?? []
  if (!rows.length) return null
  return <div className="grid shrink-0 gap-2 border-b p-4">
    {rows.map(row => {
      const href = rulesAddress({template: customerId, ruleQuestions: row.id})
      return <Item key={row.id} variant="outline" size="sm" asChild>
        <a href={href} aria-labelledby={`rq-${row.id}-title`} aria-describedby={`rq-${row.id}-progress`} onClick={event => followRulesLink(event, href)}>
          <ItemContent><ItemTitle id={`rq-${row.id}-title`} wrap>{questionnaireVersionLabel(row)} 待确认问题</ItemTitle><ItemDescription id={`rq-${row.id}-progress`}>{row.closed ? `已关闭 · ${progress(row)}` : progress(row)}</ItemDescription></ItemContent>
          <ItemActions aria-hidden="true"><span className="flex items-center gap-1 text-sm text-primary">{row.closed ? '查看' : '去作答'}<Icon icon={iconChevronRight} className="size-3.5"/></span></ItemActions>
        </a>
      </Item>
    })}
  </div>
}

/** One questionnaire on a wide page: a fixed column with the title, progress and the groups, and every card shown in full beside it. */
export function RuleQuestionnairePage({questionnaireId}: {questionnaireId: string}) {
  const [view, setView] = useState<QuestionnaireView | null>(null)
  const [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [current, setCurrent] = useState('')
  const generation = useRef(0)
  const read = useCallback(async () => {
    const token = ++generation.current
    try {
      const next = await request<QuestionnaireView>(`/rule-questionnaires/${encodeURIComponent(questionnaireId)}`)
      if (token !== generation.current) return
      setView(next); setError('')
    } catch (cause) {if (token === generation.current) setError(message(cause))}
  }, [questionnaireId])
  useEffect(() => {
    setView(null); setError('')
    void read()
    return () => {generation.current++}
  }, [read])
  const groups = view ? groupQuestions(view.questions) : []
  const conflict = async () => {setNotice('问卷已有更新，已重新读取，请核对后再保存。'); await read()}
  // Only the content area scrolls; the page frame (navigation) stays where it is.
  const jump = (key: string) => {
    setCurrent(key)
    const target = document.getElementById(`rq-group-${key}`)
    let scroller = target?.parentElement ?? null
    while (scroller && !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY)) scroller = scroller.parentElement
    if (target && scroller) scroller.scrollTo({top: scroller.scrollTop + target.getBoundingClientRect().top - scroller.getBoundingClientRect().top, behavior: 'smooth'})
  }

  if (error && !view) return <div className="max-w-3xl"><Alert variant="destructive"><AlertTitle>待确认问题未能读取</AlertTitle><AlertDescription><p>{error}</p><Button variant="outline" size="sm" onClick={() => {setError(''); void read()}}>重新读取</Button></AlertDescription></Alert></div>
  if (!view) return <ReadSkeleton label="正在读取待确认问题…" kind="detail" rows={6}/>
  const active = current || groups[0]?.key
  return <div className="grid items-start gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
    <aside className="grid gap-4 lg:sticky lg:top-0">
      <a className="w-fit text-sm text-primary hover:underline" href={rulesAddress({template: view.customer_id})} onClick={event => followRulesLink(event, rulesAddress({template: view.customer_id}))}>{view.customer_name}</a>
      <h1 className="text-xl font-semibold">{questionnaireVersionLabel(view)} 待确认问题</h1>
      <p className="text-sm tabular-nums text-muted-foreground">{progress(view)} · 我答了 {view.mine} 题</p>
      <nav aria-label="分组"><ul className="grid gap-1">{groups.map(group => <li key={group.key}>
        <button type="button" aria-current={group.key === active ? 'true' : undefined} aria-describedby={`rq-nav-${group.key}`} onClick={() => jump(group.key)} className={`flex h-10 w-full items-center justify-between rounded-md px-3 text-left text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 ${group.key === active ? 'bg-primary-soft font-semibold text-primary' : 'hover:bg-muted'}`}>
          {group.label}<span id={`rq-nav-${group.key}`} className="tabular-nums">{group.answered} / {group.questions.length}</span>
        </button>
      </li>)}</ul></nav>
    </aside>
    <div className="grid min-w-0 gap-6">
      {view.closed ? <Alert><AlertTitle>问卷已关闭</AlertTitle><AlertDescription>答复只能查看，不能再作答或修改。</AlertDescription></Alert>
        : <p className="text-sm text-muted-foreground">这些问题来自对这一版规则原文的逐条检查。每张卡可单独作答，每个人的答复分开记录。「已答」只表示收到答复，不代表规则已确认。</p>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
      {groups.map(group => <section key={group.key} id={`rq-group-${group.key}`} aria-labelledby={`rq-group-${group.key}-title`} className="grid scroll-mt-4 gap-4">
        <div className="flex flex-wrap items-baseline gap-3"><h2 id={`rq-group-${group.key}-title`} className="text-base font-semibold">{group.label}</h2><span className="text-sm tabular-nums text-muted-foreground">{progress({answered: group.answered, total: group.questions.length})}</span></div>
        {group.questions.map(question => <QuestionCard key={question.key} question={question} questionnaireId={view.id} closed={view.closed} onSaved={receipt => setView(current => current && applyReceipt(current, receipt))} onConflict={conflict}/>)}
      </section>)}
    </div>
  </div>
}

function AnswerBody({question, answer}: {question: Question; answer: Pick<QuestionAnswer, 'choice' | 'text' | 'note'>}) {
  const chosen = choiceLabel(question, answer.choice)
  return <>
    {chosen && <p>选择：{chosen}</p>}
    {answer.text && <p className="whitespace-pre-wrap break-words">{answer.text}</p>}
    {answer.note && <p className="whitespace-pre-wrap break-words">备注：{answer.note}</p>}
  </>
}

/** Each answerer's current answer, with what it was before each change written right below it. */
function AnswerRow({question, answer}: {question: Question; answer: QuestionAnswer}) {
  return <li className="grid gap-1 text-sm">
    <p className="text-xs text-muted-foreground">{answer.mine ? `${answer.answerer}（我）` : answer.answerer} · <time dateTime={answer.answered_at}>{displayTime(answer.answered_at)}</time>{answer.history.length > 0 && ' · 已修改'}</p>
    <AnswerBody question={question} answer={answer}/>
    {[...answer.history].reverse().map(old => <div key={old.revision} className="grid gap-0.5 text-muted-foreground">
      <p>改前 · <time dateTime={old.answered_at}>{displayTime(old.answered_at)}</time>{old.choice ? `：${choiceLabel(question, old.choice)}` : ''}</p>
      {old.text && <p className="whitespace-pre-wrap break-words">{old.text}</p>}
      {old.note && <p className="whitespace-pre-wrap break-words">备注：{old.note}</p>}
    </div>)}
  </li>
}

/** The rule text as written, with the passages the question turns on marked. */
function SourceText({text, quotes}: {text: string; quotes: string[]}) {
  const marks = quotes.filter(quote => quote && text.includes(quote))
  if (!marks.length) return <>{text}</>
  const pattern = new RegExp(`(${marks.map(quote => quote.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`)
  return <>{text.split(pattern).map((part, index) => marks.includes(part) ? <mark key={index} className="rounded-sm bg-primary-soft px-0.5 text-foreground">{part}</mark> : part)}</>
}

function Source({source, label = '规则原文'}: {source: Question['sources'][number]; label?: string}) {
  return <blockquote className="grid gap-1 rounded-r-md border-l-4 bg-background px-3 py-2">
    <span className="font-semibold text-muted-foreground">{label}</span>
    <p className="whitespace-pre-wrap"><SourceText text={source.text} quotes={source.quotes}/></p>
  </blockquote>
}

const OWN = 'own'

function QuestionCard({question, questionnaireId, closed, onSaved, onConflict}: {question: Question; questionnaireId: string; closed: boolean; onSaved: (receipt: AnswerReceipt) => void; onConflict: () => Promise<void>}) {
  const own = question.answers.find(answer => answer.mine)
  const others = question.answers.filter(answer => !answer.mine)
  const choices = [...(question.relayed ? [{key: RELAYED, label: choiceLabel(question, RELAYED), detail: ''}] : []), ...question.options]
  // Writing one's own words is the last choice, not an addition to another one.
  const initial = () => own?.choice || (own?.text && choices.length ? OWN : '')
  const [editing, setEditing] = useState(!own)
  const [choice, setChoice] = useState(initial), [text, setText] = useState(own?.text ?? ''), [note, setNote] = useState(own?.note ?? '')
  const [saving, setSaving] = useState(false), [error, setError] = useState('')
  const [pending, setPending] = useState<AnswerRequest | null>(null)
  const id = useId()
  const path = `/rule-questionnaires/${encodeURIComponent(questionnaireId)}/answers`
  function edit() {setChoice(initial()); setText(own?.text ?? ''); setNote(own?.note ?? ''); setError(''); setEditing(true)}
  function saved(receipt: AnswerReceipt) {setPending(null); setEditing(false); onSaved(receipt)}
  async function save() {
    if (saving || pending) return
    const writes = !choices.length || choice === OWN
    const input: AnswerRequest = {operation_id: crypto.randomUUID(), question_key: question.key, expected_revision: own?.revision ?? 0, choice: writes ? null : choice || null, text: writes ? text.trim() || null : null, note: note.trim() || null}
    if (!input.choice && !input.text) {setError(writes ? '请写下你的说法。' : '请选择一项，或选“都不合适，我自己写”并写下你的说法。'); return}
    setSaving(true); setError(''); setPending(input)
    try {saved(checkedReceipt(await request<AnswerReceipt>(path, 'POST', input), questionnaireId, input))}
    catch (cause) {
      const status = (cause as {status?: number})?.status ?? 0
      if (status >= 400 && status < 500) {
        setPending(null); setError(`${message(cause)} 这次答复没有保存。`)
        if (status === 409) await onConflict()
      } else setError('答复的保存结果还不确定。请先查看保存结果，不要重复提交。')
    } finally {setSaving(false)}
  }
  async function recover() {
    if (!pending || saving) return
    setSaving(true)
    try {
      const receipt = await request<AnswerReceipt | null>(`${path}/${pending.operation_id}`)
      if (receipt) saved(checkedReceipt(receipt, questionnaireId, pending))
      else {setPending(null); setError('这次答复没有保存，可以重新保存。')}
    } catch (cause) {setError(`${message(cause)} 保存结果仍待确认。`)}
    finally {setSaving(false)}
  }
  const option = 'flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-1.5 text-sm hover:bg-muted'
  return <article aria-labelledby={`${id}-question`} className="grid gap-8 rounded-xl border bg-card p-6 xl:grid-cols-[minmax(0,58fr)_minmax(0,42fr)]">
    <div className="grid min-w-0 content-start gap-4 text-sm">
      {question.clauses.length > 0 && <p>{question.clauses.join('、')}</p>}
      <h3 id={`${id}-question`} className="text-base font-semibold">{question.question}</h3>
      {question.sources.length === 1 && !question.related.length && <Source source={question.sources[0]}/>}
      {question.why && <div className="grid gap-1"><p className="font-semibold text-muted-foreground">为什么要问</p><p className="whitespace-pre-wrap">{question.why}</p></div>}
      {question.sources.length > 1 && !question.related.length && question.sources.map(source => <Source key={source.clause} source={source} label={`${source.clause} 原文`}/>)}
      {question.related.length > 0 && <div className="grid gap-2"><h4 className="font-semibold">涉及的条款（{question.related.length}）</h4>
        <ul className="grid gap-4">{question.related.map((row, index) => {
          const source = question.sources.find(item => item.clause === row.clause)
          return <li key={index} className="grid gap-2"><p className="font-semibold">{row.clause}</p>{source && <Source source={source} label="原文"/>}<p>{row.question}</p>{row.example && <p>例：{row.example}</p>}</li>
        })}</ul>
      </div>}
      {question.relayed && <div className="grid gap-1 rounded-md bg-secondary px-4 py-3">
        {question.relayed.applied ? <><p>按共性答复处理为：{question.relayed.applied}。请确认。</p><p>依据的共性答复：{question.relayed.answer}</p></> : <p>已转达的答复：{question.relayed.answer}</p>}
        <p className="text-xs text-muted-foreground">{question.relayed.source} · {question.relayed.date}</p>
      </div>}
    </div>
    <div className="grid min-w-0 content-start gap-4 text-sm xl:border-l xl:pl-8">
      {(closed || !editing) && <div className="grid gap-2"><h4 className="font-semibold">我的答复</h4>
        {own ? <ul><AnswerRow question={question} answer={own}/></ul> : <p className="text-muted-foreground">没有作答。</p>}
        {!closed && own && <Button type="button" variant="secondary" size="sm" className="justify-self-start" onClick={edit}>修改我的答复</Button>}
      </div>}
      {!closed && editing && <form className="grid gap-3" onSubmit={event => {event.preventDefault(); void save()}}>
        <fieldset disabled={saving || Boolean(pending)} className="grid gap-3">
          <legend className="mb-1 font-semibold">{own ? '修改我的答复' : '我的答复'}</legend>
          {question.expect && <p className="text-muted-foreground">期待的答案：{question.expect}</p>}
          {choices.length > 0 ? <RadioGroup aria-labelledby={`${id}-question`} value={choice} onValueChange={setChoice}>
            {choices.map(row => <label key={row.key} className={option}><RadioGroupItem value={row.key} className="mt-1"/><span className="min-w-0"><span>{row.label}</span>{row.detail && <span className="block">{row.detail}</span>}</span></label>)}
            <div className="grid gap-2 rounded-md px-2 py-1.5">
              <label className="flex cursor-pointer items-start gap-2.5 font-semibold"><RadioGroupItem value={OWN} className="mt-1"/>都不合适，我自己写</label>
              <Textarea aria-label="我的说法" className="min-h-32" placeholder="写出你的说法" value={text} maxLength={4000} onFocus={() => setChoice(OWN)} onChange={event => {setText(event.target.value); setChoice(OWN)}}/>
            </div>
          </RadioGroup> : <div className="grid gap-2"><Label htmlFor={`${id}-text`}>你的答案</Label><Textarea id={`${id}-text`} value={text} maxLength={4000} onChange={event => setText(event.target.value)}/></div>}
          <div className="grid gap-2"><Label htmlFor={`${id}-note`}>备注（可选）</Label><Textarea id={`${id}-note`} value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></div>
          <div className="flex flex-wrap gap-2"><Button type="submit" size="sm">{saving ? '正在保存…' : '保存答复'}</Button>{own && <Button type="button" variant="ghost" size="sm" onClick={() => {setEditing(false); setError('')}}>取消</Button>}</div>
        </fieldset>
        {error && <p role="alert" className="text-destructive">{error}</p>}
        {pending && !saving && <Button type="button" variant="outline" size="sm" className="justify-self-start" onClick={() => void recover()}>查看保存结果</Button>}
      </form>}
      {others.length > 0 && <section aria-label="其他人的答复" className="grid gap-3 border-t pt-4"><h4 className="font-semibold">其他人的答复（{others.length}）</h4><ul className="grid gap-4">{others.map(answer => <AnswerRow key={`${answer.answerer}:${answer.first_answered_at}`} question={question} answer={answer}/>)}</ul></section>}
    </div>
  </article>
}
