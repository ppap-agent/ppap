import assert from 'node:assert/strict'
import test from 'node:test'
import {meaningChangedWithoutText} from './rule-impact-state.ts'

const clause = {clause_id: 'c1', kind: 'unchanged' as const, before: '同一句原文', after: '同一句原文', checker: null}

test('a confirmed meaning change is visible when the clause text is unchanged', () => {
  assert.equal(meaningChangedWithoutText({changed: true, appendix_changed: false, clauses: [clause]}), true)
  assert.equal(meaningChangedWithoutText({changed: false, appendix_changed: false, clauses: [clause]}), false)
  assert.equal(meaningChangedWithoutText({changed: true, appendix_changed: true, clauses: [clause]}), false)
  assert.equal(meaningChangedWithoutText({changed: true, appendix_changed: false, clauses: [{...clause, kind: 'changed'}]}), false)
  assert.equal(meaningChangedWithoutText({changed: true}), false)
})
