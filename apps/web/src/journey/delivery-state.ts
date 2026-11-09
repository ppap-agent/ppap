// Prototype-only delivery facts, owned by the existing decision draft.
// A package and a send attempt retain their exact versions, even after a new selection.
import {strToU8, zip, type AsyncZippable} from 'fflate'
import {isObject} from './saved-draft'
import {sourceDocumentHTML, sourceKey, type DemoDocument, type SourceFile} from './source-documents'
import {optionalLocalFile, readLocalFile} from './local-files'
import type {RecordedDecision} from './decision'

export const deliveryTargets = {group: '质量审核组（示例）', document: 'PPAP 评审文档（示例）'} as const
export type DeliveryTarget = keyof typeof deliveryTargets
export type DeliveryPackage = {id: string; at: string; files: SourceFile[]; omitted: SourceFile[]; documents: DemoDocument[]}
export type DeliveryAttempt = {
  id: string; intent: string; packageId: string; target: DeliveryTarget; at: string
  phase: 'sending' | 'unknown' | 'checking' | 'not_sent' | 'sent'
  remote: 'sent' | 'absent'; observedAt?: string; receipt?: string
}
export type DeliveryState = {
  selection: string[]; packages: DeliveryPackage[]; packageId: string
  preparation: 'idle' | 'preparing' | 'failed' | 'interrupted'
  target: DeliveryTarget; authorized: boolean; attempts: DeliveryAttempt[]
  downloads: {packageId: string; at: string}[]
  manual: {packageId: string; recipient: string; channel: string; note: string; at: string}[]
  manualDraft: {recipient: string; channel: string; note: string; acknowledged: boolean}
}
export function initialDelivery(files: SourceFile[]): DeliveryState {
  return {selection: files.map(sourceKey), packages: [], packageId: '', preparation: 'idle', target: 'group', authorized: false, attempts: [], downloads: [], manual: [], manualDraft: {recipient: '', channel: '', note: '', acknowledged: false}}
}
const strings = (value: Record<string, unknown>, fields: string[]) => fields.every(key => typeof value[key] === 'string')
const source = (value: unknown): value is SourceFile => isObject(value) && strings(value, ['name', 'revision']) && Number.isInteger(value.sourceVersion) && Number(value.sourceVersion) > 0 && optionalLocalFile(value.localFile)
const document = (value: unknown) => isObject(value) && typeof value.origin === 'string' && ['flow','control','dimension'].includes(String(value.kind)) && (value.measurements === undefined || Array.isArray(value.measurements) && value.measurements.every(item => typeof item === 'number')) && source(value)
export function validDelivery(value: unknown): value is DeliveryState {
  if (!isObject(value) || !Array.isArray(value.selection) || !value.selection.every(key => typeof key === 'string') || !Array.isArray(value.packages)) return false
  if (!value.packages.every(pack => isObject(pack) && strings(pack, ['id','at']) && Array.isArray(pack.files) && pack.files.every(source) && Array.isArray(pack.omitted) && pack.omitted.every(source) && Array.isArray(pack.documents) && pack.documents.every(document))) return false
  return typeof value.packageId === 'string' && ['idle','preparing','failed','interrupted'].includes(String(value.preparation)) && ['group','document'].includes(String(value.target)) && typeof value.authorized === 'boolean'
    && Array.isArray(value.attempts) && value.attempts.every(item => isObject(item) && strings(item, ['id','intent','packageId','at']) && ['group','document'].includes(String(item.target)) && ['sending','checking','unknown','not_sent','sent'].includes(String(item.phase)) && ['sent','absent'].includes(String(item.remote)) && (item.observedAt === undefined || typeof item.observedAt === 'string') && (item.receipt === undefined || typeof item.receipt === 'string'))
    && Array.isArray(value.downloads) && value.downloads.every(item => isObject(item) && strings(item, ['packageId','at']))
    && Array.isArray(value.manual) && value.manual.every(item => isObject(item) && strings(item, ['packageId','recipient','channel','note','at']))
    && isObject(value.manualDraft) && strings(value.manualDraft, ['recipient','channel','note']) && typeof value.manualDraft.acknowledged === 'boolean'
}
export const unresolvedDelivery = (state?: DeliveryState) => state?.attempts.find(item => ['sending','unknown','checking'].includes(item.phase))
export function recoveredDelivery(state: DeliveryState): DeliveryState {
  return {...state, preparation: state.preparation === 'preparing' ? 'interrupted' : state.preparation,
    attempts: state.attempts.map(item => ['sending','checking'].includes(item.phase) ? {...item, phase: 'unknown'} : item)}
}
export function deliveryLabel(state?: DeliveryState, hasPreviousManualDelivery = false) {
  const pending = unresolvedDelivery(state)
  if (pending) return pending.phase === 'sending' ? '正在发送' : pending.phase === 'checking' ? '正在核对发送结果' : '发送结果待确认'
  if (state?.attempts.at(-1)?.phase === 'not_sent') return '发送未完成'
  if (state?.attempts.some(item => item.phase === 'sent')) return '已有发送结果'
  if (state?.manual.length || hasPreviousManualDelivery) return '已登记系统外交付'
  return state?.packages.length ? '已有资料包 · 待交付' : '待准备交付'
}
const escape = (value: string) => value.replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]!))
export async function packageBytes(pack: DeliveryPackage, report: string, record: RecordedDecision, signal?: AbortSignal, onProgress?: (label: string) => void) {
  signal?.throwIfAborted()
  const contents: AsyncZippable = {'审核报告.html': strToU8(report), '依据记录.json': strToU8(JSON.stringify(record, null, 2))}
  const safeName = (name: string) => name.replace(/[\\/\x00-\x1f<>:"|?*]/g, '_')
  async function original(file: SourceFile, path: string) {
    signal?.throwIfAborted(); onProgress?.(`正在读取 ${file.name}`)
    try {
      const blob = await readLocalFile(file.localFile!)
      signal?.throwIfAborted()
      if (!blob) throw new Error('本机原件已缺失，请先补回当时的原件。')
      contents[path] = [new Uint8Array(await blob.arrayBuffer()), {level: 0}]
      signal?.throwIfAborted()
    } catch (error) {
      signal?.throwIfAborted()
      throw new Error(`${file.name}：${error instanceof Error ? error.message : '原件读取失败。'}`)
    }
  }
  for (const [index, file] of pack.files.entries()) {
    if (file.localFile) {await original(file, `原件/${index + 1}-${safeName(file.name)}`); continue}
    signal?.throwIfAborted()
    const matches = pack.documents.filter(doc => sourceKey(doc) === sourceKey(file))
    if (matches.length !== 1) throw new Error(`${file.name}：没有可读取的原件。`)
    // Names remain data: no path traversal or accidental duplicate ZIP entries.
    contents[`示例资料/${index + 1}-${safeName(file.name)}.示例.html`] = strToU8(sourceDocumentHTML(matches[0]))
  }
  const basis = record.input.basis?.source
  if (basis?.localFile) await original({name: basis.filename, revision: basis.version, sourceVersion: 1, localFile: basis.localFile}, `审核规则/${safeName(basis.filename)}`)
  const list = (files: SourceFile[]) => files.map(file => `<li>${escape(file.name)} · Rev ${escape(file.revision)} · 文件 v${file.sourceVersion}</li>`).join('') || '<li>无</li>'
  contents['请先阅读.html'] = strToU8(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>${escape(pack.id)}</title><h1>审核资料包 · 交互原型</h1><p>${escape(pack.id)} · ${escape(pack.at)}</p><p>报告和依据记录属于本次固定决定，不构成正式批准。“原件”目录保留上传字节；“示例资料”目录是合成 HTML，不是真实检验单据。</p><h2>包含资料</h2><ul>${list(pack.files)}</ul><h2>未包含资料</h2><ul>${list(pack.omitted)}</ul><h2>审核规则</h2><p>${basis?.localFile ? `已附原件：${escape(basis.filename)} · ${escape(basis.version)}` : '条款记录在依据记录与报告中；未附规则原件。'}</p><p>生成或下载资料包不代表对外送达。</p></html>`)
  signal?.throwIfAborted(); onProgress?.('正在打包')
  return new Promise<Uint8Array>((resolve, reject) => {
    const cancel = () => {terminate(); reject(signal?.reason ?? new DOMException('已取消', 'AbortError'))}
    const terminate = zip(contents, {level: 6, mtime: new Date(pack.at)}, (error, bytes) => {
      signal?.removeEventListener('abort', cancel)
      if (signal?.aborted) reject(signal.reason)
      else if (error) reject(error)
      else resolve(bytes)
    })
    signal?.addEventListener('abort', cancel, {once: true})
  })
}
