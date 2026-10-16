// Stitch: projects/13261964020862199279/screens/bd6aa87ac0c64a4697acea05b9e55b3d
import {useEffect, useRef, useState} from 'react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import {FileName} from '../journey/file-name'
import {sameSnapshot} from './result-projection'
import {ShareTargets} from './share-targets'
import {ShareResults} from './share-results'
import {shareUnresolved, useShareActions} from './share-actions'
import type {ShareTarget} from './sharing-contract'
import {readTemplateZip, type AuditTemplateAdapter, type TemplateFolder, type TemplateZip} from './template-contract'
import {templateShareContent, templateShareReference, validTemplateShareScope, type AuditTemplateSharingAdapter, type TemplateShare, type TemplateShareScope} from './template-sharing-contract'

/** One retained session per folder. Its captured input never becomes another customer's share. */
export function TemplateShareSession({open, folder, adapter, templates, onClose, trigger}: {open: boolean; folder: TemplateFolder; adapter: AuditTemplateSharingAdapter; templates: AuditTemplateAdapter; onClose: () => void; trigger: HTMLButtonElement | null}) {
  const [scope, setScope] = useState<TemplateShareScope | null>(null), [loading, setLoading] = useState(false), [error, setError] = useState(''), [reload, setReload] = useState(0)
  const [job, setJob] = useState<TemplateShare | null>(null), [targets, setTargets] = useState<ShareTarget[]>([]), [mode, setMode] = useState<'link' | 'zip'>('link')
  const [zip, setZip] = useState<TemplateZip | null>(null), [preparing, setPreparing] = useState(false), [zipError, setZipError] = useState(''), [retryZip, setRetryZip] = useState(0)
  const scopeEpoch = useRef(0), zipEpoch = useRef(0)
  const actions = useShareActions(job, setJob, input => Boolean(scope && sameSnapshot(input.scope, templateShareReference(scope)) && sameSnapshot(input.content, templateShareContent(scope, input.content.kind, zip))), adapter)
  useEffect(() => {
    const run = ++scopeEpoch.current
    if (!open) {setLoading(false); actions.suspend(); return}
    if (scope || job) return
    setLoading(true); setError('')
    void adapter.readScope(structuredClone(folder)).then(next => {
      if (scopeEpoch.current !== run) return
      if (!validTemplateShareScope(next, folder)) throw new Error('template_share_scope_mismatch')
      setScope(next)
    }).catch(() => {if (scopeEpoch.current === run) setError('无法核对完整客户模板集合。未使用当前搜索页或其他客户文件替代。')}).finally(() => {if (scopeEpoch.current === run) setLoading(false)})
    return () => {scopeEpoch.current += 1}
  }, [open, reload])
  useEffect(() => {
    const run = ++zipEpoch.current
    if (!open || !scope || mode !== 'zip' || zip || job) {setPreparing(false); return}
    if (scope.zip_access.state !== 'allowed' || scope.templates.some(row => row.share_access.state !== 'allowed' || row.download_access.state !== 'allowed')) {setZipError('服务未允许分享完整模板副本，不缩小为部分文件。'); return}
    setPreparing(true); setZipError('')
    const fixedFolder = {customer_id: scope.customer_id, folder_id: scope.folder_id, folder_revision: scope.folder_revision, folder_template_count: scope.templates.length}
    void readTemplateZip(templates, fixedFolder).then(({snapshot}) => {
      if (zipEpoch.current !== run) return
      if (!templateShareContent(scope, 'zip', snapshot)) throw new Error('template_share_zip_mismatch')
      setZip(snapshot)
    }).catch(() => {if (zipEpoch.current === run) setZipError('完整模板包暂不可用。未发送不完整或身份不符的 ZIP。')}).finally(() => {if (zipEpoch.current === run) setPreparing(false)})
    return () => {zipEpoch.current += 1}
  }, [open, scope, mode, retryZip])
  const content = scope ? templateShareContent(scope, mode, zip) : null
  const validTargets = targets.length > 0 && targets.every(target => target.eligibility === 'eligible' && target.organization_id === targets[0].organization_id)
  function fresh() {actions.suspend(); setJob(null); setScope(null); setZip(null); setTargets([]); setMode('link'); setError(''); setZipError(''); setReload(value => value + 1)}
  return <Dialog open={open} onOpenChange={value => {if (!value) onClose()}}><DialogContent className="flex max-h-[88dvh] flex-col overflow-hidden sm:max-w-2xl" onCloseAutoFocus={event => {event.preventDefault(); (trigger?.isConnected && trigger.getClientRects().length ? trigger : document.getElementById('audit-main'))?.focus()}}>
    <DialogHeader><DialogTitle>分享客户模板</DialogTitle><DialogDescription>分享此客户的全部模板。</DialogDescription></DialogHeader>
    <div className="min-h-0 space-y-4 overflow-y-auto">{loading ? <p role="status">正在核对完整模板集合…</p> : error ? <Alert variant="destructive"><AlertTitle>分享范围未确认</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setReload(value => value + 1)}>重新读取分享范围</Button></AlertDescription></Alert> : scope && <>
      <section className="space-y-2" aria-label="固定模板分享范围"><h3 className="font-medium">{scope.customer_name} · 全部 {scope.templates.length} 份模板</h3><p className="break-all text-xs text-muted-foreground">文件夹修订：{job?.request.scope.folder_revision || scope.folder_revision}</p><ItemGroup aria-label="本次分享的模板">{scope.templates.map(row => <Item role="listitem" size="xs" key={row.reference.template_id}><ItemContent className="min-w-0"><FileName name={row.file.original_filename}/>{row.share_access.state !== 'allowed' && <p className="text-xs text-muted-foreground">{row.share_access.reason || '分享权限未确认。'}</p>}</ItemContent></Item>)}</ItemGroup></section>
      {!job && <Tabs value={mode} onValueChange={value => setMode(value as typeof mode)}><TabsList aria-label="模板交付方式"><TabsTrigger value="link">链接</TabsTrigger><TabsTrigger value="zip">文件副本</TabsTrigger></TabsList><TabsContent value="link"><p>发送链接不自动授予模板或文件夹权限。</p><p className="text-xs text-muted-foreground">接收人打开时仍需组织与对象授权，读取本次固定模板版本，不自动改为最新版。</p></TabsContent><TabsContent value="zip"><p>发送完整 ZIP 副本。后续修改权限不能收回已发送的副本。</p>{preparing ? <p role="status">正在准备完整模板包…</p> : zipError ? <Alert><AlertTitle>模板包未就绪</AlertTitle><AlertDescription>{zipError}<Button variant="outline" onClick={() => setRetryZip(value => value + 1)}>重新准备完整模板包</Button></AlertDescription></Alert> : zip && <p className="break-all text-sm">附件：{zip.filename} · {zip.templates.length} 份模板</p>}</TabsContent></Tabs>}
      {job && <p className="text-sm">本次发送：{job.request.content.kind === 'link' ? '固定模板链接；不自动授权' : '完整 ZIP 副本；后续撤权不能收回副本'}</p>}
      {actions.error && <Alert variant="destructive"><AlertTitle>发送回执待核对</AlertTitle><AlertDescription>{actions.error}</AlertDescription></Alert>}
      {job ? <ShareResults job={job} actions={actions} support={{scenario: '客户模板分享', facts: [
        {label: '客户', value: scope.customer_name}, {label: '文件夹', value: job.request.scope.folder_id},
        {label: '文件夹修订', value: job.request.scope.folder_revision}, {label: '交付方式', value: job.request.content.kind === 'link' ? '固定模板链接' : '完整 ZIP 副本'},
        {label: '固定模板', value: job.request.scope.templates.map(reference => `${scope.templates.find(row => sameSnapshot(row.reference, reference))?.file.original_filename || '原分享模板'}\n模板编号：${reference.template_id}\n文件版本：${reference.file_version_id}`).join('\n\n')},
      ]}}/> : <ShareTargets adapter={adapter} value={targets} onChange={setTargets}/>}
      {!job && !content && !preparing && <p role="status" className="text-sm">{scope.link_access.reason || scope.zip_access.reason || '完整模板集合或本方式的分享权限尚未确认，不能发送。'}</p>}
    </>}</div>
    <DialogFooter className="shrink-0 flex-row flex-wrap"><Button variant="outline" onClick={onClose}>关闭</Button>{job && !shareUnresolved(job) && <Button variant="outline" onClick={fresh}>新的模板分享</Button>}{!job && <Button disabled={!validTargets || !content || loading || preparing} onClick={() => {if (scope && content) void actions.send({organization_id: targets[0].organization_id, scope: templateShareReference(scope), targets: targets.map(({kind, id}) => ({kind, id})), content}, targets)}}>发送给 {targets.length} 个目标</Button>}</DialogFooter>
  </DialogContent></Dialog>
}
