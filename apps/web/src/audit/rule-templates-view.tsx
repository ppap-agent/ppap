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
import {cn} from '../lib/utils'
import {message} from '../journey/remote'
import {writeAuditAddress} from './routes'
import {automatedCount, automationLabel, pendingRows, type Automation, type RuleTemplate, type TemplateRow} from './rule-templates'

/** Rules-page addresses: a template, a preset rule or an uploaded rule. Every other rules key is dropped, including retired ones. */
export function rulesAddress(params: Record<string, string>): string {
  const url = new URL(location.href)
  for (const key of ['template', 'systemRule', 'requirement', 'reqVersion', 'reqTab', 'reqStep', 'reqQuery', 'ruleSuite', 'rulesArea', 'checkerSuite', 'checkerPath']) url.searchParams.delete(key)
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value)
  return `${url.pathname}${url.search}${url.hash}`
}

export function followRulesLink(event: MouseEvent<HTMLAnchorElement>, href: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  writeAuditAddress(href, 'push')
  window.scrollTo({top: 0})
}

// Ready reads as done, an update or an unverified program as needing a look, anything else as not yet automated.
const automationDot = (state: Automation['state']) => state === 'auto' ? 'bg-success' : state === 'stale' || state === 'unverified' ? 'bg-warning' : 'bg-muted-foreground/50'

export function AutomationBadge({automation}: {automation: Automation}) {
  return <span className="inline-flex flex-col items-start gap-0.5"><span className="inline-flex items-center gap-1.5 text-sm whitespace-nowrap"><span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', automationDot(automation.state))}/>{automationLabel[automation.state]}</span>{automation.note && <span className="text-xs text-muted-foreground">{automation.note}</span>}</span>
}

export function RulesTrail({items}: {items: {label: string; href?: string}[]}) {
  return <nav aria-label="位置" className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm text-muted-foreground">{items.map((item, index) => <Fragment key={`${index}:${item.label}`}>
    {index > 0 && <ChevronRight aria-hidden="true" className="size-3.5 shrink-0"/>}
    {item.href ? <a className="min-h-9 content-center underline-offset-4 hover:text-foreground hover:underline max-md:min-h-11" href={item.href} onClick={event => followRulesLink(event, item.href!)}>{item.label}</a> : <span aria-current="page" className="break-words text-foreground">{item.label}</span>}
  </Fragment>)}</nav>
}

const summaryLine = (template: RuleTemplate, known: boolean) => known ? `${template.rows.length} 类文件 · 有检查程序 ${automatedCount(template)} 类` : `${template.rows.length} 类文件`
const avatarTones = ['bg-primary/10 text-primary', 'bg-success/10 text-success', 'bg-warning/10 text-warning', 'bg-muted text-foreground']

/** A customer's mark: the first character of the template name on a tone picked from the name, so it stays the same across visits. */
function TemplateAvatar({name, className}: {name: string; className?: string}) {
  const tone = avatarTones[[...name].reduce((sum, char) => sum + char.codePointAt(0)!, 0) % avatarTones.length]
  return <span aria-hidden="true" className={cn('flex size-9 shrink-0 items-center justify-center rounded-md text-sm font-semibold', tone, className)}>{[...name.trim()][0] ?? '·'}</span>
}

