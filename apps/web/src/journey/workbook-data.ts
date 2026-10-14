// Read-only display data. No formula evaluator or workbook writer is used.
export type CellPosition = {r: number; c: number}
export type CellRange = {s: CellPosition; e: CellPosition}
export type WorkbookCell = {text: string; raw: string | null; formula?: string; format?: string; error: boolean}
export type WorkbookSheet = {name: string; hidden: boolean; range: CellRange | null; cells: Record<string, WorkbookCell>; merges: CellRange[]; hiddenRows: number[]; hiddenColumns: number[]}
export type WorkbookView = {sheets: WorkbookSheet[]}
export type WorkbookReply = {book: WorkbookView} | {error: string}

export function columnName(index: number) {
  let value = index + 1, name = ''
  while (value > 0) {name = String.fromCharCode(65 + (value - 1) % 26) + name; value = Math.floor((value - 1) / 26)}
  return name
}
export const cellName = ({r, c}: CellPosition) => `${columnName(c)}${r + 1}`
export function cellPosition(value: string): CellPosition | null {
  const match = /^\$?([A-Z]{1,3})\$?([1-9][0-9]{0,6})$/.exec(value.trim().toUpperCase())
  if (!match) return null
  const c = [...match[1]].reduce((sum, letter) => sum * 26 + letter.charCodeAt(0) - 64, 0) - 1, r = Number(match[2]) - 1
  return c < 16384 && r < 1048576 ? {r, c} : null
}
export const inRange = (point: CellPosition, range: CellRange) => point.r >= range.s.r && point.r <= range.e.r && point.c >= range.s.c && point.c <= range.e.c
export const rangeName = (range: CellRange) => `${cellName(range.s)}:${cellName(range.e)}`
export const containingMerge = (sheet: WorkbookSheet, point: CellPosition) => sheet.merges.find(range => inRange(point, range))
