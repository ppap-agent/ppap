import {ReadSkeleton} from '../components/read-skeleton'
// Stitch: projects/13261964020862199279/screens/639a2a5ebe7841d1adfc76ae28577f93
// Mobile: projects/13261964020862199279/screens/719e87ef8fb9452e9955b8c2762fd62d
import {useEffect, useId, useRef, useState} from 'react'
import {ChevronRight, Folder, Search, X} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible'
import {Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Item, ItemActions, ItemContent, ItemDescription, ItemGroup} from '../components/ui/item'
import {Label} from '../components/ui/label'
import {ScrollArea} from '../components/ui/scroll-area'
import {FileName} from '../journey/file-name'

type DriveFile = components['schemas']['DriveFile']
export type DrivePickerSource = {list: (query?: {folderToken?: string; pageToken?: string}) => Promise<components['schemas']['DriveFilesList']>; rootFolder?: () => Promise<DriveFile>}
type FolderState = {files: DriveFile[]; nextPage: string; loading: boolean; error: string; loaded: boolean}
type FolderPath = {token: string; name: string}[]
const emptyFolder: FolderState = {files: [], nextPage: '', loading: false, error: '', loaded: false}
const root: FolderPath = [{token: '', name: '我的云盘'}]
const nativeSources = new Map<string, {format: 'excel' | 'word'; label: string}>([
  ['sheet', {format: 'excel', label: '飞书表格'}], ['bitable', {format: 'excel', label: '飞书多维表格'}],
  ['doc', {format: 'word', label: '飞书旧版文档'}], ['docx', {format: 'word', label: '飞书文档'}],
])
const supported = (file: DriveFile) => nativeSources.has(file.type) || file.type === 'file' && /\.(xlsx|xls|xlsm|doc|docx|pdf)$/i.test(file.name)

export function FeishuFolderPicker({source, files, onChoose, onClose, description = '选择存放位置。每次新上传都会创建独立文件，不覆盖同名文件。', confirmLabel = '上传到此文件夹', busy = false}: {source: DrivePickerSource; files: readonly {name: string}[]; onChoose: (folder: {token: string; name: string}) => void; onClose: () => void; description?: string; confirmLabel?: string; busy?: boolean}) {
  const [path, setPath] = useState<FolderPath>([])
  const [listing, setListing] = useState<FolderState>(emptyFolder)
  const [revision, setRevision] = useState(0)
  const epoch = useRef(0), alive = useRef(true)
  const current = path.at(-1)
  async function load(next: FolderPath, pageToken?: string) {
    const started = ++epoch.current
    setPath(next); setListing(before => ({...pageToken ? before : emptyFolder, loading: true}))
    try {
      const result = await source.list({folderToken: next.at(-1)!.token, pageToken})
      if (!alive.current || started !== epoch.current) return
      setListing(before => ({files: [...pageToken ? before.files : [], ...result.files.filter(file => file.type === 'folder')], nextPage: result.next_page_token || '', loading: false, error: '', loaded: true}))
    } catch {
      if (alive.current && started === epoch.current) setListing(before => ({...before, loading: false, error: '无法读取文件夹，请核对飞书权限后重试。'}))
    }
  }
  useEffect(() => {
    alive.current = true
    const started = ++epoch.current
    void source.rootFolder?.().then(folder => {
      if (!alive.current || started !== epoch.current) return
      if (folder.type !== 'folder' || !folder.token) throw new Error('invalid_root_folder')
      void load([{token: folder.token, name: '我的文件夹'}])
    }).catch(() => {if (alive.current && started === epoch.current) setListing({...emptyFolder, error: '无法读取个人飞书目录，请重新登录授权后重试。'})})
    return () => {alive.current = false; epoch.current += 1}
  }, [source, revision])
  return <Dialog open onOpenChange={open => {if (!open) onClose()}}><DialogContent className="max-h-[90dvh] grid-rows-[auto_auto_minmax(0,1fr)_auto] overflow-hidden" onCloseAutoFocus={event => {event.preventDefault(); document.getElementById('audit-main')?.focus()}}>
    <DialogHeader><DialogTitle>保存到飞书</DialogTitle><DialogDescription>{description}</DialogDescription></DialogHeader>
    <nav aria-label="上传目录" className="flex flex-wrap items-center gap-1">{path.map((folder, index) => <Button key={folder.token} variant="ghost" size="sm" disabled={index === path.length - 1} onClick={() => void load(path.slice(0, index + 1))}>{index > 0 && <ChevronRight/>}{folder.name}</Button>)}</nav>
    <ScrollArea className="min-h-40 max-h-[55dvh] min-w-0"><section aria-label="选择上传目录" className="space-y-4 pr-3">
      <ItemGroup>{listing.files.map(folder => <Item key={folder.token} size="xs" asChild><Button variant="ghost" className="h-auto min-h-11 w-full justify-start whitespace-normal text-left" aria-label={`打开文件夹 ${folder.name}`} onClick={() => void load([...path, {token: folder.token, name: folder.name}])}><Folder className="shrink-0"/><span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{folder.name}</span><ChevronRight className="shrink-0"/></Button></Item>)}</ItemGroup>
      {listing.loading || !current && !listing.error ? <ReadSkeleton label="正在读取文件夹…" kind={listing.files.length ? 'inline' : 'list'}/> : listing.error ? <Alert variant="destructive"><AlertTitle>文件夹未能读取</AlertTitle><AlertDescription><p>{listing.error}</p><Button variant="outline" size="sm" onClick={() => current ? void load(path, listing.nextPage || undefined) : setRevision(value => value + 1)}>重新读取文件夹</Button></AlertDescription></Alert> : !listing.files.length && <p className="text-sm text-muted-foreground">没有子文件夹。</p>}
      {listing.nextPage && !listing.loading && !listing.error && <Button variant="outline" size="sm" onClick={() => void load(path, listing.nextPage)}>更多文件夹</Button>}
      <section className="space-y-2 border-t pt-4"><h3 className="text-sm font-medium">本次上传 · {files.length} 份</h3><ItemGroup>{files.map((file, index) => <Item key={index} size="xs"><ItemContent className="min-w-0"><FileName name={file.name}/></ItemContent></Item>)}</ItemGroup></section>
    </section></ScrollArea>
    <DialogFooter><DialogClose asChild><Button variant="outline">取消</Button></DialogClose><Button disabled={busy || !current || listing.loading || Boolean(listing.error)} onClick={() => current && onChoose({...current, name: path.map(folder => folder.name).join(' / ')})}>{confirmLabel}</Button></DialogFooter>
  </DialogContent></Dialog>
}

