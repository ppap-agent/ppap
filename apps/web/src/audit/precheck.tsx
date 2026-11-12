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

/** The stat row of the precheck: compliant, warning, blocked, matched and unmatched files, and references when there are any. */
export function PrecheckCounts({result, matched}: {result: Precheck; matched: number}) {
  const cards: [string, number, string][] = [['合规', result.counts.ok, 'text-success'], ['警告', result.counts.warn, 'text-warning'], ['阻断', result.counts.block, 'text-destructive'], ['已匹配', matched, 'text-primary'], ['未匹配', result.counts.unmatched, 'text-foreground'], ...result.counts.reference ? [['比对资料', result.counts.reference, 'text-muted-foreground'] as [string, number, string]] : []]
  return <dl aria-label="预检结果统计" className={`grid gap-3 ${cards.length > 5 ? 'grid-cols-6' : 'grid-cols-5'}`}>{cards.map(([label, value, tone]) => <div key={label} className="flex flex-col-reverse rounded-md border bg-card px-2 py-3 text-center"><dt className="text-sm text-muted-foreground">{label}</dt><dd className={`text-2xl font-semibold tabular-nums ${tone}`}>{value}</dd></div>)}</dl>
}

/** What the precheck found for one file, under its name in the matching list. */
export function PrecheckNotes({result, fileVersionId}: {result: Precheck | null; fileVersionId: string}) {
  if (!result) return null
  const items = [...result.blocks, ...result.warnings].filter(item => item.file_version_id === fileVersionId)
  // A warning never holds the start; a file-level block does until the file is removed; a content-level block goes to the formal audit.
  const effect = (item: Precheck['blocks'][number]) => item.level === 'warn' ? '仍可勾选送审' : item.scope === 'file' ? '移除后才能开始审核' : '不阻止开始审核，正式审核将核对'
  return items.length > 0 && <ul aria-label="预检发现" className="space-y-1 text-xs">{items.map(item => <li key={item.number} className={item.level === 'block' ? 'text-destructive' : 'text-warning'}><span aria-hidden="true">{item.level === 'block' ? '⛔ ' : '⚠ '}</span><span className="font-medium">{item.level === 'block' ? '阻断' : '警告'} · {item.code} {item.title}</span>：{item.problem}（{effect(item)}）<span className="text-muted-foreground"> 整改：{item.action}</span></li>)}</ul>
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
  const routed = result.files.filter(row => row.entry_key).length
  // The fixed ten chapters of the precheck outline, each with what this batch puts in it.
  const chapters: [string, string, string][] = [
    ['一', '结论摘要', conclusion], ['二', '提交范围与文件集', `${result.files.length} 份文件`], ['三', '问题总览', `阻断 ${result.blocks.length} · 警告 ${result.warnings.length}`],
    ['四', '阻断项明细', result.blocks.length ? `${result.blocks.length} 项` : '无阻断项'], ['五', '警告项明细', result.warnings.length ? `${result.warnings.length} 项，移交正式审核` : '无警告项'],
    ['六', '状态登记', result.statuses.length ? `${result.statuses.length} 项` : '无'], ['七', '整改建议汇总', `${result.blocks.length + result.warnings.length} 条`],
    ['八', '结论作用域与局限性声明', '只检查文件能否被可靠审核，不判定内容是否合规'], ['九', '附录·检查项全量结果', `${result.files.filter(row => row.criteria?.length).length} 份文件有逐项结果`], ['十', '附录·分则附加节', routed ? `${routed} 份文件已路由` : '无路由条目'],
  ]
  return <section aria-label="预检报告" className="space-y-3 rounded-lg border bg-card p-4">
    <div className="space-y-1"><h2 className="font-semibold">预检报告 · {conclusion}</h2><p className="text-xs text-muted-foreground">按《数据标准化预检总纲》统一十章模板（{result.outline_version}）生成；导出后可作为内部交接与客户沟通依据。预检免费，不进入审核文件档案。</p></div>
    <ol aria-label="预检报告章节" className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-sm">{chapters.map(([number, title, value]) => <li key={number} className="flex min-w-0 gap-2 rounded-md bg-muted/40 px-3 py-1.5"><span className="shrink-0 font-medium">{number} {title}</span><span className="min-w-0 truncate text-muted-foreground" title={value}>{value}</span></li>)}</ol>
    <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3"><p className="text-xs text-muted-foreground">报告包含：文件清单、格式合规、规则匹配、阻断与警告明细与整改建议。</p>{error && <p role="alert" className="text-xs text-destructive">{error}</p>}<Button variant="outline" disabled={busy} onClick={() => void download()}>{busy ? <Loader2 className="animate-spin motion-reduce:animate-none"/> : <Download/>}导出预检报告（PDF）</Button></div>
  </section>
}
