import {forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState} from 'react'
import {apiUrl, originalUrl, request, type Material, type Finding, type OfficePreviewResponse, type PreviewResponse, type SemanticSource} from './api'
import {parseExcerptCells, sourceLocation} from './helpers'
import OfficePreview from './OfficePreview'
import Help from './Help'
import {Download} from 'lucide-react'
import {Alert, AlertDescription} from '@/components/ui/alert'
import {Button} from '@/components/ui/button'
import {Input} from '@/components/ui/input'
import {NativeSelect} from '@/components/ui/native-select'
import {CodeText, ExcerptCell, ExcerptCells, ExcerptList, ExcerptRow, Pane, PlainText, PreviewFrame} from './ui/domain'

type Excerpt = PreviewResponse
type OfficePreviewConfig = OfficePreviewResponse & {
  available: true
  script_url: string
  config: Record<string, unknown>
}

interface OfficePreviewCacheEntry {
  value: OfficePreviewConfig
  expiresAt: number
}

// Office tickets are deliberately short-lived (the API issues them for ten
// minutes). Keep the config in memory only, and for at most five minutes, so
// revisiting the same file does not create another ticket while never turning
// signed file URLs into persistent browser data.
const officePreviewCache = new Map<string, OfficePreviewCacheEntry>()
const OFFICE_PREVIEW_CACHE_MS = 5 * 60 * 1000

function officePreviewCacheKey(file: Material, roundId?: string) {
  return `${roundId || 'draft'}:${file.file_id}:${file.file_version_id}`
}

function officePreviewCacheExpiry(value: OfficePreviewConfig) {
  const now = Date.now()
  const configuredExpiry = typeof value.config.exp === 'number' ? value.config.exp * 1000 - 30_000 : now + OFFICE_PREVIEW_CACHE_MS
  return Math.min(now + OFFICE_PREVIEW_CACHE_MS, configuredExpiry)
}

async function readOfficePreview(file: Material, roundId?: string): Promise<OfficePreviewResponse | null> {
  const key = officePreviewCacheKey(file, roundId)
  const cached = officePreviewCache.get(key)
  if (cached && cached.expiresAt > Date.now()) return cached.value
  if (cached) officePreviewCache.delete(key)

  const result = await request<OfficePreviewResponse>(`/files/${file.file_id}/versions/${file.file_version_id}/office${roundId ? `?round_id=${encodeURIComponent(roundId)}` : ''}`)
  if (result.available && result.script_url && result.config) {
    const ready: OfficePreviewConfig = {
      ...result,
      available: true,
      script_url: result.script_url,
      config: result.config,
    }
    const expiresAt = officePreviewCacheExpiry(ready)
    if (expiresAt > Date.now()) officePreviewCache.set(key, {value: ready, expiresAt})
    return ready
  }
  return result
}

interface PreviewPosition {
  sheet: string; startRow: number; textMode: boolean; rawExcerpt: boolean
  search: string; lookupRow: number | null; finding: string
  scrollTop: number; pageTop: number
}

function readPosition(key: string): PreviewPosition | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (!saved || typeof saved.sheet !== 'string' || typeof saved.search !== 'string') return null
    if (![saved.startRow, saved.scrollTop, saved.pageTop].every(Number.isFinite)) return null
    return {...saved, startRow: Math.max(1, Math.min(1048576, saved.startRow))}
  } catch { return null }
}

export interface OriginalPreviewHandle {findEvidence: () => Promise<void>}

