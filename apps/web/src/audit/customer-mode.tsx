import {useEffect, useState} from 'react'
import type {components} from '../generated/openapi'
import {ReadSkeleton} from '../components/read-skeleton'
import {Button} from '../components/ui/button'
import {Label} from '../components/ui/label'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import type {Coverage} from './rule-templates'
import {CustomerAvatar} from './customer-avatar'
import {photoScope} from './customer-register'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'

type CustomerDefault = components['schemas']['AuditCustomerDefault']
export type CustomerDefaultAdapter = {
  read: () => Promise<CustomerDefault>
  save: (customer: NonNullable<components['schemas']['AuditCustomerDefault']['customer_preference']>, revision: number) => Promise<CustomerDefault>
}
/** Sample photos are only checked by the photo customer's U13 preset; every other customer receives documents only. */
const documentTypes = {accept: '.xlsx,.xls,.xlsm,.doc,.docx,.pdf', hint: '可多选 Excel / PDF / Word；每个文件 ≤200 MB'}
const photoTypes = {accept: `${documentTypes.accept},.png,.jpg,.jpeg,.webp`, hint: '可多选 Excel / PDF / Word；样品照片可用 PNG / JPEG / WebP；每个文件 ≤200 MB'}
export const acceptedUploads = (customer: string) => customer === photoScope.customer_preference ? photoTypes : documentTypes

/** One customer in the picker: avatar and name (its accessible name, shown in the trigger), plus how many standard file types its rules cover as its description. */
export function CustomerItem({value, label, coverage, typeSetMissing = false}: {value: string; label: string; coverage?: Coverage | null; typeSetMissing?: boolean}) {
  const description = typeSetMissing ? '文件类型范围未配置' : coverage ? `已支持 ${coverage.covered} / ${coverage.total} 类 PPAP 文件` : undefined
  return <SelectItem value={value} description={description && <span className="-mt-1 block pl-10 tabular-nums">{description}</span>}><span className="flex min-w-0 items-center gap-3"><CustomerAvatar name={label} className="size-7 text-xs"/><span className="truncate">{label}</span></span></SelectItem>
}
export const CustomerListNote = ({count}: {count: number}) => <p className="px-2 pt-1.5 pb-1 text-xs text-muted-foreground">已开通 {count} 家客户的模板与规则</p>

/**
 * Every task names the customer whose rules it is audited by; nothing is read from the files to choose one.
 * The enterprise default only pre-fills the choice. Without a default (or with one no longer registered) the picker
 * stays empty and recognition does not start until a customer is chosen.
 */
export function CustomerMode({value, ready, options, adapter, onChange, onInitialDefault, onRefresh, coverage, typeSetMissing}: {value: string; ready: boolean; options: {value: string; label: string}[]; adapter: CustomerDefaultAdapter; onChange: (value: string) => void; onInitialDefault: (value: string) => void; onRefresh?: () => void; coverage?: (customer: string) => Coverage | null; typeSetMissing?: (customer: string) => boolean}) {
  const [current, setCurrent] = useState<CustomerDefault | null>(null)
  const [confirming, setConfirming] = useState(false), [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(''), [readRevision, setReadRevision] = useState(0)
  useEffect(() => {
    let active = true
    void adapter.read().then(result => {
      if (active) {setCurrent(result); onInitialDefault(result.customer_preference ?? ''); setMessage('')}
    }).catch(() => {if (active) setMessage('企业默认读取失败。可选择本次客户，或重新读取。')})
    return () => {active = false}
    // The intake owns whether a default may initialize this new task. A changed
    // task selection or restored task is never replaced by a later read.
  }, [adapter, readRevision])
  // The registered customers, each shown by its name and submitted by its key.
  const choices = options
  const chosen = choices.find(option => option.value === value)
  const defaultLabel = current?.customer_preference ? choices.find(option => option.value === current.customer_preference)?.label : undefined
  async function save() {
    if (!current || saving || !chosen) return
    setSaving(true); setMessage('')
    try {
      const result = await adapter.save(chosen.value as NonNullable<components['schemas']['AuditCustomerDefault']['customer_preference']>, current.revision)
      setCurrent(result); setConfirming(false); setMessage('已保存企业默认，仅影响以后新建的任务。')
    } catch {
      // A transport failure can hide a committed write. Read the value; do not
      // retry the write or turn a matching read into proof of our operation.
      setConfirming(false)
      try {setCurrent(await adapter.read()); setMessage('保存结果待确认。已读取当前企业默认，请核对后再决定是否修改。')}
      catch {setCurrent(null); setMessage('保存结果待确认，请重新读取企业默认。')}
    } finally {setSaving(false)}
  }
  return <div className="grid grid-cols-1 gap-2">
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1"><Label htmlFor="audit-customer">审核客户</Label></div>
    <div className="flex flex-wrap items-center gap-2">
      <Select value={ready && chosen ? value : ''} onValueChange={onChange} onOpenChange={open => {if (open) onRefresh?.()}}><SelectTrigger id="audit-customer" className="h-10 w-80 max-w-full"><SelectValue placeholder="请选择审核客户"/></SelectTrigger><SelectContent><CustomerListNote count={choices.length}/>{choices.map(option => <CustomerItem key={option.value} value={option.value} label={option.label} coverage={coverage?.(option.value)} typeSetMissing={typeSetMissing?.(option.value)}/>)}</SelectContent></Select>
      <Button variant="ghost" size="sm" disabled={!current || saving || !chosen} onClick={() => setConfirming(true)}>设为企业默认</Button>
      {/* The saved default is a second fact only when this task is using something else. */}
      {current ? current.customer_preference !== value && defaultLabel && <span className="text-sm text-muted-foreground">企业默认：{defaultLabel}</span> : !message && <ReadSkeleton label="正在读取企业默认…" kind="inline"/>}
    </div>
    {message && <p role="status" className="text-sm">{message}<Button variant="link" size="sm" onClick={() => setReadRevision(revision => revision + 1)}>重新读取企业默认</Button></p>}
    <Dialog open={confirming} onOpenChange={open => {if (!saving) setConfirming(open)}}><DialogContent><DialogHeader><DialogTitle>更改企业默认客户？</DialogTitle><DialogDescription>设为{chosen?.label}，仅影响本企业以后新建的任务。已有任务和历史结果保持原客户。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" disabled={saving} onClick={() => setConfirming(false)}>取消</Button><Button disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : '确认更改'}</Button></DialogFooter></DialogContent></Dialog>
  </div>
}
