import {useEffect, useMemo, useState} from 'react'
import {dateLabel, request, type Rule} from '../api'
import {latestRules} from '../helpers'
import rulesIcon from '../assets/rules.svg'
import {productHref, type ReturnTarget} from '../navigation/route'
import {Badge, Button, FeedbackState, PageHeader} from '../ui/primitives'

function belongs(rule: Rule, customer: string) {
  return customer === '未指定客户' ? !rule.customers?.length : !!rule.customers?.includes(customer)
}

export default function RuleLibrary({returnTo, returnLabel}: {returnTo?: ReturnTarget; returnLabel: string}) {
  const [rules, setRules] = useState<Rule[]>([])
  const [query, setQuery] = useState('')
  const [customer, setCustomer] = useState('')
  const [showVersions, setShowVersions] = useState(false)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    void request<{rules: Rule[]}>('/rules')
      .then(result => { if (active) setRules(result.rules) })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : '无法读取规则') })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [])

  const currentRules = useMemo(() => latestRules(rules), [rules])
  const customers = useMemo(
    () => Array.from(new Set(currentRules.flatMap(rule => rule.customers?.length ? rule.customers : ['未指定客户']))).sort(),
    [currentRules],
  )
  const visible = (showVersions ? rules : currentRules).filter(rule =>
    (!customer || belongs(rule, customer))
      && (rule.title + ' ' + rule.type).toLowerCase().includes(query.toLowerCase()),
  )

  function openRule(id: string) {
    location.href = productHref({page: 'rule-detail', ruleId: id, returnTo})
  }

  function newRule() {
    location.href = productHref({page: 'rule-import', returnTo})
  }

  return (
    <section className="page-content rules-page">
      {error && <p className="error" role="alert" id="rule-message" data-testid="rule-message">{error}</p>}
      {returnTo && <a className="back-link" href={productHref(returnTo)}>← {returnLabel}</a>}
      <PageHeader
        title="规则库"
        description="管理客户规则、适用范围和历史版本。审核受理后会冻结所选规则版本。"
        actions={visible.length > 0 ? <Button type="button" variant="primary" data-testid="new-rule" id="new-rule" disabled={busy} onClick={newRule}>上传规则</Button> : undefined}
      />
      {(visible.length > 0 || query || customer) && (
        <div className="toolbar">
          <input value={query} onChange={event => setQuery(event.target.value)} type="search" placeholder="搜索规则名称或文件类型" aria-label="搜索规则" />
          <select value={customer} aria-label="客户筛选" onChange={event => setCustomer(event.target.value)}>
            <option value="">全部客户</option>
            {customers.map(name => <option key={name}>{name}</option>)}
          </select>
          <label className="inline-check"><input type="checkbox" checked={showVersions} onChange={event => setShowVersions(event.target.checked)} />历史版本</label>
        </div>
      )}
      {busy && <FeedbackState tone="loading" detail="正在读取规则…" />}
      {!busy && !visible.length && !error && (
        <FeedbackState
          title={query || customer ? '没有匹配的规则' : '还没有客户规则'}
          detail={query || customer ? '调整筛选条件，或清除筛选查看全部规则。' : '上传客户规则后，审核时可以直接选择适用版本。'}
          icon={<img src={rulesIcon} alt="" width={36} height={36} />}
        >
          {query || customer
            ? <Button type="button" onClick={() => { setQuery(''); setCustomer('') }}>清除筛选</Button>
            : <Button type="button" variant="primary" data-testid="new-rule" id="new-rule" disabled={busy} onClick={newRule}>上传规则</Button>}
        </FeedbackState>
      )}
      {!!visible.length && (
        <div className="table-scroll">
          <table className="responsive-table rules-table">
            <thead><tr><th>规则名称</th><th>适用客户</th><th>文件类型</th><th>更新时间</th></tr></thead>
            <tbody data-testid="rule-versions" id="rule-versions">
              {visible.map(rule => (
                <tr key={rule.id}>
                  <td className="record-title">
                    <button type="button" className="text-button" disabled={busy} onClick={() => openRule(rule.id)}>
                      <img src={rulesIcon} alt="" width={18} height={18} />{rule.title}
                    </button>
                    {!currentRules.some(row => row.id === rule.id) && <Badge>历史版本</Badge>}
                  </td>
                  <td data-label="适用客户">{rule.customers?.join('、') || '未指定'}</td>
                  <td data-label="文件类型">{rule.type || '未指定'}</td>
                  <td data-label="更新时间">{dateLabel(rule.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
