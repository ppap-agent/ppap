// Stitch: projects/13261964020862199279/screens/cf4afdece8974624b77ad36da11cf669 (templates),
// 4c5d072e721f4760b801c13e4d63b3fa (template detail) and 6ab6498d58c94a1497dabd5309ede5b3 (rule detail).
import {Fragment, useEffect, useState, type MouseEvent} from 'react'
import {iconChevronRight, iconCircleExclamation, iconMagnifyingGlass} from '../components/icons'
import {Icon} from '../components/ui/icon'
import {request} from '../api'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {ReadSkeleton} from '../components/read-skeleton'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {message} from '../journey/remote'
import {rulesAddressKeys, writeAuditAddress} from './routes'
import {CustomerAvatar} from './customer-avatar'
import {clauseCount, RuleClause, RuleClauseList, RuleDetailHeader} from './rule-detail'
import {customerRulebook, ruleCoverage, rulebookLabel, type CustomerRules, type Rulebook, type RuleRow, type TypeRules} from './rule-templates'
import {QuestionnaireEntries} from './rule-questions'
import type {Question, QuestionnaireList, QuestionnaireView} from './rule-questions-contract'

/** Rules-page addresses: a customer or one rule. Every other rules key is dropped. */
export function rulesAddress(params: Record<string, string>): string {
  const url = new URL(location.href)
  for (const key of rulesAddressKeys) url.searchParams.delete(key)
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value)
  return `${url.pathname}${url.search}${url.hash}`
}

export function followRulesLink(event: MouseEvent<HTMLAnchorElement>, href: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  writeAuditAddress(href, 'push')
  window.scrollTo({top: 0})
}

export function RulesTrail({items}: {items: {label: string; href?: string}[]}) {
  return <nav aria-label="位置" className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm text-muted-foreground">{items.map((item, index) => <Fragment key={`${index}:${item.label}`}>
    {index > 0 && <Icon icon={iconChevronRight} aria-hidden="true" className="size-3.5 shrink-0"/>}
    {item.href ? <a className="min-h-9 content-center underline-offset-4 hover:text-foreground hover:underline" href={item.href} onClick={event => followRulesLink(event, item.href!)}>{item.label}</a> : <span aria-current="page" className="break-words text-foreground">{item.label}</span>}
  </Fragment>)}</nav>
}

const matches = (needle: string, ...values: string[]) => values.some(value => value.toLocaleLowerCase().includes(needle))

