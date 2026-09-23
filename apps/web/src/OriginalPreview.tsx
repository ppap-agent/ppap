import {forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState} from 'react'
import {apiUrl, originalUrl, request, type Material, type Finding} from './api'
import {parseExcerptCells} from './helpers'
import OfficePreview from './OfficePreview'
import Help from './Help'
import {Download} from 'lucide-react'
import {Button, Callout, CodeText, ExcerptCell, ExcerptCells, ExcerptList, ExcerptRow, Field, Heading, Inline, Input, Link, Pane, PlainText, PreviewFrame, Select, Stack, Text, VisuallyHidden} from './ui'

interface Excerpt {
  available: boolean; text?: string; notice: string; sheets?: string[]; sheet?: string
  rows?: {number: number; text: string}[]; start_row?: number; end_row?: number; next_row?: number | null
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

const OriginalPreview = forwardRef<OriginalPreviewHandle, {file?: Material; roundId?: string; finding?: Finding}>(function OriginalPreview({file, roundId, finding}, ref) {
  const [excerpt, setExcerpt] = useState<Excerpt | null>(null)
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
  const [office, setOffice] = useState<{available: boolean; script_url: string; config: Record<string, unknown>} | null>(null)
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
      const result = await request<Excerpt>(href.slice(apiUrl.length).replace(/\/download$/, '/preview'))
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
      const result = await request<Excerpt>(href.slice(apiUrl.length).replace(/\/download$/, '/preview') + '?' + new URLSearchParams({
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
          let result: {available: boolean; script_url: string; config: Record<string, unknown>} | null = null
          try { result = await request(`/files/${target.file_id}/versions/${target.file_version_id}/office${roundId ? `?round_id=${encodeURIComponent(roundId)}` : ''}`) }
          catch { /* Keep the authorized text preview available. */ }
          if (current !== revision.current) return
          if (result?.available) setOffice(result)
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

  function navigateSheet(nextSheet = sheet, nextStart = startRow) {
    setLookupRow(null)
    setLookupNote('')
    void readSheet(nextSheet, nextStart)
  }

  return (
    <Pane kind="original" testId="file-preview">
      <Inline justify="between" align="center">
        <Heading level={2}>原件<VisuallyHidden testId="file-preview-name"> · {file?.original_filename}</VisuallyHidden></Heading>
        <Inline gap="xs">
          {office && <Button type="button" variant="ghost" size="sm" disabled={loading} onClick={() => void showText()}>{textMode ? '排版预览' : '文字摘录'}</Button>}
          <Help title="预览说明"><Text>{file?.original_filename}</Text>{notice && <Text>{notice}</Text>}<Text>文字摘录不保留完整排版和图片；公式不执行，含宏文件不运行宏。请以原件为准。</Text></Help>
          {file && <Link data-testid="open-original" href={originalUrl(file, roundId)} target="_blank" rel="noopener" aria-label="下载原件"><Download size={18} aria-hidden="true" /></Link>}
        </Inline>
      </Inline>
      {loading && <Callout tone="loading">正在加载原件…</Callout>}
      {lookupNote && !lookupRow && <Callout>{lookupNote}</Callout>}
      {office && !textMode && <OfficePreview key={file?.file_version_id} scriptUrl={office.script_url} config={office.config} onFailed={officeFailed} />}
      {url && <PreviewFrame key={url} src={url} title={file?.original_filename || '原件预览'} />}
      {(!office || textMode) && (
        <Stack gap="sm">
          {(excerpt?.sheets?.length || sheet) && (
            <Inline gap="sm" align="end" wrap>
              <Field label="工作表">
                <Select value={sheet} disabled={loading || searching} onChange={event => { const value = event.target.value; setSheet(value); setStartRow(1); navigateSheet(value, 1) }}>
                  <option value="" disabled>选择工作表</option>
                  {(excerpt?.sheets || [sheet]).filter(Boolean).map(name => <option key={name} value={name}>{name}</option>)}
                </Select>
              </Field>
              <Field label="起始行">
                <Input value={startRow} onChange={event => setStartRow(Number(event.target.value) || 1)} type="number" min={1} max={1048576} inputMode="numeric" enterKeyHint="go" disabled={loading || searching} />
              </Field>
              <Button type="button" disabled={!sheet || loading || searching} onClick={() => navigateSheet()}>定位</Button>
            </Inline>
          )}
          {text && <Input aria-label="查找当前摘录" value={search} onChange={event => setSearch(event.target.value)} type="search" placeholder="查找文字或单元格，如 A35" enterKeyHint="search" autoCapitalize="none" />}
          {notice && !text && <Callout>{notice}</Callout>}
          {searching ? <Callout tone="loading">正在读取工作表…</Callout> : excerpt?.sheet ? (
            <Stack gap="sm">
              <Inline justify="between" align="center">
                <Text size="sm" tone="muted">摘录 · 第 {excerpt.start_row}–{excerpt.end_row || excerpt.start_row} 行 · 非完整原件</Text>
                <Button type="button" variant="ghost" size="sm" aria-pressed={rawExcerpt} onClick={() => setRawExcerpt(value => !value)}>{rawExcerpt ? '分格阅读' : '原始摘录'}</Button>
              </Inline>
              <ExcerptList label="工作表原文摘录" rootRef={excerptElement} onScroll={rememberPosition}>
                {excerptRows.map(row => (
                  <ExcerptRow key={row.number} lookup={row.number === lookupRow}>
                    <Inline justify="between">
                      <Text as="strong" weight="semibold">第 {row.number} 行</Text>
                      {row.number === lookupRow && <Text as="span" size="sm" tone="muted">查找线索 · 请核实</Text>}
                    </Inline>
                    {rawExcerpt
                      ? <CodeText>{row.text}</CodeText>
                      : <ExcerptCells>{parseExcerptCells(row.text).map((cell, index) => (
                          <ExcerptCell key={index} address={cell.address || '原文'} formula={cell.formula} value={cell.value} />
                        ))}</ExcerptCells>}
                  </ExcerptRow>
                ))}
                {!excerptRows.length && <Text>{search ? '当前摘录未找到匹配文字' : '此范围没有非空单元格'}</Text>}
              </ExcerptList>
              <Inline justify="between">
                <Button type="button" disabled={(excerpt.start_row || 1) <= 1} onClick={() => { const nextStart = Math.max(1, (excerpt.start_row || 1) - 200); setStartRow(nextStart); navigateSheet(sheet, nextStart) }}>前 200 行</Button>
                <Button type="button" disabled={!excerpt.next_row} onClick={() => { const nextStart = excerpt.next_row!; setStartRow(nextStart); navigateSheet(sheet, nextStart) }}>后续行 →</Button>
              </Inline>
            </Stack>
          ) : text ? <PlainText>{textLines.join('\n') || '当前摘录未找到匹配文字'}</PlainText> : null}
        </Stack>
      )}
    </Pane>
  )
})

export default OriginalPreview
