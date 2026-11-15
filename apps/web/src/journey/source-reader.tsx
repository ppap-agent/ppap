// Read-only original reader: closing or downloading never saves a review judgment.
import {useState} from 'react'
import {X} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger} from '../components/ui/dialog'
import type {SourceFile} from './source-documents'
import {LocalSourcePane} from './local-source-pane'
import {RemoteSourcePane} from './remote-source'
import {FileIcon} from './file-name'

type Props = {file: SourceFile; description: string; label?: string; className?: string; variant?: 'outline' | 'ghost' | 'link'}

function SourcePane({file}: {file: SourceFile}) {
  if (file.material) return <RemoteSourcePane file={file.material} roundId={file.roundId}/>
  if (file.localFile) return <LocalSourcePane file={{...file, localFile: file.localFile}} side="主要"/>
  return <section className="flex min-h-45 flex-1 items-center justify-center border bg-background p-5.5 text-muted-foreground" aria-label="主要阅读区"><p>这份原件不可读取。</p></section>
}

export function SourceReaderButton({file, description, label, className, variant}: Props) {
  const [open, setOpen] = useState(false)
  return <Dialog open={open} onOpenChange={setOpen}>
    <DialogTrigger asChild><Button variant={variant ?? 'outline'} className={className} size="sm" aria-label={label === file.name ? `阅读 ${file.name}` : `${label ?? '打开原件'} · ${file.name}`}><FileIcon name={file.name}/><span className="min-w-0 [overflow-wrap:anywhere]">{label ?? '打开原件'}</span></Button></DialogTrigger>
    {open && <DialogContent className="flex h-[90dvh] w-screen max-w-none flex-col sm:max-w-7xl" showCloseButton={false}>
      <header className="flex items-center justify-between gap-4 [&_>_div]:min-w-0"><div><DialogTitle>规则原件</DialogTitle><DialogDescription>{description}</DialogDescription></div><Button aria-label="返回当前工作" variant="outline" onClick={() => setOpen(false)}><X/><span>返回当前工作</span></Button></header>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col"><SourcePane file={file}/></div>
    </DialogContent>}
  </Dialog>
}
