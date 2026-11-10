import assert from 'node:assert/strict'
import test from 'node:test'
import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {VerificationDisclosure} from './verification-disclosure.ts'

test('all-PASS results visibly retain their frozen checker and evidence qualifications', () => {
  const verification = {suite_revision_id: 'round-suite-3', suite_revision_number: 3, status: 'unverified' as const, samples_not_rerun: false, notice: '本轮采用未经验证的检查版本；即使全部为 PASS，也不表示检查版本已通过样本验证。检查版本：第 3 版（round-suite-3）。'}
  const html = renderToStaticMarkup(createElement(VerificationDisclosure, {verification, evidenceUnverified: true}))
  assert.match(html, /role="note"/)
  assert.match(html, /本轮验证状态/)
  assert.match(html, /未经验证的检查版本/)
  assert.match(html, /部分判定的证据未经验证/)
  assert.match(html, /第 3 版（round-suite-3）/)
  assert.match(html, /data-suite-revision="round-suite-3"/)
})

test('report surfaces preserve the accepted platform qualification with the exact version', () => {
  const verification = {suite_revision_id: 'old-suite', suite_revision_number: 2, status: 'verified' as const, samples_not_rerun: true, notice: '受理本轮时官方已更新，尚未重新验证。检查版本：第 2 版（old-suite）。'}
  const html = renderToStaticMarkup(createElement(VerificationDisclosure, {verification}))
  assert.match(html, /官方已更新，尚未重新验证/)
  assert.match(html, /第 2 版（old-suite）/)
  assert.match(html, /bg-warning/)
})
