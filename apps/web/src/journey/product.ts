export function sourceLocation(locator: Record<string, unknown>) {
  if (locator.kind === 'xlsx_cell') return `${locator.sheet} · ${locator.cell}`
  if (locator.kind === 'xlsx_range') return `${locator.sheet} · ${locator.start}:${locator.end}`
  if (locator.kind === 'pdf_block') return `第 ${locator.page} 页 · 区块 ${locator.block}`
  if (locator.kind === 'pdf_cell') return `第 ${locator.page} 页 · 表格行 ${locator.row} · 列 ${locator.column}`
  if (locator.kind === 'pdf_page') {
    const region = Array.isArray(locator.region) ? locator.region : null
    if (!region) return `第 ${locator.page} 页`
    const box = region.map(value => value == null ? '未定' : String(value)).join(', ')
    return `第 ${locator.page} 页 · 区域 ${box}`
  }
  if (locator.kind === 'docx_paragraph') return `段落 ${locator.paragraph}`
  if (locator.kind === 'docx_cell') return `表 ${locator.table} · 行 ${locator.row} · 列 ${locator.column}`
  return '位置待核对'
}
