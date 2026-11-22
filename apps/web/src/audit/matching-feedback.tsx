import {useId, useState} from 'react'
import {iconArrowUpRightFromSquare} from '../components/icons'
import {Icon} from '../components/ui/icon'
import type {SourceMismatch} from './upload-board'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {TEMPLATE_GUIDE_URL, TEMPLATE_CONTACT_URL} from './template-links'
import {analysisNotice} from '../helpers'
import {Button} from '../components/ui/button'

const titles: Record<string, string> = {
  matching_document_type_required: '尚未确定文件类型',
  matching_document_type_rules_missing: '缺少此类文件的规则',
  matching_customer_rules_missing: '缺少适用客户的规则',
  matching_rules_customer_unassigned: '规则的客户范围尚未确认',
  ambiguous_available_rules: '有多份适用规则，请先确认审核依据',
}

export function MatchingFeedback({gaps, candidates, onCheck, onRules, onRemove, onTemplates}: {
  gaps: string[]; candidates?: {id: string; title: string}[]; onCheck: () => void; onRules?: () => void; onRemove?: () => void
  /** In-app template library: open it, or open its submission straight away. */
  onTemplates?: (submit: boolean) => void
}) {
  const reason = gaps.find(gap => Object.hasOwn(titles, gap))
  const needsInput = reason === 'matching_document_type_required'
  // Shown inside a file card or above a batch of files, so it is plain text rather than a bordered alert.
  return <div className="space-y-1.5">
    <p className="text-sm font-medium">{reason ? titles[reason] : '未匹配到生效规则'}</p>
    <p className="text-xs text-muted-foreground">{reason ? analysisNotice(reason) : '不会自动套用通用标准，也不会产生审核结论。'}</p>
    {candidates && candidates.length > 0 && <ul className="space-y-1 text-xs">{candidates.map(row => <li key={row.id}>{row.title}</li>)}</ul>}
    <div className="flex flex-wrap gap-2">
      {needsInput && <Button size="sm" onClick={onCheck}>返回核对文件</Button>}
      {!needsInput && onRules && <Button size="sm" onClick={onRules}>{reason === 'matching_rules_customer_unassigned' || reason === 'ambiguous_available_rules' ? '核对规则范围与版本' : '上传对应规则'}</Button>}
      {onTemplates ? <><Button variant="link" size="sm" onClick={() => onTemplates(false)}>查看标准模板</Button><Button variant="link" size="sm" onClick={() => onTemplates(true)}>反馈对应模板</Button></> : <>
        <Button asChild variant="link" size="sm"><a href={TEMPLATE_GUIDE_URL} target="_blank" rel="noopener noreferrer">查看模板<Icon icon={iconArrowUpRightFromSquare} aria-hidden="true"/></a></Button>
        <Button asChild variant="link" size="sm"><a href={TEMPLATE_CONTACT_URL} target="_blank" rel="noopener noreferrer">模板反馈<Icon icon={iconArrowUpRightFromSquare} aria-hidden="true"/></a></Button>
      </>}
      {onRemove && <Button variant="ghost" size="sm" onClick={onRemove}>移除</Button>}
    </div>
  </div>
}

export const isMatchingReason = (gap: string) => Object.hasOwn(titles, gap)

/** S07 inside the audited file's row: the comparison item cannot run; change the counterpart, its attribution, or drop the reference. */
export function SourceMismatchNotice({fact, choices, onReplace, onReassign, onRemove, removeHeld, disabled}: {
  fact: SourceMismatch
  /** Other files of this batch recognized as the same counterpart type. */
  choices: {id: string; name: string}[]
  onReplace: (id: string) => void; onReassign: () => void; onRemove: () => void
  /** Why the counterpart cannot be removed here, e.g. it is itself an audited file. */
  removeHeld: string
  disabled: boolean
}) {
  const [choosing, setChoosing] = useState(false)
  const replaceNote = useId(), removeNote = useId()
  return <div className="space-y-1.5 text-xs">
    <p className="text-warning">比对源《{fact.document}》{fact.identity} {fact.found_value} 与被审文件 {fact.value} 不一致，该项无法比对</p>
    <p className="text-muted-foreground">比对文件：<span className="[overflow-wrap:anywhere]">{fact.found_filename}</span></p>
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" disabled={disabled || !choices.length || Boolean(removeHeld)} aria-describedby={choices.length ? undefined : replaceNote} onClick={() => setChoosing(open => !open)}>更换比对文件</Button>
      <Button variant="outline" size="sm" disabled={disabled} onClick={onReassign}>更改归属</Button>
      <Button variant="ghost" size="sm" disabled={disabled || Boolean(removeHeld)} aria-describedby={removeHeld ? removeNote : undefined} onClick={onRemove}>移除</Button>
    </div>
    {!choices.length && <p id={replaceNote} className="text-muted-foreground">本批没有其他《{fact.document}》可选，可在上传页添加后重新识别。</p>}
    {removeHeld && <p id={removeNote} className="text-muted-foreground">{removeHeld}</p>}
    {choosing && choices.length > 0 && <Select onValueChange={id => {setChoosing(false); onReplace(id)}}><SelectTrigger size="sm" aria-label={`《${fact.document}》的比对文件`}><SelectValue placeholder="选择本批的比对文件"/></SelectTrigger><SelectContent>{choices.map(choice => <SelectItem key={choice.id} value={choice.id}>{choice.name}</SelectItem>)}</SelectContent></Select>}
  </div>
}
