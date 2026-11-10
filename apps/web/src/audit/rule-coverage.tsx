import {useEffect, useState} from 'react'
import {ChevronDown} from 'lucide-react'
import {request, type RulesList, type RuleTypes} from '../api'
import type {components} from '../generated/openapi'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {ruleCoverage, type CheckerAvailability, type CustomerCoverage} from './rule-coverage-state'

/** Shows which file types each customer can already be audited against, built-in rules included. It is only a reading aid, so a failed read leaves the library as is. */
export function RuleCoverage({active, revision}: {active: boolean; revision: number}) {
  const [rows, setRows] = useState<CustomerCoverage[]>([]), [selected, setSelected] = useState(''), [checkersKnown, setCheckersKnown] = useState(false), [failed, setFailed] = useState(false), [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    setCheckersKnown(false)
    setFailed(false)
    void Promise.all([
      request<RulesList>('/rules', 'GET', undefined, controller.signal),
      request<RuleTypes>('/rule-types', 'GET', undefined, controller.signal),
      request<components['schemas']['SuiteCatalog']>('/suites', 'GET', undefined, controller.signal).catch(() => null),
    ]).then(([rules, types, suites]) => {
      if (controller.signal.aborted) return
      const checkers: CheckerAvailability[] = []
      for (const item of suites?.suites ?? []) checkers.push({key: item.suite_key, provided: Boolean(item.current_revision_id), verified: item.verification_status === 'verified'})
      for (const item of suites?.predefined ?? []) if (item.suite_key) checkers.push({key: item.suite_key, provided: item.availability === 'installed' && Boolean(item.revision_id), verified: false})
      setCheckersKnown(suites !== null)
      setRows(ruleCoverage(rules.rules, types.types, checkers))
    }).catch(() => { if (!controller.signal.aborted) setFailed(true) })
    return () => controller.abort()
  }, [active, revision, attempt])
  if (failed && !rows.length) return <div role="alert" className="flex flex-wrap items-center gap-2 text-sm"><p>覆盖情况暂时不能读取。</p><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>重新读取</Button></div>
  if (!rows.length) return null
  const shown = rows.find(row => row.customer === selected)
  const ordered = [...rows].sort((a, b) => b.covered - a.covered || a.customer.localeCompare(b.customer, 'zh-CN'))
  return <section aria-label="规则覆盖度" className="space-y-2">
    <div className="flex flex-wrap items-baseline justify-between gap-2"><h2 className="font-semibold">已收录的文件类型</h2><p className="text-xs text-muted-foreground">已收录、已提供检查、已通过验证是三项分开的数量。</p></div>
    <ul className="divide-y">{ordered.map(row => <li key={row.customer}><Button variant={row.customer === selected ? 'secondary' : 'ghost'} aria-expanded={row.customer === selected} className="h-auto min-h-11 w-full justify-between" onClick={() => setSelected(value => value === row.customer ? '' : row.customer)}><span className="min-w-0 break-words text-left">{row.customer}</span><span className="flex shrink-0 items-center gap-2 text-xs tabular-nums text-muted-foreground">已收录 {row.covered}/{row.total} 类{checkersKnown ? ` · 已提供检查 ${row.provided} 类 · 已通过验证 ${row.verified} 类` : ' · 检查状态暂时不能读取'}<ChevronDown aria-hidden="true" className={row.customer === selected ? 'size-4 rotate-180' : 'size-4'}/></span></Button></li>)}</ul>
    {shown && <ul aria-label={`${shown.customer} 的文件类型`} className="grid gap-x-6 gap-y-2 rounded-md border px-3 py-3 md:grid-cols-2 xl:grid-cols-3">{shown.types.map(item => <li key={item.type} className="flex min-w-0 items-start justify-between gap-3"><span className="min-w-0"><span className="block break-words">{item.label}</span>{item.rules.length > 0 && <span className="block break-words text-xs text-muted-foreground">{item.rules.join('、')}</span>}</span><span className="flex shrink-0 flex-col items-end gap-1">{item.rules.length ? <Badge shape="square" variant="outline">已收录</Badge> : <Badge shape="square" variant="outline">未收录</Badge>}{item.provided && <Badge shape="square" variant="outline">已提供检查</Badge>}{item.verified && <Badge shape="square" variant="success">已通过验证</Badge>}</span></li>)}</ul>}
  </section>
}
