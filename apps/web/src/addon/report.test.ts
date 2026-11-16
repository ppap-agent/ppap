import assert from 'node:assert/strict'
import test from 'node:test'
import {attention, marker, resultMarkdown, verdict, type Task} from './report.ts'

function check(id: string, status: 'PASS' | 'NG' | 'UNABLE', text: string) {
  return {rule_item: {id, category: '完整性', rule_to_check: text}, execution: 'completed', coverage: 'executed', scope_notice: null, evidence: [], review: null, finding: {status, suggestions: status === 'NG' ? '特殊特性|符号缺失' : ''}}
}
function finished(overrides: Record<string, unknown> = {}): Task {
  const counts = {pass_count: 1, ng_count: 1, unknown_count: 1, not_covered_count: 0, error_count: 0}
  const audit = {execution: 'completed', conclusion: 'NG', counts, checks: [check('c1', 'PASS', '封面完整'), check('c2', 'UNABLE', '签字日期可辨认'), check('c3', 'NG', '特殊特性已标识')]}
  return {
    id: 'task-1', number: 'PPAP-20261116-003', state: 'completed', execution: 'completed', has_execution_error: false, completion_reason: 'processed',
    round_id: 'round-1', round_started_at: '2026-11-16T06:30:00Z', created_at: '2026-11-16T06:00:00Z',
    files: [
      {file: {id: 'v1', original_filename: 'PFMEA|A.xlsx'}, selection: 'selected', match: {state: 'matched'}, audit},
      {file: {id: 'v2', original_filename: '参考.pdf'}, selection: 'selected', match: {state: 'reference'}, audit: null},
    ],
    ...overrides,
  } as unknown as Task
}

test('an unfinished or failed audit is never stated as conforming', () => {
  assert.equal(verdict(finished()), 'NG')
  assert.equal(verdict(finished({state: 'reviewing', execution: 'running'})), 'RUNNING')
  assert.equal(verdict(finished({has_execution_error: true})), 'ERROR')
  assert.equal(verdict(finished({completion_reason: 'no_matching_rules'})), 'NONE')
  const passing = finished()
  passing.files[0].audit!.conclusion = 'PASS'
  assert.equal(verdict(passing), 'PASS')
  passing.files[0].audit!.conclusion = null
  assert.equal(verdict(passing), 'UNKNOWN')
  assert.throws(() => resultMarkdown(finished({has_execution_error: true}), 'https://ppap.example/t'), /round_incomplete/)
})

test('the document content names the audit, every audited file and each open item, most urgent first', () => {
  const task = finished()
  assert.deepEqual(attention(task).map(item => item.check.rule_item.id), ['c3', 'c2'])
  const text = resultMarkdown(task, 'https://ppap.example/tasks/task-1')
  assert.ok(text.startsWith(`## ${marker(task)}\n`))
  assert.match(text, /\*\*总体结论：不符合\*\*　不符合 1 项，尚未确定 1 项/)
  // A file name or reason cannot break the table or the list it is written into.
  assert.match(text, /\| PFMEA｜A\.xlsx \| 不符合 \| 1 \| 1 \|/)
  assert.match(text, /- \[ \] 【不符合】PFMEA｜A\.xlsx：特殊特性已标识——特殊特性｜符号缺失/)
  assert.match(text, /- \[ \] 【无法判断】PFMEA｜A\.xlsx：签字日期可辨认/)
  assert.ok(!text.includes('参考.pdf') && !text.includes('封面完整'))
  assert.ok(text.includes('](https://ppap.example/tasks/task-1)'))
})
