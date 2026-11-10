// Stitch: projects/13261964020862199279/screens/f71a60b240af40eebd80407eb71569f1.
import {useEffect, useState} from 'react'
import {ChevronRight, Search} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {Tabs, TabsList, TabsTrigger} from '../components/ui/tabs'
import {ReadSkeleton} from '../components/read-skeleton'
import {CheckerRelease} from './checker-release'
import {auditAddressChanged, writeAuditAddress} from './routes'
import {followRulesLink, rulesAddress, RulesTrail} from './rule-templates-view'
import type {SuitesRead} from './rules'

type Summary = components['schemas']['SuiteSummary']

/** A program needs the person when its rules changed, the platform offers a newer version, or it was never adopted. */
export const needsAttention = (suite: Summary) => suite.rule_update_pending || suite.official_update_available
export const attentionCount = (read: SuitesRead) => read.value?.suites.filter(needsAttention).length ?? 0

const verification = (suite: Summary): {label: string; variant: 'success' | 'warning' | 'outline'} => suite.rule_update_pending || suite.verification_status === 'stale' ? {label: '需重新验证', variant: 'warning'}
  : suite.verification_status === 'verified' ? {label: '已验证', variant: 'success'} : {label: '未验证', variant: 'outline'}
const reason = (suite: Summary) => suite.rule_update_pending ? '规则已更新，需重新验证' : suite.official_update_available ? '系统提供了新版本' : ''
const selectedSuite = () => new URL(location.href).searchParams.get('checkerSuite') || ''

/** Programs that run automatic review. Publishing affects only later audits; history and verification live in each program. */
export function Automation({active, suites: read, onReload, onBusyChange}: {active: boolean; suites: SuitesRead; onReload: () => void; onBusyChange: (busy: boolean) => void}) {
  const [selected, setSelected] = useState(selectedSuite)
  const [filter, setFilter] = useState<'attention' | 'all'>(() => attentionCount(read) ? 'attention' : 'all')
  const [query, setQuery] = useState('')
  useEffect(() => {
    const restore = () => setSelected(selectedSuite())
    window.addEventListener('popstate', restore); window.addEventListener(auditAddressChanged, restore)
    return () => {window.removeEventListener('popstate', restore); window.removeEventListener(auditAddressChanged, restore)}
  }, [])
  const suites = read.value?.suites ?? []
  const suite = suites.find(item => item.suite_key === selected)
  if (selected) {
    const revisionId = suite?.current_revision_id || suite?.candidate_revision_id
    return <div className="space-y-6">
      <RulesTrail items={[{label: '自动审核', href: rulesAddress({rulesArea: 'automation'})}, {label: suite?.title || '程序'}]}/>
      {read.loading && !suite && <ReadSkeleton label="正在读取程序…" kind="inline"/>}
      {!read.loading && !suite && <p role="alert">这个程序当前不可访问，请返回列表重新选择。</p>}
      {active && suite && revisionId && <CheckerRelease key={suite.suite_key} scope={`catalog:${suite.suite_key}`} suiteKey={suite.suite_key} revisionId={revisionId} refreshKey={String(read.revision)} onBusyChange={onBusyChange} onPublished={onReload} onDependency={key => writeAuditAddress(rulesAddress({rulesArea: 'automation', checkerSuite: key}), 'push')}/>}
    </div>
  }
  const needle = query.trim().toLocaleLowerCase()
  const attention = suites.filter(needsAttention)
  const shown = (filter === 'attention' ? attention : suites).filter(item => !needle || `${item.title} ${item.suite_key}`.toLocaleLowerCase().includes(needle))
  const predefinedHref = rulesAddress({rulesArea: 'predefined'})
  return <div className="space-y-6">
    <p className="text-muted-foreground">自动审核按这里正在使用的程序执行。发布新版本只影响之后的新审核。</p>
    {read.error && <Alert variant="destructive"><AlertTitle>程序列表未能读取</AlertTitle><AlertDescription><p>{read.error}</p><Button variant="outline" size="sm" disabled={read.loading} onClick={onReload}>重新读取</Button></AlertDescription></Alert>}
    {read.loading && !read.value ? <ReadSkeleton label="正在读取自动审核程序…" kind="table" rows={5}/> : <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs value={filter} onValueChange={value => setFilter(value === 'attention' ? 'attention' : 'all')}><TabsList aria-label="筛选程序"><TabsTrigger value="attention">需要处理 {attention.length}</TabsTrigger><TabsTrigger value="all">全部 {suites.length}</TabsTrigger></TabsList></Tabs>
        <div role="search" className="flex items-center gap-2 w-full max-w-xs"><Search aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/><Input aria-label="搜索程序" type="search" placeholder="搜索程序名称" maxLength={200} value={query} onChange={event => setQuery(event.target.value)}/></div>
      </div>
      {shown.length ? <div className="overflow-x-auto"><Table><TableHeader><TableRow><TableHead>程序</TableHead><TableHead className="w-32">来源</TableHead><TableHead className="w-28">状态</TableHead><TableHead className="w-32">验证</TableHead><TableHead className="w-10"><span className="sr-only">打开</span></TableHead></TableRow></TableHeader>
        <TableBody>{shown.map(item => {
          const href = rulesAddress({rulesArea: 'automation', checkerSuite: item.suite_key})
          const status = verification(item)
          return <TableRow key={item.suite_key} className="relative">
            <TableCell className="whitespace-normal"><a className="block break-words font-medium underline-offset-4 after:absolute after:inset-0 hover:underline focus-visible:outline-ring" href={href} onClick={event => followRulesLink(event, href)}>{item.title}</a>{reason(item) && <span className="block text-xs text-muted-foreground">{reason(item)}</span>}</TableCell>
            <TableCell><Badge shape="square" variant="outline">{item.origin === 'enterprise' ? '本企业修改' : '系统提供'}</Badge></TableCell>
            <TableCell>{item.current_revision_id ? '正在使用' : '未启用'}</TableCell>
            <TableCell><Badge shape="square" variant={status.variant}>{status.label}</Badge></TableCell>
            <TableCell><ChevronRight aria-hidden="true" className="size-4 text-muted-foreground"/></TableCell>
          </TableRow>
        })}</TableBody></Table></div>
        : <p className="py-8 text-center text-muted-foreground">{filter === 'attention' && !needle ? '没有需要处理的程序。' : '没有匹配的程序。'}</p>}
      {read.value?.unavailable?.length ? <p role="status" className="text-sm text-muted-foreground">另有 {read.value.unavailable.length} 个程序暂时不能读取。</p> : null}
      <p className="text-sm text-muted-foreground">在审核结果里点“这条判错了”所做的修改，也会出现在这里，验证后再发布。<a className="ml-1 underline underline-offset-4" href={predefinedHref} onClick={event => followRulesLink(event, predefinedHref)}>浏览系统程序目录</a></p>
    </>}
  </div>
}
