import {isPreview} from './access'
import {Collapsible, CollapsibleTrigger, CollapsibleContent} from '../components/ui/collapsible'
// Stitch: projects/13261964020862199279/screens/a0caaf69f431417481379fdd52f2075a.
// Same account dialog, three task hierarchies via ?recordsLayout=...; no cloud sync.
import {useEffect, useRef, useState} from 'react'
import {ArrowLeft, ArrowRight, Download, FolderClock, RefreshCw, Upload, WifiOff} from 'lucide-react'
import {Button} from '../components/ui/button'
import {Checkbox} from '../components/ui/checkbox'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {draftEvent, retryDraftSaves, unsavedDrafts} from './saved-draft'
import {clearRecords, createBackup, downloadRecords, inputBackup, localRecords, parseBackup, previewRestore, restoreBackup, type Backup, type LocalRecord, type RecordValidators} from './local-records-state'
const layouts = ['概览与恢复', '记录优先', '操作与明细']
const stamp = (at: string) => at ? new Date(at).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}) : '保存时间未知'
export function LocalSaveNotice({onOpen, validators}: {onOpen: () => void; validators: RecordValidators}) {
  const [failures, setFailures] = useState(unsavedDrafts)
  const [offline, setOffline] = useState(!navigator.onLine)
  const [notice, setNotice] = useState('')
  useEffect(() => {
    const sync = () => setFailures(unsavedDrafts()), network = () => setOffline(!navigator.onLine)
    const leaving = (event: BeforeUnloadEvent) => {if (unsavedDrafts().some(item => item.raw)) {event.preventDefault(); event.returnValue = ''}}
    window.addEventListener(draftEvent, sync); window.addEventListener('online', network); window.addEventListener('offline', network); window.addEventListener('beforeunload', leaving)
    return () => {window.removeEventListener(draftEvent, sync); window.removeEventListener('online', network); window.removeEventListener('offline', network); window.removeEventListener('beforeunload', leaving)}
  }, [])
  function saveInput() {
    try {downloadRecords(createBackup(validators, true), '当前输入备份'); setNotice('已请求下载当前输入备份；请确认下载文件。尚未改变本机保存状态。')}
    catch {downloadRecords(inputBackup(), '未保存输入'); setNotice('已请求下载未保存输入。由于本机记录不可读取，此文件不是完整事项备份。')}
  }
  if (!failures.length && !offline) return null
  return <div className={"relative z-45 flex flex-wrap items-center gap-y-2 gap-x-3 py-3 px-6 bg-muted-foreground border-b text-foreground text-sm [&_>_div]:flex-1 [&_>_div]:min-w-55 [&_>_div]:grid [&_>_div]:gap-1 [&_strong]:flex [&_strong]:items-center [&_strong]:gap-1.5 [&_span]:text-muted-foreground"} role={failures.length ? 'alert' : 'status'}>
    <div>{failures.length ? <><strong>有记录尚未保存或无法读取</strong><span>{failures.some(item => item.raw) ? '原有记录未被替换。不要刷新，先保存或导出当前输入。' : '原记录未被改动。请先导出检查；恢复读取后可重新打开页面。'}</span></> : <><strong><WifiOff size={16}/>当前处于离线状态</strong><span>已打开页面可继续本机操作；刷新或关闭后不保证能重新打开。</span></>}{notice && <span>{notice}</span>}</div>
    {failures.some(item => item.raw) && <><Button variant="outline" onClick={retryDraftSaves}>重试保存</Button><Button variant="outline" onClick={saveInput}>导出当前输入</Button></>}
    <Button variant="ghost" onClick={onOpen}>本机记录</Button>
    {failures.length > 0 && failures.every(item => !item.raw) && <Button variant="outline" onClick={() => location.reload()}>重新打开页面</Button>}
  </div>
}
export function LocalRecordsDialog({open, onOpenChange, validators}: {open: boolean; onOpenChange: (open: boolean) => void; validators: RecordValidators}) {
  const [snapshot, setSnapshot] = useState(() => localRecords(validators))
  const [backup, setBackup] = useState<Backup | null>(null)
  const [name, setName] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [clearTarget, setClearTarget] = useState<LocalRecord[] | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [reloadNeeded, setReloadNeeded] = useState(false)
  const [layout, setLayout] = useState(() => Math.max(0, layouts.indexOf(new URLSearchParams(location.search).get('recordsLayout') ?? '')))
  const input = useRef<HTMLInputElement>(null)
  const selection = useRef(0)
  function refresh() {setSnapshot(localRecords(validators))}
  useEffect(() => {
    if (!open) return
    refresh()
    const sync = () => refresh()
    window.addEventListener('storage', sync); window.addEventListener(draftEvent, sync)
    return () => {window.removeEventListener('storage', sync); window.removeEventListener(draftEvent, sync)}
  }, [open, validators])
  function choose(next: number) {setLayout(next); const url = new URL(location.href); url.searchParams.set('recordsLayout', layouts[next]); history.replaceState(null, '', url)}
  useEffect(() => {
    if (!open || !import.meta.env.DEV) return
    const cycle = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || (event.target as HTMLElement).closest('input,textarea,select,button,a,summary,[contenteditable]')) return
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
      event.preventDefault(); event.stopImmediatePropagation(); choose((layout + (event.key === 'ArrowRight' ? 1 : 2)) % 3)
    }
    window.addEventListener('keydown', cycle, true); return () => window.removeEventListener('keydown', cycle, true)
  }, [open, layout])
  function exportSaved() {
    try {downloadRecords(createBackup(validators), '本机记录'); setMessage('已请求下载备份，请在浏览器下载列表确认文件。'); setError('')}
    catch (cause) {setError((cause as Error).message)}
  }
  async function selectFile(file: File | undefined) {
    const token = ++selection.current
    setBackup(null); setError(''); setMessage(''); setName(file?.name ?? '')
    if (!file) return
    try {
      if (file.size > 10 * 1024 * 1024) throw new Error('备份超过 10 MB，未读取或写入。')
      const text = await file.text()
      if (token === selection.current) setBackup(parseBackup(text))
    } catch (cause) {if (token === selection.current) setError((cause as Error).message)}
  }
  function restore() {
    if (!backup) return
    try {const count = restoreBackup(backup, validators); setMessage(`已恢复 ${count} 条新增记录；相同内容保留本机版本。`); setError(''); setReloadNeeded(count > 0); refresh()}
    catch (cause) {setError((cause as Error).message); refresh()}
  }
  function clear() {
    if (!clearTarget || !acknowledged) return
    try {const count = clearRecords(clearTarget, validators); setMessage(`已清理 ${count} 条本机原型记录。只有已下载的备份可用于恢复。`); setClearTarget(null); setBackup(null); setError(''); setReloadNeeded(true); refresh()}
    catch (cause) {setError((cause as Error).message); setClearTarget(null); refresh()}
  }
  const preview = backup ? previewRestore(backup, validators) : null
  const cases = snapshot.records.filter(row => row.key.startsWith('intake:')).length
  const invalid = snapshot.records.filter(row => !row.valid).length
  const overview = <section className={"flex justify-between flex-wrap items-center gap-4.5 [&_strong]:text-base [&_p]:mt-1.5 [&_p]:mr-0 [&_p]:mb-0 [&_p]:ml-0"}><div><strong>{snapshot.unavailable ? '暂时无法读取完整记录' : `${cases} 项审核 · ${snapshot.records.length} 条记录`}</strong><p>{snapshot.records[0]?.at ? `最近保存 ${stamp(snapshot.records[0].at)}` : '尚无可确认的保存时间'}{invalid > 0 && ` · ${invalid} 条无法读取`}</p></div><div className={"flex flex-wrap gap-2 items-center"}><Button variant="outline" onClick={exportSaved} disabled={snapshot.unavailable || !snapshot.records.length}><Download size={16}/>下载记录备份</Button><Button className={""} onClick={() => input.current?.click()}><Upload size={16}/>选择备份恢复</Button><input ref={input} type="file" className={"sr-only"} aria-label="选择本机记录备份" accept=".json,application/json" onChange={event => {void selectFile(event.target.files?.[0]); event.target.value = ''}}/></div></section>
  const records = <section className={""} aria-label="本机已保存记录"><div className={"flex items-center justify-between gap-3 mb-1"}><h2>已保存记录</h2><Button variant="ghost" onClick={refresh}><RefreshCw size={15}/>重新读取</Button></div>{!snapshot.records.length ? <p>{snapshot.unavailable ? '读取失败不代表没有记录。请恢复浏览器存储权限后重试。' : '还没有本机事项记录。上传资料后，可在这里备份。'}</p> : snapshot.records.map(row => <div className={"flex items-center gap-3.5 py-3 px-0 border-b text-sm [&_>_div]:flex-1 [&_>_div]:min-w-0 [&_>_div]:grid [&_>_div]:gap-1 [&_strong]:font-medium last:border-b-0 [&_span]:text-muted-foreground [&_code]:break-all [&_code]:text-xs max-md:flex-wrap max-md:[&_>_div]:min-w-[60%]"} key={row.key}><div><strong>{row.title}</strong><span>{stamp(row.at)}</span><Collapsible><CollapsibleTrigger asChild><Button variant="ghost" size="sm">记录标识</Button></CollapsibleTrigger><CollapsibleContent><code>{row.key}</code></CollapsibleContent></Collapsible></div><span className={!row.valid ? "text-destructive" : ""}>{row.valid ? '可读取' : '无法读取'}</span>{!row.valid && <Button variant="outline" onClick={() => {downloadRecords({format: 'ppap-unreadable-record', key: row.key, raw: row.raw}, '原记录'); setMessage('已请求导出原始记录，未尝试修复或覆盖。')}}>导出原记录</Button>}</div>)}</section>
  const restorePanel = <section className={"border-t pt-5.5 [&_>_p]:mt-2.5 [&_>_p]:mr-0 [&_>_p]:mb-4 [&_>_p]:ml-0 [&_>_p]:break-all"} aria-label="备份核对"><h2>备份核对</h2>{backup && preview ? <><p><strong>{name}</strong><br/>{stamp(backup.at)} · {backup.records.length} 条记录{backup.source === 'current-input' && ' · 含页面输入'}</p><div className={"mb-4 [&_>_div]:flex [&_>_div]:justify-between [&_>_div]:items-center [&_>_div]:gap-4 [&_>_div]:border-b [&_>_div]:py-2.5 [&_>_div]:px-0 [&_>_div]:text-sm [&_span]:min-w-0 [&_small]:block [&_small]:text-muted-foreground [&_small]:text-xs [&_small]:break-all [&_strong]:shrink-0 [&_strong]:text-xs [&_strong]:font-medium max-md:[&_>_div]:items-start max-md:[&_strong]:max-w-22.5"}>{preview.rows.map(row => <div key={row.key}><span>{row.title}<small>{row.key}</small></span><strong>{({new: '新增', same: '相同，跳过', conflict: '内容不同，不覆盖', invalid: '无法读取'})[row.state]}</strong></div>)}</div>{preview.blocked && <p className={"text-destructive"}>{preview.pending ? '请先处理当前未保存输入。' : preview.unavailable ? '请先恢复本机记录读取。' : preview.orphan ? '备份缺少关联的来件事项，不能单独恢复判断记录。' : '存在冲突或无法读取的记录，整份备份暂不恢复。先下载本机备份，再核对要保留的版本。'}</p>}<div className={"flex flex-wrap gap-2 items-center"}><Button className={""} onClick={restore} disabled={preview.blocked || !preview.rows.some(row => row.state === 'new')}>恢复新增记录</Button><Button variant="ghost" onClick={() => {++selection.current; setBackup(null); setName('')}}>取消选取</Button></div></> : <p>选择备份后先比较记录；不会自动覆盖本机内容。</p>}</section>
  return <Dialog open={open} onOpenChange={next => {++selection.current; if (!next && reloadNeeded) {location.href = '/preview/intake.html'; return} onOpenChange(next)}}><DialogContent className={"w-[min(1000px,_calc(100vw_-_40px))] max-w-250 max-h-[calc(100dvh_-_40px)] flex flex-col max-md:w-[calc(100vw_-_20px)] max-md:max-h-[calc(100dvh_-_20px)]      "}>
    <DialogHeader><DialogTitle><FolderClock size={22}/>本机记录</DialogTitle><DialogDescription>备份包含此浏览器的登记、要求、检查和判断，不含文件原件或登录信息，也不是审核报告。</DialogDescription></DialogHeader>
    <div className={"min-h-0 overflow-y-auto flex flex-col gap-6.5 pt-0.5 pr-1 pb-3 pl-1"}>{layout === 0 ? <>{overview}{records}{restorePanel}</> : layout === 1 ? <>{records}{overview}{restorePanel}</> : <div className={"grid grid-cols-[280px_minmax(0,1fr)] gap-7 [&_>_div]:grid [&_>_div]:gap-6.5 max-md:grid-cols-1"}><div>{overview}{restorePanel}</div>{records}</div>}
    {message && <p role="status" className={"p-3 border-l bg-muted-foreground text-foreground"}>{message}</p>}{error && <p role="alert" className={"text-destructive"}>{error}</p>}
    {reloadNeeded && <Button variant="outline" onClick={() => {location.href = '/preview/intake.html'}}>重新打开工作台</Button>}
