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
import {automationLabel, coverage, effective, pendingRows, typeCode, typeGroups, type Automation, type RuleTemplate, type TemplateRow} from './rule-templates'

/** Rules-page addresses: a template, a preset rule or an uploaded rule. Every other rules key is dropped, including retired ones. */
export function rulesAddress(params: Record<string, string>): string {
  const url = new URL(location.href)
  for (const key of ['template', 'systemRule', 'requirement', 'reqVersion', 'reqTab', 'reqQuery', 'ruleSuite', 'rulesArea', 'checkerSuite', 'checkerPath']) url.searchParams.delete(key)
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
    {item.href ? <a className="min-h-9 content-center underline-offset-4 hover:text-foreground hover:underline" href={item.href} onClick={event => followRulesLink(event, item.href!)}>{item.label}</a> : <span aria-current="page" className="break-words text-foreground">{item.label}</span>}
  </Fragment>)}</nav>
}

const avatarTones = ['bg-primary', 'bg-success', 'bg-warning', 'bg-brand-light']

/** A customer's mark: the first character of the template name on a tone picked from the name, so it stays the same across visits. */
function TemplateAvatar({name, className}: {name: string; className?: string}) {
  const tone = avatarTones[[...name].reduce((sum, char) => sum + char.codePointAt(0)!, 0) % avatarTones.length]
  return <span aria-hidden="true" className={cn('flex size-9 shrink-0 items-center justify-center rounded-md text-base font-semibold text-white', tone, className)}>{[...name.trim()][0] ?? '·'}</span>
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
  if (loading) return <ReadSkeleton label="正在读取规则模板…" kind="list" rows={5}/>
  const template = selectedKey ? templates.find(item => item.key === selectedKey) : shown[0] ?? templates[0]
  // A search that names a rule narrows the chosen customer's types to it; a search that names the customer shows them all.
  const focus = needle && template && !matches(needle, template.name) ? needle : ''
  return <div className="grid grid-cols-[17.5rem_minmax(0,1fr)] gap-5">
    <aside aria-label="规则模板列表" className="sticky top-0 flex max-h-[calc(100dvh-7rem)] min-w-0 flex-col gap-3.5 self-start overflow-y-auto rounded-md border bg-card p-4">
      <h2 className="px-1 text-base font-semibold">规则库</h2>
      <div role="search" className="relative"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search aria-hidden="true" className="size-3.5"/></span><Input aria-label="搜索客户" type="search" iconStart placeholder="搜索客户" maxLength={200} value={query} onChange={event => setQuery(event.target.value)}/></div>
      {(['system', 'enterprise'] as const).map(source => {
        const group = shown.filter(item => item.source === source)
        if (!group.length) return null
        return <section key={source} aria-label={source === 'system' ? '系统提供' : '本企业'} className="space-y-2">
          <h3 className="px-1 text-xs font-medium text-muted-foreground">{source === 'system' ? '系统提供' : '本企业'}</h3>
          <ul className="grid gap-2">{group.map(item => {
            const pending = pendingRows(item).length, href = rulesAddress({template: item.key}), current = item.key === template?.key, ratio = coverageText(item)
            return <li key={item.key}><a aria-current={current ? 'page' : undefined} className={cn('flex items-center gap-3 rounded-md border bg-card p-2.5 transition-colors hover:border-primary-line focus-visible:outline-ring', current && 'border-primary bg-primary-soft/50')} href={href} onClick={event => followRulesLink(event, href)}>
              <TemplateAvatar name={item.name}/>
              <span className="min-w-0 flex-1 space-y-0.5"><span className="block truncate text-sm">{item.name}</span><span className="block text-xs tabular-nums text-muted-foreground">{ratio ? `${ratio} 类规则生效` : `${coverage(item).total} 类文件`}</span>
                {pending > 0 && <span className="flex pt-0.5"><Badge shape="square" variant="warning">{pending} 项规则更新</Badge></span>}</span>
            </a></li>
          })}</ul>
        </section>
      })}
      {/* A refused read is not an empty library; the page above says it could not read. */}{!failed && !shown.length && <div className="space-y-3 py-8 text-center"><h3 className="text-lg font-semibold">{query ? '没有匹配的模板' : '还没有规则模板'}</h3>{query && <Button variant="outline" onClick={() => setQuery('')}>清除搜索</Button>}</div>}
      <p className="border-t pt-3 text-xs leading-relaxed text-muted-foreground">规则由本企业自行上传，检查程序由平台离线准备，准备好后生效。系统提供的规则可直接使用。</p>
    </aside>
    <div className="min-w-0 space-y-4">
      <header className="space-y-1.5">
        <p className="text-sm text-muted-foreground">PPAP Audit<span aria-hidden="true" className="mx-1 text-muted-foreground/60">/</span>规则库</p>
        {/* The page's heading is the shell's; this is its visible form. */}
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1"><p aria-hidden="true" className="text-2xl font-semibold leading-tight">规则库</p><p className="text-xs text-muted-foreground">按客户归集审核规则；上传客户要求后由平台离线准备检查程序，生效的文件类型计入覆盖度。</p></div>
      </header>
      {template ? <TemplateDetail key={`${template.key}:${focus}`} template={template} focus={focus}/> : selectedKey && <p role="alert" className="rounded-md border bg-card p-4">这个模板已不存在，请从左侧另选一个模板。</p>}
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

const pageSize = 6

function TemplateDetail({template, focus}: {template: RuleTemplate; focus: string}) {
  const pending = pendingRows(template)
  const value = coverage(template)
  const groups = typeGroups(template).flatMap(group => {
    const rows = focus ? group.rows.filter(row => matches(focus, row.label, row.ruleTitle)) : group.rows
    return rows.length ? [{...group, rows}] : []
  })
  const pages = Math.max(1, Math.ceil(groups.length / pageSize))
  const [page, setPage] = useState(1)
  const current = Math.min(page, pages), shown = groups.slice((current - 1) * pageSize, current * pageSize)
  return <section aria-label={`${template.name} 的规则`} className="space-y-3">
    <header className="flex flex-wrap items-center justify-between gap-4 rounded-md border bg-card px-5 py-4">
      <div className="flex min-w-0 items-center gap-3">
        <TemplateAvatar name={template.name}/>
        <div className="min-w-0 space-y-0.5"><div className="flex flex-wrap items-center gap-2"><h2 className="break-words text-lg font-semibold">{template.name}</h2><Badge shape="square" variant="outline">{template.source === 'system' ? '系统提供' : '本企业'}</Badge></div><p className="text-xs tabular-nums text-muted-foreground">{value.known ? `已支持 ${value.effective} / ${value.total} 类 PPAP 文件` : `${value.total} 类 PPAP 文件 · 正在核对检查程序`}</p></div>
      </div>
      {value.known && <span className="inline-flex h-8 items-center rounded-full border border-primary-line bg-primary-soft px-3.5 text-sm tabular-nums text-primary">覆盖度 {value.effective}/{value.total}</span>}
    </header>
    {pending.length > 0 && <Alert><AlertCircle aria-hidden="true"/><AlertTitle>{pending.map(row => row.label).join('、')}的规则已更新，在规则页“检查程序”中采用新版本后新审核才会使用。</AlertTitle></Alert>}
    <ul aria-label={`${template.name} 的文件类型`} className="grid gap-3">{shown.map(group => {
      const label = group.rows[0].label || '未识别类型', code = typeCode(group.rows[0].type)
      return <li key={group.type} className="rounded-md border bg-card px-4.5 py-4">
        <Badge shape="pill" variant="info" className="mb-2.5">{code ? `${code} ${label}` : label}</Badge>
        <div className="grid gap-2">{group.rows.map(row => <a key={`${row.target.kind}:${row.target.id}`} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-sm border px-3.5 py-3 transition-colors hover:border-primary-line hover:bg-primary-soft/40 focus-visible:outline-ring" href={rowHref(row)} onClick={event => followRulesLink(event, rowHref(row))}>
          <span className="min-w-0 flex-1 basis-72 space-y-1.5">
            <span className="flex flex-wrap items-center gap-2"><span className="break-words text-sm font-medium">{row.ruleTitle}</span>{row.draft && <Badge variant="outline">草稿</Badge>}</span>
            <span className="flex flex-wrap gap-x-6 gap-y-1 text-xs tabular-nums text-muted-foreground"><span>文件类型：{label}</span>{row.clauses !== null && <span>审核项：{row.clauses} 条</span>}{row.version && <span>版本：{row.version}</span>}{day(row.updatedAt) && <span>最近更新：{day(row.updatedAt)}</span>}</span>
          </span>
          <RowStatus automation={row.automation}/>
          <ChevronRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/>
        </a>)}</div>
      </li>
    })}</ul>
    {!groups.length && <p className="rounded-md border bg-card p-4 text-muted-foreground">{focus ? '这个客户没有匹配的规则。' : '这个模板还没有规则。'}</p>}
    {groups.length > pageSize && <nav aria-label="文件类型分页" className="flex flex-wrap items-center justify-between gap-3 px-1 text-xs text-muted-foreground">
      <span className="tabular-nums">共 {groups.length} 类 PPAP 文件 · 当前展示第 {(current - 1) * pageSize + 1}–{Math.min(current * pageSize, groups.length)} 类</span>
      <span className="flex gap-1.5">{Array.from({length: pages}, (_, index) => <Button key={index} size="icon-xs" variant={current === index + 1 ? 'soft' : 'outline'} aria-label={`第 ${index + 1} 页`} aria-current={current === index + 1 ? 'page' : undefined} onClick={() => setPage(index + 1)}>{index + 1}</Button>)}</span>
    </nav>}
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
  const trail = [{label: '规则库', href: rulesAddress({})}, ...template ? [{label: template.name, href: rulesAddress({template: template.key})}] : [], {label: row?.label || read.rule?.body.type || '规则'}]
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
      <section aria-label="规则条款" className="divide-y border-t">{(read.rule.body.rules ?? []).map((clause, index) => <article key={clause.id} aria-label={`第 ${index + 1} 条`} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-4 py-5">
        <span className="pt-0.5 text-xs tabular-nums text-muted-foreground" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
        <div className="min-w-0 space-y-1">{clause.category && <h2 className="break-words font-medium">{clause.category}</h2>}<p className="whitespace-pre-wrap break-words leading-relaxed">{clause.rule_to_check}</p></div>
      </article>)}</section>
    </>}
  </div>
}
