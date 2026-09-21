import {forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState} from 'react'
import {apiUrl, originalUrl, request, type Material, type Finding} from './api'
import {parseExcerptCells} from './helpers'
import OfficePreview from './OfficePreview'

interface Excerpt {
  available: boolean; text?: string; notice: string; sheets?: string[]; sheet?: string
  rows?: {number: number; text: string}[]; start_row?: number; end_row?: number; next_row?: number | null
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

  async function revealLookup() {
    await Promise.resolve()
    const row = excerptElement.current?.querySelector<HTMLElement>('.is-lookup')
    row?.focus({preventScroll: true})
    row?.scrollIntoView({block: 'nearest'})
  }

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
      await readSheet(named, nextStart)
      await revealLookup()
    } else {
      setLookupNote('未识别到明确工作表，请选择工作表或输入文字查找。')
    }
  }

  const excerptRef = useRef(excerpt)
  excerptRef.current = excerpt

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
          else await fallback(target, current)
        }
      } catch (error) {
        if (current === revision.current) setNotice(error instanceof Error ? error.message : '无法预览，请下载原件。')
      } finally {
        if (current === revision.current) setLoading(false)
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
    <section className="audit-original" data-testid="file-preview">
      <header><h2>原件预览</h2>{file && <a href={originalUrl(file, roundId)} target="_blank" rel="noopener" data-testid="open-original">下载原件</a>}</header>
      <p className="preview-filename" data-testid="file-preview-name">{file?.original_filename}</p>
      {loading && <p role="status">正在加载原件…</p>}
      {office && <button type="button" disabled={loading} onClick={() => void showText()}>{textMode ? '返回排版预览' : '查看文字摘录'}</button>}
      {lookupNote && <p className="preview-notice" role="status">{lookupNote}</p>}
      {office && /\.xlsm$/i.test(file?.original_filename || '') && <p className="pc-muted preview-notice">含宏文件不运行宏。内容不可见时，可切换文字摘录或下载核对。</p>}
      {office && !textMode && <OfficePreview key={file?.file_version_id} scriptUrl={office.script_url} config={office.config} onFailed={officeFailed} />}
      {url && <iframe key={url} src={url} title={file?.original_filename || '原件预览'} sandbox="allow-same-origin" referrerPolicy="no-referrer" />}
      {(!office || textMode) && (
        <>
          {(excerpt?.sheets?.length || sheet) && (
            <form className="excerpt-toolbar" onSubmit={event => { event.preventDefault(); navigateSheet() }}>
              <label>工作表
                <select value={sheet} disabled={loading || searching} onChange={event => { const value = event.target.value; setSheet(value); setStartRow(1); navigateSheet(value, 1) }}>
                  <option value="" disabled>选择工作表</option>
                  {(excerpt?.sheets || [sheet]).filter(Boolean).map(name => <option key={name} value={name}>{name}</option>)}
                </select>
              </label>
              <label>起始行
                <input value={startRow} onChange={event => setStartRow(Number(event.target.value) || 1)} type="number" min={1} max={1048576} inputMode="numeric" enterKeyHint="go" disabled={loading || searching} />
              </label>
              <button type="submit" disabled={!sheet || loading || searching}>定位</button>
            </form>
          )}
          {text && <label className="excerpt-search">查找当前摘录<input value={search} onChange={event => setSearch(event.target.value)} type="search" placeholder="输入文字或单元格，如 A35" enterKeyHint="search" autoCapitalize="none" /></label>}
          {notice && <p className="preview-notice pc-muted">{notice}</p>}
          {searching ? <p role="status">正在读取工作表…</p> : excerpt?.sheet ? (
            <>
              <div className="excerpt-caption"><span>{excerpt.sheet} · 第 {excerpt.start_row}–{excerpt.end_row || excerpt.start_row} 行</span><button type="button" className="text-button" aria-pressed={rawExcerpt} onClick={() => setRawExcerpt(value => !value)}>{rawExcerpt ? '分格阅读' : '原始摘录'}</button></div>
              <div ref={excerptElement} className="excerpt-rows" aria-label="工作表原文摘录" tabIndex={0}>
                {excerptRows.map(row => (
                  <article key={row.number} className={'excerpt-row' + (row.number === lookupRow ? ' is-lookup' : '')} tabIndex={-1}>
                    <header><strong>第 {row.number} 行</strong>{row.number === lookupRow && <span>查找线索 · 请核实</span>}</header>
                    {rawExcerpt
                      ? <code className="excerpt-raw">{row.text}</code>
                      : <dl className="excerpt-cells">{parseExcerptCells(row.text).map((cell, index) => (
                          <div key={index} className={cell.formula ? 'is-formula' : undefined}>
                            <dt>{cell.address || '原文'}{cell.formula && <span>公式</span>}</dt>
                            <dd>{cell.formula ? <code>{cell.value}</code> : cell.value}</dd>
                          </div>
                        ))}</dl>}
                  </article>
                ))}
                {!excerptRows.length && <p className="empty-state compact">{search ? '当前摘录未找到匹配文字' : '此范围没有非空单元格'}</p>}
              </div>
              <div className="excerpt-pagination">
                <button type="button" disabled={(excerpt.start_row || 1) <= 1} onClick={() => { const nextStart = Math.max(1, (excerpt.start_row || 1) - 200); setStartRow(nextStart); navigateSheet(sheet, nextStart) }}>前 200 行</button>
                <button type="button" disabled={!excerpt.next_row} onClick={() => { const nextStart = excerpt.next_row!; setStartRow(nextStart); navigateSheet(sheet, nextStart) }}>后续行 →</button>
              </div>
            </>
          ) : text ? <pre>{textLines.join('\n') || '当前摘录未找到匹配文字'}</pre> : null}
        </>
      )}
    </section>
  )
})

export default OriginalPreview
