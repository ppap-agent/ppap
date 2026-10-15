// Stitch: projects/13261964020862199279/screens/9762b6dc995d45f99c1da3f5acb44ee9
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, Download, Search} from 'lucide-react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {Label} from '../components/ui/label'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {coveredTypes, readTemplateFile, readTemplateZip, validCatalog, validTemplatePage, type AuditTemplateAdapter, type TemplateCatalog, type TemplateItem, type TemplatePage, type TemplateQuery} from './template-contract'
import {TemplatePreview} from './template-preview'
import {TemplateShareSession} from './template-share'
import {templateFolderKey, type AuditTemplateSharingAdapter} from './template-sharing-contract'
import type {TemplateFolder} from './template-contract'
import {TemplatePermissionSession} from './template-permissions'
import type {AuditTemplatePermissionAdapter, PermissionFolder} from './template-permission-contract'

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = name; link.click()
  // Let the browser consume the object URL before releasing it.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
const accessReason = (access: TemplateItem['preview_access']) => access.reason || ({denied: '当前账号无权访问。', unknown: '访问权限尚未确认。', deleted: '该文件已删除。', allowed: ''}[access.state])

export function AuditTemplates({active, adapter, sharingAdapter, permissionAdapter, onReturn, onFeedback}: {active: boolean; adapter: AuditTemplateAdapter; sharingAdapter?: AuditTemplateSharingAdapter; permissionAdapter?: AuditTemplatePermissionAdapter; onReturn: () => void; onFeedback: () => void}) {
  const [catalog, setCatalog] = useState<TemplateCatalog | null>(null), [catalogError, setCatalogError] = useState(''), [reloadCatalog, setReloadCatalog] = useState(0)
  const [customerId, setCustomerId] = useState(''), [customerSearch, setCustomerSearch] = useState('')
  const [search, setSearch] = useState(''), [pageNumber, setPageNumber] = useState(1), [page, setPage] = useState<TemplatePage | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [reloadPage, setReloadPage] = useState(0)
  const [preview, setPreview] = useState<TemplateItem | null>(null), [downloadError, setDownloadError] = useState(''), [downloadBusy, setDownloadBusy] = useState('')
  const [shareFolders, setShareFolders] = useState<Record<string, TemplateFolder>>({}), [sharingKey, setSharingKey] = useState('')
  const shareTrigger = useRef<HTMLButtonElement | null>(null)
  const [permissionFolders, setPermissionFolders] = useState<Record<string, {folder: PermissionFolder; name: string}>>({}), [permissionKey, setPermissionKey] = useState('')
  const permissionTrigger = useRef<HTMLButtonElement | null>(null)
  const trigger = useRef<HTMLButtonElement | null>(null), epoch = useRef(0), alive = useRef(true), currentCustomer = useRef(customerId), transferring = useRef(false)
  currentCustomer.current = customerId
  useEffect(() => {if (!active) {setPreview(null); setSharingKey(''); setPermissionKey('')}}, [active])
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  useEffect(() => {
    let current = true
    setCatalogError('')
    void adapter.readCatalog().then(result => {
      if (!current) return
      if (!validCatalog(result)) throw new Error('invalid_template_coverage')
      setCatalog(result)
      setCustomerId(previous => result.customers.some(row => row.customer_id === previous) ? previous : result.customers[0]?.customer_id || '')
    }).catch(() => {if (current) setCatalogError('客户目录暂不可用，或 17 类覆盖字典不完整。未用附件数量代替生效规则覆盖度。')})
    return () => {current = false}
  }, [adapter, reloadCatalog])
  useEffect(() => {
    const run = ++epoch.current
    if (!customerId) return
    setLoading(true); setError('')
    const query: TemplateQuery = {customer_id: customerId, search: search.trim(), page: pageNumber, page_size: 10}
    const timer = window.setTimeout(() => {
      void adapter.listTemplates(query).then(result => {
        if (epoch.current !== run) return
        if (!validTemplatePage(result, query)) throw new Error('template_page_mismatch')
        setPage(result)
      }).catch(() => {if (epoch.current === run) setError('暂时无法读取所选客户的模板。保留搜索条件，未替换为其他客户或版本。')}).finally(() => {if (epoch.current === run) setLoading(false)})
    }, search ? 250 : 0)
    return () => {window.clearTimeout(timer); epoch.current += 1}
  }, [adapter, customerId, search, pageNumber, reloadPage])
  function chooseCustomer(id: string) {epoch.current += 1; setSharingKey(''); setPermissionKey(''); setCustomerId(id); setSearch(''); setPageNumber(1); setPage(null); setDownloadError('')}
  const customers = catalog?.customers.filter(row => row.name.toLowerCase().includes(customerSearch.toLowerCase())) || []
  const customer = catalog?.customers.find(row => row.customer_id === customerId)
  const shown = page?.query.customer_id === customerId ? page : null
  const stale = loading || Boolean(error) || shown?.query.search !== search.trim() || shown?.query.page !== pageNumber
  async function transfer(item?: TemplateItem) {
    if (transferring.current || !shown || stale) return
    const id = customerId
    transferring.current = true; setDownloadBusy(item?.reference.file_version_id || 'zip'); setDownloadError('')
    try {
      if (item) {
        const blob = await readTemplateFile(adapter, item, 'download')
        if (alive.current) download(blob, item.file.original_filename)
      } else {
        if (shown.zip_access.state !== 'allowed') return
        const result = await readTemplateZip(adapter, {customer_id: id, folder_id: shown.folder_id, folder_revision: shown.folder_revision, folder_template_count: shown.folder_template_count})
        if (alive.current) download(result.blob, result.snapshot.filename)
      }
    } catch {if (alive.current && currentCustomer.current === id) setDownloadError(item ? '未能读取这个模板版本。文件和审核任务未改变，可重新下载。' : '未下载不完整或身份不符的模板包。请重新读取清单后再下载；单份模板仍可使用。')}
    finally {transferring.current = false; if (alive.current) setDownloadBusy('')}
  }
  return <section className="space-y-6" aria-label="客户模板库">
    <header className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1 space-y-2"><h1 className="text-2xl font-semibold">客户模板库</h1><p className="text-muted-foreground">模板用于填写资料，不改变审核规则。</p></div><Button variant="outline" onClick={onReturn}><ArrowLeft/>返回审核</Button></header>
    {Object.entries(permissionFolders).filter(([, entry]) => entry.folder.customer_id === customerId && (!shown || stale || shown.manage_access.state !== 'allowed' || shown.folder_id !== entry.folder.folder_id)).map(([key, entry]) => <div key={key} className="flex flex-wrap items-center gap-2 text-sm"><p>{entry.name}：已有操作仍可读取原回执；这不授予新的修改权限。</p><Button variant="outline" onClick={event => {permissionTrigger.current = event.currentTarget; setPermissionKey(key)}}>恢复已有权限操作</Button></div>)}
    {Object.entries(shareFolders).filter(([, folder]) => folder.customer_id === customerId && (!shown || stale || !shown.folder_template_count || shown.folder_id !== folder.folder_id)).map(([key]) => <div key={key} className="flex flex-wrap items-center gap-2 text-sm"><p>已有模板分享可读取原发送结果，不使用当前空清单重发。</p><Button variant="outline" onClick={event => {shareTrigger.current = event.currentTarget; setSharingKey(key)}}>恢复已有模板分享</Button></div>)}
    {catalogError ? <Alert variant="destructive"><AlertTitle>客户目录读取失败</AlertTitle><AlertDescription>{catalogError}<Button variant="outline" onClick={() => setReloadCatalog(value => value + 1)}>重新读取客户</Button></AlertDescription></Alert> : !catalog ? <p role="status">正在读取有生效规则的客户…</p> : !catalog.customers.length ? <div className="space-y-3"><p>暂无具有生效规则的客户。</p><Button variant="outline" onClick={onReturn}>返回上传</Button></div> : <div className="grid min-w-0 gap-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
      <aside aria-label="模板客户" className="min-w-0 space-y-3 lg:border-r lg:pr-5"><Label htmlFor="template-customer-search">客户</Label><Input id="template-customer-search" placeholder="搜索客户" value={customerSearch} onChange={event => setCustomerSearch(event.target.value)}/><p className="text-xs text-muted-foreground">仅展示有生效规则的客户。</p><div className="lg:hidden"><Select value={customerId} onValueChange={chooseCustomer}><SelectTrigger className="w-full" aria-label="选择模板客户"><SelectValue/></SelectTrigger><SelectContent>{customers.map(row => <SelectItem key={row.customer_id} value={row.customer_id}>{row.name}</SelectItem>)}</SelectContent></Select></div><ItemGroup className="max-lg:hidden">{customers.map(row => <Item role="listitem" size="xs" key={row.customer_id}><ItemContent><Button variant={customerId === row.customer_id ? 'secondary' : 'ghost'} className="h-auto w-full justify-start whitespace-normal text-left" aria-label={`选择客户 ${row.name}`} onClick={() => chooseCustomer(row.customer_id)}>{row.name}</Button></ItemContent></Item>)}</ItemGroup>{!customers.length && <p>没有符合条件的客户。</p>}</aside>
      <div className="min-w-0 space-y-4"><div className="flex flex-wrap items-start justify-between gap-3"><div className="space-y-2"><h2 className="text-lg font-semibold">{customer?.name}</h2>{customer && <Badge variant="outline">支持 {coveredTypes(catalog, customer.effective_rule_types).length}/17 类文件</Badge>}<p className="text-xs text-muted-foreground max-md:hidden">按生效规则覆盖的文件类型去重统计，不按模板附件数量统计。</p></div><div className="space-y-1 text-xs text-muted-foreground"><p>文件夹创建人：{shown?.creator_name || '未取得'}</p><Button variant="outline" size="sm" disabled={!permissionAdapter || !shown || stale || shown.manage_access.state !== 'allowed'} onClick={event => {if (!shown) return; const folder = {customer_id: customerId, folder_id: shown.folder_id}, key = templateFolderKey(folder); permissionTrigger.current = event.currentTarget; setPermissionFolders(rows => ({...rows, [key]: {folder, name: customer?.name || customerId}})); setPermissionKey(key)}}>管理文件夹权限</Button><p>{shown?.manage_access.state !== 'allowed' ? shown?.manage_access.reason || '只有服务确认的文件夹管理者可修改权限。' : '管理范围由服务核对；修改前逐项确认。'}</p></div></div>
        <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex min-w-0 flex-1 basis-full items-center gap-2 sm:basis-0"><Search className="size-4 shrink-0 text-muted-foreground"/><Label className="sr-only" htmlFor="template-search">搜索模板</Label><Input id="template-search" className="max-w-md" placeholder="搜索模板名称或文件类型" value={search} onChange={event => {epoch.current += 1; setSearch(event.target.value); setPageNumber(1)}}/></div><Button variant="outline" disabled={!sharingAdapter || !shown || stale || !shown.folder_template_count} onClick={event => {if (!shown) return; const folder = {customer_id: customerId, folder_id: shown.folder_id, folder_revision: shown.folder_revision, folder_template_count: shown.folder_template_count}, key = templateFolderKey(folder); shareTrigger.current = event.currentTarget; setShareFolders(rows => ({...rows, [key]: folder})); setSharingKey(key)}}>分享客户模板</Button><Button variant="outline" disabled={!shown || stale || Boolean(downloadBusy) || shown.zip_access.state !== 'allowed' || !shown.folder_template_count} onClick={() => void transfer()}><Download/>{downloadBusy === 'zip' ? '正在下载模板包…' : '下载客户模板 ZIP'}</Button></div>
        {loading && <p role="status">正在读取所选客户的模板…</p>}{error && <Alert variant="destructive"><AlertTitle>模板清单读取失败</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setReloadPage(value => value + 1)}>重新读取模板清单</Button></AlertDescription></Alert>}{downloadError && <Alert variant="destructive"><AlertTitle>下载未完成</AlertTitle><AlertDescription>{downloadError}</AlertDescription></Alert>}
        {shown && <><Table aria-label="客户模板"><TableHeader><TableRow><TableHead>模板文件</TableHead><TableHead className="max-md:hidden">文件类型</TableHead><TableHead className="max-xl:hidden">更新时间 ↓</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{shown.items.map(item => <TableRow key={item.reference.template_id}><TableCell className="whitespace-normal"><FileName name={item.file.original_filename}/><span className="mt-1 block text-xs text-muted-foreground md:hidden">{item.document_type}</span>{item.preview_access.state !== 'allowed' && <p className="mt-1 text-xs text-muted-foreground">{accessReason(item.preview_access)}</p>}</TableCell><TableCell className="max-md:hidden">{item.document_type}</TableCell><TableCell className="max-xl:hidden">{item.updated_at.replace('T', ' ').slice(0, 16)}</TableCell><TableCell><div className="flex justify-end gap-1 max-sm:flex-col"><Button variant="ghost" size="sm" disabled={stale || item.preview_access.state !== 'allowed'} onClick={event => {trigger.current = event.currentTarget; setPreview(structuredClone(item))}}>预览</Button><Button variant="ghost" size="sm" disabled={stale || Boolean(downloadBusy) || item.download_access.state !== 'allowed'} title={accessReason(item.download_access)} onClick={() => void transfer(item)}>下载</Button></div></TableCell></TableRow>)}</TableBody></Table>{!shown.items.length && !loading && <div className="space-y-2 py-6"><p>{search ? '没有符合条件的模板。' : '此客户暂无可用模板，未使用其他客户的模板替代。'}</p>{search && <Button variant="outline" onClick={() => {setSearch(''); setPageNumber(1)}}>清除模板搜索</Button>}</div>}<footer className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">共 {shown.total} 份模板 · 第 {shown.query.page}/{Math.max(1, Math.ceil(shown.total / 10))} 页</p><div className="flex gap-2"><Button variant="outline" size="sm" aria-label="上一页模板" disabled={stale || pageNumber <= 1} onClick={() => setPageNumber(value => value - 1)}>上一页</Button><Button variant="outline" size="sm" aria-label="下一页模板" disabled={stale || pageNumber * 10 >= shown.total} onClick={() => setPageNumber(value => value + 1)}>下一页</Button></div></footer>{shown.zip_access.state !== 'allowed' && <p className="text-xs text-muted-foreground">{accessReason(shown.zip_access)}</p>}</>}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4"><Button variant="ghost" className="justify-start" onClick={onFeedback}>更多模板请补充</Button><p className="text-xs text-muted-foreground">分享链接不自动授权；交互预览不会真实发送。</p></div>
      </div>
    </div>}
    {active && preview && <TemplatePreview item={preview} adapter={adapter} onClose={() => setPreview(null)} trigger={trigger.current}/>}
    {sharingAdapter && Object.entries(shareFolders).map(([key, folder]) => <TemplateShareSession key={key} open={active && key === sharingKey} folder={folder} adapter={sharingAdapter} templates={adapter} onClose={() => setSharingKey('')} trigger={shareTrigger.current}/>)}
    {permissionAdapter && Object.entries(permissionFolders).map(([key, entry]) => <TemplatePermissionSession key={key} open={active && key === permissionKey} folder={entry.folder} customerName={entry.name} adapter={permissionAdapter} onClose={() => setPermissionKey('')} trigger={permissionTrigger.current}/>)}
  </section>
}
