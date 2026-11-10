// Stitch: projects/13261964020862199279/screens/cf4afdece8974624b77ad36da11cf669 (templates),
// 4c5d072e721f4760b801c13e4d63b3fa (template detail) and 6ab6498d58c94a1497dabd5309ede5b3 (rule detail).
import {Fragment, useEffect, useState, type MouseEvent, type ReactNode} from 'react'
import {AlertCircle, ChevronRight, Search} from 'lucide-react'
import {request} from '../api'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {ReadSkeleton} from '../components/read-skeleton'
import {message} from '../journey/remote'
import {writeAuditAddress} from './routes'
import {automatedCount, automationLabel, pendingRows, type Automation, type RuleTemplate, type TemplateRow} from './rule-templates'

/** Rules-page addresses: a template, a preset rule, an uploaded rule, or the automation pages. Every other rules key is dropped. */
export function rulesAddress(params: Record<string, string>): string {
  const url = new URL(location.href)
  for (const key of ['template', 'systemRule', 'requirement', 'reqVersion', 'reqTab', 'reqQuery', 'rulesArea', 'checkerSuite', 'checkerPath']) url.searchParams.delete(key)
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value)
  return `${url.pathname}${url.search}${url.hash}`
}

export function followRulesLink(event: MouseEvent<HTMLAnchorElement>, href: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  writeAuditAddress(href, 'push')
  window.scrollTo({top: 0})
}

export function AutomationBadge({automation}: {automation: Automation}) {
  const variant = automation.state === 'auto' ? 'success' : automation.state === 'stale' || automation.state === 'unverified' ? 'warning' : 'outline'
  return <span className="inline-flex flex-col items-start gap-1"><Badge shape="square" variant={variant}>{automationLabel[automation.state]}</Badge>{automation.note && <span className="text-xs text-muted-foreground">{automation.note}</span>}</span>
}

export function RulesTrail({items}: {items: {label: string; href?: string}[]}) {
  return <nav aria-label="位置" className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm text-muted-foreground">{items.map((item, index) => <Fragment key={`${index}:${item.label}`}>
    {index > 0 && <ChevronRight aria-hidden="true" className="size-3.5 shrink-0"/>}
    {item.href ? <a className="min-h-9 content-center underline-offset-4 hover:text-foreground hover:underline max-md:min-h-11" href={item.href} onClick={event => followRulesLink(event, item.href!)}>{item.label}</a> : <span aria-current="page" className="break-words text-foreground">{item.label}</span>}
  </Fragment>)}</nav>
}

const summaryLine = (template: RuleTemplate, known: boolean) => known ? `${template.rows.length} 类文件 · 可自动审核 ${automatedCount(template)} 类` : `${template.rows.length} 类文件`

export function TemplateList({templates, loading, failed, catalogKnown}: {templates: RuleTemplate[]; loading: boolean; failed: boolean; catalogKnown: boolean}) {
  const [query, setQuery] = useState('')
  const needle = query.trim().toLocaleLowerCase()
  const shown = needle ? templates.filter(template => `${template.name} ${template.rows.map(row => `${row.label} ${row.ruleTitle}`).join(' ')}`.toLocaleLowerCase().includes(needle)) : templates
  // Before the read finishes only this browser's drafts are known; a partial list would look complete.
  if (loading) return <ReadSkeleton label="正在读取规则模板…" kind="list" rows={5}/>
  return <div className="space-y-6">
    <p className="text-muted-foreground">审核文件时选用一个模板。每个模板按文件类型给出审核要求。</p>
    <div role="search" className="flex items-center gap-2 max-w-md"><Search aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/><Input aria-label="搜索规则模板" type="search" placeholder="搜索模板或文件类型" maxLength={200} value={query} onChange={event => setQuery(event.target.value)}/></div>
    {(['system', 'enterprise'] as const).map(source => {
      const group = shown.filter(template => template.source === source)
      if (!group.length) return null
      return <section key={source} aria-label={source === 'system' ? '系统提供' : '本企业'} className="space-y-2">
        <h2 className="text-xs font-medium text-muted-foreground">{source === 'system' ? '系统提供' : '本企业'}</h2>
        <ul className="divide-y rounded-lg border">{group.map(template => {
          const pending = pendingRows(template).length
          const href = rulesAddress({template: template.key})
          return <li key={template.key}><a className="flex min-h-14 flex-wrap items-center gap-x-6 gap-y-1 px-4 py-3 hover:bg-muted/60 focus-visible:outline-ring" href={href} onClick={event => followRulesLink(event, href)}>
            <span className="min-w-0 flex-1 basis-48 break-words font-medium">{template.name}</span>
            <span className="text-sm tabular-nums text-muted-foreground">{summaryLine(template, catalogKnown)}</span>
            {pending > 0 && <Badge shape="square" variant="warning">{pending} 项规则更新</Badge>}
            {catalogKnown && template.rows.length > 0 && automatedCount(template) === 0 && <Badge shape="square" variant="outline">还不能自动审核</Badge>}
            <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/>
          </a></li>
        })}</ul>
      </section>
    })}
    {/* A refused read is not an empty library; the page above says it could not read. */}{!loading && !failed && !shown.length && <div className="space-y-3 py-12 text-center"><h2 className="text-lg font-semibold">{query ? '没有匹配的模板' : '还没有规则模板'}</h2>{query && <Button variant="outline" onClick={() => setQuery('')}>清除搜索</Button>}</div>}
  </div>
}

function rowHref(row: TemplateRow) {
  return row.target.kind === 'system' ? rulesAddress({systemRule: row.target.id}) : rulesAddress({requirement: row.target.id})
}

