import type {components} from '../generated/openapi'
import {resultLabel} from './status-labels.ts'
type Hint = components['schemas']['ReviewHint']
export function reviewHintText(hint: Hint) {
  return `${hint.reason === 'same_file_changed' ? '同一份文件、同一检查版本和条款，在不同轮次出现不同结论' : '明确关联的 PDF 与 Excel，同一检查版本和条款出现不同结论'}：${resultLabel[hint.compared_status]} → ${resultLabel[hint.current_status]}。`
}
export function reviewHintUrl(hint: Hint) {
  return `/audit?${new URLSearchParams({round: hint.compared_round_id, file: hint.compared_file_version_id, clause: hint.clause_id, basis: '1'})}`
}
