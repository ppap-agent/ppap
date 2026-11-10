import {useEffect, useState} from 'react'
import {ChevronDown} from 'lucide-react'
import {request, type RulesList, type RuleTypes} from '../api'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {ruleCoverage, type CheckerAvailability, type CustomerCoverage} from './rule-coverage-state'
import type {SuitesRead} from './rules'

/** Shows which file types each customer can already be audited against, built-in rules included. It is only a reading aid, so a failed read leaves the library as is. */
export function RuleCoverage({active, revision, suites}: {active: boolean; revision: number; suites: SuitesRead}) {
  const [read, setRead] = useState<{rules: RulesList['rules']; types: RuleTypes['types']} | null>(null), [selected, setSelected] = useState(''), [failed, setFailed] = useState(false), [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    setFailed(false)
    void Promise.all([
      request<RulesList>('/rules', 'GET', undefined, controller.signal),
      request<RuleTypes>('/rule-types', 'GET', undefined, controller.signal),
    ]).then(([rules, types]) => { if (!controller.signal.aborted) setRead({rules: rules.rules, types: types.types}) })
      .catch(() => { if (!controller.signal.aborted) setFailed(true) })
    return () => controller.abort()
  }, [active, revision, attempt])
  if (failed && !read) return <div role="alert" className="flex flex-wrap items-center gap-2 text-sm"><p>覆盖情况暂时不能读取。</p><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></div>
  if (!read) return <ReadSkeleton label="正在读取覆盖情况…" kind="inline"/>
  // The checker catalog is slow; coverage shows without it and says so, rather than waiting on it.
  const catalog = suites.loading ? null : suites.value
  const checkers: CheckerAvailability[] = []
  for (const item of catalog?.suites ?? []) checkers.push({key: item.suite_key, provided: Boolean(item.current_revision_id), verified: item.verification_status === 'verified'})
  for (const item of catalog?.predefined ?? []) if (item.suite_key) checkers.push({key: item.suite_key, provided: item.availability === 'installed' && Boolean(item.revision_id), verified: false})
  const checkersKnown = catalog !== null
  const rows: CustomerCoverage[] = ruleCoverage(read.rules, read.types, checkers)
  if (!rows.length) return <p className="text-sm text-muted-foreground">还没有收录任何文件类型。</p>
  const shown = rows.find(row => row.customer === selected)
  const ordered = [...rows].sort((a, b) => b.covered - a.covered || a.customer.localeCompare(b.customer, 'zh-CN'))
  return <section aria-label="规则覆盖度" className="space-y-2">
    <div className="flex flex-wrap items-baseline justify-between gap-2"><h2 className="font-semibold">已收录的文件类型</h2><p className="text-xs text-muted-foreground">已收录、已提供检查、已通过验证是三项分开的数量。</p></div>
    <ul className="divide-y">{ordered.map(row => <li key={row.customer}><Button variant={row.customer === selected ? 'secondary' : 'ghost'} aria-expanded={row.customer === selected} className="h-auto min-h-11 w-full justify-between" onClick={() => setSelected(value => value === row.customer ? '' : row.customer)}><span className="min-w-0 break-words text-left">{row.customer}</span><span className="flex shrink-0 items-center gap-2 text-xs tabular-nums text-muted-foreground">已收录 {row.covered}/{row.total} 类{checkersKnown ? ` · 已提供检查 ${row.provided} 类 · 已通过验证 ${row.verified} 类` : suites.loading ? ' · 正在读取检查状态…' : ' · 检查状态暂时不能读取'}<ChevronDown aria-hidden="true" className={row.customer === selected ? 'size-4 rotate-180' : 'size-4'}/></span></Button></li>)}</ul>
    {shown && <ul aria-label={`${shown.customer} 的文件类型`} className="grid gap-x-6 gap-y-2 rounded-md border px-3 py-3 md:grid-cols-2 xl:grid-cols-3">{shown.types.map(item => <li key={item.type} className="flex min-w-0 items-start justify-between gap-3"><span className="min-w-0"><span className="block break-words">{item.label}</span>{item.rules.length > 0 && <span className="block break-words text-xs text-muted-foreground">{item.rules.join('、')}</span>}</span><span className="flex shrink-0 flex-col items-end gap-1">{item.rules.length ? <Badge shape="square" variant="outline">已收录</Badge> : <Badge shape="square" variant="outline">未收录</Badge>}{item.provided && <Badge shape="square" variant="outline">已提供检查</Badge>}{item.verified && <Badge shape="square" variant="success">已通过验证</Badge>}</span></li>)}</ul>}
  </section>
}
