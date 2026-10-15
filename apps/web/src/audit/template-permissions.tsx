// Stitch: projects/13261964020862199279/screens/bd6aa87ac0c64a4697acea05b9e55b3d
import {useEffect, useRef, useState} from 'react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {sameSnapshot} from './result-projection'
import {targetKey} from './sharing-contract'
import {permissionUnresolved, validPermissions, validPermissionReceipt, type AuditTemplatePermissionAdapter, type PermissionFolder, type PermissionAction, type PermissionRequest, type PermissionReceipt, type TemplatePermissions} from './template-permission-contract'

/** Closing hides the view; it does not revoke the original mutation's identity. */
export function TemplatePermissionSession({open, folder, customerName, adapter, onClose, trigger}: {open: boolean; folder: PermissionFolder; customerName: string; adapter: AuditTemplatePermissionAdapter; onClose: () => void; trigger: HTMLButtonElement | null}) {
  const [snapshot, setSnapshot] = useState<TemplatePermissions | null>(null), [query, setQuery] = useState(''), [snapshotUsable, setSnapshotUsable] = useState(false), [loading, setLoading] = useState(false), [error, setError] = useState('')
  const [proposal, setProposal] = useState<{revision: string; action: PermissionAction} | null>(null), [job, setJob] = useState<PermissionReceipt | null>(null), [busy, setBusy] = useState(false)
  const current = useRef<PermissionReceipt | null>(null), epoch = useRef(0), alive = useRef(true), observing = useRef<object | null>(null)
  function save(receipt: PermissionReceipt) {current.current = receipt; setJob(receipt)}
  useEffect(() => {alive.current = true; return () => {alive.current = false; epoch.current += 1}}, [])
  useEffect(() => {
    if (!open) {epoch.current += 1; observing.current = null; setLoading(false); setProposal(null); setBusy(false); if (current.current) save({...current.current, state: permissionUnresolved(current.current) ? 'unknown' : current.current.state}); return}
    void readPermissions(query.trim())
  }, [open])
  async function readPermissions(search: string) {
    const run = ++epoch.current; setLoading(true); setSnapshotUsable(false); setProposal(null); setError('')
    try {
      const next = await adapter.readPermissions(structuredClone(folder), search)
      if (!alive.current || epoch.current !== run) return
      if (!validPermissions(next, folder, search)) throw new Error('permission_snapshot_mismatch')
      setSnapshot(next); setSnapshotUsable(true)
    } catch {if (alive.current && epoch.current === run) setError('无法确认此文件夹的当前权限。保留原修改请求，不允许修改。')}
    finally {if (alive.current && epoch.current === run) setLoading(false)}
  }
  async function observe(observation: PermissionReceipt, operation: () => Promise<PermissionReceipt>) {
    const token = {}; observing.current = token; save(observation); setBusy(true); setError('')
    try {
      const receipt = await operation()
      if (!alive.current || current.current !== observation) return
      if (!validPermissionReceipt(receipt, observation.request) || !permissionUnresolved(observation) && !sameSnapshot(receipt, observation)) throw new Error('permission_receipt_mismatch')
      if (!permissionUnresolved(receipt)) {setSnapshotUsable(false); epoch.current += 1; setLoading(false)}
      save(receipt)
    } catch {if (alive.current && current.current === observation) {save({...observation, state: 'unknown', result_revision: null, reason: '未取得可靠修改回执。只读取原请求，不重复修改。'}); setError('修改结果仍待确认，不能把当前权限当作原请求回执。')}}
    finally {if (alive.current && observing.current === token) {observing.current = null; setBusy(false)}}
  }
  const locked = busy || permissionUnresolved(job)
  function apply() {
    if (!snapshot || !proposal || !snapshotUsable || loading || locked || snapshot.manage_access.state !== 'allowed' || snapshot.permission_revision !== proposal.revision || !snapshot.actions.some(action => sameSnapshot(action, proposal.action))) return
    const request: PermissionRequest = {request_id: crypto.randomUUID(), folder: structuredClone(folder), permission_revision: proposal.revision, action: structuredClone(proposal.action)}
    setProposal(null); setSnapshotUsable(false); epoch.current += 1
    void observe({request, state: 'pending', result_revision: null, reason: '等待本次修改回执。'}, () => adapter.apply(structuredClone(request)))
  }
  const candidates = snapshot?.actions.filter(action => !snapshot.members.some(member => targetKey(member.target) === targetKey(action.target))) || []
  const rows = [...(snapshot?.members || []), ...candidates.filter((action, index) => candidates.findIndex(other => targetKey(action.target) === targetKey(other.target)) === index).map(action => ({target: action.target, display_name: action.target_name, role: '尚未加入', source: 'direct' as const}))]
  return <><Dialog open={open} onOpenChange={value => {if (!value) onClose()}}><DialogContent className="flex max-h-[88dvh] flex-col overflow-hidden sm:max-w-xl" onCloseAutoFocus={event => {event.preventDefault(); (trigger?.isConnected && trigger.getClientRects().length ? trigger : document.getElementById('audit-main'))?.focus()}}>
    <DialogHeader><DialogTitle>文件夹权限</DialogTitle><DialogDescription>{customerName} · 只显示服务允许的修改。交互预览不会修改飞书权限。</DialogDescription></DialogHeader>
    <div className="min-h-0 space-y-4 overflow-y-auto"><p className="text-xs text-muted-foreground">权限修订：{snapshot?.permission_revision || '未取得'}。文件夹权限与模板内容版本分开。</p>
      <form className="flex flex-wrap gap-2" onSubmit={event => {event.preventDefault(); void readPermissions(query.trim())}}><Label className="sr-only" htmlFor={`permission-search-${folder.folder_id}`}>搜索协作者</Label><Input className="min-w-0 flex-1" id={`permission-search-${folder.folder_id}`} value={query} placeholder="搜索协作者" onChange={event => setQuery(event.target.value)}/><Button variant="outline" disabled={loading}>搜索</Button></form>
      {loading && <p role="status">正在读取文件夹权限…</p>}{error && <Alert variant="destructive"><AlertTitle>权限待核对</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
      {snapshot?.manage_access.state !== 'allowed' && snapshot && <Alert><AlertTitle>当前不能修改权限</AlertTitle><AlertDescription>{snapshot.manage_access.reason || '服务未确认管理权限。'}</AlertDescription></Alert>}
      <ItemGroup aria-label="文件夹协作者">{rows.map(member => {const actions = snapshot!.actions.filter(action => targetKey(action.target) === targetKey(member.target)); return <Item role="listitem" aria-label={`协作者 ${member.display_name}`} size="sm" key={targetKey(member.target)} className="flex-wrap"><ItemContent><p className="font-medium">{member.display_name}</p><p className="text-sm text-muted-foreground">{member.role}</p>{member.source !== 'direct' && <Badge variant="outline">{member.source === 'owner' ? '所有者' : '继承权限'}</Badge>}</ItemContent>{actions.length > 0 && <Select value="" disabled={!snapshotUsable || loading || locked || snapshot?.manage_access.state !== 'allowed'} onValueChange={id => {const action = actions.find(row => row.id === id); if (action && snapshot) setProposal({revision: snapshot.permission_revision, action: structuredClone(action)})}}><SelectTrigger aria-label={`修改 ${member.display_name} 的权限`} className="w-auto max-w-full"><SelectValue placeholder="选择修改"/></SelectTrigger><SelectContent>{actions.map(action => <SelectItem key={action.id} value={action.id}>{action.label}</SelectItem>)}</SelectContent></Select>}</Item>})}</ItemGroup>
      {snapshot && !rows.length && !loading && <p>没有符合条件的协作者。未把搜索未命中解释为没有权限。</p>}
      {job && <div role="status" aria-label="权限变更结果" className="space-y-2 border-t pt-3"><p className="font-medium">{job.state === 'applied' ? '修改已确认。请重新读取当前权限。' : job.state === 'conflict' ? '权限已变化，请重新读取后核对。未自动应用到新修订。' : job.state === 'rejected' ? '服务已拒绝本次修改，未应用。' : '修改结果待确认；只读取原请求，不重复修改。'}</p><p>{job.request.action.target_name}：{job.request.action.before || '未加入'} → {job.request.action.after || '移除权限'}</p>{job.reason && <p className="text-xs text-muted-foreground">{job.reason}</p>}{permissionUnresolved(job) && <Button variant="outline" disabled={busy} onClick={() => void observe(structuredClone(job), () => adapter.readChange(structuredClone(job.request)))}>读取原修改回执</Button>}</div>}
    </div><DialogFooter className="shrink-0 flex-row flex-wrap"><Button variant="outline" disabled={loading} onClick={() => void readPermissions(query.trim())}>重新读取当前权限</Button><Button variant="outline" onClick={onClose}>关闭</Button></DialogFooter>
  </DialogContent></Dialog><Dialog open={open && Boolean(proposal)} onOpenChange={value => {if (!value) setProposal(null)}}><DialogContent><DialogHeader><DialogTitle>确认权限修改</DialogTitle><DialogDescription>只修改这个文件夹的一名协作者。发送链接不会代替授权；撤销权限不能收回已发送的副本。</DialogDescription></DialogHeader><p>{customerName} · {proposal?.action.target_name}</p><p>{proposal?.action.before || '未加入'} → {proposal?.action.after || '移除权限'}</p><DialogFooter><Button variant="outline" onClick={() => setProposal(null)}>取消</Button><Button disabled={!snapshotUsable || locked || loading} onClick={apply}>确认这项修改</Button></DialogFooter></DialogContent></Dialog></>
}
