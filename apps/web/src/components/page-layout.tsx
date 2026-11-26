import type {ReactNode} from 'react'

/**
 * A single-action page (design.md「布局」): what the person decides on stays at the top, the action stays at the bottom,
 * and only the material between them scrolls, so neither leaves the screen however long the list grows.
 */
export function PageLayout({top, bottom, narrow = false, children}: {top?: ReactNode; bottom?: ReactNode; narrow?: boolean; children: ReactNode}) {
  const width = narrow ? 'mx-auto w-full max-w-5xl' : 'mx-auto w-full'
  return <div className="flex min-h-0 flex-1 flex-col">
    {top && <div className="shrink-0 border-b bg-canvas px-6 pt-5 pb-3 max-xl:px-4 max-xl:pt-4"><div className={`${width} space-y-3`}>{top}</div></div>}
    <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5 max-xl:px-4 max-xl:py-4"><div className={`${width} space-y-5`}>{children}</div></div>
    {bottom && <div className="shrink-0 border-t bg-card px-6 py-3 pb-safe-3 max-xl:px-4"><div className={`${width} space-y-3`}>{bottom}</div></div>}
  </div>
}