const OriginalPreview = forwardRef<OriginalPreviewHandle, {file?: Material; roundId?: string; finding?: Finding; source?: SemanticSource | null}>(function OriginalPreview({file, roundId, finding, source}, ref) {
  const [excerpt, setExcerpt] = useState<PreviewResponse | null>(null)
  const [sheet, setSheet] = useState('')
  const [startRow, setStartRow] = useState(1)
  const [search, setSearch] = useState('')
  const [searching, setSearching] = useState(false)
  const [lookupNote, setLookupNote] = useState('')
  const [lookupRow, setLookupRow] = useState<number | null>(null)
  const [rawExcerpt, setRawExcerpt] = useState(false)
  const excerptElement = useRef<HTMLDivElement>(null)
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [notice, setNotice] = useState('请选择一份文件。')
  const [loading, setLoading] = useState(false)
  const [textMode, setTextMode] = useState(false)
  const [office, setOffice] = useState<OfficePreviewConfig | null>(null)
  const storageKey = `ppap-view:preview:${roundId || 'draft'}:${file?.file_version_id || ''}`
  const [readyKey, setReadyKey] = useState('')
  const pendingLookup = useRef(false)
  const pendingRestore = useRef<PreviewPosition | null>(null)
  const positionRef = useRef<PreviewPosition | null>(null)
  positionRef.current = readyKey === storageKey ? {
    sheet, startRow, textMode: textMode || (!office && !!excerpt), rawExcerpt, search, lookupRow, finding: finding?.item_key || '',
    scrollTop: excerptElement.current?.scrollTop || 0, pageTop: window.scrollY,
  } : null
  const revision = useRef(0)
  const excerptRevision = useRef(0)
  const controller = useRef<AbortController | undefined>(undefined)
  const fileRef = useRef(file)
  fileRef.current = file
  const roundRef = useRef(roundId)
  roundRef.current = roundId
  const findingRef = useRef(finding)
  findingRef.current = finding

  const excerptRows = useMemo(
    () => excerpt?.rows?.filter(row => !search || row.text.toLocaleLowerCase().includes(search.toLocaleLowerCase())) || [],
    [excerpt, search],
  )
  const textLines = useMemo(
    () => (text || '').split('\n').filter(line => !search || line.toLocaleLowerCase().includes(search.toLocaleLowerCase())),
    [text, search],
  )

  function clear() {
    pendingLookup.current = false
    pendingRestore.current = null
    controller.current?.abort()
    excerptRevision.current += 1
    setExcerpt(null)
    setSheet('')
    setStartRow(1)
    setSearch('')
    setLookupNote('')
    setLookupRow(null)
    setSearching(false)
    setUrl(current => { if (current) URL.revokeObjectURL(current); return '' })
    setText('')
    setOffice(null)
    setTextMode(false)
    setRawExcerpt(false)
  }

  async function fallback(target: Material, current: number, reason = '') {
    const lookup = ++excerptRevision.current
    try {
      const href = originalUrl(target, roundRef.current)
      const result = await request<PreviewResponse>(href.slice(apiUrl.length).replace(/\/download$/, '/preview'))
      if (current !== revision.current || lookup !== excerptRevision.current) return null
      setExcerpt(result)
      setText(result.available ? result.text || '' : '')
      setNotice([reason, result.notice].filter(Boolean).join(' '))
      return result
    } catch {
      if (current === revision.current && lookup === excerptRevision.current) setNotice('无法预览，请下载原件。')
      return null
    }
  }

  async function readSheet(nextSheet = sheet, nextStart = startRow) {
    const target = fileRef.current
    if (!target || !nextSheet) return
    const current = ++excerptRevision.current
    setSearching(true)
    try {
      const href = originalUrl(target, roundRef.current)
      const result = await request<PreviewResponse>(href.slice(apiUrl.length).replace(/\/download$/, '/preview') + '?' + new URLSearchParams({
        sheet: nextSheet,
        start_row: String(Math.max(1, Math.min(1048576, Number(nextStart) || 1))),
      }))
      if (current !== excerptRevision.current) return
      setExcerpt(result)
      setText(result.text || '')
      setNotice(result.notice)
    } catch {
      if (current === excerptRevision.current) { setExcerpt(null); setText(''); setNotice('无法读取工作表，请重试或下载原件。') }
    } finally {
      if (current === excerptRevision.current) setSearching(false)
    }
  }

  // React must commit the fetched rows before we can focus or scroll them.
  useLayoutEffect(() => {
    if (loading || searching || !excerptElement.current?.getClientRects().length) return
    if (pendingRestore.current) {
      const saved = pendingRestore.current
      pendingRestore.current = null
      excerptElement.current.scrollTop = Math.max(0, saved.scrollTop)
      window.scrollTo({top: Math.max(0, saved.pageTop)})
      const bounds = excerptElement.current.getBoundingClientRect()
      // The rule link is above the preview. Returning should reveal the reading
      // position, not leave it below the viewport where that link was clicked.
      if (saved.scrollTop > 0 && (bounds.top >= window.innerHeight || bounds.bottom <= 0)) {
        excerptElement.current.scrollIntoView({block: 'start'})
      }
    }
    if (pendingLookup.current) {
      pendingLookup.current = false
      const row = excerptElement.current.querySelector<HTMLElement>('[data-lookup="true"]')
      row?.focus({preventScroll: true})
      const target = row || excerptElement.current
      target.scrollIntoView({block: 'start'})
    }
  })

  function rememberPosition() {
    if (!positionRef.current || loading || searching) return
    try {
      const visible = !!excerptElement.current?.getClientRects().length
      const saved = visible ? null : readPosition(storageKey)
      sessionStorage.setItem(storageKey, JSON.stringify({
        ...positionRef.current,
        scrollTop: visible ? excerptElement.current!.scrollTop : saved?.scrollTop || 0,
        pageTop: visible ? window.scrollY : saved?.pageTop ?? window.scrollY,
      }))
    } catch { /* Reading works even when browser storage is unavailable. */ }
  }

  useEffect(() => {
    window.addEventListener('pagehide', rememberPosition)
    document.addEventListener('click', rememberPosition)
    return () => {
      window.removeEventListener('pagehide', rememberPosition)
      document.removeEventListener('click', rememberPosition)
    }
  }, [storageKey, loading, searching])

  async function findEvidence() {
    const target = fileRef.current
    if (!target) return
    if (url) { setLookupNote('请用原件阅读器查找检查说明中的页码或文字。'); return }
    const current = revision.current
    const key = findingRef.current?.item_key
    setTextMode(true)
    setLoading(true)
    setLookupRow(null)
    const loaded = await fallback(target, current, '')
    if (current !== revision.current) return
    setLoading(false)
    if (key !== findingRef.current?.item_key) return
    const hint = [findingRef.current?.locator?.locator, findingRef.current?.suggestions, ...(findingRef.current?.locator?.evidence?.sources || []).map(source => source.locator)].filter(Boolean).join(' ')
    const named = loaded?.sheets?.filter(name => hint.includes(name)).sort((a, b) => b.length - a.length)[0]
    setSearch('')
    if (named) {
      setSheet(named)
      const after = hint.slice(hint.indexOf(named) + named.length)
      const row = after.match(/^\s*(?:[，,:：!；;]\s*)?(?:第\s*)?(?:行\s*(\d+)|(\d+)\s*行)/)
      const looked = row ? Math.max(1, Math.min(1048576, Number(row[1] || row[2]))) : null
      setLookupRow(looked)
      const nextStart = looked ? Math.max(1, looked - 2) : 1
      setStartRow(nextStart)
      setLookupNote('按检查说明查找的位置，请对照下方原文核实。')
      pendingLookup.current = true
      await readSheet(named, nextStart)
    } else {
      setLookupNote('未识别到明确工作表，请选择工作表或输入文字查找。')
    }
  }

  useImperativeHandle(ref, () => ({findEvidence}))

  useEffect(() => {
    setLookupNote('')
    setLookupRow(null)
    setSearch('')
  }, [finding?.item_key])

  function officeFailed() {
    setOffice(null)
    if (fileRef.current) officePreviewCache.delete(officePreviewCacheKey(fileRef.current, roundRef.current))
    if (fileRef.current) void fallback(fileRef.current, revision.current, 'Office 预览暂不可用，以下为文字摘录。')
  }

  async function showText() {
    setTextMode(current => !current)
    if (!textMode && fileRef.current && !text) {
      setLoading(true)
      const current = revision.current
      await fallback(fileRef.current, current, '文字摘录不执行宏，也不保留原件排版。')
      if (current === revision.current) setLoading(false)
    }
  }

  useEffect(() => {
    const current = ++revision.current
    clear()
    setReadyKey('')
    if (!file) { setNotice('请选择一份文件。'); setLoading(false); return }
    const target = file
    setLoading(true)
    setNotice('')
    void (async () => {
      try {
        const href = originalUrl(target, roundId)
        if (/\.(pdf|png|jpe?g|gif|webp)$/i.test(target.original_filename)) {
          controller.current = new AbortController()
          const response = await fetch(href, {credentials: 'include', signal: controller.current.signal})
          if (!response.ok) throw new Error('无法加载原件，请检查权限。')
          const data = await response.arrayBuffer()
          if (current !== revision.current) return
          const extension = target.original_filename.split('.').pop()!.toLowerCase()
          const mime = ({pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp'} as Record<string, string>)[extension]
          setUrl(URL.createObjectURL(new Blob([data], {type: mime || 'application/octet-stream'})))
        } else {
          let result: OfficePreviewResponse | null = null
          try { result = await readOfficePreview(target, roundId) }
          catch { /* Keep the authorized text preview available. */ }
          if (current !== revision.current) return
          if (result?.available && result.script_url && result.config) setOffice({...result, available: true, script_url: result.script_url, config: result.config})
          const saved = readPosition(storageKey)
          if (saved?.textMode) {
            setTextMode(true)
            const loaded = await fallback(target, current)
            if (current !== revision.current) return
            setRawExcerpt(!!saved.rawExcerpt)
            setSearch(saved.search)
            if (saved.sheet && loaded?.sheets?.includes(saved.sheet)) {
              setSheet(saved.sheet)
              setStartRow(saved.startRow)
              const sameFinding = saved.finding === (findingRef.current?.item_key || '')
              setLookupRow(sameFinding && Number.isInteger(saved.lookupRow) ? saved.lookupRow : null)
              if (sameFinding && saved.lookupRow) setLookupNote('按检查说明查找的位置，请对照下方原文核实。')
              await readSheet(saved.sheet, saved.startRow)
              if (current !== revision.current) return
              pendingRestore.current = saved
            }
          } else if (!result?.available) await fallback(target, current)
        }
      } catch (error) {
        if (current === revision.current) setNotice(error instanceof Error ? error.message : '无法预览，请下载原件。')
      } finally {
        if (current === revision.current) { setLoading(false); setReadyKey(storageKey) }
      }
    })()
    return () => { revision.current += 1; controller.current?.abort() }
  }, [file?.file_version_id, roundId])

  const sourceKey = source ? `${source.file_version_id}:${JSON.stringify(source.locator)}` : ''
  useEffect(() => {
    if (!source || !file || source.file_version_id !== file.file_version_id || readyKey !== storageKey || loading) return
    excerptRevision.current += 1
    pendingLookup.current = false
    const position = source.locator
    if (position.kind === 'xlsx_cell' || position.kind === 'xlsx_range') {
      const address = position.kind === 'xlsx_cell' ? position.cell : position.start
      const row = Number(address.match(/\d+/)?.[0])
      if (!Number.isInteger(row) || row < 1) {
        setLookupNote('证据坐标无法用于摘录定位，请按来源说明核对原件。')
        return
      }
      const nextStart = Math.max(1, row - 2)
      setTextMode(true)
      setSheet(position.sheet)
      setStartRow(nextStart)
      setLookupRow(row)
      setLookupNote(`按来源坐标 ${sourceLocation(source)} 打开相应行；请核对单元格与原件。`)
      pendingLookup.current = true
      void readSheet(position.sheet, nextStart)
    } else {
      setLookupRow(null)
      setLookupNote(`来源位置：${sourceLocation(source)}。当前阅读器不支持精确跳转，请结合下方摘录核对原件。`)
    }
  }, [sourceKey, readyKey, storageKey, loading])

  function navigateSheet(nextSheet = sheet, nextStart = startRow) {
    setLookupRow(null)
    setLookupNote('')
    void readSheet(nextSheet, nextStart)
  }

  return (
    <Pane kind="original" testId="file-preview">
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3">
        <h2 className="text-lg font-semibold tracking-tight">{file && !loading && !url && (textMode || (!office && excerpt?.available)) ? '文字摘录' : '原件'}<span data-testid="file-preview-name" className="sr-only"> · {file?.original_filename}</span></h2>
        <div className="flex items-center gap-1">
          {office && <Button type="button" variant="ghost" size="sm" disabled={loading} onClick={() => void showText()}>{textMode ? '排版预览' : '文字摘录'}</Button>}
          <Help title="预览说明"><p className="m-0">{file?.original_filename}</p>{notice && <p className="m-0">{notice}</p>}<p className="m-0">文字摘录不保留完整排版和图片；公式不执行，含宏文件不运行宏。请以原件为准。</p></Help>
          {file && <Button asChild variant="ghost" size="icon" data-testid="open-original"><a href={originalUrl(file, roundId)} target="_blank" rel="noopener" aria-label="下载原件"><Download className="size-4" /></a></Button>}
        </div>
      </div>
      {loading && <p className="m-0 py-2 text-sm text-muted-foreground" role="status">正在准备原件…</p>}
      {source && source.file_version_id !== file?.file_version_id && <Alert variant="destructive"><AlertDescription>本轮无法读取该来源文件版本。</AlertDescription></Alert>}
      {lookupNote && <p className="m-0 border-l-2 border-primary/40 py-1 pl-3 text-sm text-muted-foreground" role="status">{lookupNote}</p>}
      {source?.excerpt && <div className="border-l-2 border-muted-foreground/30 pl-3"><strong className="text-sm">引用摘录 · {source.role === 'target' ? '待审文件' : '参考资料'} · {sourceLocation(source)}</strong><PlainText>{source.excerpt}</PlainText></div>}
      {office && !textMode && <OfficePreview key={file?.file_version_id} scriptUrl={office.script_url} config={office.config} onFailed={officeFailed} />}
      {url && <PreviewFrame key={url} src={url} title={file?.original_filename || '原件预览'} />}
      {(!office || textMode) && (
        <div className="flex flex-col gap-3 pt-1">
          {(excerpt?.sheets?.length || sheet) && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="grid gap-1.5 text-sm font-medium">工作表<NativeSelect value={sheet} disabled={loading || searching} onChange={event => { const value = event.target.value; setSheet(value); setStartRow(1); navigateSheet(value, 1) }}><option value="" disabled>选择工作表</option>{(excerpt?.sheets || [sheet]).filter(Boolean).map(name => <option key={name} value={name}>{name}</option>)}</NativeSelect></label>
              <label className="grid gap-1.5 text-sm font-medium">起始行<Input value={startRow} onChange={event => setStartRow(Number(event.target.value) || 1)} type="number" min={1} max={1048576} inputMode="numeric" enterKeyHint="go" disabled={loading || searching} /></label>
              <Button type="button" disabled={!sheet || loading || searching} onClick={() => navigateSheet()}>定位</Button>
            </div>
          )}
          {text && <Input aria-label="查找当前摘录" value={search} onChange={event => setSearch(event.target.value)} type="search" placeholder="查找文字或单元格，如 A35" enterKeyHint="search" autoCapitalize="none" />}
          {notice && !text && <p className="m-0 text-sm text-muted-foreground">{notice}</p>}
          {searching ? <p className="m-0 text-sm text-muted-foreground" role="status">正在读取工作表…</p> : excerpt?.sheet ? (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm text-muted-foreground">摘录 · 第 {excerpt.start_row}–{excerpt.end_row || excerpt.start_row} 行 · 非完整原件</span><Button type="button" variant="ghost" size="sm" aria-pressed={rawExcerpt} onClick={() => setRawExcerpt(value => !value)}>{rawExcerpt ? '分格阅读' : '原始摘录'}</Button></div>
              <ExcerptList label="工作表原文摘录" rootRef={excerptElement} onScroll={rememberPosition}>
                {excerptRows.map(row => (
                  <ExcerptRow key={row.number} lookup={row.number === lookupRow}>
                    <div className="flex items-center justify-between gap-2"><strong>第 {row.number} 行</strong>{row.number === lookupRow && <span className="text-sm text-muted-foreground">查找线索 · 请核实</span>}</div>
                    {rawExcerpt ? <CodeText>{row.text}</CodeText> : <ExcerptCells>{parseExcerptCells(row.text).map((cell, index) => <ExcerptCell key={index} address={cell.address || '原文'} formula={cell.formula} value={cell.value} />)}</ExcerptCells>}
                  </ExcerptRow>
                ))}
                {!excerptRows.length && <p className="m-0 p-3">{search ? '当前摘录未找到匹配文字' : '此范围没有非空单元格'}</p>}
              </ExcerptList>
              <div className="flex items-center justify-between gap-2"><Button type="button" disabled={(excerpt.start_row || 1) <= 1} onClick={() => { const nextStart = Math.max(1, (excerpt.start_row || 1) - 200); setStartRow(nextStart); navigateSheet(sheet, nextStart) }}>前 200 行</Button><Button type="button" disabled={!excerpt.next_row} onClick={() => { const nextStart = excerpt.next_row!; setStartRow(nextStart); navigateSheet(sheet, nextStart) }}>后续行 →</Button></div>
            </div>
          ) : text ? <PlainText>{textLines.join('\n') || '当前摘录未找到匹配文字'}</PlainText> : null}
        </div>
      )}
    </Pane>
  )
})

export default OriginalPreview
