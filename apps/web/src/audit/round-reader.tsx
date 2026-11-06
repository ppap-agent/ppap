import {ReadSkeleton} from '../components/read-skeleton'
import {useContext, useEffect, useState} from 'react'
import {createPortal} from 'react-dom'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger} from '../components/ui/dropdown-menu'
import {FileName} from '../journey/file-name'
import {message} from '../journey/remote'
import {AuditReviewContent, type OriginalRenderer} from './review-content'
import {HistoricalFindings} from './historical-findings'
import {sameSnapshot} from './result-projection'
import {ReviewEditorContext} from './review-editor'
import {writeAuditAddress} from './routes'
import {DeepFeedback} from './deep-feedback'
import {FactQuestions} from './fact-questions'

type Round = components['schemas']['Round']
export type ReadRound = (id: string, signal: AbortSignal) => Promise<Round>

export function AuditRoundReader({roundId, readRound, renderOriginal, onReaudit, onReport, headerActions, returnHref = '/workbench'}: {roundId: string; readRound: ReadRound; renderOriginal?: OriginalRenderer; onReaudit?: () => void; onReport?: (taskId: string, fileVersionId: string) => void; headerActions?: HTMLElement | null; returnHref?: string}) {
  const reviewEditor = useContext(ReviewEditorContext)
  const [basisChanged, setBasisChanged] = useState(false)
  const [round, setRound] = useState<Round | null>(null), [error, setError] = useState(''), [revision, setRevision] = useState(0), [selected, setSelected] = useState(() => new URL(location.href).searchParams.get('file') || '')
  useEffect(() => {
    const controller = new AbortController()
    setRound(current => current?.id === roundId ? current : null); setError('')
    void readRound(roundId, controller.signal).then(value => {
      if (controller.signal.aborted) return
      const files = value.read_view?.files
      if (value.id !== roundId || !files || files.length !== value.inputs.length || new Set(files.map(row => row.file.id)).size !== value.inputs.length || files.some(row => !value.inputs.some(input => input.file_version_id === row.file.id && input.file_id === row.file.file_id && input.role === row.role && sameSnapshot(input.rule, row.rule)) || row.audit && (row.audit.round_id !== value.id || row.audit.file_version_id !== row.file.id || row.audit.result_attempt !== value.result_attempt || !sameSnapshot(row.audit.rule, row.rule)))) throw new Error('历史读取与本轮冻结文件不一致。')
      setRound(value)
    }).catch(cause => {if (!controller.signal.aborted) setError(message(cause))})
    return () => controller.abort()
  }, [roundId, readRound, revision, reviewEditor?.revision])
  useEffect(() => {
    const restore = () => setSelected(new URL(location.href).searchParams.get('file') || '')
    restore(); window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [roundId])
  function selectFile(id: string) {
    setSelected(id)
    const url = new URL(location.href); url.searchParams.set('file', id); url.searchParams.delete('clause'); url.searchParams.delete('focus')
    writeAuditAddress(url, 'push')
  }
  const view = round?.id === roundId ? round.read_view : null
  const file = view?.files.find(row => row.file.id === selected) ?? view?.files.find(row => row.role === 'target') ?? view?.files[0]
  const reportSource = view?.report_source
  const reportTaskId = reportSource?.access.state === 'allowed' ? reportSource.task_id : null
  const versions = view?.files.map(row => row.file.id) || []
  const originals = view?.files.map(row => ({file_version_id: row.file.id, file: row.file, access: {state: 'allowed' as const, reason: null}})) || []
  return <section aria-label="历史审核" data-round-id={round?.id === roundId ? round.id : undefined} data-result-attempt={round?.id === roundId ? round.result_attempt : undefined} className="mx-auto max-w-screen-2xl space-y-6">
    {headerActions ? <><h1 className="sr-only">历史审核</h1>{createPortal(<DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline" size="sm">历史操作</Button></DropdownMenuTrigger><DropdownMenuContent align="end" className="w-64">
      <DropdownMenuItem asChild><a href={returnHref}>返回工作台</a></DropdownMenuItem>
      {onReaudit && round?.id === roundId && ['completed', 'failed'].includes(round.execution) && <DropdownMenuItem onSelect={onReaudit}>补件／重新审核</DropdownMenuItem>}
      <DropdownMenuItem onSelect={() => setRevision(value => value + 1)}>重新读取</DropdownMenuItem>
      {round?.id === roundId && round.created_at && <><DropdownMenuSeparator/><DropdownMenuLabel className="whitespace-normal">审核时间：{new Date(round.created_at).toLocaleString()}</DropdownMenuLabel></>}
    </DropdownMenuContent></DropdownMenu>, headerActions)}</> : <header className="flex flex-wrap items-center justify-between gap-3">
      <div className="space-y-2"><h1 className="text-2xl font-semibold">历史审核</h1>{round?.id === roundId && round.created_at && <p className="text-sm text-muted-foreground">{new Date(round.created_at).toLocaleString()}</p>}</div>
      <div className="flex flex-wrap gap-2"><Button variant="outline" asChild><a href={returnHref}>返回工作台</a></Button>{onReaudit && round && ['completed', 'failed'].includes(round.execution) && <Button onClick={onReaudit}>补件／重新审核</Button>}<Button variant="outline" onClick={() => setRevision(value => value + 1)}>重新读取</Button></div>
    </header>}
    {error ? <Alert variant="destructive"><AlertTitle>历史审核暂不可用</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : !view ? <ReadSkeleton label="正在读取本轮审核…" kind={'detail'}/> : <>
      {round?.execution === 'completed' && <FactQuestions key={`facts:${roundId}`} roundId={roundId} files={view.files.map(row => row.file)} findings={round.findings}/>}
      {round?.analysis_mode === 'deep' && <DeepFeedback key={roundId} roundId={roundId} files={view.files.map(row => row.file)} rules={view.files.flatMap(row => row.rule?.body.kind === 'engine' ? row.rule.body.rules.map(rule => ({documentId: row.file.id, filename: row.file.original_filename, rule})) : [])} renderOriginal={renderOriginal} onBasisChanged={setBasisChanged}/>}
      {basisChanged && <h2 className="text-lg font-semibold">此前输入的审核结果</h2>}
      <nav aria-label="本轮文件" className="flex flex-wrap gap-2">{view.files.map(row => <Button key={row.file.id} variant={file?.file.id === row.file.id ? 'secondary' : 'ghost'} className="h-auto max-w-full whitespace-normal text-left" aria-pressed={file?.file.id === row.file.id} onClick={() => selectFile(row.file.id)}><FileName name={row.file.original_filename}/>{row.role === 'reference' && <span className="text-xs text-muted-foreground">比对资料</span>}</Button>)}</nav>
      {file ? file.role === 'target' ? <AuditReviewContent onReaudit={onReaudit} key={`${roundId}:${file.file.id}`} file={file} roundId={roundId} frozenRule={file.rule} frozenFileVersionIds={versions} originals={originals} renderOriginal={renderOriginal} reportAccess={reportSource?.access} onReport={onReport && reportTaskId ? () => onReport(reportTaskId, file.file.id) : undefined}/> : <section aria-label="本轮比对原件" className="space-y-4"><h2 className="font-semibold"><FileName name={file.file.original_filename}/></h2>{renderOriginal ? renderOriginal(file.file, null, roundId) : <p>原件暂不可预览。</p>}</section> : <p>本轮没有可读取的文件。</p>}
      <HistoricalFindings findings={view.unassigned_findings} roundId={roundId} resultAttempt={round?.result_attempt ?? null} versions={versions} originals={originals} renderOriginal={renderOriginal} title="未归属文件的历史记录"/>
    </>}
  </section>
}
