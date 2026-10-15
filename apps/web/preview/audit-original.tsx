import {useEffect, useMemo, useState} from 'react'
import {utils, write} from 'xlsx'
import type {components} from '../src/generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../src/components/ui/alert'
import {RemoteSourcePane, type RemoteOriginalSource} from '../src/journey/remote-source'

const inputs = new Map<string, {file: components['schemas']['FileVersion']; blob: Blob}>()
/** Only the explicit sample action registers bytes; arbitrary uploads never get a substitute. */
export function fixtureOriginalFile(file: components['schemas']['FileVersion']) {
  if (!file.original_filename.endsWith('.xlsx')) return file
  const workbook = utils.book_new()
  utils.book_append_sheet(workbook, utils.aoa_to_sheet([['交互预览输入，不是真实客户原件'], ['冻结文件版本', file.id], ['文件名', file.original_filename]]), '示例原件')
  const blob = new Blob([write(workbook, {type: 'array', bookType: 'xlsx'})], {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
  const frozen = {...file, byte_size: blob.size, content_type: blob.type}
  inputs.set(file.id, {file: frozen, blob})
  return frozen
}

/** Reuses the product reader with local sample bytes, never a real original API. */
export function FixtureRemoteOriginal({file, office}: {file: components['schemas']['FileVersion']; office: RemoteOriginalSource['office']}) {
  const [url, setUrl] = useState('')
  const input = inputs.get(file.id), blob = input?.file.file_id === file.file_id && input.file.original_filename === file.original_filename ? input.blob : null
  useEffect(() => {
    if (!blob) return
    const current = URL.createObjectURL(blob)
    setUrl(current)
    return () => URL.revokeObjectURL(current)
  }, [blob])
  const source = useMemo<RemoteOriginalSource>(() => ({office, url: target => {
    if (target.file_version_id !== file.id || target.file_id !== file.file_id) throw new Error('preview_original_identity_mismatch')
    return url
  }}), [office, url, file.id, file.file_id])
  return <div className="space-y-3"><Alert><AlertTitle>示例原件 · 非真实 Office 连接</AlertTitle><AlertDescription>只验证冻结版本的读取、失败恢复和示例输入下载，不证明真实排版、定位或文件审核。</AlertDescription></Alert>{!blob ? <p role="alert">没有该冻结版本的示例字节。未改用同名文件。</p> : url ? <RemoteSourcePane file={{file_id: file.file_id, file_version_id: file.id, original_filename: file.original_filename, role: 'target', rule_version_id: null}} source={source}/> : <p role="status">正在准备本机示例原件…</p>}</div>
}
