import type {Finding, Rule, SemanticSource} from './api'

export function latestRules(rules: Rule[]) {
  const previous = new Set(rules.map(rule => rule.predecessor_id).filter(Boolean))
  return rules.filter(rule => !previous.has(rule.id))
}

export function matchesFilter(item: Finding, status: string) {
  return status === 'unreviewed' ? !item.review : status === 'attention' ? ['NG', 'ERR', 'UNABLE'].includes(item.status) : !status || item.status === status
}

export function findingShortTitle(item: Finding, fallbackNumber: number) {
  return `${fallbackNumber} · ${item.classification || '条款'}`
}

function pdfPageLocation(page: number, region?: Array<number | null>) {
  if (!region) return `第 ${page} 页`
  const box = region.map(value => value == null ? '未定' : String(value)).join(', ')
  return `第 ${page} 页 · 区域 ${box}`
}

export function sourceLocation(source: Pick<SemanticSource, 'locator'>) {
  const place = source.locator
  if (place.kind === 'xlsx_cell') return `${place.sheet}!${place.cell}`
  if (place.kind === 'xlsx_range') return `${place.sheet}!${place.start}:${place.end}`
  if (place.kind === 'pdf_block') return `第 ${place.page} 页 · 文本块 ${place.block}`
  if (place.kind === 'pdf_cell') return `第 ${place.page} 页 · 表格行 ${place.row} · 列 ${place.column}`
  if (place.kind === 'pdf_page') return pdfPageLocation(place.page, place.region)
  if (place.kind === 'docx_paragraph') return `段落 ${place.paragraph}`
  return `表格 ${place.table} · 行 ${place.row} · 列 ${place.column}`
}

export function parseExcerptCells(text: string) {
  return text.split(/\s+\|\s+(?=[A-Z]{1,3}\d+:)/).map(part => {
    const match = part.match(/^([A-Z]{1,3}\d+):\s*([\s\S]*)$/)
    return {address: match?.[1] || '', value: match?.[2] ?? part, formula: match?.[2]?.startsWith('=') || false}
  })
}

export interface WorkspacePosition {
  file: string
  finding: string
  filter: string
  view: string
  panel: string
  scroll: number
}

export function workspaceStorageKey(roundId: string) {
  return 'ppap-view:' + roundId
}

export function parseWorkspacePosition(raw: string | null): WorkspacePosition | null {
  if (!raw) return null
  try {
    const saved = JSON.parse(raw) as WorkspacePosition
    if (!saved || typeof saved.file !== 'string') return null
    return saved
  } catch {
    return null
  }
}

const analysisNotices: Record<string, string | null> = {
  photo_content_requires_manual_confirmation: '照片格式已核实。照片中的零件、标识和内容仍需人工确认。',
  empty_document_text: '未读取到文件文字，请核对原件后重新识别。',
  document_text_unavailable: '无法读取文件文字，请核对原件后重新识别。',
  metadata_uses_excerpt: null,
  metadata_extraction_failed: '业务信息识别失败，请人工核对。',
  document_classification_failed: '无法确定文件类型，请核对原件后重新识别。',
  rule_matching_failed: '自动匹配失败，请返回检查文件后重新识别。',
  no_available_rules: '暂无可用规则，请查看模板或通过模板文档联系我们。',
  matching_customer_conflict: '文件内的明确客户标识与本次审核客户不一致。请核对审核客户，异客户资料请分别建立任务。',
  matching_customer_required: '文件中未识别出客户。请返回上传页面选择客户，再核对文件并重新识别；不会自动采用其他客户或通用规则。',
  matching_document_type_required: '尚未确定文件类型。请返回核对原件，重新识别；不会仅凭现有规则推断文件类型。',
  matching_document_type_rules_missing: '本次识别的候选中没有此类文件的生效规则。请上传并核对对应规则，保存后返回本任务重新识别。',
  matching_customer_rules_missing: '本次识别的候选中没有适用于该客户和文件类型的生效规则。请核对客户或上传对应规则，保存后重新识别。',
  matching_rules_customer_unassigned: '此类规则尚未确认客户范围。请指定客户或明确确认“不限定客户”，保存新版本后重新识别。旧版本不变。',
  ambiguous_available_rules: '有多份生效规则符合条件，未自动选择。请核对候选规则的适用范围与版本，再返回本任务重新识别。',
  matching_snapshot_invalid: '匹配依据不完整，请返回检查文件后重新识别。',
  matching_result_unknown: '尚未取得完整匹配结果，请读取识别结果。',
}

export function analysisNotice(notice: string): string | null {
  if (Object.hasOwn(analysisNotices, notice)) return analysisNotices[notice]
  return /\p{Script=Han}/u.test(notice) ? notice : '识别信息需要核对，请读取识别结果；若仍有异常，请联系支持。'
}

export const reviewJudgments = ['认可机器判断', '有异议', '待确认'] as const
