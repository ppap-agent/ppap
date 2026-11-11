import assert from 'node:assert/strict'
import test from 'node:test'
import {canRequestFormalization, checkPointLines, formalizationFailure, formalizationReceiptMatches, sameFormalizationTarget, textForCandidate, visibleFormalizationReadings} from './requirement-formalization-contract.ts'

const view = {ruleVersionId: 'rule-1', clauseId: 'c1'}

test('a changed source keeps the old reading visible but not confirmable', () => {
  const older = {cnl_text: '至少一条'}
  const latest = {cnl_text: '恰好一条'}
  const current = visibleFormalizationReadings('candidate', [older, latest])
  assert.equal(current.current, latest)
  assert.equal(current.previous, older)
  assert.equal(current.historical, null)
  const stale = visibleFormalizationReadings('source_changed', [older, latest])
  assert.equal(stale.current, null)
  assert.equal(stale.historical, latest)
})

test('stopped check points can be prepared again after a candidate exists', () => {
  assert.equal(canRequestFormalization('not_started'), true)
  assert.equal(canRequestFormalization('not_started', 'unknown'), false)
  assert.equal(canRequestFormalization('not_started', 'queued'), false)
  assert.equal(canRequestFormalization('source_changed'), true)
  assert.equal(canRequestFormalization('candidate', 'stopped'), true)
  assert.equal(canRequestFormalization('candidate', 'failed'), true)
  assert.equal(canRequestFormalization('confirmed', 'stopped'), true)
  assert.equal(canRequestFormalization('candidate', 'completed'), false)
  assert.equal(canRequestFormalization('needs_clarification', 'unknown'), false)
  assert.equal(formalizationFailure('model_unavailable'), '服务暂时不可用，请稍后重试；如持续出现，请联系管理员。')
  assert.equal(formalizationFailure('source_unchecked'), '这条的出处还没核对。先在规则页核对出处。')
  assert.equal(formalizationFailure('formalization_failed'), '这条没整理出来。')
  assert.equal(formalizationFailure('source_changed'), '这条没整理出来。')
})

test('a clarification choice does not carry to the next candidate', () => {
  assert.equal(textForCandidate('cand-1', 'cand-1', 'exact'), 'exact')
  assert.equal(textForCandidate('cand-2', 'cand-1', 'exact'), '')
  assert.equal(textForCandidate('cand-2', 'cand-1', '改成至少一条'), '')
  assert.equal(textForCandidate('cand-1', 'cand-1', '改成至少一条'), '改成至少一条')
  assert.equal(textForCandidate(undefined, 'cand-1', 'exact'), '')
})

test('a handoff result stays on the rule version and clause that started it', () => {
  const origin = {ruleVersionId: 'rule-1', clauseId: 'c1'}
  assert.equal(sameFormalizationTarget(origin, origin), true)
  assert.equal(sameFormalizationTarget(origin, {ruleVersionId: 'rule-2', clauseId: 'c1'}), false)
  assert.equal(sameFormalizationTarget(origin, {ruleVersionId: 'rule-1', clauseId: 'c2'}), false)
})

test('a receipt applies only to the same operation and clause', () => {
  const receipt = {operation_id: 'op-1', rule_version_id: 'rule-1', clause_id: 'c1'}
  assert.equal(formalizationReceiptMatches(view, receipt, 'op-1'), true)
  assert.equal(formalizationReceiptMatches(view, receipt, 'op-2'), false)
  assert.equal(formalizationReceiptMatches(view, {...receipt, clause_id: 'c2'}, 'op-1'), false)
  assert.equal(formalizationReceiptMatches(view, {...receipt, rule_version_id: 'rule-2'}, 'op-1'), false)
  assert.equal(formalizationReceiptMatches(view, {operation_id: 'op-1'}, 'op-1'), false)
  assert.equal(formalizationReceiptMatches(view, {...receipt, candidate_id: null}, 'op-1'), true)
  const pinned = {...view, candidateId: 'cand-1'}
  assert.equal(formalizationReceiptMatches(pinned, {...receipt, candidate_id: 'cand-1'}, 'op-1'), true)
  assert.equal(formalizationReceiptMatches(pinned, {...receipt, candidate_id: 'cand-2'}, 'op-1'), false)
  assert.equal(formalizationReceiptMatches(pinned, {...receipt, candidate_id: null}, 'op-1'), false)
})


test('check points are listed once, without the numbering the text already carries', () => {
  assert.deepEqual(checkPointLines('1. 文件包含测量比较报告\n2、底部有四个字段\n\n- 每个字段都有签字'), ['文件包含测量比较报告', '底部有四个字段', '每个字段都有签字'])
  assert.deepEqual(checkPointLines('检验日期 ≥ 样件生产日期。'), ['检验日期 ≥ 样件生产日期。'])
  assert.deepEqual(checkPointLines('（1）编号一致\n(2) 名称一致'), ['编号一致', '名称一致'])
  assert.deepEqual(checkPointLines('o1：每一条都有编号。\no2：编号不重复。', ['o1', 'o2']), ['每一条都有编号。', '编号不重复。'])
  // An incomplete reading hides its diagnostics; it cannot be confirmed, but its reading stays visible.
  assert.deepEqual(checkPointLines('尚不能确定：\n- uncovered_obligation：s\n- 义务 o1 没有被形式表达式覆盖（kind_not_in_expression：s）\n\n候选理解（未确认）：\no1：至少一条记录有编号。\n已声明前提：编号印在页脚', ['o1']), ['至少一条记录有编号。', '已声明前提：编号印在页脚'])
})