export function TemplateDetail({template, catalogKnown}: {template: RuleTemplate | undefined; catalogKnown: boolean}) {
  if (!template) return <div className="space-y-4"><RulesTrail items={[{label: '规则模板', href: rulesAddress({})}]}/><p role="alert">这个模板已不存在或还在读取，请返回模板列表。</p></div>
  const pending = pendingRows(template)
  return <div className="space-y-6">
    <RulesTrail items={[{label: '规则模板', href: rulesAddress({})}, {label: template.name}]}/>
    <header className="space-y-1">
      <div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-semibold tracking-tight">{template.name}</h1><Badge shape="square" variant="outline">{template.source === 'system' ? '系统提供' : '本企业'}</Badge></div>
      <p className="text-muted-foreground">{summaryLine(template, catalogKnown)}</p>
    </header>
    {pending.length > 0 && <Alert><AlertCircle aria-hidden="true"/><AlertTitle>{pending.map(row => row.label).join('、')}的规则已更新，需重新验证后新审核才会使用。</AlertTitle><AlertDescription><a className="underline underline-offset-4" href={rulesAddress({rulesArea: 'automation'})} onClick={event => followRulesLink(event, rulesAddress({rulesArea: 'automation'}))}>去处理</a></AlertDescription></Alert>}
    <div className="max-md:hidden"><Table className="table-fixed"><TableHeader><TableRow><TableHead className="w-1/4">文件类型</TableHead><TableHead>审核规则</TableHead><TableHead className="w-20">条款</TableHead><TableHead className="w-44">自动审核</TableHead><TableHead className="w-10"><span className="sr-only">打开</span></TableHead></TableRow></TableHeader>
      <TableBody>{template.rows.map(row => <TableRow key={`${row.target.kind}:${row.target.id}`} className="relative">
        <TableCell className="whitespace-normal break-words"><a className="font-medium underline-offset-4 after:absolute after:inset-0 hover:underline focus-visible:outline-ring" href={rowHref(row)} onClick={event => followRulesLink(event, rowHref(row))}>{row.label || '未识别类型'}</a></TableCell>
        <TableCell className="whitespace-normal break-words">{row.ruleTitle}{row.version && <span className="text-muted-foreground"> · {row.version}</span>}{row.draft && <Badge className="ml-2" variant="outline">草稿</Badge>}</TableCell>
        <TableCell><span className="tabular-nums">{row.clauses ?? '—'}{row.clauses !== null && ' 条'}</span></TableCell>
        <TableCell><AutomationBadge automation={row.automation}/></TableCell>
        <TableCell><ChevronRight aria-hidden="true" className="size-4 text-muted-foreground"/></TableCell>
      </TableRow>)}</TableBody></Table></div>
    <ul aria-label={`${template.name} 的文件类型`} className="divide-y border-y md:hidden">{template.rows.map(row => <li key={`${row.target.kind}:${row.target.id}`}><a className="flex items-center gap-3 py-4" href={rowHref(row)} onClick={event => followRulesLink(event, rowHref(row))}>
      <span className="min-w-0 flex-1 space-y-1.5"><span className="block break-words font-medium">{row.label || '未识别类型'}</span><span className="block break-words text-sm text-muted-foreground">{row.ruleTitle}{row.version && ` · ${row.version}`}{row.clauses !== null && ` · ${row.clauses} 条`}</span><AutomationBadge automation={row.automation}/></span>
      <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/>
    </a></li>)}</ul>
    {template.missing.length > 0 && <p className="text-sm text-muted-foreground">本模板没有这些文件的规则：{template.missing.join('、')}。审核时会提示你另选模板或人工审核。</p>}
  </div>
}

type RuleDetail = {id: string; title: string; created_at: string; body: {type?: string; customers?: string[]; rules?: {id: string; rule_to_check: string; category?: string}[]}}

/** A product preset has no uploaded original; its clauses are read as published and are not edited here. */
export function SystemRuleDetail({ruleId, template, row, actions}: {ruleId: string; template: RuleTemplate | undefined; row: TemplateRow | undefined; actions?: ReactNode}) {
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
  const trail = [{label: '规则模板', href: rulesAddress({})}, ...template ? [{label: template.name, href: rulesAddress({template: template.key})}] : [], {label: row?.label || read.rule?.body.type || '规则'}]
  return <div className="max-w-4xl space-y-6">
    <RulesTrail items={trail}/>
    {read.error && <Alert variant="destructive"><AlertTitle>规则未能读取</AlertTitle><AlertDescription><p>{read.error}</p><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></AlertDescription></Alert>}
    {!read.rule && !read.error && <ReadSkeleton label="正在读取规则…" kind="detail" rows={6}/>}
    {read.rule && <>
      <header className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-4"><h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight">{read.rule.title}</h1>{actions}</div>
        <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground"><Badge shape="square" variant="outline">系统提供</Badge><span>{read.rule.body.rules?.length ?? 0} 条</span>{row && <AutomationBadge automation={row.automation}/>}</div>
      </header>
      <section aria-label="规则条款" className="divide-y border-t">{(read.rule.body.rules ?? []).map((clause, index) => <article key={clause.id} aria-label={`第 ${index + 1} 条`} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-3 py-5 sm:gap-x-4">
        <span className="pt-0.5 text-xs tabular-nums text-muted-foreground" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
        <div className="min-w-0 space-y-1">{clause.category && <h2 className="break-words font-medium">{clause.category}</h2>}<p className="whitespace-pre-wrap break-words leading-relaxed">{clause.rule_to_check}</p></div>
      </article>)}</section>
    </>}
  </div>
}
