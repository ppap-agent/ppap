import {useEffect, useId, useRef, type ReactNode} from 'react'
import {X} from 'lucide-react'

export default function Modal({title, busy, onClose, children}: {title: string; busy?: boolean; onClose: () => void; children: ReactNode}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const node = dialog.current
    if (!node) return
    if (!node.open) node.showModal()
    return () => { if (node.open) node.close() }
  }, [])
  return (
    <dialog ref={dialog} className="modal" aria-labelledby={titleId} onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
      <header className="modal-heading"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" disabled={busy} aria-label="关闭" onClick={onClose}><X size={20} aria-hidden="true" /></button></header>
      <div className="modal-content">{children}</div>
    </dialog>
  )
}
