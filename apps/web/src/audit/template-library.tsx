import {useEffect, useId, useRef, useState} from 'react'
import {Download, Plus, Send, UploadCloud, X} from 'lucide-react'
import type {components} from '../generated/openapi'
import {ReadSkeleton} from '../components/read-skeleton'
import {showToast} from '../components/toast'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Item, ItemActions, ItemContent, ItemDescription, ItemGroup} from '../components/ui/item'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {cn} from '../lib/utils'
import {FeishuFolderPicker, type DrivePickerSource} from './feishu-picker'
import {auditAddressChanged} from './routes'

export type TemplateLibrary = components['schemas']['TemplateLibrary']
export type LibraryCustomer = components['schemas']['LibraryCustomer']
export type LibraryTemplate = components['schemas']['LibraryTemplate']
export type TemplateSaveResponse = components['schemas']['TemplateSaveResponse']
export type TemplateSaveResult = components['schemas']['TemplateSaveResult']
export type TemplateSubmissionReceipt = components['schemas']['TemplateSubmissionReceipt']

/** Transport only. The catalog, files and submission records stay server facts. */
export type TemplateLibraryAdapter = {
  read: () => Promise<TemplateLibrary>
  download: (customerId: string, templateId: string) => Promise<Blob>
  downloadAll: (customerId: string) => Promise<Blob>
  /** Present only while Feishu login is active (`personal_drive_upload`). */
  feishu?: {drive: DrivePickerSource; save: (customerId: string, folderToken: string, templateIds?: string[]) => Promise<TemplateSaveResponse>}
  /** Sends the files to the platform team; one `requestId` is one submission, safe to repeat. */
  submit: (input: {requestId: string; customer: string | null; files: File[]}) => Promise<TemplateSubmissionReceipt>
}

const avatarTones = ['bg-primary', 'bg-success', 'bg-warning', 'bg-destructive'] as const
const saveStates: Record<TemplateSaveResult['state'], string> = {saved: '已保存', exists: '目录中已有同名文件，未覆盖，也未重复保存', failed: '未保存', unknown: '待确认'}
export const submissionAccept = '.zip,.rar,.7z,.xlsx,.xls,.xlsm,.doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff'
const submissionExtensions = new Set(submissionAccept.split(','))
export const acceptedSubmission = (name: string) => submissionExtensions.has(name.match(/\.[^.]+$/)?.[0]?.toLowerCase() || '')
export const documentTypeCount = (customer: LibraryCustomer) => new Set(customer.templates.map(template => template.document_type)).size

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function Avatar({name, index, className}: {name: string; index: number; className?: string}) {
  return <span aria-hidden="true" className={cn('flex size-10 shrink-0 items-center justify-center rounded-lg text-base font-semibold text-white', avatarTones[index % avatarTones.length], className)}>{name.slice(0, 1)}</span>
}

