import {useRef} from 'react'
import {faXmark} from '@fortawesome/free-solid-svg-icons'
import {Icon} from '../components/ui/icon'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {FileTypeBadge} from './file-type-badge'
import {canResumeUpload, canRetryUpload, uploadName, type UploadRow} from './uploads'

const transferLabel = {uploading: '传输中', failed: '上传失败', unknown: '结果未确认'} as const
const transferTone = {uploading: 'info', failed: 'destructive', unknown: 'warning'} as const

export type TransferActions = {onRead: (id: string) => void; onResume?: (id: string) => void; onRetry: (id: string) => void; onRemove: (id: string) => void; onCheckDrive?: (trigger: HTMLButtonElement) => void; onAttach?: (id: string) => void}

/** One hidden picker for every transfer that needs its original file chosen again. */
export function useTransferAttach(attach: (id: string, file: File) => void) {
  const input = useRef<HTMLInputElement>(null), attaching = useRef<string | null>(null)
  const picker = <input ref={input} type="file" className="sr-only" aria-label="重新选择当时原件" onChange={event => {const file = event.target.files?.[0], id = attaching.current; event.target.value = ''; attaching.current = null; if (file && id) attach(id, file)}}/>
  return {picker, choose: (id: string) => {attaching.current = id; input.current?.click()}}
}

/** A transfer that has not produced a file yet, as a line among the upload page's files: its state, why, and what the person can do. */
export function TransferItem({row, onRead, onResume, onRetry, onRemove, onCheckDrive, onAttach}: {row: UploadRow} & TransferActions) {
  const name = uploadName(row.input)
  const state = row.receipt.state === 'ready' ? null : row.receipt.state
  const personalUnknown = state === 'unknown' && row.input.kind === 'local' && Boolean(row.input.destination) && !row.removed
  const note = personalUnknown && onCheckDrive ? '若飞书已保存，请从飞书核对并添加，再移除本次传输；原文件和结果仍保留。' : row.receipt.reason
  return <li className="grid gap-1">
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
      <FileTypeBadge name={name}/>
      <span className="min-w-0 flex-1 basis-40 truncate text-sm" title={name}>{name}</span>
      {state && !row.removed && <Badge variant={transferTone[state]}>{transferLabel[state]}</Badge>}
      {state && ['unknown', 'uploading'].includes(state) && <Button size="sm" variant="outline" disabled={row.busy} aria-label={`读取传输 ${name}`} onClick={() => onRead(row.id)}>{row.busy ? '传输处理中…' : '读取结果'}</Button>}
      {onAttach && !row.removed && row.input.kind === 'local' && (!(row.input.file instanceof File) || Boolean(row.inputIssue)) && <Button size="sm" variant="outline" disabled={row.busy} aria-label={`重新选择原件 ${name}`} onClick={() => onAttach(row.id)}>重新选择原件</Button>}
      {onResume && canResumeUpload(row) && <Button size="sm" variant="outline" aria-label={`继续传输 ${name}`} onClick={() => onResume(row.id)}>继续传输</Button>}
      {onCheckDrive && personalUnknown && <Button size="sm" variant="outline" disabled={row.busy} aria-label={`从飞书核对 ${name}`} onClick={event => onCheckDrive(event.currentTarget)}>从飞书核对</Button>}
      {canRetryUpload(row) && <Button size="sm" variant="outline" aria-label={`重试传输 ${name}`} onClick={() => onRetry(row.id)}>重试</Button>}
      {!row.removed && <Button variant="ghost" size="icon-sm" aria-label={`移除传输 ${name}`} onClick={() => onRemove(row.id)}><Icon icon={faXmark}/></Button>}
    </div>
    {note && <p className="text-xs text-muted-foreground">{note}</p>}
    {row.inputIssue && <p role="alert" className="text-xs">{row.inputIssue}</p>}
  </li>
}
