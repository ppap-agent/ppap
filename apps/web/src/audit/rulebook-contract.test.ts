import assert from 'node:assert/strict'
import test from 'node:test'
import type {Question, QuestionnaireList} from './rule-questions-contract.ts'
import {changeSummary, parseRevisionKey, questionnaireFor, questionsByClause, revisionKey, revisionLabel} from './rulebook-contract.ts'

const summary = (id: string, customer: string, label: string) => ({id, customer_id: customer, rule_version: label}) as QuestionnaireList['questionnaires'][number]

test('a revision is named and addressed the same way everywhere', () => {
  const ref = {version: 'v1.0', revision: 0}
  assert.equal(revisionLabel(ref), 'v1.0 修订 0')
  assert.deepEqual(parseRevisionKey(revisionKey(ref)), ref)
  for (const bad of ['v1.0', 'v1-m0', '../v1.0-m0', 'v1.0-m']) assert.equal(parseRevisionKey(bad), null)
  assert.equal(changeSummary({base: {version: 'v0.0', revision: 1}, changes: {same: 6, changed: 46, new: 11}}), '相对 v0.0 修订 1：意思不变 6 · 意思变了 46 · 新增 11')
  assert.equal(changeSummary({base: null, changes: null}), '')
})

test('a questionnaire belongs to the revision its label names; a bare version label is revision 0', () => {
  const list = {questionnaires: [summary('q0', 'uaes', '规范性条款 v1.0'), summary('q1', 'uaes', '规范性条款 v1.0 修订 1'), summary('other', 'li', '规范性条款 v1.0')]}
  assert.equal(questionnaireFor(list, 'uaes', {version: 'v1.0', revision: 0})?.id, 'q0')
  assert.equal(questionnaireFor(list, 'uaes', {version: 'v1.0', revision: 1})?.id, 'q1')
  assert.equal(questionnaireFor(list, 'uaes', {version: 'v1.0', revision: 2}), undefined)
  assert.equal(questionnaireFor(list, 'uaes', {version: 'v11.0', revision: 0}), undefined)
  assert.equal(questionnaireFor(null, 'uaes', {version: 'v1.0', revision: 0}), undefined)
})

test('a card shows under every clause it asks about, once each', () => {
  const card = (key: string, items: string[]) => ({key, items}) as Question
  const byClause = questionsByClause([card('common:印章', ['PKG:1#2', 'MSL:2#3', 'SCL:1#5']), card('PKG:1#1', ['PKG:1#1', 'PKG:1#G1'])])
  assert.deepEqual([...byClause].map(([clause, cards]) => [clause, cards.map(item => item.key)]), [['PKG:1', ['common:印章', 'PKG:1#1']], ['MSL:2', ['common:印章']], ['SCL:1', ['common:印章']]])
})