/** Templates on the left, the chosen template's file types on the right; without a choice the first template is shown. */
export function TemplateBrowser({templates, selectedKey, loading, failed, catalogKnown}: {templates: RuleTemplate[]; selectedKey: string; loading: boolean; failed: boolean; catalogKnown: boolean}) {
  const [query, setQuery] = useState('')
  const needle = query.trim().toLocaleLowerCase()
  const shown = needle ? templates.filter(template => `${template.name} ${template.rows.map(row => `${row.label} ${row.ruleTitle}`).join(' ')}`.toLocaleLowerCase().includes(needle)) : templates
  // Before the read finishes only this browser's drafts are known; a partial list would look complete.
  if (loading) return <ReadSkeleton label="正在读取规则模板…" kind="list" rows={5}/>
  const template = selectedKey ? templates.find(item => item.key === selectedKey) : shown[0] ?? templates[0]
  return <div className="grid gap-4 lg:grid-cols-[17rem_minmax(0,1fr)]">
    <aside aria-label="规则模板列表" className="min-w-0 space-y-4 self-start lg:rounded-lg lg:border lg:bg-card lg:p-3">
      <div role="search" className="relative"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search aria-hidden="true" className="size-3.5"/></span><Input aria-label="搜索规则模板" type="search" iconStart placeholder="搜索模板或文件类型" maxLength={200} value={query} onChange={event => setQuery(event.target.value)}/></div>
      {(['system', 'enterprise'] as const).map(source => {
        const group = shown.filter(item => item.source === source)
        if (!group.length) return null
        return <section key={source} aria-label={source === 'system' ? '系统提供' : '本企业'} className="space-y-2">
          <h2 className="px-1 text-xs font-medium text-muted-foreground">{source === 'system' ? '系统提供' : '本企业'}</h2>
          {/* Below lg the cards stack above the chosen template, two to a row where they fit. */}
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1">{group.map(item => {
            const pending = pendingRows(item).length, href = rulesAddress({template: item.key}), current = item.key === template?.key
            return <li key={item.key}><a aria-current={current ? 'page' : undefined} className={cn('flex min-h-16 items-start gap-3 rounded-lg border bg-card px-3 py-3 transition-colors hover:border-primary/40 focus-visible:outline-ring', current && 'border-primary bg-primary/5')} href={href} onClick={event => followRulesLink(event, href)}>
              <TemplateAvatar name={item.name}/>
              <span className="min-w-0 flex-1 space-y-1"><span className="block break-words font-medium">{item.name}</span><span className="block text-xs tabular-nums text-muted-foreground">{summaryLine(item, catalogKnown)}</span>
                {(pending > 0 || catalogKnown && item.rows.length > 0 && automatedCount(item) === 0) && <span className="flex flex-wrap gap-1.5 pt-0.5">{pending > 0 && <Badge shape="square" variant="warning">{pending} 项规则更新</Badge>}{catalogKnown && item.rows.length > 0 && automatedCount(item) === 0 && <Badge shape="square" variant="outline">尚无检查程序</Badge>}</span>}</span>
            </a></li>
          })}</ul>
        </section>
      })}
      {/* A refused read is not an empty library; the page above says it could not read. */}{!failed && !shown.length && <div className="space-y-3 py-8 text-center"><h2 className="text-lg font-semibold">{query ? '没有匹配的模板' : '还没有规则模板'}</h2>{query && <Button variant="outline" onClick={() => setQuery('')}>清除搜索</Button>}</div>}
    </aside>
    <div className="min-w-0">{template ? <TemplateDetail template={template} catalogKnown={catalogKnown}/> : selectedKey && <p role="alert" className="rounded-lg border bg-card p-4">这个模板已不存在，请从左侧另选一个模板。</p>}</div>
  </div>
}

function rowHref(row: TemplateRow) {
  return row.target.kind === 'system' ? rulesAddress({systemRule: row.target.id}) : rulesAddress({requirement: row.target.id})
}

function TemplateDetail({template, catalogKnown}: {template: RuleTemplate; catalogKnown: boolean}) {
  const pending = pendingRows(template)
  return <section aria-label={`${template.name} 的规则`} className="space-y-5 rounded-lg border bg-card p-4 sm:p-5">
    <header className="flex items-center gap-3">
      <TemplateAvatar name={template.name} className="size-11 text-base"/>
      <div className="min-w-0 space-y-0.5"><div className="flex flex-wrap items-center gap-2"><h2 className="break-words text-lg font-semibold">{template.name}</h2><Badge shape="square" variant="outline">{template.source === 'system' ? '系统提供' : '本企业'}</Badge></div><p className="text-sm text-muted-foreground">{summaryLine(template, catalogKnown)}</p></div>
    </header>
    {pending.length > 0 && <Alert><AlertCircle aria-hidden="true"/><AlertTitle>{pending.map(row => row.label).join('、')}的规则已更新，在规则页“检查程序”中采用新版本后新审核才会使用。</AlertTitle></Alert>}
    <ul aria-label={`${template.name} 的文件类型`} className="grid gap-2">{template.rows.map(row => <li key={`${row.target.kind}:${row.target.id}`}><a className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border px-4 py-3 transition-colors hover:border-primary/40 hover:bg-primary/5 focus-visible:outline-ring" href={rowHref(row)} onClick={event => followRulesLink(event, rowHref(row))}>
      <span className="min-w-0 flex-1 basis-56 space-y-1.5">
        <span className="flex flex-wrap items-center gap-2"><Badge shape="square" variant="info" className="h-auto whitespace-normal">{row.label || '未识别类型'}</Badge>{row.draft && <Badge variant="outline">草稿</Badge>}</span>
        <span className="block break-words font-medium">{row.ruleTitle}</span>
        {(row.version || row.clauses !== null) && <span className="block text-xs tabular-nums text-muted-foreground">{[row.version, row.clauses !== null && `${row.clauses} 条`].filter(Boolean).join(' · ')}</span>}
      </span>
      <AutomationBadge automation={row.automation}/>
      <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/>
    </a></li>)}</ul>
    {template.missing.length > 0 && <p className="text-sm text-muted-foreground">本模板没有这些文件的规则：{template.missing.join('、')}。审核时会提示你另选模板或人工审核。</p>}
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
      {program}
      <section aria-label="规则条款" className="divide-y border-t">{(read.rule.body.rules ?? []).map((clause, index) => <article key={clause.id} aria-label={`第 ${index + 1} 条`} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-3 py-5 sm:gap-x-4">
        <span className="pt-0.5 text-xs tabular-nums text-muted-foreground" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
        <div className="min-w-0 space-y-1">{clause.category && <h2 className="break-words font-medium">{clause.category}</h2>}<p className="whitespace-pre-wrap break-words leading-relaxed">{clause.rule_to_check}</p></div>
      </article>)}</section>
    </>}
  </div>
}
