import assert from 'node:assert/strict'
import test from 'node:test'
import type {RuleBody, RuleDetail} from '../api.ts'
import {basisRuleBody, publishBasis, readRuleVersions, sourceFileLabel} from './product.ts'
import {localBasis, type RequirementBasis} from './requirements-state.ts'

Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true})
const revision = 'b4024d92-2fd6-4995-b746-6d67495bc72d'
const body: RuleBody = {kind: 'engine', type: '控制计划', rule_profile: 'v3', customers: ['测试客户'], models: [], parts: ['P-1'], rules: [{id: '1', category: '基本信息', priority: null, rule_to_check: '必须填写零件号', type_of_rule: 'table'}], appendix: [{id: 'note', content: '已核对的附录'}]}
function candidate(): RequirementBasis {
  const basis = localBasis('控制计划.json')
  return {...basis, source: {...basis.source, title: '客户要求', version: 'v1'}, clauses: [{id: '1', title: '基本信息', text: '必须填写零件号', original: '必须填写零件号', source: '控制计划.json', location: 'rules[0].rule_to_check', applicability: 'apply', reason: '', checked: true, provenance: {id: 'unit-1', location: 'rules[0].rule_to_check', excerpt: '必须填写零件号', fileVersionId: 'source-version'}}], server: {body: structuredClone(body), material: {file_id: 'source-file', file_version_id: 'source-version', original_filename: '控制计划.json', role: 'reference', rule_version_id: null}}}
}
function publication(request: Record<string, unknown>, key?: string): RuleDetail {
  return {id: request.request_id as string, title: request.title as string, body: {...request.body as RuleBody, suite_key: key || `custom:${request.request_id}`, suite_revision_id: revision}, source_file_id: 'source-file', source_file_version_id: request.source_file_version_id as string, predecessor_id: request.predecessor_id as string | null, case_context: request.case_context as RuleDetail['case_context'], customer_scope: 'restricted', created_by: 'author', created_at: '2026-11-06T01:00:00Z'}
}
async function withPublicationApi(run: (state: {posts: Record<string, unknown>[]; reads: string[]; records: Map<string, RuleDetail>}) => Promise<void>, options: {lostReply?: boolean; legacy?: boolean; change?: (rule: RuleDetail) => RuleDetail} = {}) {
  const original = globalThis.fetch, state = {posts: [] as Record<string, unknown>[], reads: [] as string[], records: new Map<string, RuleDetail>()}
  globalThis.fetch = async (input, init) => {
    const path = String(input)
    if (init?.method === 'POST' && path === '/rules') {
      const request = JSON.parse(String(init.body)); state.posts.push(request)
      const previous = state.records.get(request.predecessor_id)
      let rule = publication(request, previous?.body.kind === 'engine' ? previous.body.suite_key : undefined)
      if (options.legacy && rule.body.kind === 'engine') {delete rule.body.suite_key; delete rule.body.suite_revision_id}
      rule = options.change?.(rule) ?? rule
      state.records.set(request.request_id, rule)
      if (options.lostReply) throw new Error('publication reply lost')
      return Response.json(rule, {status: 201})
    }
    state.reads.push(path)
    if (path === '/rules') return Response.json({rules: [...state.records.values()].map(rule => ({id: rule.id, title: rule.title, kind: 'engine'}))})
    if (path.startsWith('/rules/')) return state.records.has(path.slice(7)) ? Response.json(state.records.get(path.slice(7))) : Response.json({detail: 'missing'}, {status: 404})
    if (path === '/files/source-file/versions/source-version') return Response.json({id: 'source-version', file_id: 'source-file', original_filename: '控制计划.json'})
    throw new Error(`Unexpected request ${init?.method} ${path}`)
  }
  try {await run(state)} finally {globalThis.fetch = original}
}

