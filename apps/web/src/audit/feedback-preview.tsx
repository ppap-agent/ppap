import {lazy, Suspense, useEffect, useState} from 'react'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {FileName} from '../journey/file-name'
import {readFeedbackAttachment, type AuditFeedbackAdapter, type FeedbackFile} from './feedback-contract'

const WorkbookSource = lazy(() => import('../journey/workbook-source').then(module => ({default: module.WorkbookSource})))
const WordSource = lazy(() => import('../journey/word-source').then(module => ({default: module.WordSource})))

export function FeedbackPreview({file, adapter, onClose, trigger}: {file: FeedbackFile; adapter: AuditFeedbackAdapter; onClose: () => void; trigger: HTMLButtonElement | null}) {
  const extension = file.original_filename.split('.').pop()?.toLowerCase()
  const supported = ['xlsx', 'xls', 'xlsm', 'docx'].includes(extension || '') || ['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp'].includes(file.content_type)
  const [content, setContent] = useState<{blob: Blob; url: string} | null>(null), [error, setError] = useState(''), [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!supported) return
    let current = true, url = ''
    void readFeedbackAttachment(adapter, file).then(blob => {
      if (current) {url = URL.createObjectURL(blob); setContent({blob, url})}
    }).catch(() => {if (current) setError('附件暂时无法查看。未替换文件，关闭后仍可下载原附件。')})
    return () => {current = false; if (url) URL.revokeObjectURL(url)}
  }, [adapter, file, supported, retry])
  return <Dialog open onOpenChange={value => {if (!value) onClose()}}><DialogContent className="flex h-[80dvh] flex-col sm:max-w-4xl" onCloseAutoFocus={event => {event.preventDefault(); if (trigger?.isConnected) trigger.focus()}}><DialogHeader><DialogTitle>附件查看</DialogTitle><DialogDescription asChild><div className="space-y-1"><FileName name={file.original_filename}/><p className="break-all text-xs">版本：{file.id}</p></div></DialogDescription></DialogHeader>
    <div className="min-h-0 flex-1 overflow-auto">{!supported ? <p className="text-sm">{extension === 'zip' ? '压缩包不在浏览器内解压。请关闭后下载原附件。' : '此格式暂不能内嵌查看，请关闭后下载原附件。'}</p> : error ? <div className="space-y-3"><p role="alert">{error}</p><Button variant="outline" onClick={() => {setContent(null); setError(''); setRetry(value => value + 1)}}>重新读取附件</Button></div> : !content ? <p role="status">正在读取固定附件版本…</p> : <Suspense fallback={<p role="status">正在加载文件阅读器…</p>}>{extension === 'docx' ? <WordSource blob={content.blob} name={file.original_filename}/> : ['xlsx', 'xls', 'xlsm'].includes(extension || '') ? <WorkbookSource blob={content.blob} name={file.original_filename}/> : <img className="max-w-full object-contain" src={content.url} alt={file.original_filename} onError={() => setError('图片内容不能显示。请关闭后下载原附件核对。')}/>}</Suspense>}</div>
    <Button variant="outline" className="self-end" onClick={onClose}>关闭附件</Button>
  </DialogContent></Dialog>
}