/** Browsing and adding sources do not start an audit or validate a document. */
export function FeishuPicker({source, onAdd, onClose, trigger}: {source: DrivePickerSource; onAdd: (files: DriveFile[]) => void; onClose: () => void; trigger: HTMLButtonElement | null}) {
  const [folders, setFolders] = useState<Record<string, FolderState>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [filterExpansion, setFilterExpansion] = useState<Map<string, boolean>>(new Map())
  const [selected, setSelected] = useState<{file: DriveFile; folder: string}[]>([])
  const [query, setQuery] = useState('')
  const id = useId()
  const pending = useRef(new Map<string, number>())
  const epoch = useRef(0), alive = useRef(true)
  async function load(folderToken: string, pageToken?: string) {
    if (pending.current.has(folderToken)) return
    const request = ++epoch.current
    pending.current.set(folderToken, request)
    setFolders(previous => ({...previous, [folderToken]: {...previous[folderToken] || emptyFolder, loading: true, error: ''}}))
    try {
      const result = await source.list({folderToken, pageToken})
      if (!alive.current || pending.current.get(folderToken) !== request) return
      setFolders(previous => {
        const files = new Map((pageToken ? previous[folderToken]?.files || [] : []).map(file => [file.token, file]))
        result.files.forEach(file => files.set(file.token, file))
        return {...previous, [folderToken]: {files: [...files.values()], nextPage: result.next_page_token || '', loading: false, error: '', loaded: true}}
      })
    } catch (caught) {
      if (!alive.current || pending.current.get(folderToken) !== request) return
      const error = caught instanceof Error && caught.message === 'authorization_required' ? '需要飞书授权，尚未读取文件。已有文件不会丢失。' : '飞书文件读取失败，已有文件不会丢失。可以重新读取。'
      setFolders(previous => ({...previous, [folderToken]: {...previous[folderToken] || emptyFolder, loading: false, error}}))
    } finally {
      if (pending.current.get(folderToken) === request) pending.current.delete(folderToken)
    }
  }
  useEffect(() => {
    alive.current = true
    void load('')
    return () => {alive.current = false; pending.current.clear()}
  }, []) // Source is fixed for one dialog lifetime. Epochs also distinguish StrictMode reads.
  const picked = selected.filter(row => supported(row.file))
  const unselect = (token: string) => setSelected(rows => rows.filter(row => row.file.token !== token))
  const filter = query.trim().toLocaleLowerCase()
  function matches(file: DriveFile, ancestors: string[]): boolean {
    return file.name.toLocaleLowerCase().includes(filter) || file.type === 'folder' && !ancestors.includes(file.token) && (folders[file.token]?.files || []).some(child => matches(child, [...ancestors, file.token]))
  }
  function listing(path: FolderPath) {
    const token = path.at(-1)!.token
    const state = folders[token] || emptyFolder
    const ancestors = path.map(part => part.token)
    const shown = state.files.filter(file => matches(file, ancestors)).sort((a, b) => Number(b.type === 'folder') - Number(a.type === 'folder'))
    return <>
      <ItemGroup>{shown.map(file => {
        if (file.type === 'folder') {
          if (ancestors.includes(file.token)) return null
          const descendantMatch = !!filter && (folders[file.token]?.files || []).some(child => matches(child, [...ancestors, file.token]))
          const open = filter ? filterExpansion.get(file.token) ?? (expanded.has(file.token) || descendantMatch) : expanded.has(file.token)
          return <Collapsible key={file.token} role="listitem" open={open} onOpenChange={next => {
            if (filter) setFilterExpansion(previous => new Map(previous).set(file.token, next))
            else setExpanded(previous => {const result = new Set(previous); if (next) result.add(file.token); else result.delete(file.token); return result})
            if (next && !folders[file.token]?.loaded && !folders[file.token]?.error) void load(file.token)
          }}>
            <CollapsibleTrigger asChild><Item size="xs" asChild className="min-h-11"><Button variant="ghost" className="h-auto w-full justify-start whitespace-normal text-left" aria-label={`${open ? '收起' : '展开'}文件夹 ${file.name}`}>
              <ChevronRight className={`size-4 shrink-0 text-muted-foreground ${open ? 'rotate-90' : ''}`}/><ItemContent className="min-w-0"><span className="flex min-w-0 items-start gap-2"><Folder className="mt-0.5 size-4 shrink-0 text-muted-foreground"/><span className="min-w-0 [overflow-wrap:anywhere]">{file.name}</span></span></ItemContent>
            </Button></Item></CollapsibleTrigger>
            <CollapsibleContent><div className="ml-3 border-l pl-1 sm:ml-4 sm:pl-2">{listing([...path, {token: file.token, name: file.name}])}</div></CollapsibleContent>
          </Collapsible>
        }
        const checkboxId = `${id}-${ancestors.map(encodeURIComponent).join('/')}-${encodeURIComponent(file.token)}`
        return <div role="listitem" key={file.token}><Item size="xs" asChild variant={picked.some(row => row.file.token === file.token) ? 'muted' : 'default'} className="min-h-11 items-start"><label htmlFor={checkboxId} className="cursor-pointer">
          <Checkbox id={checkboxId} className="mt-0.5" aria-label={`选择飞书文件 ${file.name}`} disabled={!supported(file)} checked={picked.some(row => row.file.token === file.token)} onCheckedChange={checked => checked === true ? setSelected(rows => [...rows.filter(row => row.file.token !== file.token), {file, folder: path.map(part => part.name).join(' / ')}]) : unselect(file.token)}/>
          <ItemContent className="min-w-0"><FileName name={file.name} format={nativeSources.get(file.type)?.format}/>{nativeSources.has(file.type) && <ItemDescription>{nativeSources.get(file.type)!.label}</ItemDescription>}{!supported(file) && <ItemDescription>仅支持 Excel、Word、PDF</ItemDescription>}</ItemContent>
        </label></Item></div>
      })}</ItemGroup>
      {state.loading && <ReadSkeleton label="正在读取飞书文件…" kind={state.files.length ? 'inline' : 'list'}/>}
      {state.error && <Alert variant="destructive" className="my-2"><AlertTitle>暂时无法读取</AlertTitle><AlertDescription>{state.error}</AlertDescription></Alert>}
      {!state.loading && !state.error && !shown.length && <p className="px-2 py-4 text-sm text-muted-foreground">{filter ? '已加载文件中没有匹配项。' : '没有可选择的云文件。'}</p>}
      {(state.error || state.nextPage) && <div className="px-2 py-2"><Button variant="outline" size="sm" className="min-h-11" disabled={state.loading} onClick={() => void load(token, state.nextPage || undefined)}>{state.error ? '重新读取文件' : '加载更多'}</Button></div>}
    </>
  }
  return <Dialog open onOpenChange={open => {if (!open) onClose()}}><DialogContent layout="file-picker" showCloseButton={false} className="h-dvh max-h-dvh w-full max-w-none grid-rows-[auto_auto_minmax(0,1fr)_auto] overflow-hidden sm:h-[min(46rem,calc(100dvh-2rem))] sm:w-[calc(100%-2rem)] sm:max-w-5xl" onCloseAutoFocus={event => {event.preventDefault(); (trigger?.isConnected ? trigger : document.getElementById('audit-main'))?.focus()}}>
    <div className="px-4 py-3"><DialogHeader><div className="flex items-center justify-between gap-3"><div><DialogTitle>从飞书选择文件</DialogTitle><DialogDescription className="sr-only">选择要添加的 Excel、Word 或 PDF 文件。</DialogDescription></div><DialogClose asChild><Button variant="ghost" size="icon" className="size-11 shrink-0" aria-label="关闭"><X/></Button></DialogClose></div></DialogHeader></div>
    <div className="px-4 pb-4"><Label htmlFor={`${id}-search`} className="sr-only">搜索飞书文件名</Label><div className="flex items-center gap-2"><Search aria-hidden="true" className="size-4 shrink-0 text-muted-foreground"/><Input id={`${id}-search`} className="h-11" placeholder="搜索文件名" value={query} onChange={event => {setQuery(event.target.value); setFilterExpansion(new Map())}}/></div></div>
    <div className={`grid min-h-0 border-y md:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] md:grid-rows-1 ${picked.length ? 'grid-rows-[minmax(0,1.1fr)_minmax(0,1fr)]' : 'grid-rows-[minmax(0,1fr)_auto]'}`}>
      <section aria-label="云盘文件" className="flex min-h-0 min-w-0 flex-col px-2 sm:px-4">
        <div className="flex min-h-12 items-center px-2"><h2 className="text-sm font-medium">我的云盘</h2></div>
        <ScrollArea type="auto" className="min-h-0 flex-1"><div className="pb-2">{listing(root)}</div></ScrollArea>
      </section>
      <section aria-label="已选文件" className="flex min-h-0 min-w-0 flex-col border-t px-2 md:border-t-0 md:border-l sm:px-4">
        <div className="flex min-h-12 items-center justify-between gap-2 px-2"><h2 className="text-sm font-medium">已选文件 <span aria-live="polite" className="text-muted-foreground">{picked.length}</span></h2>{picked.length > 0 && <Button variant="ghost" size="sm" className="min-h-11" onClick={() => setSelected([])}>清空选择</Button>}</div>
        {picked.length ? <ScrollArea type="auto" className="min-h-0 flex-1"><div className="pb-2"><ItemGroup>{picked.map(({file, folder}) => <Item role="listitem" key={file.token} size="xs" className="items-start"><ItemContent className="min-w-0"><FileName name={file.name} format={nativeSources.get(file.type)?.format}/><ItemDescription wrap>{folder}</ItemDescription></ItemContent><ItemActions><Button variant="ghost" size="icon" className="size-11 shrink-0" aria-label={`取消选择 ${file.name}`} onClick={() => unselect(file.token)}><X/></Button></ItemActions></Item>)}</ItemGroup></div></ScrollArea> : <p className="px-2 pb-4 text-sm text-muted-foreground">尚未选择文件</p>}
      </section>
    </div>
    <DialogFooter className="m-0 flex-row items-center justify-between sm:justify-between"><span/><div className="flex gap-2"><Button variant="outline" className="min-h-11" onClick={onClose}>取消</Button><Button className="min-h-11" disabled={!picked.length} onClick={() => onAdd(picked.map(row => row.file))}>添加 {picked.length} 份文件</Button></div></DialogFooter>
  </DialogContent></Dialog>
}
