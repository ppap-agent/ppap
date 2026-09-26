import assert from 'node:assert/strict'
import test from 'node:test'
import {isFeishuHost} from './hostLogin.ts'

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
