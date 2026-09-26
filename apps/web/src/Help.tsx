import {useState, type ReactNode} from 'react'
import {Info} from 'lucide-react'
import {Button} from '@/components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '@/components/ui/dialog'

export default function Help({title, children}: {title: string; children: ReactNode}) {
  const [open, setOpen] = useState(false)
  return <>
    <Button variant="ghost" size="icon" aria-label={title} title={title} onClick={() => setOpen(true)}><Info className="size-4" aria-hidden="true" /></Button>
    {open && <Dialog open onOpenChange={setOpen}><DialogContent><DialogHeader><DialogTitle>{title}</DialogTitle><DialogDescription className="sr-only">说明</DialogDescription></DialogHeader>{children}</DialogContent></Dialog>}
  </>
}
