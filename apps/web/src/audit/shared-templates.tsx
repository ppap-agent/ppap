import {useEffect, useMemo, useRef, useState} from 'react'
import {request} from '../api'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {FileName} from '../journey/file-name'
import {sharedTemplateAdapter} from './native-adapter'
import {readTemplateFile, type TemplateItem} from './template-contract'
import {TemplatePreview} from './template-preview'
import {validTemplateShareScope, type TemplateShareScope} from './template-sharing-contract'

export function SharedTemplates({requestId}: {requestId: string}) {
  const [scope, setScope] = useState<TemplateShareScope | null>(null), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [preview, setPreview] = useState<TemplateItem | null>(null), [downloading, setDownloading] = useState(false), [downloadError, setDownloadError] = useState('')
  const trigger = useRef<HTMLButtonElement | null>(null), epoch = useRef(0), busy = useRef(false)
  const adapter = useMemo(() => sharedTemplateAdapter(requestId), [requestId])
  useEffect(() => {
    let active = true
    epoch.current++; busy.current = false; setScope(null); setPreview(null); setError(''); setDownloadError(''); setDownloading(false)
    void request<TemplateShareScope>(`/audit-template-shares/${encodeURIComponent(requestId)}/templates`).then(value => {
      if (!validTemplateShareScope(value, {...value, folder_template_count: value.templates.length})) throw new Error('invalid_template_scope')
      if (active) setScope(value)
    }).catch(() => {if (active) setError('无法读取这份分享。请使用有原文件权限的飞书账号登录，或联系发送人。')})
    return () => {active = false; epoch.current++}
  }, [requestId, revision])
  async function download(item: TemplateItem) {
    if (busy.current) return
    const run = epoch.current
    busy.current = true; setDownloading(true); setDownloadError('')
    try {
      const blob = await readTemplateFile(adapter, item, 'download')
      if (epoch.current !== run) return
      const url = URL.createObjectURL(blob), link = document.createElement('a')
      link.href = url; link.download = item.file.original_filename; link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {if (epoch.current === run) setDownloadError('下载未完成，请核对当前飞书文件权限后重试。')}
    finally {if (epoch.current === run) {busy.current = false; setDownloading(false)}}
  }
  return <main className="mx-auto max-w-7xl space-y-6 p-4 md:p-8">
    <header className="flex flex-wrap items-center justify-between gap-4"><div className="space-y-2"><h1 className="text-2xl font-semibold">客户模板</h1>{scope && <p className="text-muted-foreground">{scope.customer_name} · {scope.templates.length} 份固定版本模板</p>}</div><Button variant="outline" asChild><a href="/audit">返回审核工作台</a></Button></header>
    {error ? <Alert variant="destructive"><AlertTitle>分享暂不可用</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => setRevision(value => value + 1)}>重新读取</Button></AlertDescription></Alert> : !scope ? <p role="status">正在读取分享模板…</p> : <>
      {downloadError && <p role="alert">{downloadError}</p>}
      <Table aria-label="分享的客户模板"><TableHeader><TableRow><TableHead>模板文件</TableHead><TableHead className="max-md:hidden">文件类型</TableHead><TableHead className="text-right">操作</TableHead></TableRow></TableHeader><TableBody>{scope.templates.map(item => <TableRow key={item.reference.template_id}>
        <TableCell className="whitespace-normal"><FileName name={item.file.original_filename}/><p className="text-xs text-muted-foreground md:hidden">{item.document_type}</p>{item.download_access.state !== 'allowed' && <p className="mt-1 text-xs text-muted-foreground">{item.download_access.reason || '当前账号暂不能下载。'}</p>}</TableCell><TableCell className="max-md:hidden">{item.document_type}</TableCell>
        <TableCell><div className="flex justify-end gap-1 max-sm:flex-col"><Button size="sm" variant="ghost" disabled={item.preview_access.state !== 'allowed'} onClick={event => {trigger.current = event.currentTarget; setPreview(structuredClone(item))}}>预览</Button><Button size="sm" variant="ghost" disabled={downloading || item.download_access.state !== 'allowed'} onClick={() => void download(item)}>下载</Button></div></TableCell>
      </TableRow>)}</TableBody></Table>
    </>}
    {preview && <TemplatePreview item={preview} adapter={adapter} onClose={() => setPreview(null)} trigger={trigger.current}/>}
  </main>
}
