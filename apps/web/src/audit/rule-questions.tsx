import {useCallback, useEffect, useId, useRef, useState, type ReactNode} from 'react'
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
import {RuleDetailHeader} from './rule-detail'
import {followRulesLink, rulesAddress, RulesTrail} from './rule-templates-view'
import {applyReceipt, checkedReceipt, choiceLabel, groupQuestions, RELAYED, type AnswerReceipt, type AnswerRequest, type Question, type QuestionAnswer, type QuestionnaireList, type QuestionnaireSummary, type QuestionnaireView} from './rule-questions-contract'

const progress = (row: Pick<QuestionnaireSummary, 'answered' | 'total'>) => `已答 ${row.answered} / ${row.total} 题`

/** One open/close button with its count beside it; the count describes the button, it is not part of its name. */
function Toggle({label, count, children}: {label: string; count: number; children: ReactNode}) {
  const [open, setOpen] = useState(false)
  const id = useId()
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center gap-1"><Button type="button" variant="quiet" size="xs" aria-expanded={open} aria-controls={`${id}-panel`} aria-describedby={`${id}-count`} onClick={() => setOpen(value => !value)}><Icon icon={iconChevronRight} aria-hidden="true" className={open ? 'rotate-90' : ''}/>{label}</Button><span id={`${id}-count`} className="text-xs tabular-nums text-muted-foreground">{count} 条</span></div>
    <div id={`${id}-panel`} hidden={!open}>{children}</div>
  </div>
}

/** On the rules page, under a customer: each version of its rules that has questions waiting for the customer. */
export function QuestionnaireEntries({list, customerId}: {list: QuestionnaireList | null; customerId: string}) {
  const rows = list?.questionnaires.filter(row => row.customer_id === customerId) ?? []
  if (!rows.length) return null
  return <div className="grid shrink-0 gap-2 border-b p-4">
    {rows.map(row => {
      const href = rulesAddress({template: customerId, ruleQuestions: row.id})
      return <Item key={row.id} variant="outline" size="sm" asChild>
        <a href={href} aria-labelledby={`rq-${row.id}-title`} aria-describedby={`rq-${row.id}-progress`} onClick={event => followRulesLink(event, href)}>
          <ItemContent><ItemTitle id={`rq-${row.id}-title`} wrap>{row.rule_version} 待确认问题</ItemTitle><ItemDescription id={`rq-${row.id}-progress`}>{row.closed ? `已关闭 · ${progress(row)}` : progress(row)}</ItemDescription></ItemContent>
          <ItemActions aria-hidden="true"><span className="flex items-center gap-1 text-sm text-primary">{row.closed ? '查看' : '去作答'}<Icon icon={iconChevronRight} className="size-3.5"/></span></ItemActions>
        </a>
      </Item>
    })}
  </div>
}

