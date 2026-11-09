import {useId, useState} from 'react'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import {Textarea} from '../components/ui/textarea'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {manualEvidence, type FeedbackEvidence} from './checker-feedback-contract'
import {Disclosure} from '../components/disclosure'
import {RemoteSourcePane, type SourceCitation} from '../journey/remote-source'

export type EvidenceOriginal = {id: string; name: string; file_id?: string}
/** A human locator is an assertion to validate, never a machine-verified citation. */
export function CheckerEvidenceInput({files, roundId, disabled, onAdd}: {files: EvidenceOriginal[]; roundId: string; disabled?: boolean; onAdd: (value: FeedbackEvidence) => void}) {
  const id = useId(), [file, setFile] = useState(files[0]?.id || ''), [kind, setKind] = useState<'xlsx_cell' | 'pdf_page' | 'docx_paragraph'>('xlsx_cell')
  const [sheet, setSheet] = useState(''), [position, setPosition] = useState(''), [excerpt, setExcerpt] = useState('')
  const [reader, setReader] = useState(false), [picked, setPicked] = useState<FeedbackEvidence | null>(null)
  const original = files.find(item => item.id === file)
  const canPick = Boolean(original?.file_id && /\.(xlsx|xlsm|xls|pdf)$/i.test(original.name))
  const value = original ? picked ? {...picked, excerpt} : manualEvidence(file, kind, sheet, position, excerpt) : null
  function cite(citation: SourceCitation) {
    if (disabled || !original?.file_id) return
    const locator = citation.locator
    setKind(locator.kind); setSheet(locator.kind === 'xlsx_cell' ? locator.sheet : '')
    setPosition(locator.kind === 'xlsx_cell' ? locator.cell : String(locator.page))
    setExcerpt(citation.excerpt.slice(0, 500)); setPicked({file_version_id: file, locator, excerpt: citation.excerpt.slice(0, 500)})
    setReader(false)
  }
  return <Disclosure className="text-sm" summary={<>添加我在原件中找到的依据</>}><p>在原件上点选，或填写位置。这是你的判断依据，仍需实际验证。</p>
    <div className="space-y-2"><Label htmlFor={`${id}-file`}>依据原件</Label><NativeSelect id={`${id}-file`} disabled={disabled} value={file} onChange={event => {setFile(event.target.value); setPicked(null); setReader(false); setPosition(''); setSheet(''); setExcerpt('')}}>{files.map(item => <NativeSelectOption key={item.id} value={item.id}>{item.name}</NativeSelectOption>)}</NativeSelect></div>
    {canPick && <Button variant="outline" size="sm" disabled={disabled} onClick={() => setReader(value => !value)}>{reader ? '收起原件' : '在原件上点选位置'}</Button>}
    {reader && canPick && original?.file_id && !disabled && <div className="flex h-[60dvh] min-h-96 min-w-0"><RemoteSourcePane key={`${roundId}:${file}`} roundId={roundId} file={{file_id: original.file_id, file_version_id: original.id, original_filename: original.name, role: 'target', rule_version_id: null}} nativeOffice onCite={cite}/></div>}
    {picked && <p role="status">已从原件填入位置{picked.locator.kind === 'pdf_page' && picked.locator.region ? '与选区' : ''}，请确认后加入依据。</p>}
    <div className="space-y-2"><Label htmlFor={`${id}-kind`}>位置类型</Label><NativeSelect id={`${id}-kind`} disabled={disabled} value={kind} onChange={event => {setKind(event.target.value as typeof kind); setPosition(''); setPicked(null)}}><NativeSelectOption value="xlsx_cell">表格单元格</NativeSelectOption><NativeSelectOption value="pdf_page">PDF 页码</NativeSelectOption><NativeSelectOption value="docx_paragraph">Word 段落</NativeSelectOption></NativeSelect></div>
    {kind === 'xlsx_cell' && <div className="space-y-2"><Label htmlFor={`${id}-sheet`}>工作表名称</Label><Input id={`${id}-sheet`} value={sheet} disabled={disabled} onChange={event => {setSheet(event.target.value); setPicked(null)}}/></div>}
    <div className="space-y-2"><Label htmlFor={`${id}-position`}>{kind === 'xlsx_cell' ? '单元格（例如 B12）' : kind === 'pdf_page' ? '页码（从 1 开始）' : '段落（从 1 开始）'}</Label><Input id={`${id}-position`} value={position} disabled={disabled} onChange={event => {setPosition(event.target.value); setPicked(null)}}/></div>
    <div className="space-y-2"><Label htmlFor={`${id}-excerpt`}>原文摘录（选填）</Label><Textarea id={`${id}-excerpt`} value={excerpt} maxLength={500} disabled={disabled} onChange={event => setExcerpt(event.target.value)}/></div>
    <Button variant="outline" size="sm" disabled={disabled || !value} onClick={() => {if (value) {onAdd(value); setPosition(''); setExcerpt(''); setPicked(null)}}}>加入这处依据</Button>
  </Disclosure>
}
