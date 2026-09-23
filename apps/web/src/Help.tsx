import {useState, type ReactNode} from 'react'
import {Info} from 'lucide-react'
import {Icon, IconButton} from './ui'
import Modal from './Modal'

export default function Help({title, children}: {title: string; children: ReactNode}) {
  const [open, setOpen] = useState(false)
  return <>
    <IconButton label={title} onClick={() => setOpen(true)}><Icon icon={Info} size="md" /></IconButton>
    {open && <Modal title={title} onClose={() => setOpen(false)}>{children}</Modal>}
  </>
}
