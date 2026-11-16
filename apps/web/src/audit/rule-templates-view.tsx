// Stitch: projects/13261964020862199279/screens/cf4afdece8974624b77ad36da11cf669 (templates),
// 4c5d072e721f4760b801c13e4d63b3fa (template detail) and 6ab6498d58c94a1497dabd5309ede5b3 (rule detail).
import {Fragment, useEffect, useState, type MouseEvent, type ReactNode} from 'react'
import {AlertCircle, ChevronRight, Search} from 'lucide-react'
import {request} from '../api'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {ReadSkeleton} from '../components/read-skeleton'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {cn} from '../lib/utils'
import {message} from '../journey/remote'
import {rulesAddressKeys, writeAuditAddress} from './routes'
import {CustomerAvatar} from './customer-avatar'
import {automationLabel, coverage, effective, pendingRows, typeGroups, type Automation, type RuleTemplate, type TemplateRow} from './rule-templates'

/** Rules-page addresses: a template, a preset rule or an uploaded rule. Every other rules key is dropped. */
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

// Ready reads as done, an update or an unverified program as needing a look, anything else as not yet automated.
const automationDot = (state: Automation['state']) => state === 'auto' ? 'bg-success' : state === 'stale' || state === 'unverified' ? 'bg-warning' : 'bg-muted-foreground/50'

function AutomationBadge({automation}: {automation: Automation}) {
  return <span className="inline-flex flex-col items-start gap-0.5"><span className="inline-flex items-center gap-1.5 text-sm whitespace-nowrap"><span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', automationDot(automation.state))}/>{automationLabel[automation.state]}</span>{automation.note && <span className="text-xs text-muted-foreground">{automation.note}</span>}</span>
}

export function RulesTrail({items}: {items: {label: string; href?: string}[]}) {
  return <nav aria-label="位置" className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm text-muted-foreground">{items.map((item, index) => <Fragment key={`${index}:${item.label}`}>
    {index > 0 && <ChevronRight aria-hidden="true" className="size-3.5 shrink-0"/>}
    {item.href ? <a className="min-h-9 content-center underline-offset-4 hover:text-foreground hover:underline" href={item.href} onClick={event => followRulesLink(event, item.href!)}>{item.label}</a> : <span aria-current="page" className="break-words text-foreground">{item.label}</span>}
  </Fragment>)}</nav>
}

/** "N / M" once the checker catalog has said which programs are ready; nothing is claimed before that. */
function coverageText(template: RuleTemplate) {
  const value = coverage(template)
  return value.known ? `${value.effective} / ${value.total}` : ''
}

const matches = (needle: string, ...values: string[]) => values.some(value => value.toLocaleLowerCase().includes(needle))

