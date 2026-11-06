import assert from 'node:assert/strict'
import test from 'node:test'
import {candidateChanges, checkerEditorDirty, editableRules, nativeCheckerEditAdapter} from './checker-edit-contract.ts'
Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
const files = {'rules.json': JSON.stringify({title: '控制计划', rules: [{id: '1', category: '完整性', rule_to_check: '需要签名'}], binding: {value: 'preserved'}}), 'templates.json': '[]', 'checker.py': 'def check(ctx): pass'}
test('rule and template edits preserve frozen identifiers and untouched properties', () => {
  assert.deepEqual(editableRules(files), [{id: '1', text: '需要签名'}])
  const changes = candidateChanges(files, [{id: '1', text: '需要有效签名'}], '[{"kind":"structure","columns":["签名"]}]', {'checker.py': files['checker.py']})
  assert.deepEqual(Object.keys(changes).sort(), ['rules.json', 'templates.json'])
  assert.deepEqual(JSON.parse(changes['rules.json']), {title: '控制计划', rules: [{id: '1', category: '完整性', rule_to_check: '需要有效签名'}], binding: {value: 'preserved'}})
  assert.equal(JSON.parse(files['rules.json']).rules[0].rule_to_check, '需要签名')
})
test('editor refuses identity changes, empty rules, unauthorized templates and no-op saves', () => {
  assert.throws(() => candidateChanges(files, [{id: 'other', text: 'x'}], '[]', {}))
  assert.throws(() => candidateChanges(files, [{id: '1', text: ' '}], '[]', {}))
  assert.throws(() => candidateChanges(files, editableRules(files), '[{"kind":"licensed_blank"}]', {}), /授权/)
  assert.throws(() => candidateChanges(files, editableRules(files), '[]', {}), /尚未修改/)
  assert.throws(() => candidateChanges(files, editableRules(files), '[]', {'secrets.py': 'x'}))
})
test('unknown candidate save is recovered with GET of its original operation, never another POST', async () => {
  const original = globalThis.fetch, calls: string[] = []
  globalThis.fetch = async (url, init) => {calls.push(`${init?.method} ${String(url)}`); if (init?.method === 'POST') throw new Error('reply lost'); return new Response(JSON.stringify({id: 'candidate-a', base_revision_id: 'base/a'}), {headers: {'Content-Type': 'application/json'}})}
  try {
    await assert.rejects(nativeCheckerEditAdapter.save('base/a', {operation_id: 'op-a', files: {'checker.py': 'x'}}))
    await nativeCheckerEditAdapter.read('base/a', 'op-a')
    assert.deepEqual(calls, ['POST /suite-revisions/base%2Fa/candidates', 'GET /suite-revisions/base%2Fa/candidates/op-a'])
  } finally {globalThis.fetch = original}
})

test('unsaved valid and invalid edits block upgrading without blocking the editor save path', () => {
  const rules = editableRules(files), code = {'checker.py': files['checker.py']}
  assert.equal(checkerEditorDirty(files, rules, '[]', code), false)
  assert.equal(checkerEditorDirty(files, [{id: '1', text: ''}], '[]', code), true)
  assert.equal(checkerEditorDirty(files, rules, '{invalid JSON', code), true)
  assert.equal(checkerEditorDirty(files, rules, '[]', {'checker.py': ''}), true)
  assert.equal(checkerEditorDirty(files, rules, '[]', {}), true)
  assert.equal(checkerEditorDirty(files, rules, '[]', code), false)
})
