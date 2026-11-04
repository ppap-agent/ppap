// Reference: Stitch projects/13261964020862199279/screens/8996512ca72547bdb84f5399a3b95a22.
import {useRef, useState, type MouseEventHandler, type ReactNode} from 'react'
import {Button} from '../components/ui/button'
import {ReadSkeleton} from '../components/read-skeleton'
import {Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle} from '../components/ui/item'
import {FileIcon} from './file-name'
import {SourceReaderDialog} from './source-reader'
import {sourceKey, type SourceFile} from './source-documents'
import {useRemote} from './remote'
import type {Round} from '../api'

export type FileReference = {id: string; name: string; role?: 'target' | 'reference'; source?: SourceFile; roundId?: string}
export const fileReferences = (files: SourceFile[]): FileReference[] => files.map(file => ({id: file.material?.file_version_id || file.localFile?.id || sourceKey(file), name: file.name, role: file.material?.role, source: file, roundId: file.roundId}))

export function FileList({files, label = '文件清单'}: {files: {id: string; name: string; description?: string; action?: ReactNode; onRead?: MouseEventHandler<HTMLButtonElement>; readLabel?: string}[]; label?: string}) {
  return <ItemGroup aria-label={label}>
    {files.map(file => <Item key={file.id} size="xs" role="listitem" className="flex-nowrap items-start">
      <ItemMedia variant="icon" className="mt-0.5"><FileIcon name={file.name}/></ItemMedia>
      <ItemContent className="min-w-0"><ItemTitle wrap>{file.onRead ? <Button variant="link" size="sm" className="h-auto max-w-full justify-start whitespace-normal text-left [overflow-wrap:anywhere]" aria-label={file.readLabel || `阅读 ${file.name}`} onClick={file.onRead}>{file.name}</Button> : file.name}</ItemTitle>{file.description && <ItemDescription wrap>{file.description}</ItemDescription>}</ItemContent>
      {file.action && <ItemActions className="shrink-0">{file.action}</ItemActions>}
    </Item>)}
  </ItemGroup>
}

/** `limit` keeps dense lists scannable; the remaining files stay one click away. */
export function FileCollection({files, context = '', limit}: {files: FileReference[]; context?: string; limit?: number}) {
  const [reading, setReading] = useState<{file: FileReference; files: FileReference[]} | null>(null)
  const [expanded, setExpanded] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const roundId = reading && !reading.file.source?.material && !reading.file.source?.localFile ? reading.file.roundId : undefined
  const round = useRemote<Round>(roundId ? `/rounds/${encodeURIComponent(roundId)}` : null)
  function resolve(file: FileReference): SourceFile | undefined {
    if (file.source?.material || file.source?.localFile) return file.source
    const material = round.value && round.value.id === file.roundId ? round.value.inputs.find(item => item.file_version_id === file.id) : undefined
    return material ? {name: material.original_filename, revision: '已固定版本', sourceVersion: 1, material, roundId: round.value!.id} : undefined
  }
  const selected = reading ? resolve(reading.file) : undefined
  const sources = reading?.files.map(resolve).filter((file): file is SourceFile => Boolean(file)) ?? []
  const readError = round.error || (round.value && !round.loading && !selected ? '未能找到这份文件的本轮固定版本，请打开审核记录核对。' : '')
  if (!files.length) return null
  const hidden = limit !== undefined && !expanded ? Math.max(0, files.length - limit) : 0
  return <>
    <FileList label={context ? `${context}的文件清单` : '文件清单'} files={files.slice(0, files.length - hidden).map((file, index) => ({
      id: `${file.id}:${index}`, name: file.name,
      description: files.filter(item => item.name === file.name).length > 1 ? `版本 ${file.id}` : undefined,
      readLabel: `阅读 ${file.name}${files.filter(item => item.name === file.name).length > 1 ? ` · 版本 ${file.id}` : ''}`,
      onRead: file.source?.material || file.source?.localFile || file.roundId ? event => {trigger.current = event.currentTarget; setReading({file, files})} : undefined,
    }))}/>
    {limit !== undefined && files.length > limit && <Button variant="link" size="xs" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? '收起文件' : `另有 ${hidden} 份文件`}</Button>}
    {reading && !selected && <div className="space-y-2 p-2 text-sm">{readError ? <p role="alert">{readError}</p> : <ReadSkeleton label="正在读取原件入口…" kind="inline"/>}<div className="flex flex-wrap gap-2">{readError && <Button variant="outline" size="sm" onClick={round.refresh}>重新读取原件入口</Button>}<Button variant="ghost" size="sm" onClick={() => {setReading(null); trigger.current?.focus()}}>取消阅读</Button></div></div>}
    <SourceReaderDialog open={Boolean(selected)} onOpenChange={next => {if (!next) setReading(null)}} input={{receiptId: reading?.file.roundId || '文件清单', scope: context, files: sources, evidence: [], clarification: null}} file={selected} onCloseAutoFocus={event => {event.preventDefault(); trigger.current?.focus()}}/>
  </>
}
