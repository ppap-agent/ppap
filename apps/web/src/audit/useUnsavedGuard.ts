import {useEffect, useRef, useState} from 'react'

export function useUnsavedGuard({
  dirty,
  busy,
  remember,
  saveCurrent,
  discardCurrent,
}: {
  dirty: boolean
  busy: boolean
  remember: () => void
  saveCurrent: () => Promise<boolean>
  discardCurrent: () => void
}) {
  const [pendingChange, setPendingChange] = useState<(() => void) | null>(null)
  const dirtyRef = useRef(dirty)
  const busyRef = useRef(busy)
  const pendingRef = useRef<(() => void) | null>(pendingChange)
  const rememberRef = useRef(remember)
  const saveRef = useRef(saveCurrent)
  const discardRef = useRef(discardCurrent)

  dirtyRef.current = dirty
  busyRef.current = busy
  pendingRef.current = pendingChange
  rememberRef.current = remember
  saveRef.current = saveCurrent
  discardRef.current = discardCurrent

  function guardChange(work: () => void) {
    if (busyRef.current || pendingRef.current) return
    if (dirtyRef.current) setPendingChange(() => work)
    else work()
  }

  async function resolveChange(save: boolean) {
    if (save) {
      if (!await saveRef.current()) return
    } else {
      discardRef.current()
    }
    const work = pendingRef.current
    setPendingChange(null)
    await Promise.resolve()
    work?.()
  }

  useEffect(() => {
    function guardLeave(event: BeforeUnloadEvent) {
      rememberRef.current()
      if (dirtyRef.current) {
        event.preventDefault()
        event.returnValue = ''
      }
    }

    function guardNavigation(event: Event) {
      if (!(event instanceof MouseEvent)) return
      const link = (event.target as Element)?.closest<HTMLAnchorElement>('a[href]')
      if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return
      const url = new URL(link.href)
      if (url.origin !== location.origin || (url.pathname === location.pathname && url.search === location.search && url.hash)) return
      rememberRef.current()
      if (!dirtyRef.current) return
      event.preventDefault()
      guardChange(() => { location.href = link.href })
    }

    function rememberPage() {
      rememberRef.current()
    }

    window.addEventListener('beforeunload', guardLeave)
    window.addEventListener('pagehide', rememberPage)
    document.addEventListener('click', guardNavigation)
    return () => {
      rememberRef.current()
      window.removeEventListener('beforeunload', guardLeave)
      window.removeEventListener('pagehide', rememberPage)
      document.removeEventListener('click', guardNavigation)
    }
  }, [])

  return {pendingChange, setPendingChange, guardChange, resolveChange}
}
