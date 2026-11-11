import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import type {ReportShare, ShareInput} from './sharing-contract'
import {shareRetryAllowed, type useShareActions} from './share-actions'
import {SupportEntryContext, type SupportSnapshot} from './support'

const labels = {pending: '等待发送结果', sent: '已发送', failed: '发送失败', unknown: '结果未知'}
type Job = Pick<ReportShare, 'receipt' | 'deliveries'> & {request: Pick<ShareInput, 'organization_id'>}

export function ShareResults({job, actions, support}: {job: Job; actions: Pick<ReturnType<typeof useShareActions>, 'retry' | 'read' | 'busyRead' | 'error'>; support?: SupportSnapshot}) {
  const openSupport = useContext(SupportEntryContext)
  function contactSupport() {
    if (!support || !openSupport) return
    openSupport({scenario: support.scenario, facts: [
      {label: '原请求', value: job.receipt.request_id},
      {label: '接收状态', value: {pending: '提交结果未知', accepted: '已接收', rejected: '未被接收'}[job.receipt.state]},
      ...(job.receipt.reason ? [{label: '接收说明', value: job.receipt.reason}] : []),
      ...(support.facts || []),
      {label: '各目标发送结果', value: job.deliveries.map(row => `${row.target.display_name} · ${labels[row.state]}\n本次尝试：${row.attempt_id}${row.reason ? `\n原因：${row.reason}` : ''}`).join('\n\n')},
      ...(actions.error ? [{label: '结果读取说明', value: actions.error}] : []),
    ]})
  }
  return <section className="space-y-3" role="region" aria-label="逐目标发送结果"><h3 className="font-semibold">发送结果</h3><ItemGroup>{job.deliveries.map(delivery => <Item role="listitem" aria-label={`发送到 ${delivery.target.display_name}`} size="xs" key={`${delivery.target.kind}:${delivery.target.id}`}><ItemContent><p className="font-medium">{delivery.target.display_name}</p><Badge variant={delivery.state === 'sent' ? 'success' : delivery.state === 'failed' ? 'destructive' : 'warning'}>{labels[delivery.state]}</Badge>{delivery.reason && <p className="text-xs text-muted-foreground">{delivery.reason}</p>}</ItemContent>{delivery.state === 'failed' && <Button variant="outline" size="sm" disabled={!shareRetryAllowed(job, delivery)} onClick={() => void actions.retry(delivery.target)}>仅重试 {delivery.target.display_name}</Button>}</Item>)}</ItemGroup><div className="flex flex-wrap gap-2"><Button variant="outline" disabled={actions.busyRead} onClick={() => void actions.read()}>{actions.busyRead ? '正在读取发送结果…' : '读取发送结果'}</Button>{openSupport && support && (actions.error || job.receipt.state !== 'accepted' || job.deliveries.some(row => row.state !== 'sent')) && <Button variant="outline" onClick={contactSupport}>联系支持</Button>}</div></section>
}
import {useContext} from 'react'
