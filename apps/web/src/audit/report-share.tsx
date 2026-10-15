// Stitch: projects/13261964020862199279/screens/60fb5697910845cea732821dff699912
import {useState} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {FileName} from '../journey/file-name'
import {ShareTargets} from './share-targets'
import type {AuditSharingAdapter, ShareTarget} from './sharing-contract'
import {shareRetryAllowed, shareUnresolved, type useShareActions} from './share-actions'
import {shareContentFor} from './result-projection'

type Task = components['schemas']['AuditTask']
type Reference = components['schemas']['AuditReportReference']
const deliveryLabels = {pending: '等待发送回执', sent: '已发送', failed: '发送失败', unknown: '结果未知'}

export function ReportShareDialog({task, reports, adapter, actions, onClose, trigger}: {task: Task; reports: Reference[]; adapter: AuditSharingAdapter; actions: ReturnType<typeof useShareActions>; onClose: () => void; trigger: HTMLButtonElement | null}) {
  const [compose, setCompose] = useState(!task.report_share)
  const job = compose ? null : task.report_share
  const [targets, setTargets] = useState<ShareTarget[]>([])
  const [mode, setMode] = useState<'link' | 'file'>('link')
  const scope = job?.request.reports || reports
  const content = job?.request.content || shareContentFor(task, reports, mode)
  const validTargets = targets.length > 0 && targets.every(target => target.eligibility === 'eligible' && target.organization_id === targets[0].organization_id)
  return <Dialog open onOpenChange={open => {if (!open) onClose()}}><DialogContent className="flex max-h-[88dvh] flex-col overflow-hidden sm:max-w-xl" onCloseAutoFocus={event => {event.preventDefault(); trigger?.focus()}}>
    <DialogHeader><DialogTitle>分享审核报告</DialogTitle><DialogDescription>仅限当前组织内人员或已核对成员范围的群组。</DialogDescription></DialogHeader>
    <div className="min-h-0 space-y-4 overflow-y-auto">
      <Badge variant="outline">示例，不会实际发送</Badge>
      <section className="space-y-2" aria-label="固定分享范围"><h3 className="font-medium">本次分享 {scope.length} 份已就绪报告</h3><ItemGroup>{scope.map(reference => {
        const row = task.files.find(file => file.file.id === reference.file_version_id)
        return <Item role="listitem" size="xs" key={reference.file_version_id}><ItemContent><FileName name={`${row?.file.original_filename || '历史文件'}-审核报告`} format="pdf"/></ItemContent><Badge variant="outline">{row?.audit?.conclusion || '结论待核对'}</Badge></Item>
      })}</ItemGroup></section>
      {!job && <Tabs value={mode} onValueChange={value => setMode(value as typeof mode)}><TabsList aria-label="交付方式"><TabsTrigger value="link">链接</TabsTrigger><TabsTrigger value="file">文件</TabsTrigger></TabsList><TabsContent value="link"><p className="text-sm">发送固定报告的应用入口；接收人访问时仍需组织鉴权。</p></TabsContent><TabsContent value="file">{content ? <p>附件：{content.artifact?.filename}</p> : <Alert><AlertTitle>所选 ZIP 尚未就绪</AlertTitle><AlertDescription>请先打包本次选择，不能改用其他报告或未就绪文件。<Button variant="outline" onClick={onClose}>返回报告页打包</Button></AlertDescription></Alert>}</TabsContent></Tabs>}
      {job && job.request.content.kind !== 'link' && <p>附件：{job.request.content.artifact?.filename}</p>}
      {actions.error && <Alert variant="destructive"><AlertTitle>回执待核对</AlertTitle><AlertDescription>{actions.error}</AlertDescription></Alert>}
      {job ? <section className="space-y-3" aria-label="逐目标发送结果"><h3 className="font-semibold">发送结果</h3><p className="text-xs text-muted-foreground">已发送不表示对方已阅读。</p><ItemGroup>{job.deliveries.map(delivery => <Item role="listitem" aria-label={`发送到 ${delivery.target.display_name}`} size="xs" key={`${delivery.target.kind}:${delivery.target.id}`}><ItemContent><p className="font-medium">{delivery.target.display_name}</p><Badge variant="outline">{deliveryLabels[delivery.state]}</Badge>{delivery.reason && <p className="text-xs text-muted-foreground">{delivery.reason}</p>}</ItemContent>{delivery.state === 'failed' && <Button variant="outline" size="sm" disabled={!shareRetryAllowed(job, delivery)} onClick={() => void actions.retry(delivery.target)}>仅重试 {delivery.target.display_name}</Button>}</Item>)}</ItemGroup><Button variant="outline" disabled={actions.busyRead} onClick={() => void actions.read()}>{actions.busyRead ? '正在读取发送结果…' : '读取发送结果'}</Button></section> : <ShareTargets adapter={adapter} value={targets} onChange={setTargets}/>}
    </div>
    <DialogFooter className="shrink-0 flex-row flex-wrap items-center"><p className="mr-auto text-xs text-muted-foreground max-sm:w-full">关闭不撤回发送；原报告与勾选保留。</p><Button variant="outline" onClick={onClose}>关闭</Button>{job && !shareUnresolved(job) && shareContentFor(task, reports, 'link') && <Button variant="outline" onClick={() => {setTargets([]); setMode('link'); setCompose(true)}}>新的分享</Button>}{!job && <Button disabled={!validTargets || !content} onClick={() => {if (content) {void actions.send({organization_id: targets[0].organization_id, reports, targets: targets.map(({kind, id}) => ({kind, id})), content}, targets); setCompose(false)}}}>发送给 {targets.length} 个目标</Button>}</DialogFooter>
  </DialogContent></Dialog>
}
