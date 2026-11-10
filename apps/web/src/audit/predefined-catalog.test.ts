import assert from 'node:assert/strict'
import test from 'node:test'
import {canOpenPredefined, checkerLabel, directoryParts, predefinedTree, readPredefinedPath, readRulesArea, sourceIdentity, type InstalledPredefinedEntry, type PredefinedEntry} from './predefined-catalog.ts'

const entry = (path: string, revision = `rev-${path}`): InstalledPredefinedEntry => ({path, title: `标题 ${path}`, availability: 'installed', suite_key: path.split('/').slice(1).join(':'), revision_id: revision, clause_count: 0, bound_clause_count: 0, evidence_reader_clause_ids: []})

test('real source paths form a version/customer/group tree including uninstalled and historical directories', () => {
  const sources: PredefinedEntry[] = [entry('v6/ACME/Z99'), entry('v5/UAES/U08'), {path: 'v5/UAES/U12', title: '特殊特性', availability: 'unseeded'}, {path: 'v4', title: 'v4', availability: 'historical'}, {path: 'v4/uaes', title: 'uaes', availability: 'historical'}, entry('legacy.csv', 'should-not-open')]
  const {nodes, rejected} = predefinedTree(sources)
  assert.deepEqual(nodes.map(node => node.name), ['v6', 'v5', 'v4'])
  assert.deepEqual(nodes[1]?.children[0]?.children.map(node => [node.path, node.entry?.availability]), [['v5/UAES/U08', 'installed'], ['v5/UAES/U12', 'unseeded']])
  assert.equal(nodes[2]?.children[0]?.path, 'v4/uaes')
  assert.deepEqual(rejected.map(row => row.path), ['legacy.csv'])
  assert.equal(canOpenPredefined(sources[0]!), true)
  assert.equal(canOpenPredefined(sources[2]!), false)
  assert.equal(canOpenPredefined({...sources[2]!, revision_id: 'forged'}), false)
  assert.equal(canOpenPredefined({...entry('v5/UAES/U08'), availability: 'unavailable'}), false)
  assert.equal(canOpenPredefined({...entry('v5/UAES/U08'), suite_key: 'OTHER:U08'}), false)
  assert.equal(canOpenPredefined({...entry('v5/UAES/U08'), revision_id: ''}), false)
})

test('duplicate and escaping path identities are rejected instead of choosing the first source', () => {
  const {nodes, rejected} = predefinedTree([entry('v5/UAES/U08'), entry('v5/UAES/U08', 'other'), entry('v5/../U08'), entry('v5/UAES:BAD/U08')])
  assert.deepEqual(nodes, [])
  assert.equal(rejected.length, 4)
  assert.deepEqual(directoryParts('v4/uaes'), ['v4', 'uaes'])
})

test('friendly labels retain the original title and do not supply navigation identity', () => {
  const cp = {...entry('v5/UAES/U08'), title: '控制计划（CP）审核规则'}
  assert.equal(checkerLabel(cp), 'U08 · 控制计划')
  assert.equal(checkerLabel({...cp, title: '潜在失效模式（PFMEA）审核规则'}), 'U08 · PFMEA')
  assert.equal(cp.title, '控制计划（CP）审核规则')
})

test('rule templates are the default; old checker addresses land on automatic review or the program directory', () => {
  const area = (address: string) => readRulesArea(new URL(`https://ppap.test/requirements${address}`))
  assert.equal(readPredefinedPath(new URL('https://ppap.test/requirements?checkerPath=v5%2FUAES%2FU08')), 'v5/UAES/U08')
  assert.equal(area('?checkerPath=v5%2FUAES%2FU08'), 'predefined')
  assert.equal(area('?rulesArea=predefined'), 'predefined')
  assert.equal(area('?checkerSuite=UAES%3AU08'), 'automation')
  assert.equal(area('?checkerSuite='), 'automation')
  assert.equal(area('?rulesArea=enterprise'), 'automation')
  assert.equal(area('?rulesArea=automation'), 'automation')
  for (const address of ['', '?rulesArea=requirements', '?requirement=customer-rule', '?template=system%3AUAES', '?systemRule=r1', '?rulesArea=requirements&checkerSuite=x']) assert.equal(area(address), 'templates')
  assert.equal(readPredefinedPath(new URL('https://ppap.test/requirements?checkerSuite=UAES%3AU08')), '')
})

test('the official source revision stays distinct from the adopted enterprise revision', () => {
  const source = entry('v5/UAES/U08', 'official-v5')
  // People read which version is in use; source paths and revision ids stay out of the sentence.
  assert.match(sourceIdentity(source, 'tenant-current'), /另一个版本/)
  assert.doesNotMatch(sourceIdentity(source, 'tenant-current'), /official-v5|tenant-current|v5\/UAES/)
  assert.match(sourceIdentity(source, null), /尚未采用/)
  assert.match(sourceIdentity(source, 'official-v5'), /正在使用这个官方版本/)
})
