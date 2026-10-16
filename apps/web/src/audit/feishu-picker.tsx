// Stitch: projects/13261964020862199279/screens/44bcd2b3f57e49fe9007b1e485263936
import {useEffect, useRef, useState} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Input} from '../components/ui/input'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {Label} from '../components/ui/label'
import {FileName} from '../journey/file-name'

type DriveFile = components['schemas']['DriveFile']
export type DrivePickerSource = {list: (pageToken?: string) => Promise<components['schemas']['DriveFilesList']>}
const nativeSources = new Map<string, {format: 'excel' | 'word'; label: string}>([
  ['sheet', {format: 'excel', label: '飞书表格'}], ['bitable', {format: 'excel', label: '飞书多维表格'}],
  ['doc', {format: 'word', label: '飞书旧版文档'}], ['docx', {format: 'word', label: '飞书文档'}],
])
const supported = (file: DriveFile) => nativeSources.has(file.type) || file.type === 'file' && /\.(xlsx|xls|xlsm|doc|docx|pdf)$/i.test(file.name)

/** Lists and selects sources only. Adding a source does not assert a valid document. */
export function FeishuPicker({source, onAdd, onClose, trigger}: {source: DrivePickerSource; onAdd: (files: DriveFile[]) => void; onClose: () => void; trigger: HTMLButtonElement | null}) {
  const [files, setFiles] = useState<DriveFile[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [query, setQuery] = useState(''), [nextPage, setNextPage] = useState('')
  const [loading, setLoading] = useState(false), [error, setError] = useState('')
  const readEpoch = useRef(0)
  async function load(pageToken?: string) {
    const epoch = ++readEpoch.current
    setLoading(true); setError('')
    try {
      const result = await source.list(pageToken)
      if (readEpoch.current !== epoch) return
      setFiles(previous => {
        const merged = new Map((pageToken ? previous : []).map(file => [file.token, file]))
        result.files.forEach(file => merged.set(file.token, file))
        return [...merged.values()]
      })
      if (!pageToken) setSelected(previous => previous.filter(token => result.files.some(file => file.token === token)))
      setNextPage(result.next_page_token || '')
    } catch (caught) {
      if (readEpoch.current !== epoch) return
      setError(caught instanceof Error && caught.message === 'authorization_required' ? '需要飞书授权，尚未读取文件。已有文件不会丢失。' : '飞书文件读取失败，已有文件不会丢失。可以重新读取。')
    } finally {if (readEpoch.current === epoch) setLoading(false)}
  }
  useEffect(() => {void load(); return () => {readEpoch.current += 1}}, []) // Source is fixed for one dialog lifetime.
  const shown = files.filter(file => file.name.toLowerCase().includes(query.toLowerCase()))
  const picked = files.filter(file => selected.includes(file.token) && supported(file))
  return <Dialog open onOpenChange={open => {if (!open) onClose()}}><DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-xl" onCloseAutoFocus={event => {event.preventDefault(); (trigger?.isConnected ? trigger : document.getElementById('audit-main'))?.focus()}}>
    <DialogHeader><DialogTitle>从飞书选择文件</DialogTitle><DialogDescription>选择要审核的文件。</DialogDescription></DialogHeader>
    <Label htmlFor="drive-search" className="sr-only">搜索飞书文件名</Label><Input id="drive-search" placeholder="搜索已加载的文件名" value={query} onChange={event => setQuery(event.target.value)}/>
    {error && <Alert variant="destructive"><AlertTitle>暂时无法读取</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>}
    {loading && <p role="status" className="text-sm text-muted-foreground">正在读取飞书文件…</p>}
    <ItemGroup className="max-h-80 overflow-y-auto">{shown.map(file => <Item role="listitem" key={file.token} className="items-start"><Checkbox id={`drive-${file.token}`} className="mt-1" aria-label={`选择飞书文件 ${file.name}`} disabled={!supported(file)} checked={selected.includes(file.token)} onCheckedChange={checked => setSelected(tokens => checked === true ? [...tokens, file.token] : tokens.filter(token => token !== file.token))}/><ItemContent><Label htmlFor={`drive-${file.token}`} className="block"><FileName name={file.name} format={nativeSources.get(file.type)?.format}/></Label><p className="text-xs text-muted-foreground">{supported(file) ? (nativeSources.get(file.type) ? `${nativeSources.get(file.type)?.label} · 转为原件后仍需预检查` : '原件格式仍需预检查') : '此入口仅选择 Excel、Word、PDF 文件；格式不是预检查结论。'}</p></ItemContent></Item>)}</ItemGroup>
    {!loading && !shown.length && <p className="py-5 text-sm text-muted-foreground">{query ? '已加载文件中没有匹配项。' : error ? '尚未取得文件列表。' : '没有可选择的云文件。'}</p>}
    <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-muted-foreground">已加载 {files.length} 份 · 搜索只覆盖已加载文件</p><div className="flex gap-2">{error && <Button variant="outline" size="sm" disabled={loading} onClick={() => void load(nextPage || undefined)}>重新读取文件</Button>}{nextPage && <Button variant="outline" size="sm" disabled={loading} onClick={() => void load(nextPage)}>加载更多</Button>}</div></div>
    <DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button disabled={!picked.length || loading} onClick={() => onAdd(picked)}>添加所选文件（{picked.length}）</Button></DialogFooter>
  </DialogContent></Dialog>
}
