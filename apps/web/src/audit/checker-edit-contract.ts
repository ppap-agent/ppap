import {request} from '../api.ts'
import type {operations} from '../generated/openapi'
import type {CheckerRevision} from './checker-contract.ts'
export type RuleText = {id: string; text: string}
export type SaveCheckerCandidate = NonNullable<operations['suite_candidate_suite_revisions__revision_id__candidates_post']['requestBody']>['content']['application/json'] & {operation_id: string}
export function editableRules(files: Record<string, string>): RuleText[] {
  const body = JSON.parse(files['rules.json'] || '{}')
  if (!Array.isArray(body.rules)) throw new Error('检查规则缺少条款列表。')
  return body.rules.map((row: Record<string, unknown>) => {
    if (typeof row.id !== 'string' || typeof row.rule_to_check !== 'string') throw new Error('条款格式不支持直接编辑，请联系支持。')
    return {id: row.id, text: row.rule_to_check}
  })
}
export function candidateChanges(files: Record<string, string>, rules: RuleText[], templates: string, code: Record<string, string>): Record<string, string> {
  const body = JSON.parse(files['rules.json']), original = editableRules(files)
  if (rules.length !== original.length || rules.some((row, index) => row.id !== original[index].id || !row.text.trim())) throw new Error('条款身份必须保持不变，规则正文不能为空。')
  const next: Record<string, string> = {}
  if (rules.some((row, index) => row.text !== original[index].text)) next['rules.json'] = JSON.stringify({...body, rules: body.rules.map((row: Record<string, unknown>, index: number) => ({...row, rule_to_check: rules[index].text.trim()}))}, null, 2)
  if (templates !== (files['templates.json'] || '[]')) {
    const value = JSON.parse(templates)
    if (!Array.isArray(value) || value.some(row => !row || typeof row !== 'object' || Array.isArray(row))) throw new Error('模板映射必须是对象列表。')
    if (value.some(row => row.kind === 'licensed_blank' && row.distributable !== true)) throw new Error('模板没有分发授权，请仅保存结构映射。')
    next['templates.json'] = JSON.stringify(value, null, 2)
  }
  for (const [path, text] of Object.entries(code)) {
    if (!['checker.py', 'resources.py'].includes(path)) throw new Error('只能编辑当前检查的代码文件。')
    if (text !== files[path]) next[path] = text
  }
  if (!Object.keys(next).length) throw new Error('尚未修改规则、模板或检查代码。')
  return next
}
export const nativeCheckerEditAdapter = {
  save: async (base: string, input: SaveCheckerCandidate) => checkedCandidate(await request<CheckerRevision>(`/suite-revisions/${encodeURIComponent(base)}/candidates`, 'POST', input), base, input.files),
  read: async (base: string, operation: string) => checkedCandidate(await request<CheckerRevision>(`/suite-revisions/${encodeURIComponent(base)}/candidates/${encodeURIComponent(operation)}`), base),
}

export function checkedCandidate(value: CheckerRevision, base: string, files?: Record<string, string>) {
  if (!value.id || value.id === base || value.base_revision_id !== base || files && Object.entries(files).some(([path, text]) => value.files[path] !== text)) throw new Error('待发布修改与原保存操作不一致，请读取原操作。')
  return value
}