/** One questionnaire: the cards grouped with common questions first; answering is the page's one task. */
export function RuleQuestionnairePage({questionnaireId}: {questionnaireId: string}) {
  const [view, setView] = useState<QuestionnaireView | null>(null)
  const [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [shown, setShown] = useState<Record<string, boolean> | null>(null)
  const [mounted, setMounted] = useState<Record<string, boolean>>({})
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
    setView(null); setError(''); setShown(null)
    void read()
    return () => {generation.current++}
  }, [read])
  const groups = view ? groupQuestions(view.questions) : []
  // The first group opens by itself; the others wait for their button.
  useEffect(() => {
    if (view && shown === null && groups.length) {setShown({[groups[0].key]: true}); setMounted({[groups[0].key]: true})}
  }, [view, shown, groups])
  const toggle = (key: string) => {setShown(current => ({...current, [key]: !current?.[key]})); setMounted(current => ({...current, [key]: true}))}
  const conflict = async () => {setNotice('问卷已有更新，已重新读取，请核对后再保存。'); await read()}

  if (error && !view) return <div className="max-w-4xl space-y-6"><Alert variant="destructive"><AlertTitle>待确认问题未能读取</AlertTitle><AlertDescription><p>{error}</p><Button variant="outline" size="sm" onClick={() => {setError(''); void read()}}>重新读取</Button></AlertDescription></Alert></div>
  if (!view) return <div className="max-w-4xl"><ReadSkeleton label="正在读取待确认问题…" kind="detail" rows={6}/></div>
  return <div className="max-w-4xl space-y-6">
    <RulesTrail items={[{label: view.customer_name, href: rulesAddress({template: view.customer_id})}]}/>
    <RuleDetailHeader title={`${view.rule_version} 待确认问题`}>
      <span className="text-sm tabular-nums text-muted-foreground">{progress(view)} · 我答了 {view.mine} 题</span>
    </RuleDetailHeader>
    {view.closed ? <Alert><AlertTitle>问卷已关闭</AlertTitle><AlertDescription>答复只能查看，不能再作答或修改。</AlertDescription></Alert>
      : <p className="text-sm leading-relaxed text-muted-foreground">这些问题来自对这一版规则原文的逐条检查：每处都附了例子，不答，这份文件就得不到确定结论。每张卡单独保存，不用一次答完；同一张卡每个人的答复分开记录。</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    {groups.map(group => {
      const open = Boolean(shown?.[group.key]), id = `rq-group-${group.key}`
      return <section key={group.key} aria-labelledby={`${id}-title`} className="space-y-4 border-t pt-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 id={`${id}-title`} className="text-base font-semibold">{group.label}</h2>
          <span id={`${id}-count`} className="text-sm tabular-nums text-muted-foreground">{progress({answered: group.answered, total: group.questions.length})}</span>
          <Button type="button" variant="outline" size="sm" aria-expanded={open} aria-controls={`${id}-panel`} aria-labelledby={`${id}-toggle ${id}-title`} aria-describedby={`${id}-count`} onClick={() => toggle(group.key)}><Icon icon={iconChevronRight} aria-hidden="true" className={open ? 'rotate-90' : ''}/><span id={`${id}-toggle`}>{open ? '收起' : '展开'}</span></Button>
        </div>
        <div id={`${id}-panel`} hidden={!open} className="space-y-4">
          {mounted[group.key] && group.questions.map(question => <QuestionCard key={question.key} question={question} questionnaireId={view.id} closed={view.closed} onSaved={receipt => setView(current => current && applyReceipt(current, receipt))} onConflict={conflict}/>)}
        </div>
      </section>
    })}
  </div>
}

function AnswerBody({question, answer}: {question: Question; answer: Pick<QuestionAnswer, 'choice' | 'text' | 'note'>}) {
  const chosen = choiceLabel(question, answer.choice)
  return <>
    {chosen && <p>选择：{chosen}</p>}
    {answer.text && <p className="whitespace-pre-wrap break-words">{answer.text}</p>}
    {answer.note && <p className="whitespace-pre-wrap break-words text-muted-foreground">备注：{answer.note}</p>}
  </>
}

