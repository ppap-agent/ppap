// The frozen original, shown by OnlyOffice in view mode. The file comes from the audit's
// own file version; when the result points at a spreadsheet cell the viewer goes there.
import {useEffect, useId, useRef, useState} from 'react'
import type {components} from '../generated/openapi'

type Locator = components['schemas']['AuditEvidenceReference']['locator']
export type Evidence = {file: components['schemas']['FileVersion']; locator: Locator; label: string | null; excerpt: string; clause: string}
type Office = components['schemas']['OfficePreviewResponse']
type Editor = {destroyEditor: () => void}
declare global {
  interface Window {DocsAPI?: {DocEditor: new (id: string, config: Record<string, unknown>) => Editor}}
}

let script: Promise<void> | undefined
function loadScript(src: string) {
  script ??= new Promise<void>((resolve, reject) => {
    const tag = document.createElement('script')
    tag.src = src
    tag.referrerPolicy = 'no-referrer'
    tag.onload = () => resolve()
    tag.onerror = () => { tag.remove(); script = undefined; reject(new Error('office')) }
    document.head.append(tag)
  })
  return script
}

/** Ask the spreadsheet viewer to show the cell. Best effort: the viewer still opens the file without it. */
function goTo(frame: HTMLIFrameElement | null, locator: Locator) {
  if (!frame || !locator || locator.kind !== 'xlsx_cell' && locator.kind !== 'xlsx_range') return
  try {
    const editor = (frame.contentWindow as unknown as {Asc?: {editor?: Record<string, (...args: unknown[]) => unknown>}} | null)?.Asc?.editor
    if (!editor) return
    const count = Number(editor.asc_getWorksheetsCount())
    for (let index = 0; index < count; index += 1) if (editor.asc_getWorksheetName(index) === locator.sheet) editor.asc_showWorksheet(index)
    window.setTimeout(() => { try { editor.asc_findCell(locator.kind === 'xlsx_cell' ? locator.cell : `${locator.start}:${locator.end}`) } catch { /* the file is still shown */ } }, 300)
  } catch { /* another origin or a different viewer build: the file is still shown */ }
}

export function OfficeView({evidence, roundId, call, onClose, onOpenWeb}: {evidence: Evidence; roundId: string; call: <T>(path: string) => Promise<T>; onClose: () => void; onOpenWeb: () => void}) {
  const id = 'office-' + useId().replace(/:/g, '')
  const [state, setState] = useState<'loading' | 'ready' | 'unavailable'>('loading')
  const [notice, setNotice] = useState('')
  const holder = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let disposed = false
    let editor: Editor | undefined
    void (async () => {
      try {
        const office = await call<Office>(`/files/${evidence.file.file_id}/versions/${evidence.file.id}/office?round_id=${roundId}`)
        if (disposed) return
        if (!office.available || !office.script_url || !office.config) { setNotice(office.notice || '这份原件暂时不能在这里查看。'); setState('unavailable'); return }
        await loadScript(office.script_url)
        if (disposed || !window.DocsAPI) throw new Error('office')
        editor = new window.DocsAPI.DocEditor(id, {...office.config, events: {
          onDocumentReady: () => { if (!disposed) { setState('ready'); goTo(holder.current?.querySelector('iframe') ?? null, evidence.locator) } },
          onError: () => { if (!disposed) { setNotice('原件没有打开成功。'); setState('unavailable') } },
        }})
      } catch { if (!disposed) { setNotice('原件没有打开成功。'); setState('unavailable') } }
    })()
    return () => { disposed = true; editor?.destroyEditor() }
  }, [call, evidence, id, roundId])
  const where = evidence.label
  return (
    <section className="flex h-full flex-col gap-2" aria-label="对照原件">
      <header className="flex items-center gap-3">
        <button type="button" className="text-primary underline-offset-4 hover:underline disabled:opacity-50" onClick={onClose}>返回审核结果</button>
        <span className="min-w-0 truncate" title={evidence.file.original_filename}>{evidence.file.original_filename}</span>
      </header>
      <p className="line-clamp-3">{evidence.clause}</p>
      {(where || evidence.excerpt) && <p className="text-muted-foreground text-xs">{where}{where && evidence.excerpt ? '：' : ''}{evidence.excerpt}</p>}
      {state === 'loading' && <p role="status" className="text-muted-foreground">正在打开原件…</p>}
      {state === 'unavailable' && <p className="mb-2 rounded-md bg-warning/10 px-3 py-2 text-warning">{notice}<button type="button" className="text-primary underline-offset-4 hover:underline disabled:opacity-50" onClick={onOpenWeb}>在 PPAP 中查看</button></p>}
      <div className="min-h-80 flex-1 overflow-hidden rounded-md border [&_iframe]:size-full [&>div]:size-full" ref={holder} hidden={state === 'unavailable'}><div id={id} /></div>
    </section>
  )
}
