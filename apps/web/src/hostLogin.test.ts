import assert from 'node:assert/strict'
import test from 'node:test'
import {isFeishuHost, openedInFeishu} from './hostLogin.ts'

test('isFeishuHost recognizes the Feishu H5 SDK and the test stub', () => {
  assert.equal(isFeishuHost({} as Window), false)
  assert.equal(isFeishuHost({h5sdk: {}, tt: {}} as unknown as Window), false)
  assert.equal(isFeishuHost({
    h5sdk: {},
    tt: {requestAccess() {}},
  } as unknown as Window), true)
  assert.equal(isFeishuHost({
    __PPAP_FEISHU_HOST__: {login: async () => 'code'},
  } as unknown as Window), true)
})

test('openedInFeishu knows the Feishu client from its user agent before the SDK loads', () => {
  const browser = (userAgent: string) => ({navigator: {userAgent}}) as unknown as Window
  assert.equal(openedInFeishu(browser('Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/120.0 Safari/537.36')), false)
  assert.equal(openedInFeishu(browser('Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Lark/7.30.5 LarkLocale/zh_CN')), true)
  assert.equal(openedInFeishu(browser('Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Feishu/7.30.0')), true)
  assert.equal(openedInFeishu({navigator: {userAgent: 'Chrome'}, __PPAP_FEISHU_HOST__: {login: async () => 'code'}} as unknown as Window), true)
})