/** Customers' rule sets on the left, the chosen one's file types on the right; without a choice the first one is shown. */
export function TemplateBrowser({templates, selectedKey, loading, failed}: {templates: RuleTemplate[]; selectedKey: string; loading: boolean; failed: boolean}) {
  const [query, setQuery] = useState('')
  const needle = query.trim().toLocaleLowerCase()
  const shown = needle ? templates.filter(template => matches(needle, template.name, ...template.rows.flatMap(row => [row.label, row.ruleTitle]))) : templates
  // Before the read finishes only this browser's drafts are known; a partial list would look complete.
  if (loading) return <div className="flex min-h-0 flex-1 flex-col"><ReadSkeleton label="正在读取规则模板…" kind="list" rows={5}/></div>
  const template = selectedKey ? templates.find(item => item.key === selectedKey) : shown[0] ?? templates[0]
  // A search that names a rule narrows the chosen customer's types to it; a search that names the customer shows them all.
  const focus = needle && template && !matches(needle, template.name) ? needle : ''
  return <div className="grid min-h-0 flex-1 grid-cols-[17.5rem_minmax(0,1fr)] grid-rows-1 gap-4">
    <aside aria-label="规则模板列表" className="flex min-h-0 min-w-0 flex-col gap-3 overflow-hidden rounded-lg border bg-card p-3">
      <div role="search" className="relative"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search aria-hidden="true" className="size-3.5"/></span><Input aria-label="搜索客户" type="search" iconStart placeholder="搜索客户" maxLength={200} value={query} onChange={event => setQuery(event.target.value)}/></div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">{(['system', 'enterprise'] as const).map(source => {
        const group = shown.filter(item => item.source === source)
        if (!group.length) return null
        return <section key={source} aria-label={source === 'system' ? '系统提供' : '本企业'} className="space-y-2">
          <h3 className="px-1 text-xs font-medium text-muted-foreground">{source === 'system' ? '系统提供' : '本企业'}</h3>
          <ul className="grid gap-1">{group.map(item => {
            const pending = pendingRows(item).length, href = rulesAddress({template: item.key}), current = item.key === template?.key, ratio = coverageText(item)
            return <li key={item.key}><Button asChild variant={current ? 'soft' : 'ghost'} className="h-auto w-full justify-start whitespace-normal text-left"><a aria-current={current ? 'page' : undefined} href={href} onClick={event => followRulesLink(event, href)}><span className="flex min-w-0 flex-1 items-center gap-3 py-2">
              <CustomerAvatar name={item.name}/>
              <span className="min-w-0 flex-1 space-y-0.5 font-normal"><span className="block truncate text-sm" title={item.name}>{item.name}</span><span className="block text-xs tabular-nums text-muted-foreground">{ratio ? `${ratio} 类规则生效` : `${coverage(item).total} 类文件`}</span>
                {pending > 0 && <span className="flex pt-0.5"><Badge shape="square" variant="warning">{pending} 项规则更新</Badge></span>}</span>
            </span></a></Button></li>
          })}</ul>
        </section>
      })}
      {/* A refused read is not an empty library; the page above says it could not read. */}{!failed && !shown.length && <div className="grid justify-items-center gap-3 py-12 text-center"><p className="text-sm text-muted-foreground">{query ? '没有匹配的模板' : '还没有规则模板'}</p>{query && <Button variant="outline" size="sm" onClick={() => setQuery('')}>清除搜索</Button>}</div>}
      </div>
      <p className="border-t pt-3 text-xs leading-relaxed text-muted-foreground">规则由本企业自行上传，检查程序由平台离线准备，准备好后生效。系统提供的规则可直接使用。</p>
    </aside>
    <div className="flex min-h-0 min-w-0 flex-col">
      {template ? <TemplateDetail key={`${template.key}:${focus}`} template={template} focus={focus}/> : selectedKey && <Alert><AlertTitle>这个模板已不存在</AlertTitle><AlertDescription>请从左侧另选一个模板。</AlertDescription></Alert>}
    </div>
  </div>
}

function rowHref(row: TemplateRow) {
  return row.target.kind === 'system' ? rulesAddress({systemRule: row.target.id}) : rulesAddress({requirement: row.target.id})
}

const day = (value: string) => {
  const date = new Date(value)
  return value && Number.isFinite(date.getTime()) ? `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}` : ''
}

/** Ready (or partly people-judged) programs run in new audits; everything else waits on the rule page and does not count. */
function RowStatus({automation}: {automation: Automation}) {
  if (automation.state === 'unknown' || automation.state === 'unreadable') return <AutomationBadge automation={automation}/>
  const on = effective(automation)
  const detail = on ? automation.note : [automationLabel[automation.state], automation.note].filter(Boolean).join(' · ')
  return <span className="inline-flex flex-col items-end gap-0.5 text-right"><span className="inline-flex items-center gap-1.5 text-sm whitespace-nowrap"><span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', on ? 'bg-success' : 'bg-warning')}/>{on ? '生效中' : '未生效 · 不计入覆盖'}</span>{detail && <span className="text-xs text-muted-foreground">{detail}</span>}</span>
}

