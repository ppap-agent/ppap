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
import {shareUnresolved, type useShareActions, type ShareContentFor} from './share-actions'
import type {ReportShare, ShareRequest} from './sharing-contract'
import {ShareResults} from './share-results'

export type ShareDescription = (reference: Reference) => {name: string; conclusion: string; context?: string}
type Reference = components['schemas']['AuditReportReference']

export function ReportShareDialog({open, share, describe, contentFor, reports, adapter, actions, onClose, trigger}: {open: boolean; share?: ReportShare | null; describe: ShareDescription; contentFor: ShareContentFor; reports: Reference[]; adapter: AuditSharingAdapter; actions: ReturnType<typeof useShareActions<ShareRequest>>; onClose: () => void; trigger: HTMLButtonElement | null}) {
  const [composingAfter, setComposingAfter] = useState<string | null>(null)
  const job = share && (shareUnresolved(share) || composingAfter !== share.request.request_id) ? share : null
  const [targets, setTargets] = useState<ShareTarget[]>([])
  const [targetsReady, setTargetsReady] = useState(false)
  const [mode, setMode] = useState<'link' | 'file'>('link')
  const scope = job?.request.reports || reports
  const content = job?.request.content || contentFor(reports, mode)
  const validTargets = open && targetsReady && targets.length > 0 && targets.every(target => target.eligibility === 'eligible' && target.organization_id === targets[0].organization_id)
  return <Dialog open={open} onOpenChange={open => {if (!open) onClose()}}><DialogContent className="flex max-h-[88dvh] flex-col overflow-hidden sm:max-w-xl" onCloseAutoFocus={event => {event.preventDefault(); trigger?.focus()}}>
    <DialogHeader><DialogTitle>分享审核报告</DialogTitle><DialogDescription>发送至当前组织的人员或已核验内部群。</DialogDescription></DialogHeader>
    <div className="min-h-0 space-y-5 overflow-y-auto">
      <section className="rounded-lg border bg-muted/30 p-3" aria-label="固定分享范围"><h3 className="sr-only">本次分享 {scope.length} 份已就绪报告</h3><ItemGroup>{scope.map(reference => {
        const row = describe(reference)
        return <Item role="listitem" size="xs" key={JSON.stringify(reference)}><ItemContent><FileName name={`${row.name}-审核报告`} format="pdf"/>{row.context && <p className="text-xs text-muted-foreground">{row.context}</p>}</ItemContent><Badge variant="outline">{row.conclusion}</Badge></Item>
      })}</ItemGroup></section>
      {!job && <section className="space-y-2" aria-label="发送方式"><h3 className="font-medium">发送方式</h3><Tabs value={mode} onValueChange={value => setMode(value as typeof mode)}><TabsList aria-label="交付方式"><TabsTrigger value="link">链接</TabsTrigger><TabsTrigger value="file">文件</TabsTrigger></TabsList><TabsContent value="link"><p className="text-xs text-muted-foreground">接收人需登录，并有权查看这份报告。</p></TabsContent><TabsContent value="file">{content ? <p className="[overflow-wrap:anywhere] text-xs text-muted-foreground">附件：{content.artifact?.filename}</p> : <Alert><AlertTitle>所选 ZIP 尚未就绪</AlertTitle><AlertDescription>请先打包所选报告。<Button variant="outline" onClick={onClose}>返回报告页打包</Button></AlertDescription></Alert>}</TabsContent></Tabs></section>}
      {job && job.request.content.kind !== 'link' && <p>附件：{job.request.content.artifact?.filename}</p>}
      {actions.error && <Alert variant="destructive"><AlertTitle>回执待核对</AlertTitle><AlertDescription>{actions.error}</AlertDescription></Alert>}
      {job ? <ShareResults job={job} actions={actions} support={{scenario: '审核报告分享', facts: [
        {label: '交付方式', value: {link: '链接', pdf: 'PDF 文件', zip: 'ZIP 文件'}[job.request.content.kind]},
        {label: '固定报告', value: job.request.reports.map(reference => `${describe(reference).name}-审核报告\n任务：${reference.task_id}\n审核轮次：${reference.round_id}\n文件版本：${reference.file_version_id}\n结果尝试：${reference.result_attempt}\n报告编号：${reference.report_id}`).join('\n\n')},
      ]}}/> : open && <ShareTargets adapter={adapter} value={targets} onChange={setTargets} onReadyChange={setTargetsReady}/>}
    </div>
    <div className="shrink-0 border-t pt-4"><DialogFooter className="flex-row flex-wrap items-center">{job && <p className="mr-auto text-xs text-muted-foreground max-sm:w-full">关闭不会撤回已发送的报告。</p>}<Button variant="outline" onClick={onClose}>关闭</Button>{job && !shareUnresolved(job) && contentFor(reports, 'link') && <Button variant="outline" onClick={() => {setTargets([]); setMode('link'); setComposingAfter(job.request.request_id)}}>新的分享</Button>}{!job && <Button disabled={!validTargets || !content} onClick={() => {if (content) void actions.send({organization_id: targets[0].organization_id, reports, targets: targets.map(({kind, id}) => ({kind, id})), content}, targets)}}>{targets.length ? `发送给 ${targets.length} 个目标` : '选择接收人后发送'}</Button>}</DialogFooter></div>
  </DialogContent></Dialog>
}
