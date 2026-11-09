import {useEffect, useState} from 'react'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {RuleImpactSummary} from './rule-impact'

/** Rule uploads only refresh deterministic impact; optimization stays explicit. */
export function RuleUpdates({active, revision}: {active: boolean; revision: number}) {
  const [suites, setSuites] = useState<components['schemas']['SuiteActive'][]>([]), [error, setError] = useState('')
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    void request<components['schemas']['SuiteCatalog']>('/suites', 'GET', undefined, controller.signal).then(value => {
      if (!controller.signal.aborted) {setSuites(value.suites.filter(suite => suite.rule_update || suite.official_rule_impact?.changed)); setError(value.unavailable?.length ? '部分检查的规则更新影响暂不可读，请重新读取。' : '')}
    }).catch(() => {if (!controller.signal.aborted) setError('规则更新影响暂不可读，请重新读取。')})
    return () => controller.abort()
  }, [active, revision])
  if (!suites.length && !error) return null
  return <section aria-label="待处理规则更新" className="space-y-4">{error && <p role="status">{error}</p>}{suites.map(suite => <article key={suite.suite_key} className="space-y-2 rounded-md border p-3"><h3 className="font-medium">{suite.title} · 需要重新验证</h3><p className="text-sm">新版规则尚未采用。新审核继续使用已发布的旧版检查及规则；历史结果不变。本次上传没有启动模型起草。</p><RuleImpactSummary impact={(suite.rule_update || suite.official_rule_impact)!}/><p className="text-sm">在这项检查的审核结果中打开“检查版本”，即可按新版规则起草并运行全部样本；开始前会确认你的模型 Key 与费用。</p></article>)}</section>
}