function TemplateDetail({template, focus}: {template: RuleTemplate; focus: string}) {
  const pending = pendingRows(template)
  const groups = typeGroups(template).flatMap(group => {
    const rows = focus ? group.rows.filter(row => matches(focus, row.label, row.ruleTitle)) : group.rows
    return rows.length ? [{...group, rows}] : []
  })
  const rows = groups.flatMap(group => group.rows)
  return <section aria-label={`${template.name} 的规则`} className="flex min-h-0 flex-1 flex-col gap-3">
    <header className="flex items-center gap-3 rounded-lg border bg-card p-4">
      <CustomerAvatar name={template.name}/>
      <h2 className="min-w-0 truncate text-lg font-semibold" title={template.name}>{template.name}</h2>
    </header>
    {pending.length > 0 && <Alert><AlertCircle aria-hidden="true"/><AlertTitle>{pending.map(row => row.label).join('、')}的规则已更新，在规则页“检查程序”中采用新版本后新审核才会使用。</AlertTitle></Alert>}
    {template.missing.length > 0 && <p className="text-sm text-muted-foreground">本模板没有这些文件的规则：{template.missing.join('、')}。审核时会提示你另选模板或人工审核。</p>}
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-lg border bg-card">
      {rows.length ? <Table aria-label={`${template.name} 的规则列表`} containerClassName="min-h-0 min-w-0 flex-1 overflow-auto"><TableHeader variant="muted" sticky><TableRow><TableHead>规则</TableHead><TableHead>审核项</TableHead><TableHead>最近更新</TableHead><TableHead>状态</TableHead></TableRow></TableHeader><TableBody>{rows.map(row => {
        const href = rowHref(row), name = row.ruleTitle || row.label || '未识别类型', updated = day(row.updatedAt)
        return <TableRow key={`${row.target.kind}:${row.target.id}`}>
          <TableCell className="whitespace-normal"><span className="flex min-w-0 flex-wrap items-center gap-2"><a className="rounded-sm font-medium text-primary outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50" href={href} onClick={event => followRulesLink(event, href)}>{name}</a>{row.draft && <Badge variant="outline">草稿</Badge>}</span></TableCell>
          <TableCell>{row.clauses !== null ? <span className="tabular-nums">{row.clauses} 条</span> : <span className="text-muted-foreground">—</span>}</TableCell>
          <TableCell>{updated || row.version ? <span className="text-muted-foreground tabular-nums">{[updated, row.version && `版本 ${row.version}`].filter(Boolean).join(' · ')}</span> : <span className="text-muted-foreground">—</span>}</TableCell>
          <TableCell><RowStatus automation={row.automation}/></TableCell>
        </TableRow>
      })}</TableBody></Table> : <p className="grid flex-1 place-items-center py-12 text-center text-sm text-muted-foreground">{focus ? '这个客户没有匹配的规则。' : '这个模板还没有规则。'}</p>}
      {rows.length > 0 && <footer className="border-t px-3 py-3 text-right text-xs text-muted-foreground tabular-nums">共 {rows.length} 条规则{rows.length !== groups.length && ` · ${groups.length} 类文件`}</footer>}
    </div>
  </section>
}

type RuleDetail = {id: string; title: string; created_at: string; body: {type?: string; customers?: string[]; rules?: {id: string; rule_to_check: string; category?: string}[]}}

/** A product preset has no uploaded original; its clauses are read as published and are not edited here. Its program's card leads the page. */
export function SystemRuleDetail({ruleId, template, row, actions, program}: {ruleId: string; template: RuleTemplate | undefined; row: TemplateRow | undefined; actions?: ReactNode; program?: ReactNode}) {
  const [read, setRead] = useState<{rule: RuleDetail | null; error: string}>({rule: null, error: ''})
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setRead({rule: null, error: ''})
    void request<RuleDetail>(`/rules/${encodeURIComponent(ruleId)}`, 'GET', undefined, controller.signal)
      .then(rule => {if (!controller.signal.aborted) setRead({rule, error: ''})})
      .catch(cause => {if (!controller.signal.aborted) setRead({rule: null, error: message(cause)})})
    return () => controller.abort()
  }, [ruleId, attempt])
  const trail = template ? [{label: template.name, href: rulesAddress({template: template.key})}] : []
  return <div className="max-w-4xl space-y-6">
    {trail.length > 0 && <RulesTrail items={trail}/>}
    {read.error && <Alert variant="destructive"><AlertTitle>规则未能读取</AlertTitle><AlertDescription><p>{read.error}</p><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></AlertDescription></Alert>}
    {!read.rule && !read.error && <ReadSkeleton label="正在读取规则…" kind="detail" rows={6}/>}
    {read.rule && <>
      <header className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-4"><h1 className="min-w-0 break-words text-xl font-semibold">{read.rule.title}</h1>{actions}</div>
        <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground"><Badge shape="square" variant="outline">系统提供</Badge><span>{read.rule.body.rules?.length ?? 0} 条</span>{row && !program && <AutomationBadge automation={row.automation}/>}</div>
      </header>
      {program}
      <section aria-label="规则条款" className="divide-y border-t">{(read.rule.body.rules ?? []).map((clause, index) => <article key={clause.id} aria-label={`第 ${index + 1} 条`} className="grid grid-cols-[1.5rem_minmax(0,1fr)] items-baseline gap-x-4 py-5">
        <span className="text-xs tabular-nums text-muted-foreground" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
        <div className="min-w-0 space-y-1">{clause.category && !new RegExp(`^(?:条款\\s*${index + 1}|第\\s*${index + 1}\\s*条|0*${index + 1})$`).test(clause.category.trim()) && <h2 className="break-words font-medium">{clause.category}</h2>}<p className="whitespace-pre-wrap break-words leading-relaxed">{clause.rule_to_check}</p></div>
      </article>)}</section>
    </>}
  </div>
}
