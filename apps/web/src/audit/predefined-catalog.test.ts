import assert from 'node:assert/strict'
import test from 'node:test'
import {canOpenPredefined, groupPredefined, readPredefinedPath, sourceIdentity, type PredefinedEntry} from './predefined-catalog.ts'

const entry = (path: string, revision = `rev-${path}`): PredefinedEntry => ({path, title: `标题 ${path}`, suite_key: path.split('/').slice(1).join(':'), revision_id: revision, clause_count: 0, bound_clause_count: 0, evidence_reader_clause_ids: []})

test('directory paths group version then customer then suite without a fixed registry', () => {
  const {groups, rejected} = groupPredefined([
    entry('v6/ACME/Z99'),
    entry('v5/UAES/U08'),
    entry('v5/示例客户/Z99'),
    entry('v6/ACME/Z01'),
    entry('legacy.csv', 'should-not-open'),
    {...entry('v5/UAES/U12'), revision_id: ''},
  ])
  assert.deepEqual(rejected.map(row => row.path), ['legacy.csv', 'v5/UAES/U12'])
  assert.deepEqual(groups.map(group => group.version), ['v6', 'v5'])
  assert.deepEqual(groups[0]?.customers.map(row => [row.customer, row.entries.map(item => item.path)]), [['ACME', ['v6/ACME/Z99', 'v6/ACME/Z01']]])
  assert.deepEqual(groups[1]?.customers.map(row => row.customer), ['UAES', '示例客户'])
  assert.equal(canOpenPredefined(entry('v6/示例客户/Z99')), true)
  assert.equal(canOpenPredefined(entry('not/a/v-path')), false)
  assert.equal(canOpenPredefined({...entry('v5/UAES/U08'), revision_id: ''}), false)
})

test('checkerPath keeps the source path and the official revision stays distinct from the adopted revision', () => {
  assert.equal(readPredefinedPath(new URL('https://ppap.test/requirements?checkerSuite=UAES%3AU08&checkerPath=v5%2FUAES%2FU08')), 'v5/UAES/U08')
  assert.equal(readPredefinedPath(new URL('https://ppap.test/requirements?checkerSuite=UAES%3AU08')), '')
  const source = entry('v5/UAES/U08', 'official-v5')
  assert.match(sourceIdentity(source, 'tenant-current'), /official-v5/)
  assert.match(sourceIdentity(source, 'tenant-current'), /tenant-current/)
  assert.match(sourceIdentity(source, 'tenant-current'), /不同/)
  assert.match(sourceIdentity(source, null), /尚未采用/)
  assert.doesNotMatch(sourceIdentity(source, null), /tenant-current/)
  assert.match(sourceIdentity(source, 'official-v5'), /相同/)
})
