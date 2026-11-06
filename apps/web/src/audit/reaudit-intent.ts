import type {components} from '../generated/openapi.ts'

type ReauditFile = components['schemas']['AuditTaskFile']
export type ReauditRecognition = 'source' | 'selected' | 'rematch'

/** The inherited rule identifies applicability, never the Suite version to execute. */
export function reauditRecognition(file: ReauditFile): ReauditRecognition {
  if (!file.preferred_rule_version_id) return 'rematch'
  return file.preferred_rule_version_id === file.rule_options?.[0]?.id ? 'source' : 'selected'
}
export function chooseReauditRecognition(file: ReauditFile, choice: ReauditRecognition): ReauditFile {
  if (choice === 'selected') return file
  const preferred = choice === 'source' ? file.rule_options?.[0]?.id : undefined
  if (choice === 'source' && !preferred) throw new Error('原适用检查关联尚未读取。')
  return {...file, preferred_rule_version_id: preferred}
}
