import {useEffect, useId, useRef, useState} from 'react'
import {LoaderCircle} from 'lucide-react'

interface Editor {destroyEditor: () => void}
declare global {
  interface Window {DocsAPI?: {DocEditor: new (id: string, config: Record<string, unknown>) => Editor}}
}

let loadedScript: Promise<void> | undefined
let scriptSource = ''
function loadEditor(src: string) {
  if (scriptSource && scriptSource !== src) return Promise.reject(new Error('Office 服务地址已变化，请刷新页面。'))
  if (loadedScript) return loadedScript
  scriptSource = src
  loadedScript = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = src
    script.async = true
    script.referrerPolicy = 'no-referrer'
    const timer = setTimeout(() => { script.remove(); loadedScript = undefined; reject(new Error('Office 服务连接超时')) }, 15000)
    script.onload = () => { clearTimeout(timer); resolve() }
    script.onerror = () => { clearTimeout(timer); script.remove(); loadedScript = undefined; reject(new Error('Office 服务不可用')) }
    document.head.append(script)
  })
  return loadedScript
}

export default function OfficePreview({scriptUrl, config, onFailed}: {scriptUrl: string; config: Record<string, unknown>; onFailed: () => void}) {
  const reactId = useId()
  const id = useRef('office-' + reactId.replace(/:/g, '')).current
  const [loading, setLoading] = useState(true)
  const onFailedRef = useRef(onFailed)
  onFailedRef.current = onFailed
  const configRef = useRef(config)
  configRef.current = config
  useEffect(() => {
    let disposed = false
    let editor: Editor | undefined
    void (async () => {
      try {
        await loadEditor(scriptUrl)
        if (disposed) return
        if (!window.DocsAPI) throw new Error('Office 服务未就绪')
        editor = new window.DocsAPI.DocEditor(id, {...configRef.current, events: {onDocumentReady: () => { setLoading(false) }, onError: () => { if (!disposed) onFailedRef.current() }}})
      } catch { if (!disposed) onFailedRef.current() }
    })()
    return () => { disposed = true; editor?.destroyEditor() }
  }, [scriptUrl, id])
  return <div className="relative h-[min(72dvh,54rem)] min-h-[28rem] w-full overflow-hidden rounded-lg border bg-muted/20">
    <div id={id} className="size-full" />
    {loading && <div className="absolute inset-0 grid place-items-center bg-background/80" role="status">
      <div className="flex items-center gap-2 rounded-lg border bg-background px-3 py-2 text-sm text-muted-foreground shadow-sm">
        <LoaderCircle className="size-4 animate-spin text-primary" aria-hidden="true" />
        <span>正在加载 Office 排版预览…</span>
      </div>
    </div>}
  </div>
}
