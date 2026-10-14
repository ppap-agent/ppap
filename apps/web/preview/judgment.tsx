// Stitch: projects/13261964020862199279/screens/495a9272bf66454cb39c2dc3baf363f2.
// One human judgment editor shared by the three decision layouts.
import {Plus, Trash2} from 'lucide-react'
import {Button} from '../src/components/ui/button'
import {Input} from '../src/components/ui/input'
import {Textarea} from '../src/components/ui/textarea'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../src/components/ui/select'
import type {DecisionEvidence, DecisionInput} from './decision'
import {isObject} from './saved-draft'
import {sourceKey, type DemoDocument} from './source-documents'
import {SourceReaderButton} from './source-reader'

export type Citation = {source: string; location: string; excerpt: string}
export type Judgment = {
  value: 'pending' | 'confirm' | 'disagree' | 'unknown' | 'manual-satisfied' | 'manual-unsatisfied'
  reason: string; citations?: Citation[]; confirmed?: string
}
export const judgmentLabels: Record<Judgment['value'], string> = {
  pending: '尚未判断', confirm: '确认机器结果', disagree: '不同意机器结果', unknown: '仍无法判断',
  'manual-satisfied': '人工确认满足要求', 'manual-unsatisfied': '人工确认不满足要求',
}
export const isManual = (judgment?: Judgment) => judgment?.value === 'manual-satisfied' || judgment?.value === 'manual-unsatisfied'
export function validJudgment(value: unknown): value is Judgment {
  return isObject(value) && Object.hasOwn(judgmentLabels, String(value.value)) && typeof value.reason === 'string'
    && (value.confirmed === undefined || typeof value.confirmed === 'string')
    && (value.citations === undefined || Array.isArray(value.citations) && value.citations.every(cite => isObject(cite) && ['source', 'location', 'excerpt'].every(key => typeof cite[key] === 'string')))
}
export function judgmentKey(input: DecisionInput, row: DecisionEvidence, judgment: Judgment) {
  return JSON.stringify([input, row.id, judgment.value, judgment.reason, judgment.citations])
}
export function manualReady(input: DecisionInput, judgment: Judgment) {
  return Boolean(isManual(judgment) && judgment.reason.trim() && judgment.citations?.length
    && judgment.citations.every(cite => cite.location.trim() && cite.excerpt.trim()
      && input.files.filter(file => sourceKey(file) === cite.source && !file.pending).length === 1))
}
export function judgmentComplete(input: DecisionInput, row: DecisionEvidence, judgment?: Judgment) {
  if (!judgment || judgment.value === 'pending' || !judgment.reason.trim()) return false
  return isManual(judgment) ? manualReady(input, judgment) && judgment.confirmed === judgmentKey(input, row, judgment) : true
}
export function requirementSatisfied(input: DecisionInput, row: DecisionEvidence, judgment?: Judgment) {
  return judgmentComplete(input, row, judgment)
    && (judgment?.value === 'manual-satisfied' || row.result === '满足' && judgment?.value === 'confirm')
}

// A retry may change one result, not the files, requirements or receipt. Preserve
// only judgments whose entire row is unchanged; changed inputs invalidate all.
export function judgmentsAfterCheck(previous: DecisionInput, next: DecisionInput, judgments: Record<string, Judgment>) {
  const {evidence: oldRows, ...oldInput} = previous
  const {evidence: newRows, ...newInput} = next
  if (JSON.stringify(oldInput) !== JSON.stringify(newInput)) return {}
  return Object.fromEntries(newRows.flatMap(row => {
    const oldRow = oldRows.find(item => item.id === row.id), judgment = judgments[row.id]
    if (!oldRow || !judgment || JSON.stringify(oldRow) !== JSON.stringify(row)) return []
    return [[row.id, isManual(judgment) && judgmentComplete(previous, oldRow, judgment)
      ? {...judgment, confirmed: judgmentKey(next, row, judgment)} : judgment]]
  }))
}

