import {analysisNotice} from '../helpers'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'

const titles: Record<string, string> = {
  matching_customer_required: '请先核对客户',
  matching_document_type_required: '尚未确定文件类型',
  matching_document_type_rules_missing: '缺少此类文件的规则',
  matching_customer_rules_missing: '缺少适用客户的规则',
  matching_rules_customer_unassigned: '规则的客户范围尚未确认',
  ambiguous_available_rules: '有多份适用规则，请先确认审核依据',
}

export function MatchingFeedback({gaps, candidates, onCheck, onRules, onTemplates, onFeedback, onRemove}: {
  gaps: string[]; candidates?: {id: string; title: string}[]; onCheck: () => void; onRules?: () => void; onTemplates: () => void; onFeedback: () => void; onRemove: () => void
}) {
  const reason = gaps.find(gap => Object.hasOwn(titles, gap))
  const needsInput = reason === 'matching_customer_required' || reason === 'matching_document_type_required'
  return <Alert><AlertTitle>{reason ? titles[reason] : '未匹配到生效规则'}</AlertTitle><AlertDescription>
    <p>{reason ? analysisNotice(reason) : '此文件不会自动套用通用标准，也不会产生审核结论。'}</p>
    {candidates && candidates.length > 0 && <ul className="space-y-1">{candidates.map(row => <li key={row.id}>{row.title}<span className="ml-2 text-xs text-muted-foreground">{row.id.slice(0, 8)}</span></li>)}</ul>}
    <div className="flex flex-wrap gap-2 pt-2">
      {needsInput && <Button variant="outline" size="sm" onClick={onCheck}>{reason === 'matching_customer_required' ? '返回核对客户' : '返回核对文件'}</Button>}
      {!needsInput && onRules && <Button variant="outline" size="sm" onClick={onRules}>{reason === 'matching_rules_customer_unassigned' || reason === 'ambiguous_available_rules' ? '核对规则范围与版本' : '上传对应规则'}</Button>}
      <Button variant="outline" size="sm" onClick={onTemplates}>标准模板</Button>
      <Button variant="outline" size="sm" onClick={onFeedback}>反馈对应模板</Button>
      <Button variant="ghost" size="sm" onClick={onRemove}>移除</Button>
    </div>
  </AlertDescription></Alert>
}

export const isMatchingReason = (gap: string) => Object.hasOwn(titles, gap)
