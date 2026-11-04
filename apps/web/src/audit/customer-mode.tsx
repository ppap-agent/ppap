import {useEffect, useState} from 'react'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Label} from '../components/ui/label'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'

type CustomerDefault = components['schemas']['AuditCustomerDefault']
export type CustomerDefaultAdapter = {
  read: () => Promise<CustomerDefault>
  save: (customer: CustomerDefault['customer_preference'], revision: number) => Promise<CustomerDefault>
}
const presetCustomers = [{value: 'UAES', label: 'UAES（联电）'}, {value: '理想', label: '理想'}, {value: '蔚来', label: '蔚来'}]

/** Sample photos are only checked by the UAES U13 preset; every other customer receives documents only. */
const documentTypes = {accept: '.xlsx,.xls,.xlsm,.doc,.docx,.pdf', hint: 'Excel、Word、PDF · 单份最大 200 MB'}
const photoTypes = {accept: `${documentTypes.accept},.png,.jpg,.jpeg,.webp`, hint: 'Excel、Word、PDF；样品照片可用 PNG、JPEG、WebP · 单份最大 200 MB'}
export const acceptedUploads = (customer: string) => customer === 'UAES' ? photoTypes : documentTypes

export function CustomerMode({value, ready, options, adapter, onChange, onInitialDefault, onRefresh}: {value: string; ready: boolean; options: {value: string; label: string}[]; adapter: CustomerDefaultAdapter; onChange: (value: string) => void; onInitialDefault: (value: string) => void; onRefresh?: () => void}) {
  const [current, setCurrent] = useState<CustomerDefault | null>(null)
  const [confirming, setConfirming] = useState(false), [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(''), [readRevision, setReadRevision] = useState(0)
  useEffect(() => {
    let active = true
    void adapter.read().then(result => {
      if (active) {setCurrent(result); onInitialDefault(result.customer_preference || 'auto'); setMessage('')}
    }).catch(() => {if (active) setMessage('企业默认读取失败。可选择本次客户，或重新读取。')})
    return () => {active = false}
    // The intake owns whether a default may initialize this new task. A changed
    // task selection or restored task is never replaced by a later read.
  }, [adapter, readRevision])
  const canSave = value === 'auto' || presetCustomers.some(option => option.value === value)
  const choices = [...presetCustomers, ...options.filter(option => !presetCustomers.some(preset => preset.value.toLowerCase() === option.value.toLowerCase()))]
  async function save() {
    if (!current || saving || !canSave) return
    setSaving(true); setMessage('')
    try {
      const result = await adapter.save(value === 'auto' ? null : value as CustomerDefault['customer_preference'], current.revision)
      setCurrent(result); setConfirming(false); setMessage('已保存企业默认，仅影响以后新建的任务。')
    } catch {
      // A transport failure can hide a committed write. Read the value; do not
      // retry the write or turn a matching read into proof of our operation.
      setConfirming(false)
      try {setCurrent(await adapter.read()); setMessage('保存结果待核对。已读取当前企业默认，请核对后再决定是否修改。')}
      catch {setCurrent(null); setMessage('保存结果待核对，请重新读取企业默认。')}
    } finally {setSaving(false)}
  }
  return <div className="grid gap-2">
    <Label htmlFor="audit-customer">审核客户</Label>
    <div className="flex flex-wrap items-center gap-2">
      <Select value={ready ? value : ''} onValueChange={onChange} onOpenChange={open => {if (open) onRefresh?.()}}><SelectTrigger id="audit-customer" className="w-56"><SelectValue placeholder="请选择本次审核客户"/></SelectTrigger><SelectContent><SelectItem value="auto">自动识别</SelectItem>{choices.map(option => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent></Select>
      <Button variant="ghost" disabled={!current || saving || !canSave} onClick={() => setConfirming(true)}>设为企业默认</Button>
    </div>
    <p className="text-sm text-muted-foreground">本次切换只影响当前任务。{current && `企业默认：${presetCustomers.find(option => option.value === current.customer_preference)?.label || '自动识别'}。`}</p>
    {!current && !message && <p role="status" className="text-sm">正在读取企业默认，或请选择本次审核客户。</p>}
    {message && <p role="status" className="text-sm">{message}<Button variant="link" onClick={() => setReadRevision(revision => revision + 1)}>重新读取企业默认</Button></p>}
    <Dialog open={confirming} onOpenChange={open => {if (!saving) setConfirming(open)}}><DialogContent><DialogHeader><DialogTitle>更改企业默认客户？</DialogTitle><DialogDescription>设为{choices.find(option => option.value === value)?.label || '自动识别'}，仅影响本企业以后新建的任务。已有任务和历史结果保持原客户模式。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={saving} onClick={() => setConfirming(false)}>取消</Button><Button disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : '确认更改'}</Button></DialogFooter></DialogContent></Dialog>
  </div>
}
