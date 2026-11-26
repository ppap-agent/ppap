import {useEffect, useState} from 'react'

/**
 * Below 1280px (design.md「Viewport」). Only layout pieces read it (split panes, drawers); a page never branches on it
 * (apps/web/README「窄屏不另起一套」). Prefer a CSS breakpoint wherever one is enough.
 */
export const narrowQuery = '(width < 80rem)'
export const narrowNow = () => typeof window !== 'undefined' && window.matchMedia(narrowQuery).matches

export function useNarrow() {
  const [narrow, setNarrow] = useState(narrowNow)
  useEffect(() => {
    const media = window.matchMedia(narrowQuery)
    const update = () => setNarrow(media.matches)
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  return narrow
}
