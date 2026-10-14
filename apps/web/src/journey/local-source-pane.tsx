// Stitch: projects/13261964020862199279/screens/65ba88c68ccd47a6b5d40ac512f508c2.
import {lazy, Suspense, useEffect, useRef, useState} from 'react'
import {BookOpen, Download, RotateCcw, Upload} from 'lucide-react'
import {Button} from '../components/ui/button'
import {fileError, readLocalFile, restoreLocalFile, type LocalFileRef} from './local-files'
import type {SourceFile} from './source-documents'
const PdfSource = lazy(() => import('./pdf-source').then(module => ({default: module.PdfSource})))
const WorkbookSource = lazy(() => import('./workbook-source').then(module => ({default: module.WorkbookSource})))
const WordSource = lazy(() => import('./word-source').then(module => ({default: module.WordSource})))

export function LocalSourcePane({file, side}: {file: SourceFile & {localFile: LocalFileRef}; side: string}) {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<{kind: 'loading' | 'missing' | 'failed' | 'ready'; url?: string; blob?: Blob; text?: string; message?: string}>({kind: 'loading'})
  const [notice, setNotice] = useState(''), [restoring, setRestoring] = useState(false), [renderFailed, setRenderFailed] = useState(false)
  const picker = useRef<HTMLInputElement>(null), alive = useRef(true)
  const extension = file.name.split('.').at(-1)?.toLowerCase() ?? ''
  const mime = ({pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', txt: 'text/plain', csv: 'text/plain'} as Record<string, string>)[extension]
  const textFile = extension === 'txt' || extension === 'csv'
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  useEffect(() => {
    let current = true, url = ''
    setState({kind: 'loading'}); setRenderFailed(false)
    void readLocalFile(file.localFile).then(async blob => {
      if (!blob) {if (current) setState({kind: 'missing'}); return}
      // Never trust a filename/MIME to embed HTML or scripts. Only explicit passive readers.
      let text: string | undefined
      if (textFile) {
        try {text = new TextDecoder('utf-8', {fatal: true}).decode(await blob.slice(0, 1024 * 1024).arrayBuffer(), {stream: blob.size > 1024 * 1024})}
        catch { /* Non-UTF-8 text remains downloadable as the unchanged original. */ }
      }
      if (!current) return
      url = URL.createObjectURL(blob.slice(0, blob.size, mime ?? 'application/octet-stream'))
      setState({kind: 'ready', url, blob, text})
    }).catch(cause => {if (current) setState({kind: 'failed', message: fileError(cause)})})
    return () => {current = false; if (url) URL.revokeObjectURL(url)}
  }, [file.localFile.id, file.localFile.digest, attempt, mime, textFile])
  async function restore(chosen?: File) {
    if (!chosen || restoring) return
    setRestoring(true); setNotice('')
    try {await restoreLocalFile(file.localFile, chosen); if (alive.current) {setAttempt(value => value + 1); setNotice('已找回同一份原件，审核判断没有改变。')}}
    catch (cause) {if (alive.current) setNotice(fileError(cause))}
    finally {if (alive.current) setRestoring(false)}
  }
  function download() {
    if (!state.url) return
    const a = document.createElement('a'); a.href = state.url; a.download = file.name; a.click()
    setNotice('已请求下载原件，请在浏览器下载列表确认。')
  }
  return <Suspense fallback={<p role="status">正在准备原件阅读器…</p>}><section className={"flex flex-col min-h-0 flex-1 bg-background border [&_>_[hidden]]:hidden [&_>_footer]:flex [&_>_footer]:shrink-0 [&_>_footer]:flex-wrap [&_>_footer]:items-center [&_>_footer]:gap-2.5 [&_>_footer]:py-2.5 [&_>_footer]:px-3 [&_>_footer]:text-xs [&_>_footer]:text-muted-foreground [&_>_footer]:border-t [&_>_footer_>_span]:flex-1"} aria-label={`${side}阅读区`}>
    <div className={"flex shrink-0 flex-wrap gap-1 py-1 px-2 border-b max-md:gap-0"} role="toolbar" aria-label={`${side}原件工具`}><Button variant="ghost" size="sm" disabled={state.kind !== 'ready'} onClick={download}><Download/>下载原件</Button><Button variant="ghost" size="sm" disabled={state.kind === 'loading' || restoring} onClick={() => setAttempt(value => value + 1)}><RotateCcw/>重新读取</Button></div>
    {state.kind !== 'ready' ? <div className={"flex-1 min-h-45 overflow-auto p-5.5 bg-background max-lg:p-3 max-md:min-h-0 max-md:p-2"}><div className={"min-h-55 grid justify-items-start gap-3.5 max-w-115 m-auto [&_h3]:text-lg [&_h3]:font-semibold [&_p]:leading-relaxed [&_p]:text-muted-foreground"}><BookOpen/><h3>{state.kind === 'loading' ? '正在读取原件…' : state.kind === 'missing' ? '此设备缺少原件' : '原件读取失败'}</h3><p>{state.kind === 'loading' ? '正在核对本机文件内容。' : state.message ?? '登记记录仍在。重新选择当时的文件即可恢复；文件名相同不代表内容相同。'}</p>{state.kind !== 'loading' && <Button variant="outline" disabled={restoring} onClick={() => picker.current?.click()}><Upload/>{restoring ? '正在核对内容…' : '重新选择当时原件'}</Button>}</div></div>
      : <div className={"flex-1 min-h-45 overflow-auto p-5.5 bg-background max-lg:p-3 max-md:min-h-0 max-md:p-2 [&_>_img]:block [&_>_img]:max-w-full [&_>_img]:h-auto [&_>_img]:m-auto"}>
        {extension === 'pdf' ? <PdfSource key={state.url} url={state.url!} name={file.name}/>
          : ['xlsx', 'xlsm', 'xls'].includes(extension) ? <WorkbookSource key={state.url} blob={state.blob!} name={file.name}/>
          : extension === 'docx' ? <WordSource key={state.url} blob={state.blob!} name={file.name}/>
          : mime?.startsWith('image/') && !renderFailed ? <img src={state.url} alt={`${file.name} 上传原件`} onError={() => setRenderFailed(true)}/>
            : textFile && state.text !== undefined ? <div className={"p-6"}><pre>{state.text || '（空文件）'}</pre>{file.localFile.size > 1024 * 1024 && <p>仅显示前 1 MB；请下载查看完整文本。</p>}</div>
              : <div className={"min-h-55 grid justify-items-start gap-3.5 max-w-115 m-auto [&_h3]:text-lg [&_h3]:font-semibold [&_p]:leading-relaxed [&_p]:text-muted-foreground"}><BookOpen/><h3>{renderFailed ? '图片无法显示' : '使用本机应用查看'}</h3><p>{renderFailed ? '文件内容已保存，但浏览器无法解码此图片。' : textFile ? '文本不是 UTF-8 编码，未显示可能错误的文字。请下载原件，用本机应用查看。' : '此格式暂不支持页面内阅读，包括旧版 .doc。下载原件核对，或补充 PDF / DOCX 版本后继续。'}</p><Button variant="outline" onClick={download}><Download/>下载原件</Button></div>}
      </div>}
    <input ref={picker} type="file" className={"sr-only"} aria-label={`${side}补回原件`} onChange={event => {void restore(event.target.files?.[0]); event.target.value = ''}}/>
    <footer><span role="status">{notice || (state.kind === 'ready' ? `${(file.localFile.size / 1024).toLocaleString('zh-CN', {maximumFractionDigits: 1})} KB · 本机原件` : '')}</span></footer>
  </section></Suspense>
}
