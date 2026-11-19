// Stitch: projects/13261964020862199279/screens/cf4afdece8974624b77ad36da11cf669 (templates),
// 4c5d072e721f4760b801c13e4d63b3fa (template detail) and 6ab6498d58c94a1497dabd5309ede5b3 (rule detail).
import {Fragment, useEffect, useState, type MouseEvent} from 'react'
import {AlertCircle, ChevronRight, Search} from 'lucide-react'
import {request} from '../api'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {ReadSkeleton} from '../components/read-skeleton'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {message} from '../journey/remote'
import {rulesAddressKeys, writeAuditAddress} from './routes'
import {CustomerAvatar} from './customer-avatar'
import {clauseCount, RuleClause, RuleClauseList, RuleDetailHeader} from './rule-detail'
import {ruleCoverage, type CustomerRules, type RuleRow, type TypeRules} from './rule-templates'

/** Rules-page addresses: a customer or one rule. Every other rules key is dropped. */
export function rulesAddress(params: Record<string, string>): string {
  const url = new URL(location.href)
  for (const key of rulesAddressKeys) url.searchParams.delete(key)
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value)
  return `${url.pathname}${url.search}${url.hash}`
}

function followRulesLink(event: MouseEvent<HTMLAnchorElement>, href: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  writeAuditAddress(href, 'push')
  window.scrollTo({top: 0})
}

export function RulesTrail({items}: {items: {label: string; href?: string}[]}) {
  return <nav aria-label="位置" className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm text-muted-foreground">{items.map((item, index) => <Fragment key={`${index}:${item.label}`}>
    {index > 0 && <ChevronRight aria-hidden="true" className="size-3.5 shrink-0"/>}
    {item.href ? <a className="min-h-9 content-center underline-offset-4 hover:text-foreground hover:underline" href={item.href} onClick={event => followRulesLink(event, item.href!)}>{item.label}</a> : <span aria-current="page" className="break-words text-foreground">{item.label}</span>}
  </Fragment>)}</nav>
}

const matches = (needle: string, ...values: string[]) => values.some(value => value.toLocaleLowerCase().includes(needle))

/** Registered customers on the left (short names), the chosen one's file types and current rules on the right; without a choice the first one is shown. */
export function CustomerRulesBrowser({entries, selectedId, loading}: {entries: CustomerRules[]; selectedId: string; loading: boolean}) {
  const [query, setQuery] = useState('')
  const needle = query.trim().toLocaleLowerCase()
  if (loading) return <div className="flex min-h-0 flex-1 flex-col"><ReadSkeleton label="正在读取客户规则…" kind="list" rows={5}/></div>
  const shown = needle ? entries.filter(entry => matches(needle, entry.customer.full_name, entry.customer.short_name, ...(entry.types ?? []).flatMap(type => [type.label, ...type.rules.map(rule => rule.title)]))) : entries
  // An address from before customers had ids (or a removed customer) opens the first customer rather than an error.
  const entry = entries.find(item => item.customer.customer_id === selectedId) ?? shown[0] ?? entries[0]
  // A search that names a rule narrows the chosen customer's types to it; a search that names the customer shows them all.
  const focus = needle && entry && !matches(needle, entry.customer.full_name, entry.customer.short_name) ? needle : ''
  return <div className="grid min-h-0 flex-1 grid-cols-[17.5rem_minmax(0,1fr)] grid-rows-1 gap-4">
    <aside aria-label="客户" className="flex min-h-0 min-w-0 flex-col gap-3 overflow-hidden rounded-lg border bg-card p-3">
      <div role="search" className="relative"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search aria-hidden="true" className="size-3.5"/></span><Input aria-label="搜索客户" type="search" iconStart placeholder="搜索客户" maxLength={200} value={query} onChange={event => setQuery(event.target.value)}/></div>
      <ul className="grid min-h-0 flex-1 content-start gap-1 overflow-y-auto">{shown.map(item => {
        const {customer} = item, href = rulesAddress({template: customer.customer_id}), current = customer.customer_id === entry?.customer.customer_id, ratio = ruleCoverage(customer), described = `rules-customer-${customer.customer_id}`
        return <li key={customer.customer_id}><a aria-current={current ? 'page' : undefined} aria-describedby={described} href={href} onClick={event => followRulesLink(event, href)} className={`flex min-w-0 items-center gap-3 rounded-md px-3 py-2 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50 ${current ? 'bg-primary-soft text-primary' : 'hover:bg-muted'}`}>
          <CustomerAvatar name={customer.short_name}/>
          <span className="min-w-0 flex-1 space-y-0.5"><span className="block truncate text-sm" title={customer.full_name}>{customer.short_name}</span><span id={described} className="block text-xs tabular-nums text-muted-foreground">{ratio ? `${ratio.covered} / ${ratio.total} 类有生效规则` : '文件类型范围未配置'}</span></span>
        </a></li>
      })}
      {!shown.length && <li className="grid justify-items-center gap-3 py-12 text-center"><p className="text-sm text-muted-foreground">{query ? '没有匹配的客户' : '还没有客户规则'}</p>{query && <Button variant="outline" size="sm" onClick={() => setQuery('')}>清除搜索</Button>}</li>}
      </ul>
    </aside>
    <div className="flex min-h-0 min-w-0 flex-col">
      {entry && <CustomerRulesDetail key={`${entry.customer.customer_id}:${focus}`} entry={entry} focus={focus}/>}
    </div>
  </div>
}