function QuestionCard({question, questionnaireId, closed, onSaved, onConflict}: {question: Question; questionnaireId: string; closed: boolean; onSaved: (receipt: AnswerReceipt) => void; onConflict: () => Promise<void>}) {
  const own = question.answers.find(answer => answer.mine)
  const [editing, setEditing] = useState(!own)
  const [choice, setChoice] = useState(own?.choice ?? ''), [text, setText] = useState(own?.text ?? ''), [note, setNote] = useState(own?.note ?? '')
  const [saving, setSaving] = useState(false), [error, setError] = useState('')
  const [pending, setPending] = useState<AnswerRequest | null>(null)
  const id = useId()
  const choices = [...(question.relayed ? [{key: RELAYED, label: choiceLabel(question, RELAYED), detail: ''}] : []), ...question.options]
  const path = `/rule-questionnaires/${encodeURIComponent(questionnaireId)}/answers`
  function edit() {setChoice(own?.choice ?? ''); setText(own?.text ?? ''); setNote(own?.note ?? ''); setError(''); setEditing(true)}
  function saved(receipt: AnswerReceipt) {setPending(null); setEditing(false); onSaved(receipt)}
  async function save() {
    if (saving || pending) return
    const input: AnswerRequest = {operation_id: crypto.randomUUID(), question_key: question.key, expected_revision: own?.revision ?? 0, choice: choice || null, text: text.trim() || null, note: note.trim() || null}
    if (!input.choice && !input.text) {setError(choices.length ? '请选择一项，或写下你的答案。' : '请写下你的答案。'); return}
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
  return <article aria-labelledby={`${id}-question`} className="space-y-3 rounded-lg border bg-card p-4">
    {question.clauses.length > 0 && <p className="text-xs text-muted-foreground">{question.clauses.join('、')}</p>}
    <h3 id={`${id}-question`} className="font-medium leading-relaxed">{question.question}</h3>
    {question.why && <p className="whitespace-pre-wrap text-sm leading-relaxed"><span className="text-muted-foreground">为什么要问：</span>{question.why}</p>}
    {question.related.length > 0 && <Toggle label="涉及的条款" count={question.related.length}><ul className="space-y-2 text-sm">{question.related.map((row, index) => <li key={index} className="space-y-1 border-l-2 pl-3"><p className="text-xs text-muted-foreground">{row.clause}</p><p>{row.question}</p>{row.example && <p className="text-muted-foreground">例：{row.example}</p>}</li>)}</ul></Toggle>}
    {question.relayed && <div className="space-y-1 rounded-md bg-muted/50 p-3 text-sm">
      {question.relayed.applied ? <><p>按共性答复处理为：{question.relayed.applied}。请确认。</p><p className="text-muted-foreground">依据的共性答复：{question.relayed.answer}</p></> : <p>已转达的答复：{question.relayed.answer}</p>}
      <p className="text-xs text-muted-foreground">{question.relayed.source} · {question.relayed.date}</p>
    </div>}
    {question.answers.length > 0 && <ul aria-label="答复" className="space-y-3">{question.answers.filter(answer => !(answer.mine && editing)).map(answer => <li key={`${answer.answerer}:${answer.first_answered_at}`} className="space-y-1 border-l-2 border-primary-line pl-3 text-sm">
      <p className="text-xs text-muted-foreground">{answer.mine ? `${answer.answerer}（我）` : answer.answerer} · <time dateTime={answer.answered_at}>{displayTime(answer.answered_at)}</time>{answer.history.length > 0 && ' · 已修改'}</p>
      <AnswerBody question={question} answer={answer}/>
      {answer.history.length > 0 && <Toggle label="修改记录" count={answer.history.length}><ol className="space-y-2">{[...answer.history].reverse().map(old => <li key={old.revision} className="space-y-1 text-muted-foreground"><p className="text-xs">改前 · <time dateTime={old.answered_at}>{displayTime(old.answered_at)}</time></p><AnswerBody question={question} answer={old}/></li>)}</ol></Toggle>}
    </li>)}</ul>}
    {!closed && !editing && own && <Button type="button" variant="outline" size="sm" onClick={edit}>修改我的答复</Button>}
    {!closed && editing && <form className="space-y-3 border-t pt-3" onSubmit={event => {event.preventDefault(); void save()}}>
      <fieldset disabled={saving || Boolean(pending)} className="space-y-3">
        <legend className="text-sm font-medium">{own ? '修改我的答复' : '我的答复'}</legend>
        {question.expect && <p className="text-xs text-muted-foreground">期待的答案：{question.expect}</p>}
        {choices.length > 0 && <div className="space-y-2">
          <RadioGroup aria-labelledby={`${id}-question`} value={choice} onValueChange={setChoice}>
            {choices.map(option => <label key={option.key} className="flex items-start gap-2 text-sm leading-relaxed"><RadioGroupItem value={option.key} className="mt-1"/><span className="min-w-0"><span>{option.label}</span>{option.detail && <span className="block text-muted-foreground">{option.detail}</span>}</span></label>)}
          </RadioGroup>
          {choice && <Button type="button" variant="quiet" size="xs" onClick={() => setChoice('')}>不选</Button>}
        </div>}
        <div className="space-y-2"><Label htmlFor={`${id}-text`}>{choices.length ? '自己写答案（可选）' : '你的答案'}</Label><Textarea id={`${id}-text`} value={text} maxLength={4000} onChange={event => setText(event.target.value)}/></div>
        <div className="space-y-2"><Label htmlFor={`${id}-note`}>备注（可选）</Label><Textarea id={`${id}-note`} value={note} maxLength={2000} onChange={event => setNote(event.target.value)}/></div>
        <div className="flex flex-wrap gap-2"><Button type="submit" size="sm">{saving ? '正在保存…' : '保存答复'}</Button>{own && <Button type="button" variant="ghost" size="sm" onClick={() => {setEditing(false); setError('')}}>取消</Button>}</div>
      </fieldset>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {pending && !saving && <Button type="button" variant="outline" size="sm" onClick={() => void recover()}>查看保存结果</Button>}
    </form>}
  </article>
}
