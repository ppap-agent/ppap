import {useState} from 'react'
import {request} from '../api'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {message} from '../journey/remote'

type Round = components['schemas']['Round']
export function ReviewDocumentPairs({roundId}: {roundId: string}) {
  const [round, setRound] = useState<Round | null>(null), [open, setOpen] = useState(false)
  const [pdf, setPdf] = useState(''), [excel, setExcel] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function load() {
    setOpen(value => !value)
    if (open) return
    setBusy(true); setError('')
    try {
      const value = await request<Round>(`/rounds/${encodeURIComponent(roundId)}`)
      if (value.id !== roundId) throw new Error('文件关系与本轮审核不一致。')
      setRound(value)
    } catch (cause) {setError(message(cause))} finally {setBusy(false)}
  }
  async function save() {
    if (busy || !pdf || !excel) return
    setBusy(true); setError('')
    try {
      const value = await request<components['schemas']['ReviewDocumentPairs']>(`/rounds/${encodeURIComponent(roundId)}/review-document-pairs`, 'PUT', {pdf_file_version_id: pdf, excel_file_version_id: excel})
      if (value.round_id !== roundId || !value.pairs.some(pair => pair.pdf_file_version_id === pdf && pair.excel_file_version_id === excel)) throw new Error('文件关系结果不一致，请重新读取。')
      window.location.reload()
    } catch (cause) {setError(message(cause))} finally {setBusy(false)}
  }
  const paired = new Set(round?.review_document_pairs?.flatMap(pair => [pair.pdf_file_version_id, pair.excel_file_version_id]) || [])
  const targets = round?.inputs.filter(row => row.role === 'target' && !paired.has(row.file_version_id)) || []
  return <section aria-label="PDF 与 Excel 关系" className="text-sm">
    <Button variant="ghost" size="sm" aria-expanded={open} disabled={busy} onClick={() => void load()}>关联同一文档的 PDF 与 Excel</Button>
    {open && <div className="space-y-3 rounded-md border p-3"><p>仅当你确认两份文件是同一文档的两种载体时关联。系统只比较本轮文件中同一检查版本和条款的结论，不会改判。</p>
      {(['pdf', 'excel'] as const).map(kind => <Select key={kind} value={kind === 'pdf' ? pdf : excel} onValueChange={kind === 'pdf' ? setPdf : setExcel}><SelectTrigger aria-label={kind === 'pdf' ? '选择 PDF 原件' : '选择 Excel 原件'}><SelectValue placeholder={kind === 'pdf' ? '选择 PDF 原件' : '选择 Excel 原件'}/></SelectTrigger><SelectContent>{targets.filter(row => (kind === 'pdf' ? /\.pdf$/i : /\.xlsx?$/i).test(row.original_filename)).map(row => <SelectItem key={row.file_version_id} value={row.file_version_id}>{row.original_filename}</SelectItem>)}</SelectContent></Select>)}
      <Button size="sm" disabled={busy || !pdf || !excel} onClick={() => void save()}>确认是同一文档</Button>
      {Boolean(round?.review_document_pairs?.length) && <p>本轮已确认 {round!.review_document_pairs!.length} 组关系，已关联文件不重复配对。</p>}
      {error && <p role="alert">{error}</p>}
    </div>}
  </section>
}
