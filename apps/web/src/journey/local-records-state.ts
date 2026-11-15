// Backup and recovery of this browser's rule-library draft, not a second business ledger.
import {draftPrefix, isObject, notifyDrafts, parseDraft, unsavedDrafts} from './saved-draft'
import {isRequirementLibrary} from './requirement-library-state'

export type LocalRecord = {key: string; raw: string; valid: boolean; title: string; at: string}
export type Backup = {format: 'ppap-local-records'; version: 1; at: string; source: 'saved' | 'current-input'; records: {key: string; raw: string}[]}
const recordKey = 'requirement-library'
const ownedKey = (key: string) => key === recordKey
const sameRecord = (left: string | null | undefined, right: string) => left === right || !!left && !!parseDraft(left) && !!parseDraft(right) && JSON.stringify(parseDraft(left)?.value) === JSON.stringify(parseDraft(right)?.value)
export function describeRecord(key: string, raw: string): LocalRecord {
  const data = parseDraft(raw)
  let valid = false
  try {valid = !!data && ownedKey(key) && isRequirementLibrary(data.value)} catch { /* Unsupported records stay untouched. */ }
  return {key, raw, valid, title: '审核规则库', at: data?.updatedAt ?? ''}
}
export function localRecords() {
  const records: LocalRecord[] = []
  try {
    for (const fullKey of Object.keys(localStorage).filter(key => key.startsWith(draftPrefix) && ownedKey(key.slice(draftPrefix.length)))) {
      const raw = localStorage.getItem(fullKey)
      if (raw !== null) records.push(describeRecord(fullKey.slice(draftPrefix.length), raw))
    }
    records.sort((a, b) => b.at.localeCompare(a.at))
    return {records, unavailable: false}
  } catch {return {records, unavailable: true}}
}
export function createBackup(currentInput = false): Backup {
  const snapshot = localRecords()
  if (snapshot.unavailable) throw new Error('本机记录未完整读出，不能生成完整备份。请先导出未保存输入。')
  const records = new Map(snapshot.records.map(({key, raw}) => [key, raw]))
  if (currentInput) for (const item of unsavedDrafts()) if (item.raw) records.set(item.key, item.raw)
  return {format: 'ppap-local-records', version: 1, at: new Date().toISOString(), source: currentInput ? 'current-input' : 'saved', records: [...records].map(([key, raw]) => ({key, raw}))}
}
export function parseBackup(text: string): Backup {
  if (text.length > 10 * 1024 * 1024) throw new Error('备份超过 10 MB，未读取或写入本机记录。')
  let data: unknown
  try {data = JSON.parse(text)} catch {throw new Error('文件不是可读取的 JSON 备份。')}
  if (!isObject(data) || data.format !== 'ppap-local-records' || data.version !== 1 || typeof data.at !== 'string' || !Number.isFinite(Date.parse(data.at)) || !['saved', 'current-input'].includes(String(data.source)) || !Array.isArray(data.records) || !data.records.length || data.records.length > 2000 || !data.records.every(row => isObject(row) && typeof row.key === 'string' && ownedKey(row.key) && typeof row.raw === 'string') || new Set(data.records.map(row => row.key)).size !== data.records.length) throw new Error('不支持此备份格式，或备份包含重复、空白及范围外记录。没有修改本机内容。')
  return data as Backup
}
export function previewRestore(backup: Backup) {
  const current = localRecords(), existing = new Map(current.records.map(row => [row.key, row.raw]))
  const rows = backup.records.map(row => {
    const record = describeRecord(row.key, row.raw)
    const present = existing.get(row.key)
    return {...record, state: !record.valid ? 'invalid' as const : sameRecord(present, row.raw) ? 'same' as const : present === undefined ? 'new' as const : 'conflict' as const}
  })
  const pending = unsavedDrafts().filter(row => row.raw).length
  // A conflict, an unreadable record or unsaved input blocks the whole restore; nothing is overwritten.
  const blocked = current.unavailable || rows.some(row => row.state === 'conflict' || row.state === 'invalid') || pending > 0
  return {rows, blocked, unavailable: current.unavailable, pending}
}
export function restoreBackup(backup: Backup) {
  const preview = previewRestore(backup)
  if (preview.blocked) throw new Error('记录有冲突、无法读取或当前输入未保存，未执行恢复。')
  const added: {key: string; raw: string}[] = []
  try {
    for (const row of preview.rows.filter(row => row.state === 'new')) {
      if (localStorage.getItem(draftPrefix + row.key) !== null) throw new Error('记录在核对后改变')
      localStorage.setItem(draftPrefix + row.key, row.raw); added.push(row)
    }
    if (!backup.records.every(row => sameRecord(localStorage.getItem(draftPrefix + row.key), row.raw))) throw new Error('恢复后的读回不一致')
    notifyDrafts()
    return added.length
  } catch {
    let retained = 0
    for (const row of added) try {if (localStorage.getItem(draftPrefix + row.key) === row.raw) localStorage.removeItem(draftPrefix + row.key)} catch {retained++}
    notifyDrafts()
    throw new Error(retained ? '恢复未完成，部分新增记录未能撤回。请保留备份，重新读取后再核对；没有覆盖原有内容。' : '恢复未完成，本次新增记录已撤回；原有记录未改动。请释放浏览器存储空间后重试。')
  }
}
export function clearRecords(expected: LocalRecord[]) {
  const current = localRecords()
  if (current.unavailable || unsavedDrafts().some(row => row.raw) || current.records.length !== expected.length || expected.some(row => !current.records.some(item => item.key === row.key && item.raw === row.raw))) throw new Error('记录在确认期间改变或有未保存输入。没有开始清理，请重新核对范围。')
  let removed = 0
  try {for (const row of expected) {
    if (localStorage.getItem(draftPrefix + row.key) !== row.raw) throw new Error('记录已改变')
    localStorage.removeItem(draftPrefix + row.key); removed++
  }} catch {throw new Error(`清理中断：已删除 ${removed} 条，其余记录仍保留。请重新读取后核对。`)}
  finally {notifyDrafts()}
  return removed
}
export function downloadRecords(value: unknown, label: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'}))
  const link = document.createElement('a'); link.href = url; link.download = `ppap-${label}-${new Date().toISOString().replace(/[:.]/g, '-')}.json`; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