export function TemplateLibraryPage({adapter, headerActions, onRules}: {adapter: TemplateLibraryAdapter; headerActions?: HTMLElement | null; onRules?: () => void}) {
  const [library, setLibrary] = useState<TemplateLibrary | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState(''), [reload, setReload] = useState(0)
  const [selected, setSelected] = useState(''), [busy, setBusy] = useState(''), [actionError, setActionError] = useState('')
  const [picking, setPicking] = useState(false), [saving, setSaving] = useState(false), [saved, setSaved] = useState<{customer: string; folder: string; results: TemplateSaveResult[]} | null>(null)
  // A link from an unmatched file may ask for the submission straight away.
  const [submitting, setSubmitting] = useState(() => new URL(location.href).searchParams.get('templateRequest') === '1')
  useEffect(() => {
    const requested = () => {if (new URL(location.href).searchParams.get('templateRequest') === '1') setSubmitting(true)}
    window.addEventListener(auditAddressChanged, requested)
    return () => window.removeEventListener(auditAddressChanged, requested)
  }, [])
  function closeSubmission() {
    setSubmitting(false)
    const url = new URL(location.href)
    if (url.searchParams.has('templateRequest')) {url.searchParams.delete('templateRequest'); history.replaceState(history.state, '', url)}
  }
  useEffect(() => {
    let current = true
    setLoading(true); setError('')
    adapter.read().then(next => {if (current) {setLibrary(next); setLoading(false)}}).catch(() => {if (current) {setError('模板库暂时无法读取，请重新读取。'); setLoading(false)}})
    return () => {current = false}
  }, [adapter, reload])
  const customers = library?.customers ?? []
  const customer = customers.find(row => row.id === selected) ?? customers[0]
  const customerIndex = customer ? customers.indexOf(customer) : 0
  async function run(key: string, action: () => Promise<void>, failure: string) {
    if (busy) return
    setBusy(key); setActionError('')
    try {await action()} catch {setActionError(failure)} finally {setBusy('')}
  }
  async function saveTo(folder: {token: string; name: string}) {
    if (!customer || !adapter.feishu || saving) return
    setSaving(true); setActionError('')
    try {
      const response = await adapter.feishu.save(customer.id, folder.token)
      setSaved({customer: customer.id, folder: folder.name, results: response.results}); setPicking(false)
    } catch {
      setActionError('保存结果尚未确认。请先在飞书目录中查看；再次保存时会先核对同名文件，不会重复保存。'); setPicking(false)
    } finally {setSaving(false)}
  }
  const title = headerActions ? <h1 className="sr-only">模板库</h1> : <header><h1 className="text-2xl font-semibold">模板库</h1></header>
  if (loading && !library) return <section aria-label="模板库" className="space-y-4">{title}<ReadSkeleton label="正在读取模板库…" kind="table"/></section>
  if (error && !library) return <section aria-label="模板库" className="space-y-4">{title}<Alert variant="destructive"><AlertTitle>模板库未能读取</AlertTitle><AlertDescription><p>{error}</p><Button variant="outline" size="sm" onClick={() => setReload(value => value + 1)}>重新读取模板库</Button></AlertDescription></Alert></section>
  const shownSave = saved && customer && saved.customer === customer.id ? saved : null
  const unsettled = shownSave?.results.filter(row => row.state !== 'saved') ?? [], open = unsettled.filter(row => row.state === 'failed' || row.state === 'unknown').length
  return <section aria-label="模板库" className="space-y-4">
    {title}
    <div className="grid grid-cols-[minmax(0,16rem)_minmax(0,1fr)] gap-4">
      <nav aria-label="客户" className="flex min-w-0 flex-col gap-2">
        {customers.map((row, index) => <button key={row.id} type="button" aria-pressed={row.id === customer?.id} onClick={() => {setSelected(row.id); setActionError('')}} className="flex min-h-11 min-w-0 items-center gap-3 rounded-lg border bg-card p-3 text-left outline-none transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 aria-pressed:border-primary aria-pressed:bg-primary/5">
          <Avatar name={row.name} index={index}/>
          <span className="min-w-0"><span className="block truncate font-medium">{row.name}</span><span className="block text-sm text-muted-foreground">已支持 {documentTypeCount(row)} 类</span></span>
        </button>)}
        <Button variant="link" className="justify-start" onClick={() => setSubmitting(true)}><Plus aria-hidden="true"/>更多模板，请补充</Button>
        {/* Waiting for the platform is not the only way: a customer's own requirement becomes a rule the same day. */}
        {onRules && <div className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm"><p className="font-medium">不想等平台补充？</p><p className="text-muted-foreground">上传客户的要求文件，逐条确认后生成检查程序，验证通过即可用于审核。</p><Button variant="soft" size="sm" onClick={onRules}>上传要求，生成自有规则</Button></div>}
      </nav>
      {customer ? <div className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3"><Avatar name={customer.name} index={customerIndex}/><div className="min-w-0"><h2 className="truncate text-lg font-semibold">{customer.name}</h2><p className="text-sm text-muted-foreground">已提供 {customer.templates.length} 份模板</p></div></div>
          <div className="flex flex-wrap gap-2">
            {adapter.feishu && <Button disabled={Boolean(busy) || saving || !customer.templates.length} onClick={() => setPicking(true)}>保存到我的飞书</Button>}
            <Button variant="outline" disabled={Boolean(busy) || !customer.templates.length} onClick={() => void run('zip', async () => saveBlob(await adapter.downloadAll(customer.id), `${customer.name}模板.zip`), '模板 ZIP 暂时无法下载，请重试。')}><Download aria-hidden="true"/>{busy === 'zip' ? '正在打包…' : '下载全部（ZIP）'}</Button>
          </div>
        </div>
        {actionError && <Alert variant="destructive"><AlertTitle>操作未完成</AlertTitle><AlertDescription>{actionError}</AlertDescription></Alert>}
        {shownSave && <Alert variant={open ? 'destructive' : 'default'}><AlertTitle>{open ? `已保存 ${shownSave.results.filter(row => row.state === 'saved').length} 份，${open} 份未完成` : '模板已在所选飞书目录中'}</AlertTitle><AlertDescription><p>保存位置：{shownSave.folder}</p>{unsettled.length > 0 && <ul className="space-y-1">{unsettled.map(row => <li key={row.template_id}>{row.filename}：{saveStates[row.state]}{row.state !== 'exists' && row.reason ? `。${row.reason}` : ''}</li>)}</ul>}</AlertDescription></Alert>}
        <div className="overflow-hidden rounded-xl border bg-card">
          {customer.templates.length ? <Table>
            <TableHeader variant="muted"><TableRow><TableHead>模板名称</TableHead><TableHead>文件类型</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
            <TableBody>{customer.templates.map(template => <TableRow key={template.id}>
              <TableCell className="whitespace-normal"><FileName name={template.filename} colored/></TableCell>
              <TableCell><span className="text-muted-foreground">{template.document_type}</span></TableCell>
              <TableCell className="text-right"><Button variant="ghost" size="sm" aria-label={`下载 ${template.filename}`} disabled={Boolean(busy)} onClick={() => void run(template.id, async () => saveBlob(await adapter.download(customer.id, template.id), template.filename), `${template.filename} 暂时无法下载，请重试。`)}><Download aria-hidden="true"/>{busy === template.id ? '下载中…' : '下载'}</Button></TableCell>
            </TableRow>)}</TableBody>
          </Table> : <p className="p-6 text-sm text-muted-foreground">该客户暂未提供模板。</p>}
        </div>
      </div> : <p className="text-sm text-muted-foreground">暂无可用模板。</p>}
    </div>
    {picking && customer && adapter.feishu && <FeishuFolderPicker source={adapter.feishu.drive} files={customer.templates.map(template => ({name: template.filename}))} busy={saving} description="选择存放位置。目录中已有同名文件时不覆盖，也不重复保存。" confirmLabel={saving ? '正在保存…' : '保存到此文件夹'} onChoose={folder => void saveTo(folder)} onClose={() => {if (!saving) setPicking(false)}}/>}
    {submitting && <TemplateSubmissionDialog adapter={adapter} onClose={closeSubmission}/>}
  </section>
}

export function TemplateSubmissionDialog({adapter, customer = null, onClose}: {adapter: Pick<TemplateLibraryAdapter, 'submit'>; customer?: string | null; onClose: () => void}) {
  const [files, setFiles] = useState<File[]>([]), [dragging, setDragging] = useState(false), [sending, setSending] = useState(false), [error, setError] = useState(''), [rejected, setRejected] = useState<string[]>([])
  const requestId = useRef(crypto.randomUUID())
  const id = useId()
  function add(picked: File[]) {
    if (sending) return
    const accepted = picked.filter(file => acceptedSubmission(file.name))
    setRejected(picked.filter(file => !acceptedSubmission(file.name)).map(file => file.name))
    if (!accepted.length) return
    // A different file set is a different submission.
    requestId.current = crypto.randomUUID(); setError('')
    setFiles(rows => [...rows, ...accepted.filter(file => !rows.some(row => row.name === file.name && row.size === file.size))].slice(0, 50))
  }
  function remove(index: number) {
    if (sending) return
    requestId.current = crypto.randomUUID()
    setFiles(rows => rows.filter((_row, at) => at !== index))
  }
  async function submit() {
    if (!files.length || sending) return
    setSending(true); setError('')
    try {
      const receipt = await adapter.submit({requestId: requestId.current, customer, files})
      showToast(`已提交 ${receipt.file_count} 份材料`)
      onClose()
    } catch (failure) {
      setError(failure instanceof Error && failure.message ? failure.message : '材料尚未提交成功，请重试。')
    } finally {setSending(false)}
  }
  return <Dialog open onOpenChange={open => {if (!open && !sending) onClose()}}><DialogContent className="max-h-[90dvh] overflow-y-auto">
    <DialogHeader><DialogTitle>补充模板</DialogTitle><DialogDescription>提交后我们会评估并补充到模板库；模板仅用于识别与规则匹配，不会对外共享。</DialogDescription></DialogHeader>
    <label htmlFor={`${id}-files`} data-dragging={dragging} className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed bg-muted/30 px-4 py-8 text-center data-[dragging=true]:border-primary data-[dragging=true]:bg-primary/5" onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); add(Array.from(event.dataTransfer.files))}}>
      <UploadCloud aria-hidden="true" className="size-8 text-muted-foreground"/>
      <span className="font-medium">拖入文件，或点击选择</span>
      <span className="text-sm text-muted-foreground">支持 ZIP、RAR、7Z 压缩包，或多份 Excel、Word、PDF、图片</span>
      <input id={`${id}-files`} type="file" multiple accept={submissionAccept} className="sr-only" disabled={sending} onChange={event => {add(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
    </label>
    {rejected.length > 0 && <p role="status" className="text-sm text-destructive">未添加不支持的文件：{rejected.join('、')}</p>}
    {files.length > 0 && <section aria-label="已选材料" className="space-y-2"><h3 className="text-sm font-medium">已选 {files.length} 份</h3><ItemGroup>{files.map((file, index) => <Item key={`${file.name}-${file.size}-${file.lastModified}`} size="xs" variant="outline"><ItemContent className="min-w-0"><FileName name={file.name} colored/><ItemDescription>{file.size >= 1024 * 1024 ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(file.size / 1024))} KB`}</ItemDescription></ItemContent><ItemActions><Button variant="ghost" size="icon" aria-label={`移除 ${file.name}`} disabled={sending} onClick={() => remove(index)}><X/></Button></ItemActions></Item>)}</ItemGroup></section>}
    {error && <Alert variant="destructive"><AlertTitle>材料未提交</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    <DialogFooter><DialogClose asChild><Button variant="outline" disabled={sending}>取消</Button></DialogClose><Button disabled={!files.length || sending} onClick={() => void submit()}><Send aria-hidden="true"/>{sending ? '正在提交…' : '提交材料'}</Button></DialogFooter>
  </DialogContent></Dialog>
}
