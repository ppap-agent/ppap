import {useEffect, useState} from 'react'
import {Download, Loader2} from 'lucide-react'
import type {components} from '../generated/openapi'
import {Button} from '../components/ui/button'

export type Precheck = components['schemas']['AuditTaskPrecheck']
type Task = components['schemas']['AuditTask']
export type PrecheckAdapter = {read: (taskId: string, signal?: AbortSignal) => Promise<Precheck>; report: (taskId: string) => Promise<Blob>}

/** The precheck follows the recognized task; a changed file set reads it again. */
export function usePrecheck(adapter: PrecheckAdapter | undefined, task: Task | null | undefined, enabled: boolean) {
  const [state, setState] = useState<{key: string; result: Precheck | null; error: string}>({key: '', result: null, error: ''})
  const key = task ? `${task.id}:${task.state}:${task.files.map(row => `${row.file.id}.${row.selection}.${row.match?.state ?? ''}.${row.replacement_of_file_version_id ?? ''}`).join('|')}` : ''
  useEffect(() => {
    if (!adapter || !task || !enabled) return
    const controller = new AbortController()
    adapter.read(task.id, controller.signal).then(result => setState({key, result, error: ''})).catch(() => {if (!controller.signal.aborted) setState({key, result: null, error: '预检结果暂时无法读取，不影响识别与审核。'})})
    return () => controller.abort()
  }, [adapter, key, enabled]) // the key captures every task change the precheck depends on
  return state.key === key ? state : {key, result: null, error: ''}
}

const cards = [['ok', '合规', 'text-success'], ['warn', '警告', 'text-warning'], ['block', '阻断', 'text-destructive'], ['unmatched', '未匹配', 'text-muted-foreground'], ['reference', '比对资料', 'text-primary']] as const

export function PrecheckCounts({result}: {result: Precheck}) {
  return <dl aria-label="预检结果统计" className="grid grid-cols-2 gap-3 sm:grid-cols-5">{cards.map(([level, label, tone]) => <div key={level} className="flex flex-col-reverse rounded-lg border bg-card px-4 py-3 text-center"><dt className="text-xs text-muted-foreground">{label}</dt><dd className={`text-2xl font-semibold tabular-nums ${tone}`}>{result.counts[level]}</dd></div>)}</dl>
}

/** What the precheck found for one file, under its name in the matching list. */
export function PrecheckNotes({result, fileVersionId}: {result: Precheck | null; fileVersionId: string}) {
  if (!result) return null
  const items = [...result.blocks, ...result.warnings].filter(item => item.file_version_id === fileVersionId)
  return items.length > 0 && <ul aria-label="预检发现" className="space-y-1 text-xs">{items.map(item => <li key={item.number} className={item.level === 'block' ? 'text-destructive' : 'text-warning'}><span className="font-medium">{item.level === 'block' ? '阻断' : '警告'} · {item.code} {item.title}</span>：{item.problem}<span className="text-muted-foreground"> 整改：{item.action}</span></li>)}</ul>
}

/** The ten-chapter report is generated on demand, never billed and never filed with audit results. */
export function PrecheckReport({adapter, taskId, result}: {adapter: PrecheckAdapter; taskId: string; result: Precheck}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function download() {
    setBusy(true); setError('')
    try {
      const url = URL.createObjectURL(await adapter.report(taskId))
      const link = document.createElement('a'); link.href = url; link.download = '预检报告.pdf'; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (failure) {setError(failure instanceof Error ? failure.message : '预检报告暂时无法生成。')} finally {setBusy(false)}
  }
  const conclusion = {pass: '通过：可以进入正式审核', fail: '不通过：存在阻断项', not_run: '未执行：识别尚未完成'}[result.conclusion]
  return <section aria-label="预检报告" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4">
    <div className="space-y-1"><h2 className="font-semibold">预检报告 · {conclusion}</h2><p className="text-xs text-muted-foreground">按《数据标准化预检总纲》十章模板生成；只检查文件能否被可靠审核，不判定内容是否合规。预检免费，不进入审核文件档案。</p>{error && <p role="alert" className="text-xs text-destructive">{error}</p>}</div>
    <Button variant="outline" disabled={busy} onClick={() => void download()}>{busy ? <Loader2 className="animate-spin motion-reduce:animate-none"/> : <Download/>}导出预检报告（PDF）</Button>
  </section>
}
