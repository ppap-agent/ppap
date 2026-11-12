import {useEffect, useState, type ReactNode} from 'react'
import {Loader2} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Label} from '../components/ui/label'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import type {Coverage} from './customer-coverage'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'

type CustomerDefault = components['schemas']['AuditCustomerDefault']
export type CustomerDefaultAdapter = {
  read: () => Promise<CustomerDefault>
  save: (customer: CustomerDefault['customer_preference'], revision: number) => Promise<CustomerDefault>
}
const presetCustomers = [{value: 'UAES', label: 'UAES（联电）'}, {value: '理想', label: '理想'}, {value: '蔚来', label: '蔚来'}]

/** Sample photos are only checked by the UAES U13 preset; every other customer receives documents only. */
const documentTypes = {accept: '.xlsx,.xls,.xlsm,.doc,.docx,.pdf', hint: '可多选 Excel / PDF / Word；每个文件 ≤200 MB'}
const photoTypes = {accept: `${documentTypes.accept},.png,.jpg,.jpeg,.webp`, hint: '可多选 Excel / PDF / Word；样品照片可用 PNG / JPEG / WebP；每个文件 ≤200 MB'}
export const acceptedUploads = (customer: string) => customer === 'UAES' ? photoTypes : documentTypes

const avatarTones = ['bg-primary', 'bg-success', 'bg-warning', 'bg-destructive', 'bg-info']
/** The customer's first character on a coloured tile, as in the customer picker. */
export function CustomerAvatar({name, className = ''}: {name: string; className?: string}) {
  const tone = name === '自动识别' ? 'bg-muted-foreground' : avatarTones[[...name].reduce((sum, char) => sum + (char.codePointAt(0) ?? 0), 0) % avatarTones.length]
  return <span aria-hidden="true" data-avatar="" className={`flex size-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold text-white ${tone} ${className}`}>{[...name.trim()][0] ?? '?'}</span>
}
/** One customer in the picker: avatar, name and how many standard file types its rules cover. The trigger shows the name only. */
export function CustomerItem({value, label, coverage}: {value: string; label: string; coverage?: Coverage | null}) {
  return <SelectItem value={value} aria-label={label}><span className="flex min-w-0 items-center gap-3"><CustomerAvatar name={label}/><span className="grid min-w-0 gap-0.5 text-left"><span className="truncate">{label}</span>{value === 'auto' ? <span data-coverage="" className="text-xs text-muted-foreground">按文件内容匹配客户规则</span> : coverage && <span data-coverage="" className="text-xs text-muted-foreground tabular-nums">已支持 {coverage.supported} / {coverage.total} 类 PPAP 文件</span>}</span></span></SelectItem>
}
export const CustomerListNote = ({count}: {count: number}) => <p className="px-2 pt-1.5 pb-1 text-xs text-muted-foreground">已开通 {count} 家客户的模板与规则</p>

export function CustomerMode({value, ready, options, adapter, onChange, onInitialDefault, onRefresh, coverage, hint}: {value: string; ready: boolean; options: {value: string; label: string}[]; adapter: CustomerDefaultAdapter; onChange: (value: string) => void; onInitialDefault: (value: string) => void; onRefresh?: () => void; coverage?: (customer: string) => Coverage | null; hint?: ReactNode}) {
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
      try {setCurrent(await adapter.read()); setMessage('保存结果待确认。已读取当前企业默认，请核对后再决定是否修改。')}
      catch {setCurrent(null); setMessage('保存结果待确认，请重新读取企业默认。')}
    } finally {setSaving(false)}
  }
  return <div className="grid gap-2">
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1"><Label htmlFor="audit-customer">审核客户</Label>{hint}</div>
    <div className="flex flex-wrap items-center gap-2">
      <Select value={ready ? value : ''} onValueChange={onChange} onOpenChange={open => {if (open) onRefresh?.()}}><SelectTrigger id="audit-customer" className="h-10 w-80 max-w-full [&_[data-coverage]]:hidden"><SelectValue placeholder="请选择本次审核客户"/></SelectTrigger><SelectContent><CustomerListNote count={choices.length}/><CustomerItem value="auto" label="自动识别"/>{choices.map(option => <CustomerItem key={option.value} value={option.value} label={option.label} coverage={coverage?.(option.value)}/>)}</SelectContent></Select>
      <Button variant="ghost" disabled={!current || saving || !canSave} onClick={() => setConfirming(true)}>设为企业默认</Button>
      {/* The enterprise default sits beside its action in one quiet line; choosing here changes only this task. */}
      {current ? <span className="text-sm text-muted-foreground">企业默认：{presetCustomers.find(option => option.value === current.customer_preference)?.label || '自动识别'}</span> : !message && <span role="status" className="flex items-center gap-1.5 text-sm text-muted-foreground"><Loader2 aria-hidden="true" className="size-3.5 animate-spin motion-reduce:animate-none"/>正在读取企业默认…</span>}
    </div>
    {message && <p role="status" className="text-sm">{message}<Button variant="link" onClick={() => setReadRevision(revision => revision + 1)}>重新读取企业默认</Button></p>}
    <Dialog open={confirming} onOpenChange={open => {if (!saving) setConfirming(open)}}><DialogContent><DialogHeader><DialogTitle>更改企业默认客户？</DialogTitle><DialogDescription>设为{choices.find(option => option.value === value)?.label || '自动识别'}，仅影响本企业以后新建的任务。已有任务和历史结果保持原客户模式。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={saving} onClick={() => setConfirming(false)}>取消</Button><Button disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : '确认更改'}</Button></DialogFooter></DialogContent></Dialog>
  </div>
}
