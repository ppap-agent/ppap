import type {RequirementBasis} from './requirements-state'
import type {SourceFile} from './source-documents'

/** One requirement's machine result and the original passages it rests on. */
export type DecisionEvidence = {
  displayId?: string
  id: string; title: string; requirement: string
  result: '满足' | '不满足' | '无法判断' | '未覆盖' | '执行失败' | '不适用'
  explanation: string; sources: {file: string; fileVersionId?: string; location: string; excerpt: string}[]
}
export type DecisionInput = {
  roundId?: string
  sample?: 'complete' | 'outlier'
  receiptId: string; scope: string; basis: RequirementBasis | null
  files: SourceFile[]
  clarification: string | null; evidence: DecisionEvidence[]
  receipts?: {id: string; source: string; files: SourceFile[]; notes: string}[]
}