test('publication accepts only server Suite metadata additions and retains them through reuse, readback and editing', async () => {
  await withPublicationApi(async state => {
    const first = await publishBasis(candidate())
    assert.equal(first.server!.body.suite_key, `custom:${first.server!.ruleId}`)
    assert.equal(first.server!.body.suite_revision_id, revision)
    assert.deepEqual(basisRuleBody(first), body)
    assert.equal(await publishBasis(first), first)
    assert.equal(state.posts.length, 1)
    const [loaded] = await readRuleVersions([first.server!.ruleId!], new AbortController().signal)
    assert.equal(loaded.basis.server!.body.suite_revision_id, revision)
    assert.equal(await publishBasis(loaded.basis), loaded.basis)
    assert.equal(state.posts.length, 1, 'unchanged reopened versions must not be published again')
    const edited = {...loaded.basis, source: {...loaded.basis.source, version: 'v2'}, clauses: loaded.basis.clauses.map(clause => ({...clause, text: '必须填写零件号和版本'}))}
    const second = await publishBasis(edited)
    assert.equal(state.posts.length, 2)
    assert.equal(state.posts[1].predecessor_id, first.server!.ruleId)
    assert.equal((state.posts[1].body as RuleBody).suite_revision_id, undefined)
    assert.equal(second.server!.body.suite_key, first.server!.body.suite_key)
    assert.equal(second.server!.body.rules[0].rule_to_check, '必须填写零件号和版本')
  })
})
test('a lost publication response recovers the enriched original receipt without reposting', async () => {
  await withPublicationApi(async state => {
    const saved = await publishBasis(candidate())
    assert.equal(state.posts.length, 1)
    assert.equal(saved.server!.body.suite_revision_id, revision)
    assert.equal(state.reads.filter(path => path === `/rules/${saved.server!.ruleId}`).length, 2)
  }, {lostReply: true})
})
test('catalog reconciliation recognizes enriched matching receipts without another publication', async () => {
  await withPublicationApi(async state => {
    const input = candidate()
    const first = await publishBasis(input), stored = state.records.get(first.server!.ruleId!)!
    state.records.clear()
    state.records.set('historical-publication', {...stored, id: 'historical-publication', body: {...stored.body, suite_key: 'custom:historical-publication'} as RuleBody})
    const recovered = await publishBasis(input)
    assert.equal(recovered.server!.ruleId, 'historical-publication')
    assert.equal(recovered.server!.body.suite_key, 'custom:historical-publication')
    assert.equal(state.posts.length, 1)
  })
})
test('editing a bound version cannot silently lose its known Suite lineage', async () => {
  await withPublicationApi(async state => {
    const first = await publishBasis(candidate())
    const edit = {...first, clauses: first.clauses.map(clause => ({...clause, text: '修订后的要求'}))}
    await assert.rejects(publishBasis(edit), /保存回执的检查版本关联不一致/)
    assert.equal(state.posts.length, 2)
  }, {change: rule => {
    if (rule.predecessor_id && rule.body.kind === 'engine') {delete rule.body.suite_key; delete rule.body.suite_revision_id}
    return rule
  }})
})
test('older immutable receipts without Suite metadata remain readable without weakening content matching', async () => {
  await withPublicationApi(async state => {const saved = await publishBasis(candidate()); assert.equal(saved.server!.body.suite_key, undefined); assert.equal(state.posts.length, 1)}, {legacy: true})
})
test('content, origin, request, registration and Suite lineage conflicts still reject publication and recovery', async t => {
  const changes: Record<string, (rule: RuleDetail) => RuleDetail> = {
    clause: rule => ({...rule, body: {...rule.body, rules: [{...body.rules[0], rule_to_check: '另一个要求'}]}} as RuleDetail),
    profile: rule => ({...rule, body: {...rule.body, rule_profile: 'v2'}} as RuleDetail),
    checkerBinding: rule => ({...rule, body: {...rule.body, checker_binding: {'1': 'unrequested'}}} as RuleDetail),
    appendix: rule => ({...rule, body: {...rule.body, appendix: []}}),
    type: rule => ({...rule, body: {...rule.body, type: '过程流程图'}} as RuleDetail),
    customers: rule => ({...rule, body: {...rule.body, customers: []}} as RuleDetail),
    models: rule => ({...rule, body: {...rule.body, models: ['其他车型']}} as RuleDetail),
    parts: rule => ({...rule, body: {...rule.body, parts: []}} as RuleDetail),
    unknownField: rule => ({...rule, body: {...rule.body, unrequested: true}} as unknown as RuleDetail),
    source: rule => ({...rule, source_file_id: 'another-file'}),
    sourceVersion: rule => ({...rule, source_file_version_id: 'another-version'}),
    predecessor: rule => ({...rule, predecessor_id: 'another-rule'}),
    registration: rule => ({...rule, case_context: {registration: '{}'}}),
    request: rule => ({...rule, id: 'another-request'}),
    title: rule => ({...rule, title: '另一个名称'}),
    suite: rule => ({...rule, body: {...rule.body, suite_key: 'custom:another-rule'}} as RuleDetail),
    revision: rule => ({...rule, body: {...rule.body, suite_revision_id: ''}} as RuleDetail),
    incompleteBinding: rule => {const broken = structuredClone(rule); if (broken.body.kind === 'engine') delete broken.body.suite_key; return broken},
  }
  for (const [name, change] of Object.entries(changes)) await t.test(name, () => withPublicationApi(async state => {await assert.rejects(publishBasis(candidate()), /保存回执/); assert.equal(state.posts.length, 1, 'a mismatched receipt must never trigger a second publication')}, {change}))
})

test('a denied source file stays unreadable on the same rule and customer', async () => {
  await withPublicationApi(async state => {
    const saved = await publishBasis(candidate())
    const id = saved.server!.ruleId!
    const paths: string[] = []
    const savedFetch = globalThis.fetch
    globalThis.fetch = async (input, init) => {
      const path = String(input)
      paths.push(path)
      if (path === '/files/source-file/versions/source-version') return Response.json({error: 'access_denied'}, {status: 403})
      return savedFetch(input, init)
    }
    const [loaded] = await readRuleVersions([id], new AbortController().signal)
    assert.equal(loaded.id, id)
    assert.equal(loaded.scope.customer, '测试客户')
    assert.equal(loaded.basis.source.filename, '控制计划.json')
    assert.equal(loaded.basis.server!.material.original_filename, '控制计划.json')
    assert.equal(loaded.basis.server!.material.status, 'unreadable')
    assert.equal(loaded.basis.clauses[0].source, '控制计划.json')
    assert.equal(sourceFileLabel(loaded.basis.source.filename, loaded.basis.server!.material.status), '控制计划.json · 原件不可读')
    assert.deepEqual(paths.filter(path => path.includes('/files/')), ['/files/source-file/versions/source-version'])
    assert.equal(paths.some(path => path.includes('another')), false)
    state.records.get(id)!.case_context = {}
    const [unnamed] = await readRuleVersions([id], new AbortController().signal)
    assert.equal(unnamed.basis.source.filename, '原件不可读')
    assert.notEqual(unnamed.basis.source.filename, state.records.get(id)!.title)
    assert.equal(unnamed.scope.customer, '测试客户')
    assert.equal(sourceFileLabel(unnamed.basis.source.filename, unnamed.basis.server!.material.status), '原件不可读')
    assert.equal(sourceFileLabel('控制计划.json', undefined), '控制计划.json')
  })
})
