import {useCallback, useEffect, useState} from 'react'
import {request} from '../api'
import {iconChevronRight} from '../components/icons'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Icon} from '../components/ui/icon'
import {Item, ItemActions, ItemContent, ItemDescription, ItemTitle} from '../components/ui/item'
import {Label} from '../components/ui/label'
import {Switch} from '../components/ui/switch'
import {ReadSkeleton} from '../components/read-skeleton'
import {message} from '../journey/remote'
import {RuleClause, RuleClauseList} from './rule-detail'
import {QuestionnaireEntries, reveal} from './rule-questions'
import type {Question, QuestionnaireList, QuestionnaireView} from './rule-questions-contract'
import {followRulesLink, rulesAddress, RulesTrail} from './rule-templates-view'
import {CHANGE_LABEL, changeSummary, parseRevisionKey, questionnaireFor, questionsByClause, revisionKey, revisionLabel, type RevisionList, type RevisionSummary, type RevisionView} from './rulebook-contract'

const progress = (row: {answered: number; total: number}) => `待确认问题 已答 ${row.answered} / ${row.total}`
const status = (summary: Pick<RevisionSummary, 'in_audit'>) => summary.in_audit ? '审核用' : '评审中，审核未使用'

/** Under a customer on the rules page: its rulebook revisions, then any questionnaire not reached through one. */
export function CustomerRulebook({customerId, questionnaires}: {customerId: string; questionnaires: QuestionnaireList | null}) {
  const [revisions, setRevisions] = useState<RevisionSummary[]>([])
  useEffect(() => {
    const controller = new AbortController()
    setRevisions([])
    // Rules stay readable without it: a failed read only leaves the revisions out.
    void request<RevisionList>(`/rulebooks/${encodeURIComponent(customerId)}`, 'GET', undefined, controller.signal)
      .then(list => {if (!controller.signal.aborted) setRevisions(list.revisions)})
      .catch(() => {})
    return () => controller.abort()
  }, [customerId])
  const shown = new Set(revisions.flatMap(row => questionnaireFor(questionnaires, customerId, row)?.id ?? []))
  return <>
    {revisions.length > 0 && <div className="grid shrink-0 gap-2 border-b p-4">
      {[...revisions].reverse().map(row => {
        const href = rulesAddress({template: customerId, rulebook: revisionKey(row)}), id = `rb-${revisionKey(row)}`
        const questionnaire = questionnaireFor(questionnaires, customerId, row)
        const facts = [status(row), `${row.clause_count} 条`, changeSummary(row), questionnaire ? progress(questionnaire) : ''].filter(Boolean).join(' · ')
        return <Item key={id} variant="outline" size="sm" asChild>
          <a href={href} aria-labelledby={`${id}-title`} aria-describedby={`${id}-facts`} onClick={event => followRulesLink(event, href)}>
            <ItemContent><ItemTitle id={`${id}-title`} wrap>规则 {revisionLabel(row)}</ItemTitle><ItemDescription id={`${id}-facts`}>{facts}</ItemDescription></ItemContent>
            <ItemActions aria-hidden="true"><span className="flex items-center gap-1 text-sm text-primary">查看<Icon icon={iconChevronRight} className="size-3.5"/></span></ItemActions>
          </a>
        </Item>
      })}
    </div>}
    <QuestionnaireEntries list={questionnaires} customerId={customerId} shown={shown}/>
  </>
}

