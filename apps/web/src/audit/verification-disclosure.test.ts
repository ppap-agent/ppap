import assert from 'node:assert/strict'
import test from 'node:test'
import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {VerificationDisclosure} from './verification-disclosure.ts'

type Props = Parameters<typeof VerificationDisclosure>[0]
const render = (props: Props) => renderToStaticMarkup(createElement(VerificationDisclosure, props))
const base = {suite_revision_id: 'suite-3', suite_revision_number: 3, notice: '冻结说明'}

test('an unverified suite and unverified evidence produce a warning summary even when every result is PASS', () => {
  const html = render({verification: {...base, status: 'unverified', samples_not_rerun: false}, evidenceUnverified: true})
  assert.match(html, /检查版本第 3 版未经样本验证 · 部分证据未验证，结论请对照原件核对/)
  assert.match(html, /bg-warning/)
  assert.match(html, /data-suite-revision="suite-3"/)
})

test('an official update after acceptance stays a warning; a verified suite reads as verified', () => {
  const stale = render({verification: {...base, status: 'verified', samples_not_rerun: true}})
  assert.match(stale, /官方已更新，尚未重新验证，结论请对照原件核对/)
  assert.doesNotMatch(stale, /已通过样本验证/)
  assert.match(stale, /bg-warning/)
  const verified = render({verification: {...base, status: 'verified', samples_not_rerun: false}})
  assert.match(verified, /检查版本第 3 版已通过样本验证/)
  assert.doesNotMatch(verified, /结论请对照原件核对/)
  assert.match(verified, /bg-success/)
  assert.equal(render({verification: null}), '')
})
