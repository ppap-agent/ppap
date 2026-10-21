import {useContext, useEffect, useState} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Button} from '../components/ui/button'
import {FileName} from '../journey/file-name'
import {message} from '../journey/remote'
import {AuditReviewContent, type OriginalRenderer} from './review-content'
import {HistoricalFindings} from './historical-findings'
import {sameSnapshot} from './result-projection'
import {ReviewEditorContext} from './review-editor'

type Round = components['schemas']['Round']
export type ReadRound = (id: string, signal: AbortSignal) => Promise<Round>

export function AuditRoundReader({roundId, readRound, renderOriginal, onReaudit, returnHref = '/workbench'}: {roundId: string; readRound: ReadRound; renderOriginal?: OriginalRenderer; onReaudit?: () => void; returnHref?: string}) {
  const reviewEditor = useContext(ReviewEditorContext)
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
    history.pushState(history.state, '', url)
  }
  const view = round?.id === roundId ? round.read_view : null
  const file = view?.files.find(row => row.file.id === selected) ?? view?.files.find(row => row.role === 'target') ?? view?.files[0]
  const versions = view?.files.map(row => row.file.id) || []
  const originals = view?.files.map(row => ({file_version_id: row.file.id, file: row.file, access: {state: 'allowed' as const, reason: null}})) || []
  return <section aria-label="历史审核" className="mx-auto max-w-screen-2xl space-y-6">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div className="space-y-2"><h1 className="text-2xl font-semibold">历史审核</h1>{round?.id === roundId && round.created_at && <p className="text-sm text-muted-foreground">{new Date(round.created_at).toLocaleString()}</p>}</div>
      <div className="flex flex-wrap gap-2"><Button variant="outline" asChild><a href={returnHref}>返回工作台</a></Button>{onReaudit && round && ['completed', 'failed'].includes(round.execution) && <Button onClick={onReaudit}>补件／重新审核</Button>}<Button variant="outline" onClick={() => setRevision(value => value + 1)}>重新读取</Button></div>
    </header>
    {error ? <Alert variant="destructive"><AlertTitle>历史审核暂不可用</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : !view ? <p role="status">正在读取本轮审核…</p> : <>
      <nav aria-label="本轮文件" className="flex flex-wrap gap-2">{view.files.map(row => <Button key={row.file.id} variant={file?.file.id === row.file.id ? 'secondary' : 'ghost'} className="h-auto max-w-full whitespace-normal text-left" aria-pressed={file?.file.id === row.file.id} onClick={() => selectFile(row.file.id)}><FileName name={row.file.original_filename}/>{row.role === 'reference' && <span className="text-xs text-muted-foreground">比对资料</span>}</Button>)}</nav>
      {file ? file.role === 'target' ? <AuditReviewContent key={`${roundId}:${file.file.id}`} file={file} roundId={roundId} frozenRule={file.rule} frozenFileVersionIds={versions} originals={originals} renderOriginal={renderOriginal}/> : <section aria-label="本轮比对原件" className="space-y-4"><h2 className="font-semibold"><FileName name={file.file.original_filename}/></h2>{renderOriginal ? renderOriginal(file.file, null, roundId) : <p>原件暂不可预览。</p>}</section> : <p>本轮没有可读取的文件。</p>}
      <HistoricalFindings findings={view.unassigned_findings} roundId={roundId} resultAttempt={round?.result_attempt ?? null} versions={versions} originals={originals} renderOriginal={renderOriginal} title="未归属文件的历史记录"/>
    </>}
  </section>
}