/** Registered customers on the left (short names), the chosen one's file types and current rules on the right; without a choice the first one is shown. */
export function CustomerRulesBrowser({entries, selectedId, loading, questionnaires = null}: {entries: CustomerRules[]; selectedId: string; loading: boolean; questionnaires?: QuestionnaireList | null}) {
  const [query, setQuery] = useState('')
  const needle = query.trim().toLocaleLowerCase()
  if (loading) return <div className="flex min-h-0 flex-1 flex-col"><ReadSkeleton label="正在读取客户规则…" kind="list" rows={5}/></div>
  const shown = needle ? entries.filter(entry => matches(needle, entry.customer.full_name, entry.customer.short_name, ...(entry.types ?? []).flatMap(type => [type.label, ...type.rules.map(rule => rule.title)]))) : entries
  // An address from before customers had ids (or a removed customer) opens the first customer rather than an error.
  const entry = entries.find(item => item.customer.customer_id === selectedId) ?? shown[0] ?? entries[0]
  // A search that names a rule narrows the chosen customer's types to it; a search that names the customer shows them all.
  const focus = needle && entry && !matches(needle, entry.customer.full_name, entry.customer.short_name) ? needle : ''
  // One surface split by a divider (Feishu master-detail): the list chooses, the table shows; the choice is not repeated as a heading.
  return <div className="grid min-h-0 flex-1 grid-cols-[17.5rem_minmax(0,1fr)] grid-rows-1 overflow-hidden rounded-lg bg-card">
    <aside aria-label="客户" className="flex min-h-0 min-w-0 flex-col gap-3 overflow-hidden border-r p-3">
      <div role="search" className="relative"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Icon icon={iconMagnifyingGlass} aria-hidden="true" className="size-3.5"/></span><Input aria-label="搜索客户" type="search" iconStart placeholder="搜索客户" maxLength={200} value={query} onChange={event => setQuery(event.target.value)}/></div>
      <ul className="grid min-h-0 flex-1 content-start gap-1 overflow-y-auto">{shown.map(item => {
        const {customer} = item, href = rulesAddress({template: customer.customer_id}), current = customer.customer_id === entry?.customer.customer_id, ratio = ruleCoverage(customer), described = `rules-customer-${customer.customer_id}`
        return <li key={customer.customer_id}><a aria-current={current ? 'page' : undefined} aria-describedby={described} href={href} onClick={event => followRulesLink(event, href)} className={`flex min-w-0 items-center gap-3 rounded-md px-3 py-2 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50 ${current ? 'bg-primary-soft text-primary' : 'hover:bg-muted'}`}>
          <CustomerAvatar name={customer.short_name}/>
          <span className="min-w-0 flex-1 space-y-0.5"><span className="block truncate text-sm" title={customer.short_name}>{customer.short_name}</span><span id={described} className="block text-xs tabular-nums text-muted-foreground">{ratio ? `${ratio.covered} / ${ratio.total} 类有生效规则` : '文件类型范围未配置'}</span></span>
        </a></li>
      })}
      {!shown.length && <li className="grid justify-items-center gap-3 py-12 text-center"><p className="text-sm text-muted-foreground">{query ? '没有匹配的客户' : '还没有客户规则'}</p>{query && <Button variant="outline" size="sm" onClick={() => setQuery('')}>清除搜索</Button>}</li>}
      </ul>
    </aside>
    <div className="flex min-h-0 min-w-0 flex-col">
      {entry && <CustomerRulesDetail key={`${entry.customer.customer_id}:${focus}`} entry={entry} focus={focus} questionnaires={questionnaires}/>}
    </div>
  </div>
}

const day = (value: string) => {
  const date = new Date(value)
  return value && Number.isFinite(date.getTime()) ? `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` : ''
}

/** The rulebook revision a customer's rules come from: whether it is still being clarified, how far the checkers have caught up, and its questions. */
function RulebookStatus({entry, questionnaires}: {entry: CustomerRules; questionnaires: QuestionnaireList | null}) {
  const rulebook = customerRulebook(entry), customerId = entry.customer.customer_id
  const open = rulebook && questionnaires?.questionnaires.find(row => row.customer_id === customerId && row.rule_version === rulebook.label && !row.closed)
  const pending = open ? open.total - open.answered : 0
  const entries = <QuestionnaireEntries list={questionnaires} customerId={customerId} current={rulebook?.label}/>
  if (!rulebook) return questionnaires?.questionnaires.some(row => row.customer_id === customerId) ? <div className="grid shrink-0 gap-2 border-b p-4">{entries}</div> : null
  return <div className="grid shrink-0 gap-3 border-b p-4">
    <div className="flex flex-wrap items-center gap-2">
      <h3 className="text-base font-semibold">{rulebook.label}</h3>
      {pending > 0 && <Badge variant="warning">待澄清</Badge>}
    </div>
    <p className="text-sm text-muted-foreground">
      {pending > 0 && <>还有 {pending} 个问题待确认。</>}
      检查程序已对齐 <span className="tabular-nums">{rulebook.checked} / {rulebook.clauses}</span> 条；其余条款审核时显示“规则已更新，暂未检查”，不会判为通过。
    </p>
    {entries}
  </div>
}

