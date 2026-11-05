import {useId, useState} from 'react'
import {Button} from '../components/ui/button'
import {Input} from '../components/ui/input'
import {Label} from '../components/ui/label'
import {Textarea} from '../components/ui/textarea'
import {NativeSelect, NativeSelectOption} from '../components/ui/native-select'
import {manualEvidence, type FeedbackEvidence} from './checker-feedback-contract'

/** A human locator is an assertion to validate, never a machine-verified citation. */
export function CheckerEvidenceInput({files, disabled, onAdd}: {files: {id: string; name: string}[]; disabled?: boolean; onAdd: (value: FeedbackEvidence) => void}) {
  const id = useId(), [file, setFile] = useState(files[0]?.id || ''), [kind, setKind] = useState<'xlsx_cell' | 'pdf_page' | 'docx_paragraph'>('xlsx_cell')
  const [sheet, setSheet] = useState(''), [position, setPosition] = useState(''), [excerpt, setExcerpt] = useState('')
  const value = files.some(item => item.id === file) ? manualEvidence(file, kind, sheet, position, excerpt) : null
  return <details className="space-y-3 text-sm"><summary className="cursor-pointer">添加我在原件中找到的依据</summary><p>对照原件后填写位置。这是你的判断依据，仍需实际验证。</p>
    <div className="space-y-2"><Label htmlFor={`${id}-file`}>依据原件</Label><NativeSelect id={`${id}-file`} disabled={disabled} value={file} onChange={event => setFile(event.target.value)}>{files.map(item => <NativeSelectOption key={item.id} value={item.id}>{item.name}</NativeSelectOption>)}</NativeSelect></div>
    <div className="space-y-2"><Label htmlFor={`${id}-kind`}>位置类型</Label><NativeSelect id={`${id}-kind`} disabled={disabled} value={kind} onChange={event => {setKind(event.target.value as typeof kind); setPosition('')}}><NativeSelectOption value="xlsx_cell">表格单元格</NativeSelectOption><NativeSelectOption value="pdf_page">PDF 页码</NativeSelectOption><NativeSelectOption value="docx_paragraph">Word 段落</NativeSelectOption></NativeSelect></div>
    {kind === 'xlsx_cell' && <div className="space-y-2"><Label htmlFor={`${id}-sheet`}>工作表名称</Label><Input id={`${id}-sheet`} value={sheet} disabled={disabled} onChange={event => setSheet(event.target.value)}/></div>}
    <div className="space-y-2"><Label htmlFor={`${id}-position`}>{kind === 'xlsx_cell' ? '单元格（例如 B12）' : kind === 'pdf_page' ? '页码（从 1 开始）' : '段落（从 1 开始）'}</Label><Input id={`${id}-position`} value={position} disabled={disabled} onChange={event => setPosition(event.target.value)}/></div>
    <div className="space-y-2"><Label htmlFor={`${id}-excerpt`}>原文摘录（选填）</Label><Textarea id={`${id}-excerpt`} value={excerpt} maxLength={500} disabled={disabled} onChange={event => setExcerpt(event.target.value)}/></div>
    <Button variant="outline" size="sm" disabled={disabled || !value} onClick={() => {if (value) {onAdd(value); setPosition(''); setExcerpt('')}}}>加入这处依据</Button>
  </details>
}
