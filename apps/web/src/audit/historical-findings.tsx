import {useContext, useState} from 'react'
import type {components} from '../generated/openapi'
import {Alert, AlertDescription, AlertTitle} from '../components/ui/alert'
import {Badge} from '../components/ui/badge'
import {Button} from '../components/ui/button'
import {Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle} from '../components/ui/dialog'
import {Item, ItemContent, ItemGroup, ItemTitle} from '../components/ui/item'
import {RecordedReview, frozenOriginal} from './recorded-review'
import {sameSnapshot} from './result-projection'
import type {OriginalRenderer} from './review-content'
import {ReviewEditorContext} from './review-editor'
import {resultLabel, resultTone} from './status-labels'

type Finding = components['schemas']['Finding']
type Citation = components['schemas']['ReviewCitation']

export function HistoricalFindings({findings, roundId, resultAttempt, versions, originals, renderOriginal, fileVersionId, title = '历史机器记录'}: {findings: Finding[]; roundId: string; resultAttempt: number | null; versions: string[]; originals: components['schemas']['AuditArchiveOriginal'][]; renderOriginal?: OriginalRenderer; fileVersionId?: string; title?: string}) {
  const reviewEditor = useContext(ReviewEditorContext)
  const [selected, setSelected] = useState<{key: string; version: string; citation?: Citation} | null>(null)
  const finding = findings.find(row => row.item_key === selected?.key)
  const valid = finding && (!selected?.citation || finding.review?.citations?.some(cite => sameSnapshot(cite, selected.citation)))
  const originalFor = (id: string) => frozenOriginal(originals, versions, id)
  const original = valid && selected ? originalFor(selected.version) : undefined
  if (!findings.length) return null
  return <section aria-label={title} className="space-y-4 border-t pt-4"><h3 className="font-semibold">{title}</h3><p className="text-sm text-muted-foreground">以下原始记录未建立与具体条款的对应关系，不计为已核验条款。</p><ItemGroup>{findings.map(row => {
    const version = row.locator?.file_version_id || fileVersionId || ''
    return <Item role="listitem" key={row.item_key}><ItemContent className="min-w-0">
      <ItemTitle wrap>{row.item || row.classification || '历史审核记录'}</ItemTitle><Badge variant={resultTone(row.status)}>原机器记录：{resultLabel[row.status]}</Badge>
      {row.classification && row.classification !== row.item && <p className="whitespace-pre-wrap text-sm">{row.classification}</p>}{row.suggestions && <p className="whitespace-pre-wrap text-sm">{row.suggestions}</p>}{row.locator?.locator && <p className="whitespace-pre-wrap text-xs text-muted-foreground">原记录位置：{row.locator.locator}</p>}
      <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={!originalFor(version)} onClick={() => setSelected({key: row.item_key, version})}>核对记录原件</Button>{reviewEditor && <Button variant="outline" size="sm" onClick={() => reviewEditor.open({roundId, attempt: resultAttempt, itemKey: row.item_key, title: row.item || row.classification || '历史审核记录', files: originals.filter(original => versions.includes(original.file_version_id)).map(original => ({id: original.file_version_id, name: original.file?.original_filename || '本轮冻结原件'}))})}>记录人工复核</Button>}</div>
      {row.review && <RecordedReview review={row.review} originalFor={originalFor} onCitation={citation => setSelected({key: row.item_key, version: citation.file_version_id, citation})}/>}
    </ItemContent></Item>
  })}</ItemGroup><Dialog open={selected !== null} onOpenChange={open => {if (!open) setSelected(null)}}><DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-5xl" data-file-version={original?.file_version_id}><DialogHeader><DialogTitle>历史原件对照</DialogTitle><DialogDescription>核对本轮冻结文件，人工位置记录不等于已验证的自动定位。</DialogDescription></DialogHeader>{original?.access.state === 'allowed' && original.file ? <div className="space-y-3">{selected?.citation && <section aria-label="人工记录位置" className="space-y-2 text-sm"><p>{selected.citation.location}</p><p className="whitespace-pre-wrap text-muted-foreground">{selected.citation.excerpt}</p></section>}{renderOriginal ? renderOriginal(original.file, null, roundId) : <p>原件暂不可预览。</p>}</div> : <Alert><AlertTitle>本次原件不可访问</AlertTitle><AlertDescription>{original?.access.reason || '记录或引用不在本轮有效的冻结范围内。'}</AlertDescription></Alert>}</DialogContent></Dialog></section>
}
