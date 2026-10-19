// Stitch: projects/13261964020862199279/screens/44bcd2b3f57e49fe9007b1e485263936
import {Fragment, useEffect, useRef, useState} from 'react'
import {ChevronRight, Folder, X} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator} from '../components/ui/breadcrumb'
import {Checkbox} from '../components/ui/checkbox'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Item, ItemActions, ItemContent, ItemDescription, ItemGroup} from '../components/ui/item'
import {Label} from '../components/ui/label'
import {ScrollArea} from '../components/ui/scroll-area'
import {FileName} from '../journey/file-name'

type DriveFile = components['schemas']['DriveFile']
export type DrivePickerSource = {list: (query?: {folderToken?: string; pageToken?: string}) => Promise<components['schemas']['DriveFilesList']>}
const nativeSources = new Map<string, {format: 'excel' | 'word'; label: string}>([
  ['sheet', {format: 'excel', label: '飞书表格'}], ['bitable', {format: 'excel', label: '飞书多维表格'}],
  ['doc', {format: 'word', label: '飞书旧版文档'}], ['docx', {format: 'word', label: '飞书文档'}],
])
const supported = (file: DriveFile) => nativeSources.has(file.type) || file.type === 'file' && /\.(xlsx|xls|xlsm|doc|docx|pdf)$/i.test(file.name)

