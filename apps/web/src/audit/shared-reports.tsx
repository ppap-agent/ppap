import {useEffect, useMemo, useState} from 'react'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Item, ItemContent, ItemDescription, ItemGroup, ItemTitle} from '../components/ui/item'
import {sharedReportAdapter} from './native-adapter'
import {ReportPreview} from './report-preview'

export function SharedReports({requestId}: {requestId: string}) {
  const [result, setResult] = useState<components['schemas']['AuditSharedReports'] | null>(null)
  const [selected, setSelected] = useState(''), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const adapter = useMemo(() => sharedReportAdapter(requestId), [requestId])
  useEffect(() => {
    let active = true
    setResult(null); setError('')
    void request<components['schemas']['AuditSharedReports']>(`/audit-report-shares/${encodeURIComponent(requestId)}/reports`).then(value => {
      if (active) {setResult(value); setSelected(value.reports[0]?.reference.report_id || '')}
    }).catch(() => {if (active) setError('无法读取这份分享。请使用原组织的飞书账号登录，或联系发送人。')})
    return () => {active = false}
  }, [requestId, revision])
  const current = result?.reports.find(row => row.reference.report_id === selected)
  return <main className="mx-auto max-w-7xl space-y-6 p-4 md:p-8"><header className="flex flex-wrap items-center justify-between gap-4"><h1 className="text-2xl font-semibold">审核报告</h1><Button variant="outline" asChild><a href="/audit">返回审核工作台</a></Button></header>{error ? <Alert variant="destructive"><AlertTitle>分享暂不可用</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setRevision(value => value + 1)}>重新读取</Button></AlertDescription></Alert> : !result ? <p role="status">正在读取分享报告…</p> : <div className="grid min-w-0 gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]"><nav aria-label="分享的报告"><ItemGroup>{result.reports.map(row => <Item key={row.reference.report_id} asChild variant={selected === row.reference.report_id ? 'muted' : 'default'}><button type="button" className="text-left" aria-current={selected === row.reference.report_id ? 'true' : undefined} onClick={() => setSelected(row.reference.report_id)}><ItemContent className="min-w-0"><ItemTitle wrap>{row.name}</ItemTitle><ItemDescription>{row.conclusion === 'PASS' ? '通过' : '不符合'}</ItemDescription></ItemContent></button></Item>)}</ItemGroup></nav>{current && <ReportPreview name={current.name} rule={current.rule.title} reference={current.reference} report={current.report} ready issue="" adapter={adapter}/>}</div>}</main>
}