/** One rulebook revision for review: every clause by document type, how it changed, and the questions asked about it. */
export function RulebookRevisionPage({customer, revision, questionnaires}: {customer: {id: string; name: string}; revision: string; questionnaires: QuestionnaireList | null}) {
  const ref = parseRevisionKey(revision)
  const [view, setView] = useState<RevisionView | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [questions, setQuestions] = useState<Map<string, Question[]>>(new Map())
  const [asked, setAsked] = useState(false)
  const [current, setCurrent] = useState(0)
  useEffect(() => {
    if (!ref) return
    const controller = new AbortController()
    setView(null); setError('')
    void request<RevisionView>(`/rulebooks/${encodeURIComponent(customer.id)}/${encodeURIComponent(ref.version)}/${ref.revision}`, 'GET', undefined, controller.signal)
      .then(next => {if (!controller.signal.aborted) setView(next)})
      .catch(cause => {if (!controller.signal.aborted) setError(message(cause))})
    return () => controller.abort()
  }, [customer.id, revision, attempt])
  const questionnaire = ref ? questionnaireFor(questionnaires, customer.id, ref) : undefined
  useEffect(() => {
    setQuestions(new Map())
    if (!questionnaire) return
    const controller = new AbortController()
    // The clauses read without it; a failed read only leaves the questions out.
    void request<QuestionnaireView>(`/rule-questionnaires/${encodeURIComponent(questionnaire.id)}`, 'GET', undefined, controller.signal)
      .then(next => {if (!controller.signal.aborted) setQuestions(questionsByClause(next.questions))})
      .catch(() => {})
    return () => controller.abort()
  }, [questionnaire?.id])
  const jump = useCallback((index: number) => {
    setCurrent(index)
    reveal(document.getElementById(`rb-doc-${index}`))
  }, [])

  const trail = <RulesTrail items={[{label: customer.name, href: rulesAddress({template: customer.id})}]}/>
  if (!ref) return <div className="max-w-3xl space-y-4">{trail}<Alert variant="destructive"><AlertTitle>没有这份规则修订</AlertTitle><AlertDescription>请从客户的规则页重新进入。</AlertDescription></Alert></div>
  if (error) return <div className="max-w-3xl space-y-4">{trail}<Alert variant="destructive"><AlertTitle>规则修订未能读取</AlertTitle><AlertDescription><p>{error}</p><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></AlertDescription></Alert></div>
  if (!view) return <ReadSkeleton label="正在读取规则修订…" kind="detail" rows={6}/>
  const cardsOf = (clause: string) => questions.get(clause) ?? []
  const documents = view.documents.map((document, index) => ({...document, index, clauses: document.clauses.filter(clause => !asked || cardsOf(clause.id).length > 0)})).filter(document => document.clauses.length > 0)
  const questionnaireHref = questionnaire && rulesAddress({template: customer.id, ruleQuestions: questionnaire.id}).split('#', 1)[0]!
  return <div className="grid items-start gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
    <aside className="grid gap-4 lg:sticky lg:top-0">
      {trail}
      <div className="grid gap-2">
        <h1 className="text-xl font-semibold">规则 {revisionLabel(view)}</h1>
        <p className="flex flex-wrap items-center gap-2 text-sm"><Badge variant={view.in_audit ? 'success' : 'info'}>{view.in_audit ? '审核用' : '评审中'}</Badge>{!view.in_audit && <span className="text-muted-foreground">审核未使用这份修订</span>}</p>
        <p className="text-sm text-muted-foreground">{view.clause_count} 条{view.changes ? `；${changeSummary(view)}` : ''}</p>
      </div>
      {questionnaire && questionnaireHref && <Item variant="outline" size="sm" asChild>
        <a href={questionnaireHref} aria-labelledby="rb-questions-title" aria-describedby="rb-questions-progress" onClick={event => followRulesLink(event, questionnaireHref)}>
          <ItemContent><ItemTitle id="rb-questions-title">待确认问题</ItemTitle><ItemDescription id="rb-questions-progress">已答 {questionnaire.answered} / {questionnaire.total}{questionnaire.closed ? ' · 已关闭' : ''}</ItemDescription></ItemContent>
          <ItemActions aria-hidden="true"><span className="flex items-center gap-1 text-sm text-primary">{questionnaire.closed ? '查看' : '去作答'}<Icon icon={iconChevronRight} className="size-3.5"/></span></ItemActions>
        </a>
      </Item>}
      {questions.size > 0 && <div className="flex items-center gap-2"><Switch id="rb-asked" checked={asked} onCheckedChange={setAsked}/><Label htmlFor="rb-asked">只看有待确认问题的条款</Label></div>}
      <nav aria-label="文件类型"><ul className="grid gap-1">{documents.map(document => <li key={document.index}>
        <button type="button" aria-current={document.index === current ? 'true' : undefined} aria-describedby={`rb-nav-${document.index}`} onClick={() => jump(document.index)} className={`flex min-h-10 w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 ${document.index === current ? 'bg-primary-soft font-semibold text-primary' : 'hover:bg-muted'}`}>
          <span className="min-w-0 break-words">{document.title}</span><span id={`rb-nav-${document.index}`} className="shrink-0 tabular-nums">{document.clauses.length} 条</span>
        </button>
      </li>)}</ul></nav>
    </aside>
    <div className="grid min-w-0 gap-8">
      {!documents.length && <p className="py-12 text-center text-sm text-muted-foreground">没有挂着待确认问题的条款。</p>}
      {documents.map(document => <section key={document.index} id={`rb-doc-${document.index}`} aria-labelledby={`rb-doc-${document.index}-title`} className="grid scroll-mt-4 gap-2">
        <h2 id={`rb-doc-${document.index}-title`} className="text-base font-semibold">{document.title}</h2>
        <RuleClauseList className="border-t">{document.clauses.map(clause => {
          const cards = cardsOf(clause.id), number = Number(clause.id.split(':')[1]) || 0
          return <RuleClause key={clause.id} number={number} title={clause.category} text={clause.text}
            aside={clause.change && <Badge variant={clause.change.kind === 'same' ? 'outline' : clause.change.kind === 'new' ? 'info' : 'warning'}>{CHANGE_LABEL[clause.change.kind]}</Badge>}
            details={(clause.change?.note || cards.length > 0) && <div className="grid gap-3 text-sm">
              {clause.change?.note && <p className="text-muted-foreground">{view.base ? `相对 ${revisionLabel(view.base)}：` : ''}{clause.change.note}</p>}
              {cards.length > 0 && questionnaireHref && <div className="grid gap-1.5">
                <p className="font-medium">待确认问题</p>
                <ul className="grid gap-1.5">{cards.map(card => {
                  const href = `${questionnaireHref}#${encodeURIComponent(card.key)}`
                  return <li key={card.key}><a className="rounded-sm text-primary outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50" href={href} onClick={event => followRulesLink(event, href)}>{card.question}</a>
                    <span className="text-muted-foreground"> · {card.answers.length ? `${card.answers.length} 人已答复` : '尚未答复'}</span></li>
                })}</ul>
              </div>}
            </div>}/>
        })}</RuleClauseList>
      </section>)}
    </div>
  </div>
}
