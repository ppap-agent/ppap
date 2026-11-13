import {useEffect, useId, useRef, useState} from 'react'
import {Download, Plus, Search, Send, Share2, Trash2} from 'lucide-react'
import type {components} from '../generated/openapi'
import {ReadSkeleton} from '../components/read-skeleton'
import {showToast} from '../components/toast'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Pager} from '../components/pager'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileTypeBadge} from './file-type-badge'
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
/** Standard PPAP types (T01–T17) the customer's templates cover, and types only this customer has (E codes). An undecided type counts for neither. */
/** Coverage against the customer's own rule types, as the template library service reports them. */
export function templateCoverage(customer: LibraryCustomer) {
  const codes = new Set(customer.templates.map(template => template.type_code).filter((code): code is string => Boolean(code)))
  return {covered: customer.standard_type_codes.filter(code => codes.has(code)).length, total: customer.standard_type_codes.length}
}
const customerParam = 'templateCustomer'

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a'); link.href = url; link.download = name; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function Avatar({name, index, className}: {name: string; index: number; className?: string}) {
  return <span aria-hidden="true" className={cn('flex size-9 shrink-0 items-center justify-center rounded-md text-base font-semibold text-white', avatarTones[index % avatarTones.length], className)}>{name.slice(0, 1)}</span>
}