const day = (value: string) => {
  const date = new Date(value)
  return value && Number.isFinite(date.getTime()) ? `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` : ''
}

function CustomerRulesDetail({entry, focus}: {entry: CustomerRules; focus: string}) {
  const {customer} = entry
  const types = (entry.types ?? []).flatMap(type => {
    if (!focus || matches(focus, type.label)) return [type]
    const rules = type.rules.filter(rule => matches(focus, rule.title))
    return rules.length ? [{...type, rules}] : []
  })
  const rows = types.flatMap((type): {type: TypeRules; rule: RuleRow | null; first: boolean}[] => type.rules.length ? type.rules.map((rule, index) => ({type, rule, first: index === 0})) : [{type, rule: null, first: true}])
  return <section aria-label={`${customer.full_name} 的规则`} className="flex min-h-0 flex-1 flex-col gap-3">
    <header className="flex items-center gap-3 rounded-lg border bg-card p-4">
      <CustomerAvatar name={customer.short_name}/>
      <h2 className="min-w-0 truncate text-lg font-semibold" title={customer.full_name}>{customer.full_name}</h2>
    </header>
    {entry.types === null ? <Alert><AlertCircle aria-hidden="true"/><AlertTitle>这个客户的文件类型范围尚未配置</AlertTitle><AlertDescription>暂不显示规则覆盖。请联系我们配置后再查看。</AlertDescription></Alert> : <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg border bg-card">
      {rows.length ? <Table aria-label={`${customer.full_name} 的规则列表`} containerClassName="min-h-0 min-w-0 flex-1 overflow-auto"><TableHeader variant="muted" sticky><TableRow><TableHead>文件类型</TableHead><TableHead>规则</TableHead><TableHead>审核项</TableHead><TableHead>最近更新</TableHead></TableRow></TableHeader><TableBody>{rows.map(({type, rule, first}) => {
        const href = rule && rulesAddress({systemRule: rule.id}), updated = rule ? day(rule.updatedAt) : ''
        return <TableRow key={rule ? rule.id : `type:${type.code}`}>
          <TableCell className="whitespace-normal">{first ? type.label : <span className="sr-only">{type.label}</span>}</TableCell>
          <TableCell className="whitespace-normal">{rule && href ? <a className="rounded-sm font-medium text-primary outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50" href={href} onClick={event => followRulesLink(event, href)}>{rule.title}</a> : <span className="text-muted-foreground">暂无生效规则</span>}</TableCell>
          <TableCell>{rule?.clauses != null ? <span className="tabular-nums">{rule.clauses} 条</span> : <span className="text-muted-foreground">—</span>}</TableCell>
          <TableCell>{updated ? <span className="text-muted-foreground tabular-nums">{updated}</span> : <span className="text-muted-foreground">—</span>}</TableCell>
        </TableRow>
      })}</TableBody></Table> : <p className="grid flex-1 place-items-center py-12 text-center text-sm text-muted-foreground">这个客户没有匹配的规则。</p>}
    </div>}
  </section>
}

type RuleBody = {id: string; title: string; created_at: string; body: {rules?: {id: string; rule_to_check: string; category?: string}[]}}

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
  return <div className="max-w-4xl space-y-6">
    {customer && <RulesTrail items={[{label: customer.name, href: rulesAddress({template: customer.id})}]}/>}
    {read.error && <Alert variant="destructive"><AlertTitle>规则未能读取</AlertTitle><AlertDescription><p>{read.error}</p><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></AlertDescription></Alert>}
    {!read.rule && !read.error && <ReadSkeleton label="正在读取规则…" kind="detail" rows={6}/>}
    {read.rule && <>
      <RuleDetailHeader title={read.rule.title}><span className="text-sm text-muted-foreground">{clauseCount(read.rule.body.rules?.length ?? 0)}</span></RuleDetailHeader>
      <RuleClauseList className="border-t">{(read.rule.body.rules ?? []).map((clause, index) => <RuleClause key={clause.id} number={index + 1} title={clause.category} text={clause.rule_to_check}/>)}</RuleClauseList>
    </>}
  </div>
}
