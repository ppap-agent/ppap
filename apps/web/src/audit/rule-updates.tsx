import {useEffect, useState} from 'react'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {RuleImpactSummary} from './rule-impact'
import type {SuitesRead} from './rules'

/** Rule uploads only refresh deterministic impact; optimization stays explicit. The catalog flags which Suites changed; only those are read in detail. */
export function RuleUpdates({suites: read, onReload}: {suites: SuitesRead; onReload: () => void}) {
  const pending = read.value?.suites.filter(suite => suite.rule_update_pending) ?? []
  const pendingKeys = pending.map(suite => suite.suite_key).join('\n')
  const [details, setDetails] = useState<Record<string, components['schemas']['SuiteActive']>>({})
  useEffect(() => {
    setDetails({})
    if (!pendingKeys) return
    const controller = new AbortController()
    for (const key of pendingKeys.split('\n')) {
      void request<components['schemas']['SuiteActive']>(`/suites/${encodeURIComponent(key)}/active`, 'GET', undefined, controller.signal)
        .then(value => {if (!controller.signal.aborted) setDetails(previous => ({...previous, [key]: value}))})
        .catch(() => {})
    }
    return () => controller.abort()
  }, [pendingKeys, read.revision])
  const error = read.error ? '规则更新影响暂不可读，请重新读取。' : read.value?.unavailable?.length ? '部分检查的规则更新影响暂不可读，请重新读取。' : ''
  if (!pending.length && !error) return null
  return <section aria-label="待处理规则更新" className="space-y-4">{error && <div role="status" className="flex flex-wrap items-center gap-2 text-sm"><p>{error}</p><Button variant="outline" size="sm" disabled={read.loading} onClick={onReload}>重新读取</Button></div>}{pending.map(suite => {
    const impact = details[suite.suite_key]?.rule_update || details[suite.suite_key]?.official_rule_impact
    return <article key={suite.suite_key} className="space-y-2 rounded-md border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">{suite.title} · 规则已更新，检查还在用旧版</h3><Button variant="outline" size="sm" asChild><a href={`/requirements?checkerSuite=${encodeURIComponent(suite.suite_key)}`}>去检查版本处理</a></Button></div><p className="text-sm text-muted-foreground">新审核继续用旧规则，直到你在检查版本中按新规则重新验证并发布。已有结果不变。</p>{impact && <RuleImpactSummary impact={impact}/>}</article>
  })}</section>
}
