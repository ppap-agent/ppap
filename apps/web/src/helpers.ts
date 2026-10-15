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

export function sourceLocation(source: Pick<SemanticSource, 'locator'>) {
  const place = source.locator
  if (place.kind === 'xlsx_cell') return `${place.sheet}!${place.cell}`
  if (place.kind === 'xlsx_range') return `${place.sheet}!${place.start}:${place.end}`
  if (place.kind === 'pdf_block') return `第 ${place.page} 页 · 文本块 ${place.block}`
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

export const analysisNotices: Record<string, string> = {
  empty_document_text: '未提取到文字，将主要按文件名匹配。',
  document_text_unavailable: '文字提取失败，请核对文件类型与规则。',
  metadata_uses_excerpt: '业务信息基于文字摘录识别；审核仍读取完整文件。',
  metadata_extraction_failed: '业务信息识别失败，请人工核对。',
  rule_matching_failed: '自动匹配失败，请手动选择规则。',
  no_available_rules: '暂无可匹配的规则，请先上传规则。',
}

export const reviewJudgments = ['认可机器判断', '有异议', '待确认'] as const
