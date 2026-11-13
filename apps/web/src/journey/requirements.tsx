import type {RequirementBasis} from './requirements-state'
import {SourceReaderButton} from './source-reader'

export function RequirementOriginal({basis, label = '阅读规则原件', variant}: {basis: RequirementBasis; label?: string; variant?: 'outline' | 'ghost' | 'link'}) {
  // An unreadable original cannot be opened; the source line already says so.
  if (!basis.source.localFile && !basis.server || basis.server?.material.status === 'unreadable') return null
  const file = {name: basis.source.filename, revision: basis.source.version || '待确认', sourceVersion: 1, localFile: basis.source.localFile, material: basis.server?.material}
  return <SourceReaderButton file={file} label={label} variant={variant} input={{receiptId: basis.source.version || '要求草稿', scope: basis.source.title, files: [file], evidence: [], clarification: null}}/>
}
