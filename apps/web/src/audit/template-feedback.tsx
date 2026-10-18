// Stitch: projects/13261964020862199279/screens/3976a221e25c4216a89f46d42d69e065
import {useContext, useEffect, useRef, useState} from 'react'
import {Check, Download, Eye, Plus, X} from 'lucide-react'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Item, ItemContent, ItemGroup} from '../components/ui/item'
import {FileName} from '../journey/file-name'
import {feedbackFileAccept, readFeedbackAttachment, validFeedbackReceipt, validFeedbackHistory, type AuditFeedbackAdapter, type FeedbackFile, type FeedbackReceipt, type FeedbackHistory, type FeedbackEntry} from './feedback-contract'
import {canResumeUpload, canRetryUpload, uploadName, useUploads, type UploadRow} from './uploads'
import {FeedbackPreview} from './feedback-preview'
import {SupportEntryContext} from './support'

type Submission = {receipt: FeedbackReceipt; files: FeedbackFile[]; busy: boolean}
const ready = (row: UploadRow) => row.receipt.state === 'ready' && row.receipt.file?.status === 'ready' && row.receipt.file.byte_size > 0
const statusLabel = {uploading: '上传中', ready: '已上传', failed: '上传失败', unknown: '上传结果未知'}
const feedbackLabels = {pending: '正在核对提交回执', unknown: '提交结果未知', failed: '提报未完成', rejected: '材料未受理', received: '材料已收到'}

