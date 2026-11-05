import {useEffect, useState} from 'react'
import {ChevronDown} from 'lucide-react'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {ruleCoverage} from './rule-coverage-state'

/** Shows which file types each customer can already be audited against. It is only a reading aid, so a failed read leaves the library as is. */
export function RuleCoverage({entries, active}: {entries: components['schemas']['RuleLibraryEntry'][]; active: boolean}) {
  const [types, setTypes] = useState<string[]>([])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void request<components['schemas']['RuleTypes']>('/rule-types', 'GET', undefined, controller.signal).then(result => {if (!controller.signal.aborted) setTypes(result.types)}).catch(() => {})
    return () => controller.abort()
  }, [active])
  const rows = types.length ? ruleCoverage(entries, types) : []
  if (!rows.length) return null
  return <section aria-label="规则覆盖度" className="space-y-2">
    <h2 className="font-semibold">规则覆盖度</h2>
    <ul className="divide-y rounded-md border">{rows.map(row => <li key={row.customer}><Collapsible>
      <CollapsibleTrigger asChild><Button variant="ghost" size="row" className="group w-full justify-between"><span className="min-w-0 break-words font-medium">{row.customer}</span><span className="flex shrink-0 items-center gap-3 text-xs tabular-nums text-muted-foreground">已覆盖 {row.covered}/{row.total} 类<ChevronDown aria-hidden="true" className="size-4 transition-transform group-data-[state=open]:rotate-180"/></span></Button></CollapsibleTrigger>
      <CollapsibleContent><ul aria-label={`${row.customer} 的文件类型`} className="grid gap-x-6 gap-y-2 border-t px-3 py-3 md:grid-cols-2">{row.types.map(item => <li key={item.type} className="flex min-w-0 items-start justify-between gap-3"><span className="min-w-0"><span className="block break-words">{item.type}</span>{item.rules.length > 0 && <span className="block break-words text-xs text-muted-foreground">{item.rules.join('、')}</span>}</span><Badge shape="square" variant={item.rules.length ? 'success' : 'outline'}>{item.rules.length ? '生效中' : '未覆盖'}</Badge></li>)}</ul></CollapsibleContent>
    </Collapsible></li>)}</ul>
  </section>
}
