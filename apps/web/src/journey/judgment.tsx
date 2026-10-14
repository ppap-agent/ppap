// Stitch: projects/13261964020862199279/screens/495a9272bf66454cb39c2dc3baf363f2.
// One human judgment editor shared by the three decision layouts.
import {Plus, Trash2} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Textarea} from '../components/ui/textarea'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import type {DecisionEvidence, DecisionInput} from './decision'
import {isManual, judgmentComplete, judgmentKey, judgmentLabels, manualReady, type Citation, type Judgment} from './judgment-state'
import {sourceKey, type DemoDocument} from './source-documents'
import {SourceReaderButton} from './source-reader'
import {ReviewRecord} from './review-record'

export function JudgmentEditor({input, row, judgment, frozen, documents, onChange}: {
  input: DecisionInput; row: DecisionEvidence; judgment: Judgment; frozen: boolean
  documents: DemoDocument[]; onChange: (next: Judgment) => void
}) {
  const manual = isManual(judgment)
  const complete = judgmentComplete(input, row, judgment)
  const update = (patch: Partial<Judgment>) => onChange({...judgment, ...patch, confirmed: undefined})
  const citations = judgment.citations ?? []
  const editCitation = (index: number, patch: Partial<Citation>) => update({citations: citations.map((cite, i) => i === index ? {...cite, ...patch} : cite)})
  return <div className={"mt-8.5 border-t [&_header]:pt-5.5 [&_header]:pr-0 [&_header]:pb-2 [&_header]:pl-0 [&_h2]:mt-0 [&_h2]:mr-0 [&_h2]:mb-1.5 [&_h2]:ml-0 [&_h2]:text-lg [&_header_p]:m-0 [&_header_p]:text-muted-foreground [&_header_p]:text-sm [&_header_p]:leading-normal [&_article]:grid [&_article]:grid-cols-[minmax(0,_1fr)_180px] [&_article]:gap-y-3.5 [&_article]:gap-x-5.5 [&_article]:py-5 [&_article]:px-0 [&_article]:border-b [&_article_>_div]:grid [&_article_>_div]:gap-1.5 [&_article_>_div]:min-w-0 [&_label]:grid [&_label]:gap-1.5 [&_label]:min-w-0 [&_strong]:text-sm [&_small]:text-muted-foreground [&_small]:text-xs [&_article_>_div_p]:mt-0.5 [&_article_>_div_p]:mr-0 [&_article_>_div_p]:mb-0 [&_article_>_div_p]:ml-0 [&_article_>_div_p]:text-foreground [&_article_>_div_p]:text-xs [&_article_>_div_p]:leading-normal [&_label]:text-muted-foreground [&_label]:text-xs [&_label]:font-semibold [&_article_>_label:last-child]:col-span-full max-sm:[&_article]:grid-cols-1 max-sm:[&_article_>_label:last-child]:col-[auto] grid gap-4 mt-5 max-w-190 [&[data-readonly=true]]:gap-2"} data-readonly={frozen}>
    <div className={"flex flex-wrap items-center gap-2 justify-between [&>span]:text-muted-foreground [&>span]:text-sm"}><strong>人工判断</strong><SourceReaderButton input={input} clauseId={row.id} documents={documents} label="阅读原件"/></div>
    {frozen ? <p>{judgmentLabels[judgment.value]}</p> : <Select value={judgment.value} onValueChange={value => update({value: value as Judgment['value']})}>
      <SelectTrigger className="w-full min-w-0 max-w-full" aria-label={`${row.displayId || row.id} 人工判断`}><SelectValue className="min-w-0"/></SelectTrigger><SelectContent>
        {Object.entries(judgmentLabels).filter(([key]) => !['confirm', 'disagree'].includes(key) || ['满足', '不满足'].includes(row.result)).map(([key, label]) => <SelectItem key={key} value={key}>{label}</SelectItem>)}
      </SelectContent>
    </Select>}
    {manual && <div className={""}><div className={"flex flex-wrap items-center gap-2 justify-between [&>span]:text-muted-foreground [&>span]:text-sm"}><strong>引用的证据</strong>{!frozen && <Button variant="ghost" onClick={() => update({citations: [...citations, {source: '', location: '', excerpt: ''}]})}><Plus/>添加证据</Button>}</div>
      {!citations.length && <p>选择原件版本，记录位置和摘录。跨文件判断可添加多份证据。</p>}
      {citations.map((cite, index) => {
        const file = input.files.find(item => sourceKey(item) === cite.source)
        return <div key={index} className={"grid gap-3 border-l pt-2 pr-0 pb-2 pl-4 my-3 mx-0 [&_pre]:whitespace-pre-wrap [&_pre]:break-all [&_pre]:m-0"}>
          <div className={"flex flex-wrap items-center gap-2 justify-between [&>span]:text-muted-foreground [&>span]:text-sm"}><span>证据 {index + 1}</span>{file && <SourceReaderButton input={input} clauseId={row.id} documents={documents} file={file} label={`阅读证据 ${index + 1} 原件`}/>} {!frozen && <Button size="icon" variant="ghost" aria-label={`移除 ${row.displayId || row.id} 证据 ${index + 1}`} onClick={() => update({citations: citations.filter((_, i) => i !== index)})}><Trash2/></Button>}</div>
          {frozen ? <><strong>{file ? `${file.name} · Rev ${file.revision} · 文件 v${file.sourceVersion}` : '原件版本不在本次范围'}</strong><p>{cite.location}</p><pre>{cite.excerpt}</pre></> : <>
            <label>文件与版本<Select value={cite.source} onValueChange={source => editCitation(index, {source, location: '', excerpt: ''})}><SelectTrigger className="w-full min-w-0 max-w-full" aria-label={`${row.displayId || row.id} 证据 ${index + 1} 文件`}><SelectValue className="min-w-0" placeholder="选择原件版本"/></SelectTrigger><SelectContent>{input.files.filter(item => !item.pending).map((item, i) => <SelectItem key={sourceKey(item)} value={sourceKey(item)}>{item.name} · Rev {item.revision} · 文件 v{item.sourceVersion} · 第 {i + 1} 份</SelectItem>)}</SelectContent></Select></label>
            <label>原件位置<Input aria-label={`${row.displayId || row.id} 证据 ${index + 1} 位置`} placeholder="如：第 2 页，或工作表「尺寸」D8:F8" value={cite.location} onChange={event => editCitation(index, {location: event.target.value})}/></label>
            <label>人工摘录<Textarea aria-label={`${row.displayId || row.id} 证据 ${index + 1} 摘录`} placeholder="如实摘录与本条要求有关的内容" value={cite.excerpt} onChange={event => editCitation(index, {excerpt: event.target.value})}/></label>
          </>}
        </div>
      })}
    </div>}
    {frozen ? <p>{judgment.reason}</p> : <label>判断理由<Textarea aria-label={`${row.displayId || row.id} 复核依据`} placeholder={manual ? '说明这些证据如何支持判断，并列明限制' : '记录核对的位置与判断理由；无法判断时说明缺什么'} value={judgment.reason} onChange={event => update({reason: event.target.value})}/></label>}
    {manual && !input.roundId && !frozen && <div className={"flex flex-wrap items-center gap-2 justify-between [&>span]:text-muted-foreground [&>span]:text-sm"}><span role="status">{complete ? '本项判断已记录' : '草稿保留，判断尚未记录'}</span><Button disabled={!manualReady(input, judgment) || complete} onClick={() => onChange({...judgment, confirmed: judgmentKey(input, row, judgment)})}>记录本项判断</Button></div>}
    <ReviewRecord input={input} row={row} judgment={judgment} onChange={onChange} frozen={frozen}/>
  </div>
}
