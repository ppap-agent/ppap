import type {components} from '../generated/openapi'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {FileName} from '../journey/file-name'
import {judgmentLabels} from '../journey/judgment-state'

type Original = components['schemas']['AuditArchiveOriginal']
type Citation = components['schemas']['ReviewCitation']

export function frozenOriginal(originals: Original[], versions: string[], id: string) {
  const matches = originals.filter(row => row.file_version_id === id)
  return versions.includes(id) && matches.length === 1 && (!matches[0].file || matches[0].file.id === id) ? matches[0] : undefined
}

export function RecordedReview({review, originalFor, onCitation}: {review: components['schemas']['Review']; originalFor: (id: string) => Original | undefined; onCitation: (citation: Citation) => void}) {
  return <section aria-label="已有人工复核" className="space-y-3 border-t pt-4"><div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-medium">已有人工复核</h4><span className="text-xs text-muted-foreground">复核记录 v{review.version}</span></div><Badge variant="outline">{Object.hasOwn(judgmentLabels, review.judgment) ? judgmentLabels[review.judgment as keyof typeof judgmentLabels] : review.judgment}</Badge><p className="whitespace-pre-wrap text-sm">{review.rationale}</p>{review.next_step && <div className="space-y-1 text-sm"><p className="font-medium">后续建议</p><p className="whitespace-pre-wrap">{review.next_step}</p></div>}{review.citations?.map((cite, index) => {
    const target = originalFor(cite.file_version_id)
    return <div key={`${cite.file_version_id}-${index}`} className="space-y-2 text-sm"><p className="whitespace-pre-wrap">{cite.location}</p><p className="whitespace-pre-wrap text-muted-foreground">{cite.excerpt}</p><Button variant="outline" size="sm" disabled={!target} aria-label={`核对复核原件 ${target?.file?.original_filename || '不在本轮的原件'}`} onClick={() => onCitation(cite)}>{target ? <FileName name={target.file?.original_filename || '该次复核原件'}/> : '引用不在本轮冻结范围'}</Button></div>
  })}</section>
}
