import type {components} from '../generated/openapi'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {FileName} from '../journey/file-name'
import {judgmentLabels} from '../journey/judgment-state'
import {useState} from 'react'
import {Disclosure} from '../components/disclosure'
import {displayTime} from '../helpers'

type Original = components['schemas']['AuditArchiveOriginal']
type Citation = components['schemas']['ReviewCitation']

export function frozenOriginal(originals: Original[], versions: string[], id: string) {
  const matches = originals.filter(row => row.file_version_id === id)
  return versions.includes(id) && matches.length === 1 && (!matches[0].file || matches[0].file.id === id) ? matches[0] : undefined
}

type Version = components['schemas']['ReviewVersion']
const judgmentText = (judgment: string) => Object.hasOwn(judgmentLabels, judgment) ? judgmentLabels[judgment as keyof typeof judgmentLabels] : judgment

/** The review in effect, and on request every earlier version of it: a correction never erases one (#358). */
export function RecordedReview({review, originalFor, onCitation, loadHistory}: {review: components['schemas']['Review']; originalFor: (id: string) => Original | undefined; onCitation: (citation: Citation) => void; loadHistory?: () => Promise<Version[]>}) {
  const [history, setHistory] = useState<{state: 'idle' | 'loading' | 'ready' | 'error'; earlier: Version[]}>({state: 'idle', earlier: []})
  function open(value: boolean) {
    if (!value || !loadHistory || history.state === 'loading' || history.state === 'ready') return
    setHistory({state: 'loading', earlier: []})
    loadHistory().then(rows => setHistory({state: 'ready', earlier: rows.filter(row => row.version < review.version)}), () => setHistory({state: 'error', earlier: []}))
  }
  return <section aria-label="已有人工复核" className="space-y-3 border-t pt-4"><div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-medium">已有人工复核</h4><span className="text-xs text-muted-foreground">第 {review.version} 次复核</span></div><div className="flex flex-wrap gap-2"><Badge variant="outline">{judgmentText(review.judgment)}</Badge>{review.severity && <Badge variant="outline">人工改为 {review.severity} 级</Badge>}</div><p className="whitespace-pre-wrap text-sm">{review.rationale}</p>{review.next_step && <div className="space-y-1 text-sm"><p className="font-medium">后续建议</p><p className="whitespace-pre-wrap">{review.next_step}</p></div>}{review.citations?.map((cite, index) => {
    const target = originalFor(cite.file_version_id)
    return <div key={`${cite.file_version_id}-${index}`} className="space-y-2 text-sm"><p className="whitespace-pre-wrap">{cite.location}</p><p className="whitespace-pre-wrap text-muted-foreground">{cite.excerpt}</p><Button variant="outline" size="sm" disabled={!target} aria-label={`核对复核原件 ${target?.file?.original_filename || '不在本轮的原件'}`} onClick={() => onCitation(cite)}>{target ? <FileName name={target.file?.original_filename || '该次复核原件'}/> : '引用不在本轮范围'}</Button></div>
  })}{loadHistory && review.version > 1 && <Disclosure summary={`更早的复核（${review.version - 1} 次）`} onOpenChange={open}>
    {history.state === 'loading' || history.state === 'idle' ? <p role="status" className="text-sm text-muted-foreground">正在读取更早的复核…</p>
      : history.state === 'error' ? <p role="status" className="text-sm text-muted-foreground">暂时无法读取更早的复核，请稍后再展开。</p>
      : <ol className="space-y-3">{history.earlier.map(row => <li key={row.version} className="space-y-1 border-t pt-3 text-sm first:border-t-0 first:pt-0"><p className="text-xs text-muted-foreground">第 {row.version} 次 · {row.reviewer || '复核人'} · {displayTime(row.reviewed_at)}</p><p><span className="font-medium">{judgmentText(row.judgment)}</span>{row.severity && <span className="text-muted-foreground"> · {row.severity} 级</span>}</p>{row.rationale && <p className="whitespace-pre-wrap">{row.rationale}</p>}{row.next_step && <p className="whitespace-pre-wrap text-muted-foreground">后续建议：{row.next_step}</p>}</li>)}</ol>}
  </Disclosure>}</section>
}
