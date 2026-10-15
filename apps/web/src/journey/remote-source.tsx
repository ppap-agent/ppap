// Stitch: projects/13261964020862199279/screens/9b9779cdcfd848ee9da0538030cb7771.
import {useEffect, useRef, useState} from 'react'
import {Download, Maximize, Minimize, RotateCcw} from 'lucide-react'
import {originalUrl, request, type Material, type OfficePreviewResponse} from '../api'
import {Button} from '../components/ui/button'
import OfficePreview from '../OfficePreview'
import {message} from './remote'
import type {SourceFocus} from './source-documents'

const imageTypes: Record<string, string> = {png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp'}

function BrowserOriginal({file, roundId}: {file: Material; roundId?: string}) {
  const [url, setUrl] = useState(''), [text, setText] = useState(''), [error, setError] = useState('')
  const [truncated, setTruncated] = useState(false)
  const extension = file.original_filename.split('.').at(-1)?.toLowerCase() ?? ''
  const textFile = ['json', 'txt'].includes(extension)
  const mime = extension === 'pdf' ? 'application/pdf' : imageTypes[extension]
  useEffect(() => {
    const controller = new AbortController()
    let objectUrl = ''
    void fetch(originalUrl(file, roundId), {credentials: 'include', signal: controller.signal}).then(async response => {
      if (!response.ok) throw new Error('无法读取这份原件，请重新登录或稍后重试。')
      const blob = await response.blob()
      if (textFile) {
        const contents = new TextDecoder('utf-8', {fatal: true}).decode(await blob.slice(0, 1024 * 1024).arrayBuffer(), {stream: blob.size > 1024 * 1024})
        if (!controller.signal.aborted) {setText(contents); setTruncated(blob.size > 1024 * 1024)}
      }
      if (controller.signal.aborted) return
      // An uploaded filename must not cause HTML or scripts to run in an iframe.
      if (mime) objectUrl = URL.createObjectURL(blob.slice(0, blob.size, mime))
      setUrl(objectUrl || 'text')
    }).catch(cause => {if (!controller.signal.aborted) setError(message(cause))})
    return () => {controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl)}
  }, [file.file_id, file.file_version_id, roundId, mime, textFile])
  if (error) return <p role="alert" className="p-4 text-sm">{error}</p>
  if (!url) return <p role="status" className="p-4 text-sm text-muted-foreground">正在读取原件…</p>
  if (extension === 'pdf') return <iframe className="absolute inset-0 size-full border-0" src={url} title={file.original_filename + ' 原件'}/>
  if (imageTypes[extension]) return <div className="absolute inset-0 overflow-auto p-3"><img className="mx-auto max-w-full" src={url} alt={file.original_filename}/></div>
  return <div className="absolute inset-0 overflow-auto p-4"><pre className="whitespace-pre-wrap break-words text-sm">{text || '（空文件）'}</pre>{truncated && <p className="mt-3 text-sm text-muted-foreground">仅显示前 1 MB，请下载完整原件。</p>}</div>
}

export function RemoteSourcePane({file, roundId, focus}: {file: Material; roundId?: string; focus?: SourceFocus}) {
  const [office, setOffice] = useState<OfficePreviewResponse | null>(null), [officeFailed, setOfficeFailed] = useState(false), [attempt, setAttempt] = useState(0)
  const [fullscreen, setFullscreen] = useState(false), [canFullscreen, setCanFullscreen] = useState(false), [notice, setNotice] = useState('')
  const host = useRef<HTMLElement>(null)
  const extension = file.original_filename.split('.').at(-1)?.toLowerCase() ?? ''
  const browserOriginal = extension === 'pdf' || Boolean(imageTypes[extension]) || ['json', 'txt'].includes(extension)
  useEffect(() => {
    if (browserOriginal) return
    const controller = new AbortController()
    setOffice(null); setOfficeFailed(false)
    void request<OfficePreviewResponse>('/files/' + file.file_id + '/versions/' + file.file_version_id + '/office' + (roundId ? '?round_id=' + roundId : ''), 'GET', undefined, controller.signal)
      .then(value => {if (!controller.signal.aborted) setOffice(value)})
      .catch(() => {if (!controller.signal.aborted) setOfficeFailed(true)})
    return () => controller.abort()
  }, [file.file_id, file.file_version_id, roundId, attempt, browserOriginal])
  useEffect(() => {
    setCanFullscreen(document.fullscreenEnabled)
    const changed = () => setFullscreen(document.fullscreenElement === host.current)
    document.addEventListener('fullscreenchange', changed)
    return () => document.removeEventListener('fullscreenchange', changed)
  }, [])
  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement === host.current) await document.exitFullscreen()
      else await host.current?.requestFullscreen()
      setNotice('')
    } catch {setNotice('浏览器未允许全屏，请使用原件内的缩放工具。')}
  }
  return <section ref={host} aria-label="原件预览" className={['flex min-h-0 min-w-0 flex-1 flex-col gap-2 bg-background', fullscreen ? 'p-4' : ''].join(' ')}>
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
      <span className="text-sm text-muted-foreground">原件</span>
      <div className="flex flex-wrap items-center gap-1">
        {canFullscreen && <Button variant="ghost" size="sm" onClick={() => void toggleFullscreen()}>{fullscreen ? <Minimize/> : <Maximize/>}{fullscreen ? '退出全屏' : '全屏'}</Button>}
        <Button variant="ghost" size="icon-sm" aria-label="重新读取原件" onClick={() => setAttempt(value => value + 1)}><RotateCcw/></Button>
        <Button variant="outline" size="sm" asChild><a href={originalUrl(file, roundId)}><Download/>下载原件</a></Button>
      </div>
    </div>
    {focus?.location && focus.location !== '位置待核对' && <p className="shrink-0 break-words text-sm text-muted-foreground">核对位置：{focus.location}</p>}
    {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
    <div className="relative min-h-80 min-w-0 flex-1">
      {browserOriginal ? <BrowserOriginal key={attempt} file={file} roundId={roundId}/>
        : officeFailed || office && (!office.available || !office.script_url || !office.config) ? <div className="grid min-h-80 content-center justify-items-start gap-3 p-4 text-sm"><p role="alert">原件预览暂不可用</p><p className="text-muted-foreground">请重新读取，或下载原件核对。</p><Button variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}><RotateCcw/>重新读取</Button></div>
          : office?.script_url && office.config ? <OfficePreview key={attempt} scriptUrl={office.script_url} config={office.config} onFailed={() => setOfficeFailed(true)}/>
            : <p role="status" className="p-4 text-sm text-muted-foreground">正在连接原件预览…</p>}
    </div>
  </section>
}