/** Mounted by the page, not by the portal: closing keeps uploads and the original attempt. */
export function TemplateFeedback({open, onClose, adapter, trigger}: {open: boolean; onClose: () => void; adapter?: AuditFeedbackAdapter; trigger: HTMLElement | null}) {
  const openSupport = useContext(SupportEntryContext)
  const input = useRef<HTMLInputElement>(null), alive = useRef(true), current = useRef<Submission | null>(null), transfer = useRef(0)
  const [job, setJob] = useState<Submission | null>(null), [discard, setDiscard] = useState(false)
  const [notice, setNotice] = useState(''), [downloading, setDownloading] = useState('')
  const [preview, setPreview] = useState<FeedbackFile | null>(null)
  const [history, setHistory] = useState<FeedbackHistory | null>(null), [historyPage, setHistoryPage] = useState(1), [historyRefresh, setHistoryRefresh] = useState(0)
  const [historyLoading, setHistoryLoading] = useState(false), [historyError, setHistoryError] = useState('')
  const previewTrigger = useRef<HTMLButtonElement | null>(null)
  const unavailable: AuditFeedbackAdapter = {upload: async () => {throw new Error('feedback_unavailable')}, readUpload: async () => {throw new Error('feedback_unavailable')}, readAttachment: async () => {throw new Error('feedback_unavailable')}}
  const uploads = useUploads(adapter || unavailable, () => {})
  useEffect(() => {alive.current = true; return () => {alive.current = false; transfer.current += 1}}, [])
  useEffect(() => {
    if (open) return
    setDiscard(false)
    setPreview(null)
    // Stop trusting this observation, not the remote operation. Read the same identity next.
    const previous = current.current
    if (previous?.busy) save({...previous, busy: false, receipt: {...previous.receipt, state: 'unknown', retry_allowed: false, reason: '关闭时尚未取得可靠回执。请读取原提交结果，不要重新提交。'}})
    if (uploads.rows.some(row => row.busy)) uploads.restore(uploads.snapshot())
  }, [open])
  const locked = Boolean(job && job.receipt.state !== 'rejected')
  const rows = uploads.rows.filter(row => !row.removed)
  const files = rows.filter(ready).map(row => row.receipt.file!)
  const showHistory = Boolean(open && adapter?.listHistory && !job && !rows.length)
  useEffect(() => {
    if (!showHistory || !adapter?.listHistory) {setHistoryLoading(false); return}
    let cancelled = false
    setHistoryLoading(true); setHistoryError('')
    void adapter.listHistory(historyPage).then(update => {
      if (cancelled) return
      if (!validFeedbackHistory(update, historyPage)) throw new Error('feedback_history_mismatch')
      setHistory(structuredClone(update))
    }).catch(() => {if (!cancelled) setHistoryError('暂时无法读取提报记录，请重试。')}).finally(() => {if (!cancelled) setHistoryLoading(false)})
    return () => {cancelled = true}
  }, [showHistory, adapter, historyPage, historyRefresh])
  const blocker = !adapter ? '暂时无法提交材料，请稍后再试。' : !adapter.submit || !adapter.read ? '运营提交暂不可用，附件尚未提交给运营。' : !rows.length ? '请先上传至少 1 份附件。' : rows.some(row => row.busy || !ready(row)) ? '请先完成所有附件上传，或移除不能提交的附件。' : new Set(files.map(file => file.id)).size !== files.length ? '附件版本重复，请移除重复项。' : ''
  function save(next: Submission | null) {current.current = next; if (alive.current) setJob(next)}
  async function observe(observation: Submission, readOnly: boolean) {
    if (!adapter?.submit || !adapter.read) return
    save(observation); setNotice('')
    const {request, attempt_id} = observation.receipt
    try {
      const update = await (readOnly ? adapter.read(structuredClone(request), attempt_id) : adapter.submit(structuredClone(request), attempt_id))
      if (!alive.current || current.current !== observation) return
      if (!validFeedbackReceipt(update, request, attempt_id)) throw new Error('feedback_receipt_mismatch')
      save({...observation, busy: false, receipt: {...update, retry_allowed: update.state === 'failed' && update.retry_allowed}})
    } catch {
      if (alive.current && current.current === observation) save({...observation, busy: false, receipt: {...observation.receipt, state: 'unknown', retry_allowed: false, reason: '未取得可靠提交回执。材料可能已收到；请读取原提交结果，不要再次提交。'}})
    }
  }
  function submit() {
    if (blocker || current.current && current.current.receipt.state !== 'rejected') return
    const receipt: FeedbackReceipt = {request: {request_id: crypto.randomUUID(), file_version_ids: files.map(file => file.id)}, attempt_id: crypto.randomUUID(), state: 'pending', submission_id: null, received_file_version_ids: [], received_at: null, retry_allowed: false, reason: null}
    void observe({receipt, files: structuredClone(files), busy: true}, false)
  }
  function read() {const previous = current.current; if (previous && !previous.busy && ['pending', 'unknown'].includes(previous.receipt.state)) void observe({...previous, busy: true}, true)}
  function retry() {
    const previous = current.current
    if (previous && !previous.busy && previous.receipt.state === 'failed' && previous.receipt.retry_allowed) void observe({...previous, busy: true, receipt: {...previous.receipt, attempt_id: crypto.randomUUID(), state: 'pending', retry_allowed: false, reason: null}}, false)
  }
  function reset() {save(null); uploads.reset(); transfer.current += 1; setDownloading(''); setNotice(''); setDiscard(false); setPreview(null)}
  function restore(entry: FeedbackEntry) {
    if (!showHistory || historyLoading || !entry.files_available || !adapter?.read || current.current || rows.length) return
    void observe({receipt: structuredClone(entry.receipt), files: structuredClone(entry.files), busy: true}, true)
  }
  async function download(file: FeedbackFile) {
    if (!adapter || downloading) return
    const run = ++transfer.current
    setDownloading(file.id); setNotice('')
    try {
      const blob = await readFeedbackAttachment(adapter, file)
      if (!alive.current || transfer.current !== run) return
      const url = URL.createObjectURL(blob), link = document.createElement('a')
      link.href = url; link.download = file.original_filename; link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {if (alive.current && transfer.current === run) setNotice('附件暂时无法下载，请稍后重试。')}
    finally {if (alive.current && transfer.current === run) setDownloading('')}
  }
  const receipt = job?.receipt
  function contactSupport() {
    if (!receipt || !job || !openSupport) return
    openSupport({scenario: '补充模板提报', facts: [
      {label: '状态', value: feedbackLabels[receipt.state]},
      {label: '原请求', value: receipt.request.request_id}, {label: '本次尝试', value: receipt.attempt_id},
      {label: '原因', value: receipt.reason || '服务未提供原因。'},
      {label: '固定附件', value: job.files.map(file => `${file.original_filename}\n文件版本：${file.id}`).join('\n\n')},
    ]})
  }
  return <Dialog open={open} onOpenChange={value => {if (!value) onClose()}}><DialogContent className="max-h-[88dvh] overflow-y-auto sm:max-w-2xl" onCloseAutoFocus={event => {event.preventDefault(); (trigger?.isConnected && trigger.getClientRects().length ? trigger : document.getElementById('audit-main'))?.focus()}}>
    <DialogHeader><DialogTitle>补充模板</DialogTitle><DialogDescription>提交模板或说明材料，供运营核对。</DialogDescription></DialogHeader>
    {receipt?.state === 'received' ? <section className="space-y-4"><Check className="size-6"/><h3 className="text-lg font-semibold">已收到 {receipt.received_file_version_ids.length} 份材料</h3><p>材料待处理，不代表审核规则已生效。</p><p className="break-all text-xs text-muted-foreground">提报编号：{receipt.submission_id}</p><ItemGroup aria-label="已收到的模板附件">{job?.files.map(file => <Item role="listitem" size="sm" key={file.id}><ItemContent className="min-w-0"><FileName name={file.original_filename}/></ItemContent><Button variant="ghost" size="icon" aria-label={`下载 ${file.original_filename}`} disabled={Boolean(downloading)} onClick={() => void download(file)}><Download/></Button></Item>)}</ItemGroup><Button variant="outline" onClick={reset}>继续补充模板</Button></section> : <>
      <input ref={input} className="sr-only" type="file" multiple accept={feedbackFileAccept} aria-label="选择模板附件" disabled={locked || !adapter} onChange={event => {if (!locked) uploads.add(Array.from(event.target.files || []).map(file => ({kind: 'local', file}))); event.target.value = ''}}/>
      <div className="space-y-2"><Button variant="outline" disabled={locked || !adapter} onClick={() => input.current?.click()}><Plus/>{rows.length ? '继续添加附件' : '选择附件'}</Button><p className="text-xs text-muted-foreground">ZIP、Excel、Word、图片 · 单份最大 200 MB。不要提交密码或敏感凭据。</p></div>
      <ItemGroup aria-label="模板附件">{rows.map(row => <Item role="listitem" size="sm" key={row.id}><ItemContent className="min-w-0"><FileName name={uploadName(row.input)}/><p className="text-xs text-muted-foreground">{canResumeUpload(row) ? '待继续传输' : statusLabel[row.receipt.state]}{row.receipt.state === 'ready' && !ready(row) ? ' · 附件状态不完整，不能提交' : ''}</p>{row.receipt.reason && <p className="text-xs text-muted-foreground">{row.receipt.reason}</p>}</ItemContent><div className="flex flex-wrap gap-1 max-sm:w-full max-sm:justify-end">{ready(row) && <Button variant="ghost" size="icon" aria-label={`查看 ${uploadName(row.input)}`} onClick={event => {previewTrigger.current = event.currentTarget; setPreview(structuredClone(row.receipt.file!))}}><Eye/></Button>}{ready(row) && <Button variant="ghost" size="icon" aria-label={`下载 ${uploadName(row.input)}`} disabled={Boolean(downloading)} onClick={() => void download(row.receipt.file!)}><Download/></Button>}{!row.busy && ['uploading', 'unknown'].includes(row.receipt.state) && <Button variant="outline" size="sm" disabled={locked} onClick={() => uploads.read(row.id)}>读取上传结果</Button>}{canResumeUpload(row) && <Button variant="outline" size="sm" disabled={locked} onClick={() => uploads.resume(row.id)}>继续传输</Button>}{canRetryUpload(row) && <Button variant="outline" size="sm" disabled={locked} onClick={() => uploads.retry(row.id)}>重试上传</Button>}<Button variant="ghost" size="icon" aria-label={`移除 ${uploadName(row.input)}`} disabled={locked} onClick={() => uploads.remove(row.id)}><X/></Button></div></Item>)}</ItemGroup>
      {job && !rows.length && <ItemGroup aria-label="原提报附件">{job.files.map(file => <Item role="listitem" size="sm" key={file.id}><ItemContent className="min-w-0"><FileName name={file.original_filename}/></ItemContent><Button variant="ghost" size="icon" aria-label={`查看 ${file.original_filename}`} onClick={event => {previewTrigger.current = event.currentTarget; setPreview(structuredClone(file))}}><Eye/></Button><Button variant="ghost" size="icon" aria-label={`下载 ${file.original_filename}`} disabled={Boolean(downloading)} onClick={() => void download(file)}><Download/></Button></Item>)}</ItemGroup>}
      {receipt && <Alert data-request-id={receipt.request.request_id} data-attempt-id={receipt.attempt_id}><AlertTitle>{feedbackLabels[receipt.state]}</AlertTitle><AlertDescription><p>{receipt.reason || (receipt.state === 'pending' ? '请等待原提交回执，不要重复提交。' : '附件仍保留在本页。')}</p>{['pending', 'unknown'].includes(receipt.state) && <Button variant="outline" disabled={job?.busy} onClick={read}>{job?.busy ? '正在等待回执…' : '读取原提交结果'}</Button>}{receipt.state === 'failed' && receipt.retry_allowed && <Button variant="outline" disabled={job?.busy} onClick={retry}>安全重试提交</Button>}{receipt.state === 'failed' && !receipt.retry_allowed && <p>暂不能重试，请联系运营核对。</p>}{openSupport && <Button variant="outline" onClick={contactSupport}>联系支持</Button>}</AlertDescription></Alert>}
      {!locked && <div className="space-y-2"><p role="status" className="text-sm">{blocker || `待提交 ${files.length} 份附件`}</p><div className="flex flex-wrap gap-2"><Button disabled={Boolean(blocker)} onClick={submit}>提交材料</Button>{rows.length > 0 && <Button variant="ghost" onClick={() => setDiscard(true)}>放弃材料</Button>}</div></div>}
    </>}
    {showHistory && <section aria-label="提报记录" className="space-y-3">
      <h3 className="font-medium">最近提报</h3>
      {historyLoading && <p role="status" className="text-sm text-muted-foreground">正在读取提报记录…</p>}
      {historyError && <Alert><AlertDescription>{historyError}<Button variant="outline" onClick={() => setHistoryRefresh(value => value + 1)}>重新读取提报记录</Button></AlertDescription></Alert>}
      {history && <><ItemGroup>{history.entries.map(entry => <Item role="listitem" size="sm" key={entry.receipt.request.request_id}><ItemContent className="min-w-0"><div className="space-y-1">{entry.files.map(file => <FileName key={file.id} name={file.original_filename}/>)}</div><p className="text-xs text-muted-foreground">{feedbackLabels[entry.receipt.state]} · {new Date(entry.created_at).toLocaleString('zh-CN')}</p>{entry.reason && <p className="text-xs text-muted-foreground">{entry.reason}</p>}</ItemContent><Button variant="outline" size="sm" disabled={historyLoading || !entry.files_available} onClick={() => restore(entry)}>查看原提报</Button></Item>)}</ItemGroup>
        {!history.entries.length && <p className="text-sm text-muted-foreground">暂无提报记录。</p>}
        {history.total > 10 && <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><span>第 {history.page} 页 · 共 {history.total} 次</span><div className="flex gap-2"><Button variant="outline" size="sm" disabled={historyLoading || history.page <= 1} onClick={() => setHistoryPage(history.page - 1)}>上一页提报</Button><Button variant="outline" size="sm" disabled={historyLoading || history.page * 10 >= history.total} onClick={() => setHistoryPage(history.page + 1)}>下一页提报</Button></div></div>}
      </>}
    </section>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {job && adapter?.listHistory && <Button variant="outline" disabled={job.busy} onClick={() => {reset(); setHistoryRefresh(value => value + 1)}}>返回提报记录</Button>}
    {open && preview && adapter && <FeedbackPreview file={preview} adapter={adapter} onClose={() => setPreview(null)} trigger={previewTrigger.current}/>}
    <Button variant="outline" className="justify-self-end" onClick={onClose}>关闭</Button>
    <Dialog open={discard} onOpenChange={setDiscard}><DialogContent><DialogHeader><DialogTitle>放弃本页材料？</DialogTitle><DialogDescription>清空本次附件和草稿，不删除原文件。</DialogDescription></DialogHeader><div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setDiscard(false)}>保留材料</Button><Button variant="destructive" onClick={reset}>确认放弃</Button></div></DialogContent></Dialog>
  </DialogContent></Dialog>
}
