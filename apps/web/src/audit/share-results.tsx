import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import type {ReportShare, ShareInput} from './sharing-contract'
import {shareRetryAllowed, type useShareActions} from './share-actions'

const labels = {pending: '等待发送回执', sent: '已发送', failed: '发送失败', unknown: '结果未知'}
type Job = Pick<ReportShare, 'receipt' | 'deliveries'> & {request: Pick<ShareInput, 'organization_id'>}

export function ShareResults({job, actions}: {job: Job; actions: Pick<ReturnType<typeof useShareActions>, 'retry' | 'read' | 'busyRead'>}) {
  return <section className="space-y-3" role="region" aria-label="逐目标发送结果"><h3 className="font-semibold">发送结果</h3><p className="text-xs text-muted-foreground">已发送不表示对方已阅读。</p><ItemGroup>{job.deliveries.map(delivery => <Item role="listitem" aria-label={`发送到 ${delivery.target.display_name}`} size="xs" key={`${delivery.target.kind}:${delivery.target.id}`}><ItemContent><p className="font-medium">{delivery.target.display_name}</p><Badge variant="outline">{labels[delivery.state]}</Badge>{delivery.reason && <p className="text-xs text-muted-foreground">{delivery.reason}</p>}</ItemContent>{delivery.state === 'failed' && <Button variant="outline" size="sm" disabled={!shareRetryAllowed(job, delivery)} onClick={() => void actions.retry(delivery.target)}>仅重试 {delivery.target.display_name}</Button>}</Item>)}</ItemGroup><Button variant="outline" disabled={actions.busyRead} onClick={() => void actions.read()}>{actions.busyRead ? '正在读取发送结果…' : '读取发送结果'}</Button></section>
}