/** Lists and selects sources only. Adding a source does not assert a valid document. */
export function FeishuPicker({source, onAdd, onClose, trigger}: {source: DrivePickerSource; onAdd: (files: DriveFile[]) => void; onClose: () => void; trigger: HTMLButtonElement | null}) {
  const [files, setFiles] = useState<DriveFile[]>([])
  const [selected, setSelected] = useState<{file: DriveFile; folder: string}[]>([])
  const [path, setPath] = useState([{token: '', name: '我的云盘'}])
  const folder = path.at(-1)!
  const [query, setQuery] = useState(''), [nextPage, setNextPage] = useState('')
  const [loading, setLoading] = useState(false), [error, setError] = useState('')
  const readEpoch = useRef(0)
  async function load(pageToken?: string, folderToken = folder.token) {
    const epoch = ++readEpoch.current
    setLoading(true); setError('')
    if (!pageToken) {setFiles([]); setNextPage('')}
    try {
      const result = await source.list({folderToken, pageToken})
      if (readEpoch.current !== epoch) return
      setFiles(previous => {
        const merged = new Map((pageToken ? previous : []).map(file => [file.token, file]))
        result.files.forEach(file => merged.set(file.token, file))
        return [...merged.values()]
      })
      setNextPage(result.next_page_token || '')
    } catch (caught) {
      if (readEpoch.current !== epoch) return
      setError(caught instanceof Error && caught.message === 'authorization_required' ? '需要飞书授权，尚未读取文件。已有文件不会丢失。' : '飞书文件读取失败，已有文件不会丢失。可以重新读取。')
    } finally {if (readEpoch.current === epoch) setLoading(false)}
  }
  function navigate(next: typeof path) {
    setPath(next); setQuery('')
    void load(undefined, next.at(-1)!.token)
  }
  useEffect(() => {void load(); return () => {readEpoch.current += 1}}, []) // Source is fixed for one dialog lifetime.
  const shown = files.filter(file => file.name.toLowerCase().includes(query.toLowerCase())).sort((a, b) => Number(b.type === 'folder') - Number(a.type === 'folder'))
  const picked = selected.filter(row => supported(row.file))
  const unselect = (token: string) => setSelected(rows => rows.filter(row => row.file.token !== token))
  return <Dialog open onOpenChange={open => {if (!open) onClose()}}><DialogContent className="h-[min(48rem,calc(100dvh-2rem))] w-[calc(100%-2rem)] grid-rows-[auto_auto_minmax(0,1fr)_auto] overflow-hidden sm:max-w-4xl" onCloseAutoFocus={event => {event.preventDefault(); (trigger?.isConnected ? trigger : document.getElementById('audit-main'))?.focus()}}>
    <DialogHeader><DialogTitle>从飞书选择文件</DialogTitle><DialogDescription>添加后检查文件格式。</DialogDescription></DialogHeader>
    <div className="min-w-0 space-y-3">
      <Breadcrumb aria-label="飞书文件夹路径"><BreadcrumbList>{path.map((part, index) => <Fragment key={part.token}>
        {index > 0 && <BreadcrumbSeparator/>}<BreadcrumbItem className="min-w-0">{index === path.length - 1 ? <BreadcrumbPage className="break-words">{part.name}</BreadcrumbPage> : <BreadcrumbLink asChild><Button variant="ghost" size="sm" className="h-auto whitespace-normal text-left" onClick={() => navigate(path.slice(0, index + 1))}>{part.name}</Button></BreadcrumbLink>}</BreadcrumbItem>
      </Fragment>)}</BreadcrumbList></Breadcrumb>
      <div className="space-y-1"><Label htmlFor="drive-search" className="sr-only">搜索飞书文件名</Label><Input id="drive-search" placeholder="筛选当前列表" aria-describedby="drive-search-scope" value={query} onChange={event => setQuery(event.target.value)}/><p id="drive-search-scope" className="text-xs text-muted-foreground">仅筛选当前已加载项目</p></div>
    </div>
    <div className={picked.length ? 'grid min-h-0 grid-rows-2 gap-4 md:grid-cols-2 md:grid-rows-1' : 'grid min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-4 md:grid-cols-2 md:grid-rows-1'}>
      <section aria-label="当前文件夹" className="flex min-h-0 min-w-0 flex-col gap-2">
        <div className="flex min-h-11 items-center justify-between gap-2"><h2 className="font-medium">当前文件夹</h2><p className="text-xs text-muted-foreground">已加载 {files.length} 项</p></div>
        <ScrollArea type="auto" className="min-h-0 flex-1">
          {error && <Alert variant="destructive"><AlertTitle>暂时无法读取</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
          {loading && <p role="status" className="py-3 text-sm text-muted-foreground">正在读取飞书文件…</p>}
          <ItemGroup>{shown.map(file => file.type === 'folder'
            ? <div role="listitem" key={file.token}><Item size="xs" asChild className="min-h-11"><Button variant="ghost" className="h-auto w-full justify-start whitespace-normal text-left" aria-label={`打开文件夹 ${file.name}`} onClick={() => navigate([...path, {token: file.token, name: file.name}])}><span aria-hidden="true" className="size-4 shrink-0"/><ItemContent className="min-w-0"><span className="flex min-w-0 items-start gap-2"><Folder className="mt-0.5 size-4 shrink-0 text-muted-foreground"/><span className="min-w-0 [overflow-wrap:anywhere]">{file.name}</span></span></ItemContent><ChevronRight className="shrink-0"/></Button></Item></div>
            : <div role="listitem" key={file.token}><Item size="xs" asChild variant={picked.some(row => row.file.token === file.token) ? 'muted' : 'default'} className="min-h-11 items-start"><label htmlFor={`drive-${file.token}`}><Checkbox id={`drive-${file.token}`} className="mt-0.5" aria-label={`选择飞书文件 ${file.name}`} disabled={!supported(file)} checked={picked.some(row => row.file.token === file.token)} onCheckedChange={checked => checked === true ? setSelected(rows => [...rows.filter(row => row.file.token !== file.token), {file, folder: path.map(part => part.name).join(' / ')}]) : unselect(file.token)}/><ItemContent className="min-w-0"><FileName name={file.name} format={nativeSources.get(file.type)?.format}/>{nativeSources.has(file.type) && <ItemDescription>{nativeSources.get(file.type)!.label}</ItemDescription>}{!supported(file) && <ItemDescription>仅支持 Excel、Word、PDF</ItemDescription>}</ItemContent></label></Item></div>)}</ItemGroup>
          {!loading && !shown.length && <p className="py-5 text-sm text-muted-foreground">{query ? '已加载文件中没有匹配项。' : error ? '尚未取得文件列表。' : '没有可选择的云文件。'}</p>}
          <div className="flex gap-2 py-3">{error && <Button variant="outline" size="sm" disabled={loading} onClick={() => void load(nextPage || undefined)}>重新读取文件</Button>}{nextPage && <Button variant="outline" size="sm" disabled={loading} onClick={() => void load(nextPage)}>加载更多</Button>}</div>
        </ScrollArea>
      </section>
      <section aria-label="已选文件" className="flex min-h-0 min-w-0 flex-col gap-2">
        <div className="flex min-h-11 items-center justify-between gap-2"><h2 className="font-medium">已选文件 <span aria-live="polite" className="text-muted-foreground">{picked.length}</span></h2>{picked.length > 0 && <Button variant="ghost" size="sm" onClick={() => setSelected([])}>清空选择</Button>}</div>
        {picked.length ? <ScrollArea type="auto" className="min-h-0 flex-1"><ItemGroup>{picked.map(({file, folder}) => <Item role="listitem" key={file.token} size="xs" className="items-start"><ItemContent className="min-w-0"><FileName name={file.name} format={nativeSources.get(file.type)?.format}/><ItemDescription wrap>{folder}</ItemDescription></ItemContent><ItemActions><Button variant="ghost" size="icon-sm" aria-label={`取消选择 ${file.name}`} onClick={() => unselect(file.token)}><X/></Button></ItemActions></Item>)}</ItemGroup></ScrollArea> : <p className="py-3 text-sm text-muted-foreground">尚未选择文件</p>}
      </section>
    </div>
    <DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button disabled={!picked.length || loading} onClick={() => onAdd(picked.map(row => row.file))}>添加所选文件（{picked.length}）</Button></DialogFooter>
  </DialogContent></Dialog>
}
