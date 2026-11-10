import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Stitch: projects/13261964020862199279/screens/0074e1678f934db6baac07d7282af1e5.
// Shares the reader's document / comparison / clause layouts. This view is not Excel pagination.
import {useEffect, useMemo, useRef, useState} from 'react'
import {ChevronLeft, ChevronRight, Copy, Minus, Plus, Search} from 'lucide-react'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Input} from '../components/ui/input'
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../components/ui/select'
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow} from '../components/ui/table'
import {cellName, cellPosition, columnName, containingMerge, inRange, rangeName, type CellPosition, type WorkbookReply, type WorkbookView} from './workbook-data'
import type {SourceFocus} from './source-documents'
const rowsPerView = 60, columnsPerView = 16
export function WorkbookSource({blob, name, focus, onCite}: {blob: Blob; name: string; focus?: SourceFocus; onCite?: (location: string, excerpt: string, cell: {sheet: string; cell: string}) => void}) {
  const [book, setBook] = useState<WorkbookView | null>(null), [error, setError] = useState('')
  const [sheetIndex, setSheetIndex] = useState(0), [selected, setSelected] = useState<CellPosition>({r: 0, c: 0})
  const [origin, setOrigin] = useState<CellPosition>({r: 0, c: 0}), [address, setAddress] = useState('A1')
  const [query, setQuery] = useState(''), [search, setSearch] = useState(''), [matchIndex, setMatchIndex] = useState(-1)
  const [zoom, setZoom] = useState(100), [notice, setNotice] = useState('')
  const host = useRef<HTMLDivElement>(null)
  const pendingLocation = useRef<{point: CellPosition; sheet: number; focus: boolean} | null>(null)
  useEffect(() => {
    let current = true
    const worker = new Worker(new URL('./workbook-source.worker.ts', import.meta.url), {type: 'module'})
    const finish = () => {clearTimeout(timer); worker.terminate()}
    const timer = window.setTimeout(() => {if (current) {setError('读取时间过长，已停止。可重新读取，或下载原件核对。'); worker.terminate()}}, 30000)
    worker.onmessage = (event: MessageEvent<WorkbookReply>) => {
      if (!current) return
      finish()
      if ('error' in event.data) {setError(event.data.error); return}
      setBook(event.data.book)
      const index = Math.max(0, event.data.book.sheets.findIndex(sheet => !sheet.hidden))
      const point = event.data.book.sheets[index].range?.s ?? {r: 0, c: 0}
      setSheetIndex(index); setOrigin(point); setSelected(point); setAddress(cellName(point))
    }
    worker.onerror = () => {if (current) setError('表格读取器无法运行。可重新读取，或下载原件核对。'); finish()}
    void blob.arrayBuffer().then(bytes => {if (current) worker.postMessage(bytes, [bytes])}).catch(() => {if (current) setError('无法读取文件内容。'); finish()})
    return () => {current = false; finish()}
  }, [blob])
  useEffect(() => {
    if (!book || !focus) return
    // Accept explicit worksheet/cell locations only. Never infer a worksheet from a filename.
    const excel = /^(?:'((?:[^']|'')+)'|([^!]+))!\s*(\$?[A-Za-z]{1,3}\$?[1-9][0-9]{0,6})(?=$|[:：、,，;\s])/.exec(focus.location)
    const separator = focus.location.lastIndexOf(' · ')
    const sheetLabel = separator >= 0 ? focus.location.slice(0, separator).trim().replace(/^工作表\s*/, '') : ''
    const sheetName = excel ? (excel[1]?.replaceAll("''", "'") ?? excel[2]).trim() : sheetLabel.startsWith("'") && sheetLabel.endsWith("'") ? sheetLabel.slice(1, -1).replaceAll("''", "'") : sheetLabel
    const addressLabel = excel?.[3] ?? (separator >= 0 ? /^(\$?[A-Za-z]{1,3}\$?[1-9][0-9]{0,6})(?=$|[:：、,，;\s])/.exec(focus.location.slice(separator + 3).trim())?.[1] : undefined)
    const index = book.sheets.findIndex(item => item.name === sheetName)
    const point = addressLabel ? cellPosition(addressLabel) : null
    const target = book.sheets[index]
    if (!point || !target?.range || !inRange(point, target.range)) {setNotice('证据位置无法直接定位，请在原件中核对。'); return}
    const anchor = containingMerge(target, point)?.s ?? point
    pendingLocation.current = {point: anchor, sheet: index, focus: false}
    setSheetIndex(index); setSelected(anchor); setAddress(cellName(anchor))
    setOrigin({r: target.range.s.r + Math.floor((anchor.r - target.range.s.r) / rowsPerView) * rowsPerView,
      c: target.range.s.c + Math.floor((anchor.c - target.range.s.c) / columnsPerView) * columnsPerView})
    setQuery(''); setSearch(''); setMatchIndex(-1); setNotice(`已定位 ${target.name} · ${cellName(anchor)}`)
  }, [book, focus])
  useEffect(() => {
    const pending = pendingLocation.current
    if (!pending || pending.sheet !== sheetIndex) return
    // Run after React commits the requested row/column window, without waiting for a paint.
    const element = host.current?.querySelector<HTMLElement>(`[data-cell="${cellName(pending.point)}"]`)
    if (!element) return
    element.scrollIntoView({block: 'nearest', inline: 'nearest'})
    if (pending.focus) element.focus({preventScroll: true})
    pendingLocation.current = null
  }, [selected, origin, sheetIndex])
  const sheet = book?.sheets[sheetIndex], range = sheet?.range
  const matches = useMemo(() => sheet && search ? Object.entries(sheet.cells).filter(([, cell]) => `${cell.text}\n${cell.formula ?? ''}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map(([key]) => cellPosition(key)!).sort((a, b) => a.r - b.r || a.c - b.c) : [], [sheet, search])
  function choose(point: CellPosition, focus = false) {
    if (!range || !sheet || !inRange(point, range)) {setNotice('此位置不在已读取的工作表范围内。'); return}
    const anchor = containingMerge(sheet, point)?.s ?? point
    pendingLocation.current = {point: anchor, sheet: sheetIndex, focus}
    setSelected(anchor); setAddress(cellName(anchor)); setNotice('')
    setOrigin(old => ({r: anchor.r < old.r || anchor.r >= old.r + rowsPerView ? range.s.r + Math.floor((anchor.r - range.s.r) / rowsPerView) * rowsPerView : old.r,
      c: anchor.c < old.c || anchor.c >= old.c + columnsPerView ? range.s.c + Math.floor((anchor.c - range.s.c) / columnsPerView) * columnsPerView : old.c}))
  }
  function navigateMatch(direction: number, term = search) {
    if (!sheet) return
    const results = term === search ? matches : Object.entries(sheet.cells).filter(([, cell]) => `${cell.text}\n${cell.formula ?? ''}`.toLocaleLowerCase().includes(term.toLocaleLowerCase())).map(([key]) => cellPosition(key)!).sort((a, b) => a.r - b.r || a.c - b.c)
    setSearch(term)
    if (!term) {setMatchIndex(-1); setNotice('输入要查找的文字或保存值。'); return}
    if (!results.length) {setMatchIndex(-1); setNotice('当前工作表未找到匹配内容。'); return}
    const index = term !== search || matchIndex < 0 ? 0 : (matchIndex + direction + results.length) % results.length
    setMatchIndex(index); choose(results[index])
  }
  const merge = sheet && containingMerge(sheet, selected), cell = sheet?.cells[cellName(selected)]
  const location = sheet ? `'${sheet.name.replaceAll("'", "''")}'!${merge ? rangeName(merge) : cellName(selected)}` : ''
  async function copy() {
    try {await navigator.clipboard.writeText(`${name}\n${location}\n保存文字：${cell?.text ?? '（空白）'}${cell?.formula ? `\n公式（未重算）：=${cell.formula}` : ''}`); setNotice('已复制位置与内容，尚未记录为审核证据。')}
    catch {setNotice('浏览器未允许复制，请手动选择下方文字。')}
  }
  if (error) return <div className={"min-h-55 grid justify-items-start gap-3.5 max-w-115 m-auto [&_h3]:text-lg [&_h3]:font-semibold [&_p]:leading-relaxed [&_p]:text-muted-foreground"}><h3>表格无法显示</h3><p role="alert">{error}</p></div>
  if (!sheet) return <ReadSkeleton label="正在读取工作表…" kind="table" rows={8} className="min-h-55 p-3"/>
  const end = range ? {r: Math.min(origin.r + rowsPerView - 1, range.e.r), c: Math.min(origin.c + columnsPerView - 1, range.e.c)} : origin
  const rowNumbers = range ? Array.from({length: end.r - origin.r + 1}, (_, i) => origin.r + i) : []
  const columnNumbers = range ? Array.from({length: end.c - origin.c + 1}, (_, i) => origin.c + i) : []
  const visibleMerges = sheet.merges.filter(item => item.e.r >= origin.r && item.s.r <= end.r && item.e.c >= origin.c && item.s.c <= end.c)
  return <div className={"flex flex-col h-full min-h-105 min-w-0 max-md:min-h-0"} ref={host}>
    <div className={"flex flex-wrap gap-1.5 items-center p-2 border-b shrink-0 text-sm [&>[role=combobox]]:w-auto [&>[role=combobox]]:max-w-full [&>[role=combobox]]:min-w-40 [&_form]:flex [&_form]:gap-1 [&_form]:items-center [&_form]:min-w-0 max-md:gap-2 max-md:[&>[role=combobox]]:flex-[1_0_calc(100%_-_116px)] max-md:[&>[role=combobox]]:min-w-0 max-md:[&>[role=combobox]]:w-0 max-md:[&_form]:order-[2]"}><Select value={String(sheetIndex)} onValueChange={value => {const index = Number(value), point = book!.sheets[index].range?.s ?? {r: 0, c: 0}; setSheetIndex(index); setSelected(point); setAddress(cellName(point)); setOrigin(point); setQuery(''); setSearch(''); setMatchIndex(-1); setNotice('')}}><SelectTrigger className="w-full min-w-0 max-w-full" aria-label="工作表"><SelectValue className="min-w-0"/></SelectTrigger><SelectContent>{book!.sheets.map((item, i) => <SelectItem value={String(i)} key={i}>{item.name}{item.hidden ? '（原文件隐藏）' : ''}</SelectItem>)}</SelectContent></Select>
      <form aria-label="单元格定位" onSubmit={event => {event.preventDefault(); const point = cellPosition(address); if (point) choose(point, true); else setNotice('请输入有效单元格位置，例如 F12。')}}><Input aria-label="单元格位置" value={address} onChange={event => setAddress(event.target.value)} spellCheck={false}/><Button variant="outline" size="sm" type="submit" disabled={!range}>定位</Button></form>
      <form className={""} role="search" aria-label="查找当前工作表" onSubmit={event => {event.preventDefault(); navigateMatch(1, query.trim())}}><Input aria-label="查找表格内容" placeholder="查找当前工作表" value={query} onChange={event => setQuery(event.target.value)}/><Button variant="ghost" size="icon-sm" type="submit" aria-label="查找下一个匹配" disabled={!range}><Search/></Button></form>
      {search && <span className={"flex items-center gap-1 whitespace-nowrap max-md:order-[3]"}>{matchIndex >= 0 ? matchIndex + 1 : 0} / {matches.length}<Button variant="ghost" size="icon-sm" aria-label="上一个匹配" disabled={!matches.length} onClick={() => navigateMatch(-1)}><ChevronLeft/></Button></span>}
      <div className={"flex items-center gap-1 whitespace-nowrap ml-auto"}><Button variant="ghost" size="icon-sm" aria-label="缩小表格" disabled={zoom <= 100} onClick={() => setZoom(value => value - 25)}><Minus/></Button><span>{zoom}%</span><Button variant="ghost" size="icon-sm" aria-label="放大表格" disabled={zoom >= 200} onClick={() => setZoom(value => value + 25)}><Plus/></Button></div>
    </div>
    <p className="shrink-0 px-3 text-xs text-muted-foreground">只读视图，公式不重算。完整排版请下载原件核对。</p>
    <div className={"min-h-45 flex-1 flex min-w-0 overflow-hidden max-md:min-h-25"}>
      {range ? <Table className={"w-[var(--table-width)] min-w-full  max-md:[&_td]:min-w-27.5 max-md:[&_th]:min-h-10"} aria-label={`${sheet.name} 单元格内容`} style={{'--table-width': `${42 + columnNumbers.length * 110 * zoom / 100}px`} as React.CSSProperties}><colgroup><col className="w-10"/>{columnNumbers.map(c => <col key={c} className="w-[var(--column-width)]" style={{'--column-width': `${110 * zoom / 100}px`} as React.CSSProperties}/>)}</colgroup><TableHeader><TableRow><TableHead scope="col">行</TableHead>{columnNumbers.map(c => <TableHead key={c} scope="col">{columnName(c)}{sheet.hiddenColumns.includes(c) ? '（隐藏）' : ''}</TableHead>)}</TableRow></TableHeader><TableBody>{rowNumbers.map(r => <TableRow key={r}><TableHead scope="row">{r + 1}{sheet.hiddenRows.includes(r) ? '（隐藏）' : ''}</TableHead>{columnNumbers.map(c => {
        const merged = visibleMerges.find(item => inRange({r, c}, item))
        if (merged && (r !== Math.max(origin.r, merged.s.r) || c !== Math.max(origin.c, merged.s.c))) return null
        const point = merged?.s ?? {r, c}, address = cellName(point), value = sheet.cells[address]
        const chosen = point.r === selected.r && point.c === selected.c
        return <TableCell key={c} data-cell={address} data-selected={chosen} data-error={value?.error || undefined} tabIndex={chosen ? 0 : -1} aria-label={`${address}${merged ? `，合并范围 ${rangeName(merged)}` : ''}：${value?.text || '空白'}`} rowSpan={merged ? Math.min(end.r, merged.e.r) - r + 1 : undefined} colSpan={merged ? Math.min(end.c, merged.e.c) - c + 1 : undefined} onClick={() => choose(point)} onKeyDown={event => {
          const direction = ({ArrowDown: [merged ? merged.e.r - point.r + 1 : 1, 0], ArrowUp: [-1, 0], ArrowLeft: [0, -1], ArrowRight: [0, merged ? merged.e.c - point.c + 1 : 1]} as Record<string, number[]>)[event.key]
          if (direction) {event.preventDefault(); event.stopPropagation(); const next = {r: point.r + direction[0], c: point.c + direction[1]}; if (inRange(next, range)) choose(next, true)}
        }}>{value?.text.slice(0, 300)}{value && value.text.length > 300 ? '…' : ''}{value?.formula && <small className={"text-xs text-muted-foreground ml-1.5"}>ƒ</small>}</TableCell>
      })}</TableRow>)}</TableBody></Table> : <p className={"min-h-55 grid justify-items-start gap-3.5 max-w-115 m-auto [&_h3]:text-lg [&_h3]:font-semibold [&_p]:leading-relaxed [&_p]:text-muted-foreground"}>此工作表没有可显示的单元格。图形等内容请下载原件核对。</p>}
    </div>
    {range && <div className={"flex gap-1.5 flex-wrap items-center border-t py-1 px-2 shrink-0 text-xs text-muted-foreground max-md:gap-0.5"}><span>第 {origin.r + 1}–{end.r + 1} 行 / {range.e.r + 1}</span><Button variant="ghost" size="icon-sm" aria-label="前一组行" disabled={origin.r <= range.s.r} onClick={() => setOrigin(value => ({...value, r: Math.max(range.s.r, value.r - rowsPerView)}))}><ChevronLeft/></Button><Button variant="ghost" size="icon-sm" aria-label="后一组行" disabled={end.r >= range.e.r} onClick={() => setOrigin(value => ({...value, r: value.r + rowsPerView}))}><ChevronRight/></Button><span>{columnName(origin.c)}–{columnName(end.c)} 列 / {columnName(range.e.c)}</span><Button variant="ghost" size="icon-sm" aria-label="前一组列" disabled={origin.c <= range.s.c} onClick={() => setOrigin(value => ({...value, c: Math.max(range.s.c, value.c - columnsPerView)}))}><ChevronLeft/></Button><Button variant="ghost" size="icon-sm" aria-label="后一组列" disabled={end.c >= range.e.c} onClick={() => setOrigin(value => ({...value, c: value.c + columnsPerView}))}><ChevronRight/></Button></div>}
    <section className={"border-t py-2 px-2.5 max-h-45 overflow-auto shrink-0 text-sm [&>div]:flex [&>div]:flex-wrap [&>div]:gap-2 [&>div]:items-center [&>div]:justify-between [&_strong]:break-all [&_strong]:min-w-0 [&_pre]:my-1 [&_pre]:mx-0 [&_p]:break-all [&_p]:my-1 [&_p]:mx-0 [&_[role=status]:empty]:hidden max-md:max-h-36"} aria-label="选中单元格"><div><strong>{location}</strong>{onCite ? <Button variant="outline" size="sm" disabled={!range} onClick={() => onCite(location, `${cell?.text || '（空白）'}${cell?.formula ? `\n公式（未重算）：=${cell.formula}` : ''}`, {sheet: sheet.name, cell: cellName(selected)})}>引用此处</Button> : <Button variant="ghost" size="sm" disabled={!range} onClick={() => {void copy()}}><Copy/>复制位置与内容</Button>}</div><pre>{cell?.text || '（空白）'}</pre>{cell?.formula && <p>公式（未重算）：<code>={cell.formula}</code></p>}{cell && <Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">保存值与格式</Button></CollapsibleTrigger><CollapsibleContent><p>原始值：{cell.raw ?? '未保存结果'}</p><p>数值格式：{cell.format || '常规'}</p></CollapsibleContent></Collapsible>}<p role="status">{notice}</p></section>
  </div>
}
