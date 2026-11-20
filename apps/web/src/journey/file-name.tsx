import {iconFile, iconFileExcel, iconFileImage, iconFilePdf, iconFileWord} from '../components/icons'
import {Icon} from '../components/ui/icon'
import {cn} from '../lib/utils'

type FileFormat = 'excel' | 'word' | 'pdf' | 'image' | 'file'

export function FileIcon({name, format, colored = false, className}: {name: string; format?: FileFormat; colored?: boolean; className?: string}) {
  const extension = name.match(/\.([a-z0-9]+)(?:\s·|$)/i)?.[1]?.toLowerCase()
  const type = format ?? (extension && ['xlsx', 'xls', 'xlsm', 'xlsb', 'xltx', 'xltm', 'csv', 'ods'].includes(extension) ? 'excel'
    : extension && ['doc', 'docx', 'docm', 'dotx', 'dotm', 'odt', 'rtf'].includes(extension) ? 'word'
      : extension === 'pdf' ? 'pdf' : extension && ['png', 'jpg', 'jpeg', 'webp', 'gif', 'tif', 'tiff', 'bmp'].includes(extension) ? 'image' : 'file')
  const icon = {excel: iconFileExcel, word: iconFileWord, pdf: iconFilePdf, image: iconFileImage, file: iconFile}[type]
  // A coloured tile names the format at a glance, like a file browser.
  const tile = {excel: 'bg-success', word: 'bg-primary', pdf: 'bg-destructive', image: 'bg-warning', file: 'bg-muted-foreground'}[type]
  return colored ? <span aria-hidden="true" className={cn('flex size-6 shrink-0 items-center justify-center rounded-sm text-white', tile, className)}><Icon icon={icon} data-file-type={type} className="size-3.5"/></span> : <Icon icon={icon} data-file-type={type} aria-hidden="true" className={cn('size-4 shrink-0', type === 'excel' ? 'text-success' : type === 'word' ? 'text-primary' : type === 'pdf' ? 'text-destructive' : type === 'image' ? 'text-warning' : 'text-muted-foreground', className)}/>
}

export function FileName({name, format, colored = false, truncate = false, className}: {name: string; format?: FileFormat; colored?: boolean; truncate?: boolean; className?: string}) {
  const extension = truncate ? name.match(/\.[a-z0-9]{1,8}$/i)?.[0] : undefined
  return <span className={cn('flex min-w-0 items-start gap-2', className)}>
    <FileIcon name={name} format={format} colored={colored} className={colored ? '' : 'mt-0.5'}/>
    {/* The two visible parts keep the extension in view when the name is cut; split flex items would read as "名 .xlsx", so assistive tech gets the whole name. */}
    {truncate ? <span className="flex min-w-0" title={name}><span className="sr-only">{name}</span><span aria-hidden="true" className="truncate">{extension ? name.slice(0, -extension.length) : name}</span>{extension && <span aria-hidden="true" className="shrink-0">{extension}</span>}</span> : <span className="min-w-0 [overflow-wrap:anywhere]">{name}</span>}
  </span>
}
