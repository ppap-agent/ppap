import {useState, type ReactNode} from 'react'
import {Info} from 'lucide-react'
import {IconButton} from './ui'
import Modal from './Modal'

export default function Help({title, children}: {title: string; children: ReactNode}) {
  const [open, setOpen] = useState(false)
  return <>
    <IconButton label={title} onClick={() => setOpen(true)}><Info size={18} aria-hidden="true" /></IconButton>
    {open && <Modal title={title} onClose={() => setOpen(false)}>{children}</Modal>}
  </>
}