export function TemplateLibraryPage({adapter}: {adapter: TemplateLibraryAdapter}) {
  const [library, setLibrary] = useState<TemplateLibrary | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState(''), [reload, setReload] = useState(0)
  const [selected, setSelected] = useState(() => new URL(location.href).searchParams.get(customerParam) || ''), [busy, setBusy] = useState(''), [actionError, setActionError] = useState('')
  const [customerSearch, setCustomerSearch] = useState(''), [page, setPage] = useState(1)
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
  const needle = customerSearch.trim().toLocaleLowerCase(), shownCustomers = customers.filter(row => !needle || row.name.toLocaleLowerCase().includes(needle))
  // The chosen customer stays in the address, so a refresh or a shared link opens the same list.
  function choose(id: string) {
    setSelected(id); setActionError(''); setPage(1)
    const url = new URL(location.href); url.searchParams.set(customerParam, id); history.replaceState(history.state, '', url)
  }
  async function share() {
    if (!customer) return
    const url = new URL(location.href); url.searchParams.set(customerParam, customer.id); url.searchParams.delete('templateRequest')
    try {await navigator.clipboard.writeText(url.href); showToast(`已复制${customer.name}模板库链接，同事登录后可查看和下载`)}
    catch {setActionError('浏览器未允许复制链接，请复制地址栏中的链接分享。')}
  }
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
  const title = <h1 className="px-1 text-base font-semibold">模板库</h1>
  if (loading && !library) return <section aria-label="模板库" className="space-y-4">{title}<ReadSkeleton label="正在读取模板库…" kind="table"/></section>
  if (error && !library) return <section aria-label="模板库" className="space-y-4">{title}<Alert variant="destructive"><AlertTitle>模板库未能读取</AlertTitle><AlertDescription><p>{error}</p><Button variant="outline" size="sm" onClick={() => setReload(value => value + 1)}>重新读取模板库</Button></AlertDescription></Alert></section>
  const shownSave = saved && customer && saved.customer === customer.id ? saved : null
  const unsettled = shownSave?.results.filter(row => row.state !== 'saved') ?? [], open = unsettled.filter(row => row.state === 'failed' || row.state === 'unknown').length
  const coverage = customer ? templateCoverage(customer) : null
  const pageCount = Math.max(1, Math.ceil((customer?.templates.length ?? 0) / 10)), shownPage = Math.min(page, pageCount)
  return <section aria-label="模板库" className="grid grid-cols-[16rem_minmax(0,1fr)] gap-4">
    <aside aria-label="模板库客户" className="sticky top-0 flex max-h-[calc(100dvh-7rem)] min-w-0 flex-col gap-3 self-start overflow-y-auto rounded-lg border bg-card p-3">
      {title}
      <div className="relative"><span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-muted-foreground"><Search aria-hidden="true" className="size-3.5"/></span><Input type="search" aria-label="搜索客户" placeholder="搜索客户" iconStart value={customerSearch} onChange={event => setCustomerSearch(event.target.value)}/></div>
      <nav aria-label="客户" className="flex min-w-0 flex-col gap-2">
        {shownCustomers.map(row => {const value = templateCoverage(row), index = customers.indexOf(row); return <button key={row.id} type="button" aria-pressed={row.id === customer?.id} onClick={() => choose(row.id)} className="flex min-h-11 min-w-0 items-center gap-3 rounded-md border bg-card p-2.5 text-left outline-none transition-colors hover:border-primary-line focus-visible:ring-3 focus-visible:ring-ring/50 aria-pressed:border-primary aria-pressed:bg-primary/5">
          <Avatar name={row.name} index={index}/>
          <span className="min-w-0"><span className="block truncate text-sm">{row.name}</span><span className="block text-xs text-muted-foreground tabular-nums">{value.covered} / {value.total} 类</span></span>
        </button>})}
        {!shownCustomers.length && <p className="px-1 py-2 text-xs text-muted-foreground">没有匹配的客户。</p>}
      </nav>
      <div className="flex justify-end border-t pt-3"><Button variant="link" size="sm" onClick={() => setSubmitting(true)}><Plus aria-hidden="true"/>更多模板，请补充</Button></div>
    </aside>
    {customer && coverage ? <div className="min-w-0 space-y-4">
      <header className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1"><Avatar name={customer.name} index={customerIndex}/><h2 className="truncate text-xl font-semibold">{customer.name}</h2><p className="text-xs text-muted-foreground tabular-nums">已支持 {coverage.covered} / {coverage.total} 类 PPAP 文件</p></div>
        <div className="flex shrink-0 gap-2">
          <Button onClick={() => void share()}><Share2 aria-hidden="true"/>分享</Button>
          <Button variant="outline" disabled={Boolean(busy) || !customer.templates.length} onClick={() => void run('zip', async () => saveBlob(await adapter.downloadAll(customer.id), `${customer.name}模板.zip`), '模板 ZIP 暂时无法下载，请重试。')}><Download aria-hidden="true"/>{busy === 'zip' ? '正在打包…' : '下载模板（ZIP）'}</Button>
          {adapter.feishu && <Button variant="outline" disabled={Boolean(busy) || saving || !customer.templates.length} onClick={() => setPicking(true)}>保存到我的飞书</Button>}
        </div>
      </header>
      {actionError && <Alert variant="destructive"><AlertTitle>操作未完成</AlertTitle><AlertDescription>{actionError}</AlertDescription></Alert>}
      {shownSave && <Alert variant={open ? 'destructive' : 'default'}><AlertTitle>{open ? `已保存 ${shownSave.results.filter(row => row.state === 'saved').length} 份，${open} 份未完成` : '模板已在所选飞书目录中'}</AlertTitle><AlertDescription><p>保存位置：{shownSave.folder}</p>{unsettled.length > 0 && <ul className="space-y-1">{unsettled.map(row => <li key={row.template_id}>{row.filename}：{saveStates[row.state]}{row.state !== 'exists' && row.reason ? `。${row.reason}` : ''}</li>)}</ul>}</AlertDescription></Alert>}
      <div className="overflow-hidden rounded-lg border bg-card">
        {customer.templates.length ? <Table aria-label={`${customer.name}模板`}>
          <TableHeader variant="muted"><TableRow><TableHead>模板名称</TableHead><TableHead>文件类型</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader>
          <TableBody>{customer.templates.slice((shownPage - 1) * 10, shownPage * 10).map(template => <TableRow key={template.id}>
            <TableCell className="whitespace-normal"><span className="flex min-w-0 items-center gap-2"><FileTypeBadge name={template.filename}/><span className="[overflow-wrap:anywhere]">{template.filename}</span></span></TableCell>
            <TableCell><span className="text-muted-foreground">{template.document_type}</span></TableCell>
            <TableCell className="text-right"><Button variant="ghost" size="sm" aria-label={`下载 ${template.filename}`} disabled={Boolean(busy)} onClick={() => void run(template.id, async () => saveBlob(await adapter.download(customer.id, template.id), template.filename), `${template.filename} 暂时无法下载，请重试。`)}><Download aria-hidden="true"/>{busy === template.id ? '下载中…' : '下载'}</Button></TableCell>
          </TableRow>)}</TableBody>
        </Table> : <p className="p-6 text-sm text-muted-foreground">暂无模板</p>}
        {customer.templates.length > 0 && <Pager className="border-t px-3 py-3" total={customer.templates.length} page={shownPage} onPage={setPage}/>}
      </div>
    </div> : <p className="text-sm text-muted-foreground">暂无可用模板。</p>}
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
  function keep(file: File) {
    const url = URL.createObjectURL(file), link = document.createElement('a'); link.href = url; link.download = file.name; link.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <Dialog open onOpenChange={open => {if (!open && !sending) onClose()}}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
    <DialogHeader><DialogTitle>补充模板</DialogTitle><DialogDescription>提交后我们会在 1-3 个工作日内评估并补充到模板库；模板仅用于识别与规则匹配，不会对外共享。</DialogDescription></DialogHeader>
    <label htmlFor={`${id}-files`} data-dragging={dragging} className="flex min-h-64 cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed border-primary-line bg-primary-soft/40 p-5 text-center focus-within:ring-3 focus-within:ring-ring/50 data-[dragging=true]:border-primary data-[dragging=true]:bg-primary/5" onDragOver={event => {event.preventDefault(); setDragging(true)}} onDragLeave={() => setDragging(false)} onDrop={event => {event.preventDefault(); setDragging(false); add(Array.from(event.dataTransfer.files))}}>
      <Plus aria-hidden="true" className="size-10 text-primary"/>
      <span className="text-lg font-semibold">上传模板</span>
      <span className="text-sm leading-relaxed text-muted-foreground">支持压缩包（ZIP / RAR / 7Z）或多个文件<br/>支持 Excel / Word / 图片</span>
      <input id={`${id}-files`} type="file" multiple accept={submissionAccept} className="sr-only" disabled={sending} onChange={event => {add(Array.from(event.target.files ?? [])); event.target.value = ''}}/>
    </label>
    {rejected.length > 0 && <p role="status" className="text-sm text-destructive">未添加不支持的文件：{rejected.join('、')}</p>}
    {files.length > 0 && <ul aria-label="已选材料" className="grid gap-0.5">{files.map((file, index) => <li key={`${file.name}-${file.size}-${file.lastModified}`} className="flex items-center gap-3 py-1.5 text-sm">
      <FileTypeBadge name={file.name}/>
      <span className="min-w-0 flex-1 truncate text-primary" title={file.name}>{file.name}</span>
      <Button variant="ghost" size="icon-sm" aria-label={`下载 ${file.name}`} disabled={sending} onClick={() => keep(file)}><Download/></Button>
      <Button variant="ghost" size="icon-sm" aria-label={`移除 ${file.name}`} disabled={sending} onClick={() => remove(index)}><Trash2/></Button>
    </li>)}</ul>}
    {error && <Alert variant="destructive"><AlertTitle>材料未提交</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    <DialogFooter><DialogClose asChild><Button variant="outline" disabled={sending}>取消</Button></DialogClose><Button disabled={!files.length || sending} onClick={() => void submit()}><Send aria-hidden="true"/>{sending ? '正在提交…' : '提交材料'}</Button></DialogFooter>
  </DialogContent></Dialog>
}