<Collapsible className={""}><CollapsibleTrigger asChild><Button variant="ghost" size="sm">清理本机原型记录</Button></CollapsibleTrigger><CollapsibleContent><p>仅删除上方列出的记录，保留登录状态、本机原件及其他数据。请先保存备份，并关闭其他原型窗口。清理后无法直接撤销。</p>{clearTarget ? <div className={"grid gap-3.5 [&_label]:flex [&_label]:items-center [&_label]:gap-2.5 [&_label]:text-sm [&_label]:leading-relaxed"}><strong>清理刚才核对的 {clearTarget.length} 条记录？</strong><label><Checkbox checked={acknowledged} onCheckedChange={checked => setAcknowledged(checked === true)}/>我已保留所需备份，理解此浏览器中的原记录将被删除</label><div className={"flex flex-wrap gap-2 items-center"}><Button variant="destructive" onClick={clear} disabled={!acknowledged}>确认清理这 {clearTarget.length} 条记录</Button><Button variant="ghost" onClick={() => setClearTarget(null)}>取消清理</Button></div></div> : <Button variant="outline" onClick={() => {setClearTarget(snapshot.records); setAcknowledged(false)}} disabled={snapshot.unavailable || !snapshot.records.length || unsavedDrafts().some(row => row.raw)}>核对清理范围</Button>}</CollapsibleContent></Collapsible>
    <p className={"text-xs m-0"}>本机备份不是团队同步。恢复及清理前请关闭其他原型窗口；本轮不保证浏览器崩溃或多窗口同时操作时的完整恢复。</p>
    </div>
    {isPreview() && import.meta.env.DEV && <aside className={"flex shrink-0 gap-3 justify-center items-center text-xs border-t pt-2"} aria-label="本机记录布局切换"><Button variant="ghost" aria-label="上一种记录布局" onClick={() => choose((layout + 2) % 3)}><ArrowLeft size={16}/></Button><span>{layout + 1} / 3 · {layouts[layout]}</span><Button variant="ghost" aria-label="下一种记录布局" onClick={() => choose((layout + 1) % 3)}><ArrowRight size={16}/></Button></aside>}
  </DialogContent></Dialog>
}
