import {useEffect, useState} from 'react'
import {ChevronDown} from 'lucide-react'
import {request, type RulesList, type RuleTypes} from '../api'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {ruleCoverage, type CustomerCoverage} from './rule-coverage-state'

/** Shows which file types each customer can already be audited against, built-in rules included. It is only a reading aid, so a failed read leaves the library as is. */
export function RuleCoverage({active, revision}: {active: boolean; revision: number}) {
  const [rows, setRows] = useState<CustomerCoverage[]>([]), [selected, setSelected] = useState('')
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void Promise.all([request<RulesList>('/rules', 'GET', undefined, controller.signal), request<RuleTypes>('/rule-types', 'GET', undefined, controller.signal)]).then(([rules, types]) => {if (!controller.signal.aborted) setRows(ruleCoverage(rules.rules, types.types))}).catch(() => {})
    return () => controller.abort()
  }, [active, revision])
  if (!rows.length) return null
  const shown = rows.find(row => row.customer === selected)
  return <section aria-label="规则覆盖度" className="space-y-2">
    <h2 className="font-semibold">规则覆盖度</h2>
    <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-6">{rows.map(row => <li key={row.customer} className="min-w-0"><Button variant={row.customer === selected ? 'secondary' : 'outline'} size="row" aria-expanded={row.customer === selected} className="w-full justify-between" onClick={() => setSelected(value => value === row.customer ? '' : row.customer)}><span className="min-w-0 truncate font-medium" title={row.customer}>{row.customer}</span><span className="flex shrink-0 items-center gap-1.5 text-xs tabular-nums text-muted-foreground">已覆盖 {row.covered}/{row.total} 类<ChevronDown aria-hidden="true" className={row.customer === selected ? 'size-4 rotate-180 transition-transform' : 'size-4 transition-transform'}/></span></Button></li>)}</ul>
    {shown && <ul aria-label={`${shown.customer} 的文件类型`} className="grid gap-x-6 gap-y-2 rounded-md border px-3 py-3 md:grid-cols-2 xl:grid-cols-3">{shown.types.map(item => <li key={item.type} className="flex min-w-0 items-start justify-between gap-3"><span className="min-w-0"><span className="block break-words">{item.label}</span>{item.rules.length > 0 && <span className="block break-words text-xs text-muted-foreground">{item.rules.join('、')}</span>}</span><Badge shape="square" variant={item.rules.length ? 'success' : 'outline'}>{item.rules.length ? '生效中' : '未覆盖'}</Badge></li>)}</ul>}
  </section>
}
