import type {components} from '../generated/openapi'

export type RuleImpact = components['schemas']['RuleImpact']
const labels = {added: '新增', removed: '删除', changed: '正文变化', unchanged: '未变化'}

/** Server-computed exact-ID comparison; this component never calls a model. */
export function RuleImpactSummary({impact}: {impact: RuleImpact}) {
  const counts = impact.clauses.reduce((all, row) => ({...all, [row.kind]: all[row.kind] + 1}), {added: 0, removed: 0, changed: 0, unchanged: 0})
  return <section aria-label="规则更新影响" className="space-y-2 text-sm">
    <p>新增 {counts.added} 条 · 删除 {counts.removed} 条 · 正文变化 {counts.changed} 条 · 未变化 {counts.unchanged} 条</p>
    {impact.appendix_changed && <p>规则附录已修改，也需要重新验证。</p>}
    <p className="text-muted-foreground">按条款编号比较原文，不猜测编号对应关系。所有仍适用的样本都会回归，包括未变化的条款。</p>
    <details><summary className="cursor-pointer">查看逐条原文与受影响检查</summary><div className="mt-2 space-y-3">{impact.clauses.map(row => <article key={row.clause_id} className="space-y-1 border-t pt-2"><p>条款 {row.clause_id} · {labels[row.kind]}{row.checker ? ` · ${row.checker}` : row.kind === 'added' ? ' · 未绑定检查' : ''}</p>{row.before !== null && <p className="whitespace-pre-wrap">修改前：{row.before}</p>}{row.after !== null && <p className="whitespace-pre-wrap">修改后：{row.after}</p>}</article>)}</div></details>
  </section>
}
