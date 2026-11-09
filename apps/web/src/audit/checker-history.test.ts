import assert from 'node:assert/strict'
import test from 'node:test'
import {checkerHistoryChanges} from './checker-history.ts'

test('history excerpts retain the changed original source rather than describe an unrelated selected revision', () => {
  assert.deepEqual(checkerHistoryChanges({files: {}, changed_paths: ['checker.py'], diff: [{path: 'checker.py', before: 'def check():\n  return PASS\n# end', after: 'def check():\n  return NG\n# end'}]}), [{path: 'checker.py', state: '修改', before: 'return PASS', after: 'return NG'}])
})
test('history distinguishes additions, removals, unknown diff and bounded excerpts', () => {
  const changes = checkerHistoryChanges({files: {}, changed_paths: ['missing.json'], diff: [{path: 'new.py', before: null, after: 'a'.repeat(400)}, {path: 'old.py', before: 'old', after: null}]})
  assert.equal(changes[0].state, '未提供差异内容')
  assert.equal(changes[1].state, '新增')
  assert.equal(changes[1].after.length, 301)
  assert.equal(changes[2].state, '移除')
})

test('standalone optimizer snapshots compare with the preceding adopted snapshot, including rollback and repeated adoption', () => {
  const revision = {files: {'checker.py': 'def check():\n  return NG', 'unchanged.json': '{}'}, changed_paths: ['checker.py'], diff: [{path: 'checker.py', before: '', after: 'def check():\n  return NG'}]}
  const previous = {files: {'checker.py': 'def check():\n  return PASS', 'unchanged.json': '{}', 'removed.py': 'obsolete'}}
  assert.deepEqual(checkerHistoryChanges(revision, previous), [{path: 'checker.py', state: '修改', before: 'return PASS', after: 'return NG'}, {path: 'removed.py', state: '移除', before: 'obsolete', after: ''}])
  assert.deepEqual(checkerHistoryChanges(revision, revision), [])
})
