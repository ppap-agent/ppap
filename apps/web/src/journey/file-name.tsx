import {File, FileImage, FileSpreadsheet, FileText, FileType2} from 'lucide-react'
import {cn} from '../lib/utils'

type FileFormat = 'excel' | 'word' | 'pdf' | 'image' | 'file'

export function FileIcon({name, format, className}: {name: string; format?: FileFormat; className?: string}) {
  const extension = name.match(/\.([a-z0-9]+)(?:\s·|$)/i)?.[1]?.toLowerCase()
  const type = format ?? (extension && ['xlsx', 'xls', 'xlsm', 'xlsb', 'xltx', 'xltm', 'csv', 'ods'].includes(extension) ? 'excel'
    : extension && ['doc', 'docx', 'docm', 'dotx', 'dotm', 'odt', 'rtf'].includes(extension) ? 'word'
      : extension === 'pdf' ? 'pdf' : extension && ['png', 'jpg', 'jpeg', 'webp', 'gif', 'tif', 'tiff', 'bmp'].includes(extension) ? 'image' : 'file')
  const Icon = {excel: FileSpreadsheet, word: FileText, pdf: FileType2, image: FileImage, file: File}[type]
  return <Icon data-file-type={type} aria-hidden="true" className={cn('size-4 shrink-0 text-muted-foreground', className)}/>
}

export function FileName({name, format, truncate = false, className}: {name: string; format?: FileFormat; truncate?: boolean; className?: string}) {
  const extension = truncate ? name.match(/\.[a-z0-9]{1,8}$/i)?.[0] : undefined
  return <span className={cn('flex min-w-0 items-start gap-2', className)}>
    <FileIcon name={name} format={format} className="mt-0.5"/>
    {truncate ? <span className="flex min-w-0" title={name}><span className="truncate">{extension ? name.slice(0, -extension.length) : name}</span>{extension && <span className="shrink-0">{extension}</span>}</span> : <span className="min-w-0 [overflow-wrap:anywhere]">{name}</span>}
  </span>
}