function CustomerRulesDetail({entry, focus, questionnaires}: {entry: CustomerRules; focus: string; questionnaires: QuestionnaireList | null}) {
  const {customer} = entry
  const types = (entry.types ?? []).flatMap(type => {
    if (!focus || matches(focus, type.label)) return [type]
    const rules = type.rules.filter(rule => matches(focus, rule.title))
    return rules.length ? [{...type, rules}] : []
  })
  const rows = types.flatMap((type): {type: TypeRules; rule: RuleRow | null; first: boolean}[] => type.rules.length ? type.rules.map((rule, index) => ({type, rule, first: index === 0})) : [{type, rule: null, first: true}])
  return <section aria-label={`${customer.short_name} 的规则`} className="flex min-h-0 flex-1 flex-col">
    <h2 className="sr-only">{customer.short_name}</h2>
    <RulebookStatus entry={entry} questionnaires={questionnaires}/>
    {entry.types === null ? <Alert className="m-4 w-auto"><Icon icon={iconCircleExclamation} aria-hidden="true"/><AlertTitle>这个客户的文件类型范围尚未配置</AlertTitle><AlertDescription>暂不显示规则覆盖。请联系我们配置后再查看。</AlertDescription></Alert> : <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      {rows.length ? <Table aria-label={`${customer.short_name} 的规则列表`} containerClassName="min-h-0 min-w-0 flex-1 overflow-auto"><TableHeader variant="muted" sticky><TableRow><TableHead>文件类型</TableHead><TableHead>规则</TableHead><TableHead>审核项</TableHead><TableHead>最近更新</TableHead></TableRow></TableHeader><TableBody>{rows.map(({type, rule, first}) => {
        const href = rule && rulesAddress({systemRule: rule.id}), updated = rule ? day(rule.updatedAt) : ''
        return <TableRow key={rule ? rule.id : `type:${type.code}`}>
          <TableCell className="whitespace-normal">{first ? type.label : <span className="sr-only">{type.label}</span>}</TableCell>
          <TableCell className="whitespace-normal">{rule && href ? <a className="rounded-sm text-primary outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50" href={href} onClick={event => followRulesLink(event, href)}>{rule.title}</a> : <span className="text-muted-foreground">暂无生效规则</span>}</TableCell>
          <TableCell>{rule?.clauses != null ? <span className="tabular-nums">{rule.clauses} 条{rule.checked != null && <span className="text-muted-foreground"> · 已对齐 {rule.checked}</span>}</span> : <span className="text-muted-foreground">—</span>}</TableCell>
          <TableCell>{updated ? <span className="text-muted-foreground tabular-nums">{updated}</span> : <span className="text-muted-foreground">—</span>}</TableCell>
        </TableRow>
      })}</TableBody></Table> : <p className="grid flex-1 place-items-center py-12 text-center text-sm text-muted-foreground">这个客户没有匹配的规则。</p>}
    </div>}
  </section>
}

type ClauseChange = {kind: 'same' | 'changed' | 'new'; note: string}
type RuleBody = {id: string; title: string; created_at: string; checked_clauses?: string[]; body: {rulebook?: Rulebook; rules?: {id: string; rule_to_check: string; category?: string; change?: ClauseChange}[]}}
const CHANGE = {same: '与上一修订相比意思不变', changed: '与上一修订相比意思变了', new: '本修订新增的条款'} as const

/** The questions on this rulebook revision, by the clause they are about; empty while there is no questionnaire for it. */
function useClauseQuestions(customerId: string | undefined, label: string | null) {
  const [read, setRead] = useState<{id: string; byClause: Map<string, Question[]>} | null>(null)
  useEffect(() => {
    setRead(null)
    if (!customerId || !label) return
    const controller = new AbortController()
    void request<QuestionnaireList>('/rule-questionnaires', 'GET', undefined, controller.signal).then(async list => {
      const row = list.questionnaires.find(item => item.customer_id === customerId && item.rule_version === label)
      if (!row) return
      const view = await request<QuestionnaireView>(`/rule-questionnaires/${encodeURIComponent(row.id)}`, 'GET', undefined, controller.signal)
      const byClause = new Map<string, Question[]>()
      for (const question of view.questions) {
        for (const clause of new Set(question.items.map(item => item.split('#', 1)[0]!))) byClause.set(clause, [...(byClause.get(clause) ?? []), question])
      }
      if (!controller.signal.aborted) setRead({id: row.id, byClause})
    }).catch(() => {})
    return () => controller.abort()
  }, [customerId, label])
  return read
}

