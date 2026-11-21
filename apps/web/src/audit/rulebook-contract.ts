import type {components} from '../generated/openapi'
import type {Question, QuestionnaireList, QuestionnaireSummary} from './rule-questions-contract'

export type RevisionList = components['schemas']['RulebookRevisionList']
export type RevisionSummary = components['schemas']['RulebookRevisionSummary']
export type RevisionView = components['schemas']['RulebookRevisionView']
export type RevisionRef = {version: string; revision: number}
type ChangeKind = components['schemas']['RulebookClauseChange']['kind']

/** How people name a revision: v1.0 修订 0. */
export const revisionLabel = (ref: RevisionRef) => `${ref.version} 修订 ${ref.revision}`
/** The revision in a page address: v1.0-m0. */
export const revisionKey = (ref: RevisionRef) => `${ref.version}-m${ref.revision}`
export function parseRevisionKey(key: string): RevisionRef | null {
  const match = /^(v\d+\.\d+)-m(\d+)$/.exec(key)
  return match ? {version: match[1]!, revision: Number(match[2])} : null
}

export const CHANGE_LABEL: Record<ChangeKind, string> = {same: '意思不变', changed: '意思变了', new: '新增'}

/** 「相对 v0.0 修订 1：意思不变 6 · 意思变了 46 · 新增 11」, or '' for a revision without a comparison. */
export function changeSummary(summary: Pick<RevisionSummary, 'base' | 'changes'>): string {
  if (!summary.changes) return ''
  const counts = (Object.keys(CHANGE_LABEL) as ChangeKind[]).map(kind => `${CHANGE_LABEL[kind]} ${summary.changes![kind]}`).join(' · ')
  return summary.base ? `相对 ${revisionLabel(summary.base)}：${counts}` : counts
}

/**
 * The customer's questionnaire on a revision. Its version label names the revision («规范性条款 v1.0 修订 1»);
 * a label naming only the version («规范性条款 v1.0») is that version's first questionnaire, revision 0.
 */
export function questionnaireFor(list: QuestionnaireList | null, customerId: string, ref: RevisionRef): QuestionnaireSummary | undefined {
  return list?.questionnaires.find(row => row.customer_id === customerId && (row.rule_version.endsWith(` ${revisionLabel(ref)}`) || (ref.revision === 0 && row.rule_version.endsWith(` ${ref.version}`))))
}

/** Each card under every clause it asks about; a card's items are «<clause>#<place>». */
export function questionsByClause(questions: Question[]): Map<string, Question[]> {
  const byClause = new Map<string, Question[]>()
  for (const question of questions) {
    for (const clause of new Set(question.items.map(item => item.split('#', 1)[0]!))) byClause.set(clause, [...(byClause.get(clause) ?? []), question])
  }
  return byClause
}
