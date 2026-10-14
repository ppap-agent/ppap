// Reading layout: Stitch 13261964020862199279 / 0074e1678f934db6baac07d7282af1e5.
import {lazy, Suspense, useEffect, useState} from 'react'
import {Download, RotateCcw} from 'lucide-react'
import {originalUrl, request, type Material, type OfficePreviewResponse, type PreviewResponse} from '../api'
import {Button} from '../components/ui/button'
import {Table, TableBody, TableCell, TableRow} from '../components/ui/table'
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../components/ui/tabs'
import OfficePreview from '../OfficePreview'
import {message, useRemote} from './remote'

const WorkbookSource = lazy(() => import('../journey/workbook-source').then(module => ({default: module.WorkbookSource})))
const WordSource = lazy(() => import('../journey/word-source').then(module => ({default: module.WordSource})))

function Contents({file, roundId}: {file: Material; roundId?: string}) {
  const [blob, setBlob] = useState<Blob | null>(null), [url, setUrl] = useState(''), [error, setError] = useState('')
  const [text, setText] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    let objectUrl = ''
    void fetch(originalUrl(file, roundId), {credentials: 'include', signal: controller.signal}).then(async response => {
      if (!response.ok) throw new Error('无法读取这份原件，请重新登录或稍后重试。')
      const value = await response.blob()
      if (/\.(json|txt|csv)$/i.test(file.original_filename)) {
        const contents = new TextDecoder('utf-8', {fatal: true}).decode(await value.slice(0, 1024 * 1024).arrayBuffer())
        if (!controller.signal.aborted) setText(contents)
      }
      if (controller.signal.aborted) return
      objectUrl = URL.createObjectURL(value); setBlob(value); setUrl(objectUrl)
    }).catch(cause => {if (!controller.signal.aborted) setError(message(cause))})
    return () => {controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl)}
  }, [file.file_version_id, file.file_id, roundId])
  const extension = file.original_filename.split('.').at(-1)?.toLowerCase()
  const excerpt = useRemote<PreviewResponse>(roundId ? `/rounds/${roundId}/files/${file.file_id}/versions/${file.file_version_id}/preview` : null)
  if (error) return <p role="alert">{error}</p>
  if (!blob) return <p role="status">正在读取原件…</p>
  return <Suspense fallback={<p role="status">正在准备内容阅读器…</p>}>
    {['xls', 'xlsx', 'xlsm'].includes(extension || '') ? <WorkbookSource blob={blob} name={file.original_filename}/>
      : extension === 'docx' ? <WordSource blob={blob} name={file.original_filename}/>
        : extension === 'pdf' ? <iframe className={"h-full min-h-96 w-full border-0"} src={url} title={`${file.original_filename} 原件`} />
          : ['png', 'jpg', 'jpeg', 'webp'].includes(extension || '') ? <img className={"max-h-full max-w-full object-contain"} src={url} alt={file.original_filename}/>
            : ['json', 'txt', 'csv'].includes(extension || '') ? <pre className="whitespace-pre-wrap break-words p-4 text-sm">{text || '（空文件）'}{blob.size > 1024 * 1024 && '\n（仅显示前 1 MB，请下载完整文件）'}</pre>
            : <><p className={"text-sm text-muted-foreground"}>可下载原件，用本机应用核对。</p>{excerpt.value?.rows?.length ? <Table><TableBody>{excerpt.value.rows.map((row, index) => <TableRow key={index}><TableCell>{row.number}</TableCell><TableCell>{row.text}</TableCell></TableRow>)}</TableBody></Table> : <pre className={"whitespace-pre-wrap text-sm"}>{excerpt.value?.text || excerpt.value?.notice || ''}</pre>}</>}
  </Suspense>
}

export function RemoteSourcePane({file, roundId}: {file: Material; roundId?: string}) {
  const [office, setOffice] = useState<OfficePreviewResponse | null>(null), [officeFailed, setOfficeFailed] = useState(false), [mode, setMode] = useState('layout'), [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setOffice(null); setOfficeFailed(false)
    void request<OfficePreviewResponse>(`/files/${file.file_id}/versions/${file.file_version_id}/office${roundId ? `?round_id=${roundId}` : ''}`, 'GET', undefined, controller.signal).then(value => {if (!controller.signal.aborted) {setOffice(value); if (!value.available) setMode('contents')}}).catch(() => {if (!controller.signal.aborted) {setOfficeFailed(true); setMode('contents')}})
    return () => controller.abort()
  }, [file.file_id, file.file_version_id, roundId, attempt])
  return <section className={"flex min-h-96 min-w-0 flex-1 flex-col"}>
    <Tabs value={mode} onValueChange={setMode} className={"flex min-h-0 flex-1 flex-col"}>
      <div className={"flex flex-wrap items-center justify-between gap-2"}><TabsList><TabsTrigger value="layout" disabled={!office?.available || officeFailed}>排版原件</TabsTrigger><TabsTrigger value="contents">内容阅读</TabsTrigger></TabsList><div className={"flex gap-2"}><Button variant="ghost" size="icon" aria-label="重新读取原件" onClick={() => setAttempt(value => value + 1)}><RotateCcw/></Button><Button variant="outline" size="sm" asChild><a href={originalUrl(file, roundId)}><Download/>下载原件</a></Button></div></div>
      {officeFailed && <p role="status" className={"text-sm text-muted-foreground"}>排版服务暂不可用，可继续阅读内容或下载原件。</p>}
      <TabsContent value="layout" className={"relative min-h-0 flex-1"}>{office?.available && office.script_url && office.config ? <OfficePreview key={attempt} scriptUrl={office.script_url} config={office.config} onFailed={() => {setOfficeFailed(true); setMode('contents')}}/> : <p role="status">正在连接排版服务…</p>}</TabsContent>
      <TabsContent value="contents" className={"min-h-0 flex-1 overflow-auto"}><Contents key={attempt} file={file} roundId={roundId}/></TabsContent>
    </Tabs>
  </section>
}
