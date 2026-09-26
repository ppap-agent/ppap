import assert from 'node:assert/strict'
import test from 'node:test'
import {isSafeReturnTo, parseProductRoute, parseReturnTarget, productHref, type Route} from './route.ts'

const round = '00000000-0000-4000-8000-000000000001'
const draft = '00000000-0000-4000-8000-000000000002'
const rule = '00000000-0000-4000-8000-0000000000a5'
const job = '00000000-0000-4000-8000-0000000000b6'

const samples: Route[] = [
  {page: 'audit-new'},
  {page: 'audit-new', draftId: draft},
  {page: 'audit-new', draftId: draft, fromRoundId: round},
  {page: 'audit-round', roundId: round},
  {page: 'audit-round', roundId: round, review: 'next'},
  {page: 'audit-history'},
  {page: 'usage'},
  {page: 'dev'},
  {page: 'rule-library'},
  {page: 'rule-library', returnTo: {page: 'audit-round', roundId: round}},
  {page: 'rule-detail', ruleId: rule, returnTo: {page: 'audit-round', roundId: round}},
  {page: 'rule-detail', ruleId: rule, saved: true},
  {page: 'rule-import', returnTo: {page: 'audit-new', draftId: draft}},
  {page: 'rule-import', predecessorId: rule, documentType: '控制计划', returnTo: {page: 'audit-round', roundId: round}},
  {page: 'rule-import', parseId: job, predecessorId: rule},
]

test('product routes round-trip without string concatenation', () => {
  for (const route of samples) {
    const href = productHref(route)
    const url = new URL(href, 'http://ppap.local')
    assert.deepEqual(parseProductRoute(url.pathname, url.search), route)
  }
})

test('unsafe return targets are dropped', () => {
  assert.equal(isSafeReturnTo('/?round=' + round), true)
  assert.equal(isSafeReturnTo('/?draft=' + draft), true)
  for (const value of ['https://evil.example', '//evil.example', '/\\\\evil', '/rules', '/?round=ok&next=/admin', 'javascript:alert(1)']) {
    assert.equal(parseReturnTarget(value), undefined)
    assert.equal(isSafeReturnTo(value), false)
  }
  const route = parseProductRoute('/rules', '?rule=' + rule + '&return_to=' + encodeURIComponent('https://evil.example/phish'))
  assert.equal(route.page, 'rule-detail')
  if (route.page === 'rule-detail') assert.equal(route.returnTo, undefined)
  assert.equal(productHref(route).includes('evil'), false)
})

test('draft accept, review next, and rules return stay addressable', () => {
  assert.equal(productHref({page: 'audit-round', roundId: round}), '/?round=' + round)
  assert.equal(productHref({page: 'audit-round', roundId: round, review: 'next'}), '/?round=' + round + '&review=next')
  const returned = productHref({
    page: 'rule-detail',
    ruleId: rule,
    returnTo: {page: 'audit-round', roundId: round},
  })
  assert.match(returned, /^\/rules\?/)
  const parsed = parseProductRoute('/rules', returned.slice('/rules'.length))
  assert.deepEqual(parsed, {
    page: 'rule-detail',
    ruleId: rule,
    returnTo: {page: 'audit-round', roundId: round},
  })
})