export function JudgmentEditor({input, row, judgment, frozen, documents, onChange}: {
  input: DecisionInput; row: DecisionEvidence; judgment: Judgment; frozen: boolean
  documents: DemoDocument[]; onChange: (next: Judgment) => void
}) {
  const manual = isManual(judgment)
  const complete = judgmentComplete(input, row, judgment)
  const update = (patch: Partial<Judgment>) => onChange({...judgment, ...patch, confirmed: undefined})
  const citations = judgment.citations ?? []
  const editCitation = (index: number, patch: Partial<Citation>) => update({citations: citations.map((cite, i) => i === index ? {...cite, ...patch} : cite)})
  return <div className="gf-human-review" data-readonly={frozen}>
    <div className="gf-human-heading"><strong>人工判断</strong><SourceReaderButton input={input} clauseId={row.id} documents={documents} label="阅读原件"/></div>
    {frozen ? <p>{judgmentLabels[judgment.value]}</p> : <Select value={judgment.value} onValueChange={value => update({value: value as Judgment['value']})}>
      <SelectTrigger aria-label={`${row.id} 人工判断`}><SelectValue/></SelectTrigger><SelectContent>
        {Object.entries(judgmentLabels).filter(([key]) => !['confirm', 'disagree'].includes(key) || ['满足', '不满足'].includes(row.result)).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}
      </SelectContent>
    </Select>}
    {manual && <div className="gf-human-citations"><div className="gf-human-heading"><strong>引用的证据</strong>{!frozen && <Button variant="ghost" onClick={() => update({citations: [...citations, {source: '', location: '', excerpt: ''}]})}><Plus/>添加证据</Button>}</div>
      {!citations.length && <p>选择原件版本，记录位置和摘录。跨文件判断可添加多份证据。</p>}
      {citations.map((cite, index) => {
        const file = input.files.find(item => sourceKey(item) === cite.source)
        return <div key={index} className="gf-human-citation">
          <div className="gf-human-heading"><span>证据 {index + 1}</span>{file && <SourceReaderButton input={input} clauseId={row.id} documents={documents} file={file} label={`阅读证据 ${index + 1} 原件`}/>} {!frozen && <Button size="icon" variant="ghost" aria-label={`移除 ${row.id} 证据 ${index + 1}`} onClick={() => update({citations: citations.filter((_, i) => i !== index)})}><Trash2/></Button>}</div>
          {frozen ? <><strong>{file ? `${file.name} · Rev ${file.revision} · 文件 v${file.sourceVersion}` : '原件版本不在本次范围'}</strong><p>{cite.location}</p><pre>{cite.excerpt}</pre></> : <>
            <label>文件与版本<Select value={cite.source} onValueChange={source => editCitation(index, {source, location: '', excerpt: ''})}><SelectTrigger aria-label={`${row.id} 证据 ${index + 1} 文件`}><SelectValue placeholder="选择原件版本"/></SelectTrigger><SelectContent>{input.files.filter(item => !item.pending).map((item, i) => <SelectItem key={sourceKey(item)} value={sourceKey(item)}>{item.name} · Rev {item.revision} · 文件 v{item.sourceVersion} · 第 {i + 1} 份</SelectItem>)}</SelectContent></Select></label>
            <label>原件位置<Input aria-label={`${row.id} 证据 ${index + 1} 位置`} placeholder="如：第 2 页，或工作表「尺寸」D8:F8" value={cite.location} onChange={event => editCitation(index, {location: event.target.value})}/></label>
            <label>人工摘录<Textarea aria-label={`${row.id} 证据 ${index + 1} 摘录`} placeholder="如实摘录与本条要求有关的内容" value={cite.excerpt} onChange={event => editCitation(index, {excerpt: event.target.value})}/></label>
          </>}
        </div>
      })}
    </div>}
    {frozen ? <p>{judgment.reason}</p> : <label>判断理由<Textarea aria-label={`${row.id} 复核依据`} placeholder={manual ? '说明这些证据如何支持判断，并列明限制' : '记录核对的位置与判断理由；无法判断时说明缺什么'} value={judgment.reason} onChange={event => update({reason: event.target.value})}/></label>}
    {manual && !frozen && <div className="gf-human-heading"><span role="status">{complete ? '本项判断已记录' : '草稿保留，判断尚未记录'}</span><Button disabled={!manualReady(input, judgment) || complete} onClick={() => onChange({...judgment, confirmed: judgmentKey(input, row, judgment)})}>记录本项判断</Button></div>}
  </div>
}