/** One rule, read only: its clauses as published. Nothing here uploads, edits, deletes or changes which version is in effect. */
export function RuleDetail({ruleId, customer}: {ruleId: string; customer: {id: string; name: string} | undefined}) {
  const [read, setRead] = useState<{rule: RuleBody | null; error: string}>({rule: null, error: ''})
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setRead({rule: null, error: ''})
    void request<RuleBody>(`/rules/${encodeURIComponent(ruleId)}`, 'GET', undefined, controller.signal)
      .then(rule => {if (!controller.signal.aborted) setRead({rule, error: ''})})
      .catch(cause => {if (!controller.signal.aborted) setRead({rule: null, error: message(cause)})})
    return () => controller.abort()
  }, [ruleId, attempt])
  const rulebook = read.rule?.body.rulebook ? rulebookLabel(read.rule.body.rulebook) : null
  const questions = useClauseQuestions(customer?.id, rulebook)
  const clauses = read.rule?.body.rules ?? [], checked = read.rule?.checked_clauses ? new Set(read.rule.checked_clauses) : null
  return <div className="max-w-4xl space-y-6">
    {customer && <RulesTrail items={[{label: customer.name, href: rulesAddress({template: customer.id})}]}/>}
    {read.error && <Alert variant="destructive"><AlertTitle>规则未能读取</AlertTitle><AlertDescription><p>{read.error}</p><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></AlertDescription></Alert>}
    {!read.rule && !read.error && <ReadSkeleton label="正在读取规则…" kind="detail" rows={6}/>}
    {read.rule && <>
      <RuleDetailHeader title={read.rule.title}>
        {rulebook && <span className="text-sm">{rulebook}</span>}
        <span className="text-sm text-muted-foreground">{clauseCount(clauses.length)}</span>
        {checked && <span className="text-sm text-muted-foreground tabular-nums">检查程序已对齐 {checked.size} / {clauses.length}</span>}
      </RuleDetailHeader>
      <RuleClauseList className="border-t">{clauses.map((clause, index) => {
        const asked = questions?.byClause.get(clause.id) ?? []
        return <RuleClause key={clause.id} number={index + 1} title={clause.category} text={clause.rule_to_check}
          aside={checked && <Badge variant={checked.has(clause.id) ? 'success' : 'outline'}>{checked.has(clause.id) ? '已对齐' : '暂未检查'}</Badge>}
          details={(clause.change || asked.length > 0) && <div className="grid gap-3 text-sm">
            {clause.change && <p className="text-muted-foreground">{CHANGE[clause.change.kind]}{clause.change.kind === 'same' ? '。' : '：'}{clause.change.note}</p>}
            {asked.length > 0 && questions && customer && <div className="grid gap-1.5">
              <p className="font-medium">待确认问题</p>
              <ul className="grid gap-1.5">{asked.map(question => {
                const href = `${rulesAddress({template: customer.id, ruleQuestions: questions.id}).split('#', 1)[0]}#${encodeURIComponent(question.key)}`
                return <li key={question.key}><a className="rounded-sm text-primary outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50" href={href} onClick={event => followRulesLink(event, href)}>{question.question}</a>
                  <span className="text-muted-foreground"> · {question.answers.length ? `${question.answers.length} 人已答复` : '尚未答复'}</span></li>
              })}</ul>
            </div>}
          </div>}/>
      })}</RuleClauseList>
    </>}
  </div>
}
