import {useEffect, useId, useRef, type ReactNode} from 'react'
import {X} from 'lucide-react'
import {IconButton} from './controls'

export function Modal({title, busy, onClose, children}: {title: string; busy?: boolean; onClose: () => void; children: ReactNode}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const node = dialog.current
    if (!node) return
    if (!node.open) node.showModal()
    return () => { if (node.open) node.close() }
  }, [])
  return (
    <dialog ref={dialog} data-ui="modal" aria-labelledby={titleId} onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
      <header data-ui="modal-heading">
        <h2 id={titleId}>{title}</h2>
        <IconButton label="关闭" disabled={busy} onClick={onClose}><X size={20} aria-hidden="true" /></IconButton>
      </header>
      <div data-ui="modal-body">{children}</div>
    </dialog>
  )
}

export function OfficeFrame({children}: {children: ReactNode}) {
  return <div data-ui="office">{children}</div>
}

export function OfficeHost({id}: {id: string}) {
  return <div data-ui="office-host" id={id} />
}
