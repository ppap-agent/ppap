import {useEffect, useMemo, useState} from 'react'
import {dateLabel, request, type Rule} from '../api'
import {latestRules} from '../helpers'
import rulesIcon from '../assets/rules.svg'
import {productHref, type ReturnTarget} from '../navigation/route'
import {Badge, Button, Checkbox, DataTable, FeedbackState, FilterBar, Inline, Input, Link, Page, PageHeader, Select, type DataColumn} from '../ui'

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
  const columns: Array<DataColumn<Rule>> = [
    {
      key: 'title',
      header: '规则名称',
      role: 'title',
      cell: rule => (
        <Inline gap="sm" wrap>
          <Button type="button" variant="ghost" disabled={busy} onClick={() => { location.href = productHref({page: 'rule-detail', ruleId: rule.id, returnTo}) }}>
            <img src={rulesIcon} alt="" width={18} height={18} />{rule.title}
          </Button>
          {!currentRules.some(row => row.id === rule.id) && <Badge>历史版本</Badge>}
        </Inline>
      ),
    },
    {key: 'customers', header: '适用客户', cell: rule => rule.customers?.join('、') || '未指定'},
    {key: 'type', header: '文件类型', cell: rule => rule.type || '未指定'},
    {key: 'updated', header: '更新时间', cell: rule => dateLabel(rule.created_at)},
  ]

  function newRule() {
    location.href = productHref({page: 'rule-import', returnTo})
  }

  return (
    <Page>
      {error && <FeedbackState tone="error" inline title="无法读取规则" detail={error} testId="rule-message" />}
      {returnTo && <Link href={productHref(returnTo)}>← {returnLabel}</Link>}
      <PageHeader
        title="规则库"
        description="管理客户规则、适用范围和历史版本。审核受理后会冻结所选规则版本。"
        actions={visible.length > 0 ? <Button type="button" variant="primary" data-testid="new-rule" id="new-rule" disabled={busy} onClick={newRule}>上传规则</Button> : undefined}
      />
      {(visible.length > 0 || query || customer) && (
        <FilterBar onSubmit={() => undefined}>
          <Input value={query} onChange={event => setQuery(event.target.value)} type="search" placeholder="搜索规则名称或文件类型" aria-label="搜索规则" />
          <Select value={customer} aria-label="客户筛选" onChange={event => setCustomer(event.target.value)}>
            <option value="">全部客户</option>
            {customers.map(name => <option key={name}>{name}</option>)}
          </Select>
          <Checkbox label="历史版本" checked={showVersions} onChange={event => setShowVersions(event.target.checked)} />
        </FilterBar>
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
      {!!visible.length && <DataTable rows={visible} columns={columns} layout="records" label="规则库" testId="rule-versions" />}
    </Page>
  )
}
