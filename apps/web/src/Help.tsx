import {useState, type ReactNode} from 'react'
import {Info} from 'lucide-react'
import Modal from './Modal'

export default function Help({title, children}: {title: string; children: ReactNode}) {
  const [open, setOpen] = useState(false)
  return <>
    <button type="button" className="icon-button help-button" aria-label={title} title={title} onClick={() => setOpen(true)}><Info size={18} aria-hidden="true" /></button>
    {open && <Modal title={title} onClose={() => setOpen(false)}>{children}</Modal>}
  </>
}
