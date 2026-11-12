import {File, FileImage, FileSpreadsheet, FileText, FileType2} from 'lucide-react'
import {cn} from '../lib/utils'

type FileFormat = 'excel' | 'word' | 'pdf' | 'image' | 'file'

export function FileIcon({name, format, colored = false, className}: {name: string; format?: FileFormat; colored?: boolean; className?: string}) {
  const extension = name.match(/\.([a-z0-9]+)(?:\s·|$)/i)?.[1]?.toLowerCase()
  const type = format ?? (extension && ['xlsx', 'xls', 'xlsm', 'xlsb', 'xltx', 'xltm', 'csv', 'ods'].includes(extension) ? 'excel'
    : extension && ['doc', 'docx', 'docm', 'dotx', 'dotm', 'odt', 'rtf'].includes(extension) ? 'word'
      : extension === 'pdf' ? 'pdf' : extension && ['png', 'jpg', 'jpeg', 'webp', 'gif', 'tif', 'tiff', 'bmp'].includes(extension) ? 'image' : 'file')
  const Icon = {excel: FileSpreadsheet, word: FileText, pdf: FileType2, image: FileImage, file: File}[type]
  const tone = {excel: 'text-success', word: 'text-primary', pdf: 'text-destructive', image: 'text-warning', file: 'text-muted-foreground'}[type]
  // A coloured tile names the format at a glance, like a file browser.
  const tile = {excel: 'bg-success', word: 'bg-primary', pdf: 'bg-destructive', image: 'bg-warning', file: 'bg-muted-foreground'}[type]
  return colored ? <span aria-hidden="true" className={cn('flex size-6 shrink-0 items-center justify-center rounded-sm text-white', tile, className)}><Icon data-file-type={type} className="size-3.5"/></span> : <Icon data-file-type={type} aria-hidden="true" className={cn('size-4 shrink-0', tone, className)}/>
}

export function FileName({name, format, colored = false, truncate = false, className}: {name: string; format?: FileFormat; colored?: boolean; truncate?: boolean; className?: string}) {
  const extension = truncate ? name.match(/\.[a-z0-9]{1,8}$/i)?.[0] : undefined
  return <span className={cn('flex min-w-0 items-start gap-2', className)}>
    <FileIcon name={name} format={format} colored={colored} className={colored ? '' : 'mt-0.5'}/>
    {truncate ? <span className="flex min-w-0" title={name}><span className="truncate">{extension ? name.slice(0, -extension.length) : name}</span>{extension && <span className="shrink-0">{extension}</span>}</span> : <span className="min-w-0 [overflow-wrap:anywhere]">{name}</span>}
  </span>
}
