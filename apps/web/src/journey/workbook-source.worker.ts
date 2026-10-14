import * as XLSX from 'xlsx'
import * as cptable from 'xlsx/dist/cpexcel.full.mjs'
import type {WorkbookReply, WorkbookSheet, CellRange} from './workbook-data'
XLSX.set_cptable(cptable)

self.onmessage = (event: MessageEvent<ArrayBuffer>) => {
  try {
    const bytes = new Uint8Array(event.data)
    // Do not let a text/HTML file renamed .xlsx silently become a workbook.
    const zip = bytes[0] === 0x50 && bytes[1] === 0x4b
    const compound = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1].every((byte, i) => bytes[i] === byte)
    if (!zip && !compound) throw new Error('format')
    const workbook = XLSX.read(bytes, {type: 'array', cellFormula: true, cellText: true, cellHTML: false, cellNF: true, cellStyles: true, bookVBA: false, nodim: true, WTF: true})
    if (!workbook.SheetNames.length) throw new Error('empty workbook')
    const sheets: WorkbookSheet[] = workbook.SheetNames.map((name, index) => {
      const sheet = workbook.Sheets[name]
      if (!sheet || sheet['!type'] && sheet['!type'] !== 'sheet') throw new Error('unsupported sheet')
      const cells: WorkbookSheet['cells'] = Object.create(null)
      let range: CellRange | null = sheet['!ref'] ? XLSX.utils.decode_range(sheet['!ref']) : null
      for (const [address, cell] of Object.entries(sheet)) {
        if (address.startsWith('!') || !/^[A-Z]+[1-9][0-9]*$/.test(address)) continue
        if (cell.t === 'z' && !cell.f) continue
        const present = cell.v !== undefined && cell.v !== null
        cells[address] = {text: present ? cell.w ?? XLSX.utils.format_cell(cell) : cell.f ? '无保存值' : '', raw: present ? String(cell.v) : null, formula: cell.f, format: cell.z, error: cell.t === 'e'}
        const p = XLSX.utils.decode_cell(address)
        range = range ? {s: {r: Math.min(range.s.r, p.r), c: Math.min(range.s.c, p.c)}, e: {r: Math.max(range.e.r, p.r), c: Math.max(range.e.c, p.c)}} : {s: p, e: p}
      }
      const merges = sheet['!merges'] ?? []
      for (const merge of merges) range = range ? {s: {r: Math.min(range.s.r, merge.s.r), c: Math.min(range.s.c, merge.s.c)}, e: {r: Math.max(range.e.r, merge.e.r), c: Math.max(range.e.c, merge.e.c)}} : merge
      if (range && (range.e.r > 1048575 || range.e.c > 16383)) throw new Error('unsupported range')
      return {name, range, cells, merges, hidden: Boolean(workbook.Workbook?.Sheets?.[index]?.Hidden),
        hiddenRows: (sheet['!rows'] ?? []).flatMap((row, i) => row?.hidden ? [i] : []), hiddenColumns: (sheet['!cols'] ?? []).flatMap((col, i) => col?.hidden ? [i] : [])}
    })
    self.postMessage({book: {sheets}} satisfies WorkbookReply)
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : ''
    self.postMessage({error: /password|encrypt/i.test(message) ? '工作簿已加密，无法在此读取。请下载原件，用本机应用解锁后核对。' : '无法完整读取此工作簿。文件可能损坏、格式不符或含暂不支持的工作表；请下载原件核对。'} satisfies WorkbookReply)
  }
}
