// Stitch: projects/13261964020862199279/screens/495a9272bf66454cb39c2dc3baf363f2.
// Human judgments remain separate from machine results.
import {Plus, Trash2} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Textarea} from '../components/ui/textarea'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import type {DecisionEvidence, DecisionInput} from './decision'
import {isManual, judgmentComplete, judgmentKey, judgmentLabels, manualReady, type Citation, type Judgment} from './judgment-state'
import {sourceKey, type SourceFile, type DemoDocument} from './source-documents'
import {SourceReaderButton} from './source-reader'
import {ReviewRecord} from './review-record'

export function JudgmentEditor({input, row, judgment, frozen, documents, onChange, onRead, afterSave}: {
  input: DecisionInput; row: DecisionEvidence; judgment: Judgment; frozen: boolean
  documents: DemoDocument[]; onChange: (next: Judgment) => void; onRead?: (file?: SourceFile, location?: string) => void; afterSave?: () => void
}) {
  const manual = isManual(judgment)
  const complete = judgmentComplete(input, row, judgment)
  const update = (patch: Partial<Judgment>) => onChange({...judgment, ...patch, confirmed: undefined})
  const citations = judgment.citations ?? []
  const editCitation = (index: number, patch: Partial<Citation>) => update({citations: citations.map((cite, i) => i === index ? {...cite, ...patch} : cite)})
  return <div className="grid min-w-0 gap-4 border-t pt-4" data-readonly={frozen}>
    <div className={"flex flex-wrap items-center gap-2 justify-between [&>span]:text-muted-foreground [&>span]:text-sm"}><strong>人工判断</strong><>{onRead ? <Button variant="ghost" size="sm" onClick={() => onRead()}>阅读原件</Button> : <SourceReaderButton input={input} clauseId={row.id} documents={documents} label="阅读原件"/>}</></div>
    {frozen ? <p>{judgmentLabels[judgment.value]}</p> : <Select value={judgment.value} onValueChange={value => {const manual = value === 'manual-satisfied' || value === 'manual-unsatisfied'; const originals = input.files.filter(file => !file.pending); update({value: value as Judgment['value'], ...(manual && !citations.length ? {citations: [{source: originals.length === 1 ? sourceKey(originals[0]) : '', location: '', excerpt: ''}]} : {})})}}>
      <SelectTrigger className="w-full min-w-0 max-w-full" aria-label={`${row.displayId || row.id} 人工判断`}><SelectValue className="min-w-0"/></SelectTrigger><SelectContent>
        {Object.entries(judgmentLabels).filter(([key]) => !['confirm', 'disagree'].includes(key) || ['满足', '不满足'].includes(row.result)).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}
      </SelectContent>
    </Select>}
    {manual && <div className={""}><div className={"flex flex-wrap items-center gap-2 justify-between [&>span]:text-muted-foreground [&>span]:text-sm"}><strong>引用的证据</strong>{!frozen && <Button variant="ghost" onClick={() => update({citations: [...citations, {source: input.files.length === 1 && !input.files[0].pending ? sourceKey(input.files[0]) : '', location: '', excerpt: ''}]})}><Plus/>添加证据</Button>}</div>
      {!citations.length && <p>选择原件版本，记录位置和摘录。跨文件判断可添加多份证据。</p>}
      {citations.map((cite, index) => {
        const file = input.files.find(item => sourceKey(item) === cite.source)
        return <div key={index} className={"grid gap-3 border-l pt-2 pr-0 pb-2 pl-4 my-3 mx-0 [&_pre]:whitespace-pre-wrap [&_pre]:break-all [&_pre]:m-0"}>
          <div className={"flex flex-wrap items-center gap-2 justify-between [&>span]:text-muted-foreground [&>span]:text-sm"}><span>证据 {index + 1}</span>{file && (onRead ? <Button variant="ghost" size="sm" onClick={() => onRead(file, cite.location)}>阅读证据 {index + 1}</Button> : <SourceReaderButton input={input} clauseId={row.id} documents={documents} file={file} label={`阅读证据 ${index + 1} 原件`}/>)} {!frozen && <Button size="icon" variant="ghost" aria-label={`移除 ${row.displayId || row.id} 证据 ${index + 1}`} onClick={() => update({citations: citations.filter((_, i) => i !== index)})}><Trash2/></Button>}</div>
          {frozen ? <><strong>{file ? `${file.name} · ${file.material ? '本轮固定版本' : `Rev ${file.revision} · 文件 v${file.sourceVersion}`}` : '原件版本不在本次范围'}</strong><p>{cite.location}</p><pre>{cite.excerpt}</pre></> : <>
            {input.files.length === 1 && file && !file.pending ? <p className="break-words text-sm">{file.name} · {file.material ? '本轮固定版本' : `Rev ${file.revision} · v${file.sourceVersion}`}</p> : <label className="grid gap-1.5 text-sm">文件与版本<Select value={cite.source} onValueChange={source => editCitation(index, {source, location: '', excerpt: ''})}><SelectTrigger className="w-full min-w-0 max-w-full" aria-label={`${row.displayId || row.id} 证据 ${index + 1} 文件`}><SelectValue className="min-w-0" placeholder="选择原件版本"/></SelectTrigger><SelectContent>{input.files.filter(item => !item.pending).map((item, i) => <SelectItem key={sourceKey(item)} value={sourceKey(item)}>{item.name} · {item.material ? '本轮固定版本' : `Rev ${item.revision} · v${item.sourceVersion}`}{input.files.filter(file => file.name === item.name).length > 1 ? item.material ? ` · 上传版本 ${item.material.file_version_id.slice(0, 8)}` : ` · 第 ${i + 1} 份` : ''}</SelectItem>)}</SelectContent></Select></label>}
            <label className="grid gap-1.5 text-sm">原件位置<Input aria-label={`${row.displayId || row.id} 证据 ${index + 1} 位置`} placeholder="如：第 2 页，或工作表「尺寸」D8:F8" value={cite.location} onChange={event => editCitation(index, {location: event.target.value})}/></label>
            <label className="grid gap-1.5 text-sm">人工摘录<Textarea aria-label={`${row.displayId || row.id} 证据 ${index + 1} 摘录`} placeholder="如实摘录与本条要求有关的内容" value={cite.excerpt} onChange={event => editCitation(index, {excerpt: event.target.value})}/></label>
          </>}
        </div>
      })}
    </div>}
    {frozen ? <p>{judgment.reason}</p> : <label className="grid gap-1.5 text-sm">判断理由<Textarea aria-label={`${row.displayId || row.id} 复核依据`} placeholder={manual ? '说明这些证据如何支持判断，并列明限制' : '记录核对的位置与判断理由；无法判断时说明缺什么'} value={judgment.reason} onChange={event => update({reason: event.target.value})}/></label>}
    {manual && !input.roundId && !frozen && <div className={"flex flex-wrap items-center gap-2 justify-between [&>span]:text-muted-foreground [&>span]:text-sm"}><span role="status">{complete ? '本项判断已记录' : '草稿保留，判断尚未记录'}</span><Button disabled={!manualReady(input, judgment) || complete} onClick={() => onChange({...judgment, confirmed: judgmentKey(input, row, judgment)})}>记录本项判断</Button></div>}
    <ReviewRecord input={input} row={row} judgment={judgment} onChange={onChange} frozen={frozen} afterSave={afterSave}/>
  </div>
}
