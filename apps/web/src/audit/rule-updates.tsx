import {Button} from '../components/ui/button'
import {RuleImpactSummary} from './rule-impact'
import type {SuitesRead} from './rules'

/** Rule uploads only refresh deterministic impact; optimization stays explicit. */
export function RuleUpdates({suites: read, onReload}: {suites: SuitesRead; onReload: () => void}) {
  const suites = read.value?.suites.filter(suite => suite.rule_update || suite.official_rule_impact?.changed) ?? []
  const error = read.error ? '规则更新影响暂不可读，请重新读取。' : read.value?.unavailable?.length ? '部分检查的规则更新影响暂不可读，请重新读取。' : ''
  if (!suites.length && !error) return null
  return <section aria-label="待处理规则更新" className="space-y-4">{error && <div role="status" className="flex flex-wrap items-center gap-2 text-sm"><p>{error}</p><Button variant="outline" size="sm" disabled={read.loading} onClick={onReload}>重新读取</Button></div>}{suites.map(suite => <article key={suite.suite_key} className="space-y-2 rounded-md border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">{suite.title} · 规则已更新，检查还在用旧版</h3><Button variant="outline" size="sm" asChild><a href={`/requirements?checkerSuite=${encodeURIComponent(suite.suite_key)}`}>去检查版本处理</a></Button></div><p className="text-sm text-muted-foreground">新审核继续用旧规则，直到你在检查版本中按新规则重新验证并发布。已有结果不变。</p><RuleImpactSummary impact={(suite.rule_update || suite.official_rule_impact)!}/></article>)}</section>
}
