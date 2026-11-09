import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import {FileName} from '../journey/file-name'
import {showToast} from '../components/toast'
import {sameSnapshot} from './result-projection'
import {displayFields, validDisplayInfo, type DisplayInfo, type DisplayReceipt, type DisplayReference, type DisplayRequest, type DisplayValues} from './display-info-contract'

type Task = components['schemas']['AuditTask']
type File = components['schemas']['AuditTaskFile']
export type AuditDisplayAdapter = {save: (request: DisplayRequest) => Promise<DisplayReceipt>; read: (request: DisplayRequest) => Promise<DisplayReceipt>}
type Draft = {reference: DisplayReference; name: string; base: DisplayInfo; values: DisplayValues; request?: DisplayRequest; phase: 'editing' | 'sending' | 'reading' | 'unknown' | 'rejected' | 'conflict'; message: string; conflict?: DisplayInfo; observation: number}
const keyOf = (reference: DisplayReference) => JSON.stringify([reference.task_id, reference.round_id, reference.file_version_id])
const unresolved = (draft: Draft) => ['sending', 'reading', 'unknown'].includes(draft.phase)

/** Requests outlive file/task chrome. Confirmed values remain in the Task projection. */
export function useDisplayEditor(adapter: AuditDisplayAdapter | undefined, tasks: Task[], scope: string, onInfo: (reference: DisplayReference, info: DisplayInfo) => void) {
  const drafts = useRef(new Map<string, Draft>())
  const trigger = useRef<HTMLElement | null>(null)
  const latest = useRef(tasks); latest.current = tasks
  const [selected, setSelected] = useState<string | null>(null)
  const [, redraw] = useState(0)
  const refresh = () => redraw(value => value + 1)
  function currentFile(reference: DisplayReference) {return latest.current.find(task => task.id === reference.task_id && task.round_id === reference.round_id)?.files.find(file => file.file.id === reference.file_version_id)}
  function close(discard = false) {
    if (selected) {
      const draft = drafts.current.get(selected)
      if (draft) {
        draft.observation += 1
        if (unresolved(draft)) {draft.phase = 'unknown'; draft.message = '保存结果尚未确认。请核对原保存请求，不要再次保存。'}
        else if (discard) drafts.current.delete(selected)
      }
    }
    setSelected(null)
  }
  const closeCurrent = useRef(close); closeCurrent.current = close
  useEffect(() => {closeCurrent.current()}, [scope])
  useEffect(() => () => {for (const draft of drafts.current.values()) draft.observation += 1}, [])
  function open(reference: DisplayReference, file: File) {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const key = keyOf(reference)
    if (!drafts.current.has(key)) {
      if (!validDisplayInfo(file.display_info)) return
      drafts.current.set(key, {reference, name: file.file.original_filename, base: structuredClone(file.display_info), values: structuredClone(file.display_info.values), phase: 'editing', message: '', observation: 0})
    }
    setSelected(key)
  }
  const draft = selected ? drafts.current.get(selected) : undefined
  const access = draft ? currentFile(draft.reference)?.display_edit_access : undefined
  async function observe(current: Draft, read: boolean) {
    if (!adapter || !current.request) return
    const observation = ++current.observation, request = structuredClone(current.request)
    current.phase = read ? 'reading' : 'sending'; current.message = ''; refresh()
    try {
      const receipt = await (read ? adapter.read(structuredClone(request)) : adapter.save(structuredClone(request)))
      if (observation !== current.observation) return
      if (!sameSnapshot(receipt.request, request)) throw new Error('display_receipt_mismatch')
      if (receipt.state === 'saved') {
        if (!validDisplayInfo(receipt.info) || receipt.info.revision !== request.expected_revision + 1 || !sameSnapshot(receipt.info.values, request.values)) throw new Error('display_saved_mismatch')
        onInfo(request.reference, receipt.info)
        showToast('显示信息已更正')
        drafts.current.delete(keyOf(request.reference))
        setSelected(key => key === keyOf(request.reference) ? null : key)
      } else if (receipt.state === 'conflict') {
        if (!validDisplayInfo(receipt.info) || receipt.info.revision <= request.expected_revision) throw new Error('display_conflict_mismatch')
        onInfo(request.reference, receipt.info)
        current.phase = 'conflict'; current.conflict = receipt.info; current.message = receipt.reason || '显示信息已由其他操作更新。你的输入仍保留，请核对最新值。'
      } else {
        if (receipt.info !== null || !['pending', 'unknown', 'rejected'].includes(receipt.state)) throw new Error('display_receipt_invalid')
        current.phase = receipt.state === 'rejected' ? 'rejected' : 'unknown'
        current.message = receipt.reason || (current.phase === 'rejected' ? '保存未执行。输入仍保留，可以修改后再保存。' : '保存结果尚未确认。请核对原保存请求，不要再次保存。')
      }
    } catch {
      if (observation !== current.observation) return
      current.phase = 'unknown'; current.message = '保存结果尚未确认。请核对原保存请求，不要再次保存。'
    }
    refresh()
  }
  function save() {
    if (!draft || unresolved(draft) || draft.phase === 'conflict' || !adapter) return
    const file = currentFile(draft.reference)
    if (file?.display_edit_access?.state !== 'allowed' || !validDisplayInfo(file.display_info)) return
    if (!sameSnapshot(file.display_info, draft.base)) {
      draft.phase = 'conflict'; draft.conflict = file.display_info; draft.message = '显示信息已更新。输入仍保留，请先核对最新值。'; refresh(); return
    }
    draft.request = {request_id: crypto.randomUUID(), reference: draft.reference, expected_revision: draft.base.revision, values: Object.fromEntries(displayFields.map(([field]) => [field, draft.values[field]?.trim() || null])) as DisplayValues}
    void observe(draft, false)
  }
  const locked = Boolean(draft && (unresolved(draft) || draft.phase === 'conflict'))
  // Focus can arrive before Radix registers a nested layer; route Escape to this editor.
  return {open, dialog: <Dialog open={Boolean(draft)} onOpenChange={value => {if (!value) close(true)}}><DialogContent onEscapeKeyDown={event => event.preventDefault()} onKeyDown={event => {if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); close(true)}}} className="max-h-[90dvh] overflow-y-auto" onCloseAutoFocus={event => {event.preventDefault(); if (trigger.current?.isConnected) trigger.current.focus()}}><DialogHeader><DialogTitle>更正显示信息</DialogTitle><DialogDescription>仅更正显示信息，原件、审核结果和报告不变。</DialogDescription></DialogHeader>{draft && <form className="space-y-4" onSubmit={event => {event.preventDefault(); save()}}><FileName name={draft.name}/>{displayFields.map(([field, label]) => <div className="grid gap-2" key={field}><Label htmlFor={`display-${field}`}>{label}</Label><Input id={`display-${field}`} value={draft.values[field] ?? ''} disabled={locked || access?.state !== 'allowed'} onChange={event => {draft.values = {...draft.values, [field]: event.target.value}; refresh()}}/></div>)}
      {access?.state !== 'allowed' && <p role="status" className="text-sm text-muted-foreground">{access?.reason || '编辑许可尚未确认，请重新读取任务。'} 输入及原请求仍保留。</p>}
      {draft.message && <Alert><AlertTitle>{draft.phase === 'conflict' ? '请核对最新显示信息' : draft.phase === 'rejected' ? '保存未执行' : '保存结果待确认'}</AlertTitle><AlertDescription>{draft.message}</AlertDescription></Alert>}
      {draft.phase === 'conflict' && draft.conflict && <section aria-label="最新显示信息" className="space-y-3 text-sm"><dl>{displayFields.map(([field, label]) => <div key={field} className="grid grid-cols-[5rem_minmax(0,1fr)] gap-2"><dt className="text-muted-foreground">{label}</dt><dd className="break-words">{draft.conflict!.values[field] || '—'}</dd></div>)}</dl><Button type="button" variant="outline" disabled={access?.state !== 'allowed'} onClick={() => {draft.base = structuredClone(draft.conflict!); draft.phase = 'editing'; draft.request = undefined; draft.conflict = undefined; draft.message = ''; refresh()}}>以最新版本继续更正</Button></section>}
      <DialogFooter><Button type="button" variant="outline" onClick={() => close(true)}>{unresolved(draft) ? '关闭，稍后核对' : '取消'}</Button>{unresolved(draft) ? <Button type="button" disabled={draft.phase === 'sending' || draft.phase === 'reading'} onClick={() => void observe(draft, true)}>{draft.phase === 'sending' ? '正在保存…' : draft.phase === 'reading' ? '正在核对…' : '核对保存结果'}</Button> : <Button type="submit" disabled={locked || access?.state !== 'allowed' || !adapter}>保存显示信息</Button>}</DialogFooter>
    </form>}</DialogContent></Dialog>}
}
