import {useEffect, useMemo, useState} from 'react'
import {dateLabel, request, type Rule, type RulesList} from '../api'
import {latestRules} from '../helpers'
import {productHref, type ReturnTarget} from '../navigation/route'
import {Alert, AlertDescription, AlertTitle} from '@/components/ui/alert'
import {Badge} from '@/components/ui/badge'
import {Button} from '@/components/ui/button'
import {Checkbox} from '@/components/ui/checkbox'
import {Input} from '@/components/ui/input'
import {NativeSelect} from '@/components/ui/native-select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '@/components/ui/table'
import {DataSurface, FilterBar, PageHeader, PageLayout} from '../ui/PageLayout'

function belongs(rule: Rule, customer: string) {
  return customer === '未指定客户' ? !rule.customers?.length : !!rule.customers?.includes(customer)
}

function RuleMobileRow({rule, current, returnTo}: {rule: Rule; current: boolean; returnTo?: ReturnTarget}) {
  return (
    <article className="grid gap-2 py-4 first:pt-0 last:pb-0">
      <div className="flex items-start justify-between gap-4">
        <a className="min-w-0 text-base font-medium text-primary underline-offset-4 hover:underline" href={productHref({page: 'rule-detail', ruleId: rule.id, returnTo})}>{rule.title}</a>
        {!current && <Badge variant="secondary" className="shrink-0">历史版本</Badge>}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span>{rule.customers?.join('、') || '未指定客户'}</span>
        <span aria-hidden="true">·</span>
        <span>{rule.type || '未指定'}</span>
        <span aria-hidden="true">·</span>
        <time>{dateLabel(rule.created_at)}</time>
      </div>
    </article>
  )
}

export default function RuleLibrary({returnTo, returnLabel}: {returnTo?: ReturnTarget; returnLabel: string}) {
  const [rules, setRules] = useState<Rule[]>([])
  const [query, setQuery] = useState('')
  const [customer, setCustomer] = useState('')
  const [showVersions, setShowVersions] = useState(false)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let active = true
    setBusy(true)
    setError('')
    void request<RulesList>('/rules')
      .then(result => { if (active) setRules(result.rules) })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : '无法读取规则') })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [reload])

  const currentRules = useMemo(() => latestRules(rules), [rules])
  const currentIds = useMemo(() => new Set(currentRules.map(rule => rule.id)), [currentRules])
  const customers = useMemo(
    () => Array.from(new Set(currentRules.flatMap(rule => rule.customers?.length ? rule.customers : ['未指定客户']))).sort(),
    [currentRules],
  )
  const search = query.trim().toLowerCase()
  const visible = (showVersions ? rules : currentRules).filter(rule =>
    (!customer || belongs(rule, customer))
      && (rule.title + ' ' + (rule.type || '')).toLowerCase().includes(search),
  )
  const filtered = Boolean(search || customer || showVersions)
  return (
    <PageLayout>
      <PageHeader
        title="规则库"
        back={returnTo && <a className="text-primary underline-offset-4 hover:underline" href={productHref(returnTo)}>← {returnLabel}</a>}
        action={<Button asChild size="sm" data-testid="new-rule" id="new-rule"><a href={productHref({page: 'rule-import', returnTo})}>上传规则</a></Button>}
      />
      <FilterBar role="search" label="筛选规则" className="grid items-center gap-2 lg:grid-cols-[minmax(0,1fr)_14rem_auto]">
          <Input value={query} onChange={event => setQuery(event.target.value)} type="search" placeholder="搜索规则名称或文件类型" aria-label="搜索规则" />
          <NativeSelect value={customer} aria-label="客户筛选" onChange={event => setCustomer(event.target.value)}>
            <option value="">全部客户</option>
            {customers.map(name => <option key={name}>{name}</option>)}
          </NativeSelect>
          <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 text-sm text-muted-foreground"><Checkbox checked={showVersions} onCheckedChange={checked => setShowVersions(checked === true)} /><span>显示历史版本</span></label>
      </FilterBar>
      {error && <Alert variant="destructive" data-testid="rule-message"><AlertTitle>无法读取规则</AlertTitle><AlertDescription><div className="flex flex-wrap items-center gap-2">{error}<Button type="button" variant="outline" onClick={() => setReload(value => value + 1)}>重试</Button></div></AlertDescription></Alert>}
      {busy && <Alert><AlertDescription>正在读取规则…</AlertDescription></Alert>}
      {!busy && !visible.length && !error && (
        <Alert><AlertTitle>{filtered ? '没有匹配的规则' : '还没有规则'}</AlertTitle><AlertDescription>{filtered ? '调整筛选条件，或清除筛选查看全部规则。' : '上传规则后，可在审核中选择。'}{filtered && <Button type="button" className="mt-2" onClick={() => { setQuery(''); setCustomer(''); setShowVersions(false) }}>清除筛选</Button>}</AlertDescription></Alert>
      )}
      {!busy && !error && !!visible.length && <div data-testid="rule-versions">
        <div className="hidden lg:block"><DataSurface><Table><TableHeader><TableRow><TableHead>规则名称</TableHead><TableHead>适用客户</TableHead><TableHead>文件类型</TableHead></TableRow></TableHeader><TableBody>{visible.map(rule => <TableRow key={rule.id}><TableCell><div className="flex flex-col gap-1"><a className="text-primary underline-offset-4 hover:underline" href={productHref({page: 'rule-detail', ruleId: rule.id, returnTo})}>{rule.title}</a><div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">{dateLabel(rule.created_at)}{!currentIds.has(rule.id) && <Badge variant="secondary">历史版本</Badge>}</div></div></TableCell><TableCell><span className="text-sm text-muted-foreground">{rule.customers?.join('、') || '未指定客户'}</span></TableCell><TableCell>{rule.type || '未指定'}</TableCell></TableRow>)}</TableBody></Table></DataSurface></div>
        <div className="divide-y lg:hidden">{visible.map(rule => <RuleMobileRow key={rule.id} rule={rule} current={currentIds.has(rule.id)} returnTo={returnTo} />)}</div>
      </div>}
    </PageLayout>
  )
}
