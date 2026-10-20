import {File, FileImage, FileSpreadsheet, FileText, FileType2} from 'lucide-react'
import {cn} from '../lib/utils'

type FileFormat = 'excel' | 'word' | 'pdf' | 'image' | 'file'

export function FileIcon({name, format, colored = false, className}: {name: string; format?: FileFormat; colored?: boolean; className?: string}) {
  const extension = name.match(/\.([a-z0-9]+)(?:\s·|$)/i)?.[1]?.toLowerCase()
  const type = format ?? (extension && ['xlsx', 'xls', 'xlsm', 'xlsb', 'xltx', 'xltm', 'csv', 'ods'].includes(extension) ? 'excel'
    : extension && ['doc', 'docx', 'docm', 'dotx', 'dotm', 'odt', 'rtf'].includes(extension) ? 'word'
      : extension === 'pdf' ? 'pdf' : extension && ['png', 'jpg', 'jpeg', 'webp', 'gif', 'tif', 'tiff', 'bmp'].includes(extension) ? 'image' : 'file')
  const Icon = {excel: FileSpreadsheet, word: FileText, pdf: FileType2, image: FileImage, file: File}[type]
  const color = colored ? {excel: 'border-success/30 bg-success/5 text-success', word: 'border-info/30 bg-info/5 text-info', pdf: 'border-destructive/30 bg-destructive/5 text-destructive', image: 'border-border bg-muted text-muted-foreground', file: 'border-border bg-muted text-muted-foreground'}[type] : ''
  return colored ? <span aria-hidden="true" className={cn('flex size-6 shrink-0 items-center justify-center rounded-sm border', color, className)}><Icon data-file-type={type} className="size-3.5"/></span> : <Icon data-file-type={type} aria-hidden="true" className={cn('size-4 shrink-0 text-muted-foreground', className)}/>
}

export function FileName({name, format, colored = false, truncate = false, className}: {name: string; format?: FileFormat; colored?: boolean; truncate?: boolean; className?: string}) {
  const extension = truncate ? name.match(/\.[a-z0-9]{1,8}$/i)?.[0] : undefined
  return <span className={cn('flex min-w-0 items-start gap-2', className)}>
    <FileIcon name={name} format={format} colored={colored} className={colored ? '' : 'mt-0.5'}/>
    {truncate ? <span className="flex min-w-0" title={name}><span className="truncate">{extension ? name.slice(0, -extension.length) : name}</span>{extension && <span className="shrink-0">{extension}</span>}</span> : <span className="min-w-0 [overflow-wrap:anywhere]">{name}</span>}
  </span>
}
