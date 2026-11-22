import assert from 'node:assert/strict'
import test from 'node:test'
import {applyReceipt, checkedReceipt, choiceLabel, groupQuestions, questionnaireVersionLabel, type Question, type QuestionnaireView} from './rule-questions-contract.ts'

const card = (key: string, group: Question['group'], extra: Partial<Question> = {}): Question => ({
  key, group, clauses: [], question: `${key}？`, why: '', options: [{key: 'A', label: '按原文', detail: ''}], expect: '', related: [], items: [], relayed: null, answers: [], ...extra,
})
const view = (questions: Question[]): QuestionnaireView => ({
  id: 'q1', customer_id: 'uaes', customer_name: 'UAES', rule_version: '规范性条款 v1.0', rulebook: {version: 'v1.0', revision: 0}, title: 'UAES', imported_at: '', closed_at: null, closed: false,
  total: questions.length, answered: questions.filter(row => row.answers.length).length, mine: 0, questions,
})
const receipt = (revision: number, choice: string | null, text: string | null = null) => ({operation_id: `op-${revision}`, questionnaire_id: 'q1', question_key: 'c1', answerer: '张三', mine: true, revision, choice, text, note: null, answered_at: `2026-11-2${revision}T00:00:00Z`})

test('a questionnaire is titled by the revision it was asked on', () => {
  const existing = view([card('c1', 'common')])
  const answered = applyReceipt(existing, receipt(1, 'A'))
  assert.equal(questionnaireVersionLabel(existing), '规范性条款 v1.0 修订 0')
  assert.equal(questionnaireVersionLabel(answered), '规范性条款 v1.0 修订 0')
  assert.deepEqual([answered.id, answered.rule_version, answered.answered], ['q1', '规范性条款 v1.0', 1])
  assert.equal(questionnaireVersionLabel({...existing, rulebook: {version: 'v1.0', revision: 2}}), '规范性条款 v1.0 修订 2')
})

test('groups follow the answering order with common questions first, empty groups left out', () => {
  const groups = groupQuestions([card('x', 'clause'), card('c1', 'common', {answers: [{...receipt(1, 'A'), first_answered_at: '', history: []}]}), card('g', 'general')])
  assert.deepEqual(groups.map(group => [group.label, group.questions.length, group.answered]), [['共性问题', 1, 1], ['通用判定说明', 1, 0], ['逐条问题', 1, 0]])
})

test('a saved answer is added beside other people’s, and a change keeps the earlier answer as history', () => {
  const other = {...receipt(1, null, '看模板'), answerer: '李四', mine: false, first_answered_at: '', history: []}
  let current = view([card('c1', 'common', {answers: [other]})])
  current = applyReceipt(current, receipt(1, 'A'))
  assert.deepEqual(current.questions[0].answers.map(row => [row.answerer, row.mine, row.choice]), [['李四', false, null], ['张三', true, 'A']])
  assert.deepEqual([current.answered, current.mine], [1, 1])
  current = applyReceipt(current, receipt(2, null, '改了'))
  const own = current.questions[0].answers[1]
  assert.deepEqual([own.revision, own.choice, own.text, own.history.map(row => row.choice)], [2, null, '改了', ['A']])
  assert.deepEqual([current.answered, current.mine], [1, 1])
  // A late duplicate of an older receipt changes nothing.
  assert.deepEqual(applyReceipt(current, receipt(2, null, '改了')), current)
  assert.deepEqual(applyReceipt(current, receipt(1, 'A')), current)
})

test('the relayed answer is confirmed in the customer’s words, and only the request’s own receipt counts', () => {
  const relayed = card('p', 'clause', {relayed: {answer: '全部满足即通过。', applied: '缺章不通过', source: '经产品负责人转达', date: '2026-11-20'}})
  assert.equal(choiceLabel(relayed, 'relayed'), '确认按共性答复处理')
  assert.equal(choiceLabel(relayed, 'A'), '按原文')
  const input = {operation_id: 'op-1', question_key: 'c1', expected_revision: 0, choice: 'A', text: null, note: null}
  assert.equal(checkedReceipt(receipt(1, 'A'), 'q1', input).revision, 1)
  assert.throws(() => checkedReceipt({...receipt(1, 'A'), question_key: 'other'}, 'q1', input))
  assert.throws(() => checkedReceipt(null, 'q1', input))
})
