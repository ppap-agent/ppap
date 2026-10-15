import {lazy, Suspense, useEffect, useState} from 'react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {FileName} from '../journey/file-name'
import {readTemplateFile, type AuditTemplateAdapter, type TemplateItem} from './template-contract'

const WorkbookSource = lazy(() => import('../journey/workbook-source').then(module => ({default: module.WorkbookSource})))
const WordSource = lazy(() => import('../journey/word-source').then(module => ({default: module.WordSource})))
const PdfSource = lazy(() => import('../journey/pdf-source').then(module => ({default: module.PdfSource})))

/** The dialog owns one fixed version. Catalog updates cannot replace its content. */
export function TemplatePreview({item, adapter, onClose, trigger}: {item: TemplateItem; adapter: AuditTemplateAdapter; onClose: () => void; trigger: HTMLButtonElement | null}) {
  const [content, setContent] = useState<{blob: Blob; url: string} | null>(null), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  useEffect(() => {
    let current = true, url = ''
    void readTemplateFile(adapter, item, 'preview').then(blob => {if (current) {url = URL.createObjectURL(blob); setContent({blob, url}); setError('')}}).catch(() => {if (current) setError('当前模板版本无法读取。未改用其他模板；可重新读取或关闭后下载原文件。')})
    return () => {current = false; if (url) URL.revokeObjectURL(url)}
  }, [adapter, item, retry])
  const name = item.file.original_filename, extension = name.split('.').pop()?.toLowerCase()
  return <Dialog open onOpenChange={open => {if (!open) onClose()}}><DialogContent className="flex h-[88dvh] max-w-[calc(100%-2rem)] flex-col sm:max-w-5xl" onCloseAutoFocus={event => {event.preventDefault(); trigger?.focus()}}>
    <DialogHeader><DialogTitle>模板预览</DialogTitle><DialogDescription asChild><div className="space-y-1"><FileName name={name}/><p className="break-all text-xs">版本：{item.reference.file_version_id}</p></div></DialogDescription></DialogHeader>
    <div className="min-h-0 flex-1 overflow-auto border">{error ? <Alert variant="destructive"><AlertTitle>模板暂不可用</AlertTitle><AlertDescription>{error}<Button variant="outline" onClick={() => {setError(''); setRetry(value => value + 1)}}>重新读取模板</Button></AlertDescription></Alert> : !content ? <p role="status" className="p-4">正在读取固定模板版本…</p> : <Suspense fallback={<p role="status">正在加载文件阅读器…</p>}>{extension === 'pdf' ? <PdfSource url={content.url} name={name}/> : ['xlsx', 'xls', 'xlsm'].includes(extension || '') ? <WorkbookSource blob={content.blob} name={name}/> : extension === 'docx' ? <WordSource blob={content.blob} name={name}/> : <p className="p-4">此格式暂不支持内嵌预览，请关闭后下载原文件。</p>}</Suspense>}</div>
    <Button variant="outline" className="self-end" onClick={onClose}>关闭</Button>
  </DialogContent></Dialog>
}
