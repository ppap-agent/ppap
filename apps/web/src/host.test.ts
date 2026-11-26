import assert from 'node:assert/strict'
import test from 'node:test'
import {hostCapabilities} from './host.ts'

const opened = (userAgent: string) => ({navigator: {userAgent}} as unknown as Window)

test('only the Feishu phone client loses local files and browser downloads', () => {
  const all = {localFiles: true, browserDownload: true}, phone = {localFiles: false, browserDownload: false}
  assert.deepEqual(hostCapabilities(opened('Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/120.0 Safari/537.36')), all)
  assert.deepEqual(hostCapabilities(opened('Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Lark/7.30.5 LarkLocale/zh_CN')), all)
  assert.deepEqual(hostCapabilities(opened('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1')), all)
  assert.deepEqual(hostCapabilities(opened('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Lark/7.30.0 LarkLocale/zh_CN')), phone)
  assert.deepEqual(hostCapabilities(opened('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36 Feishu/7.30.0')), phone)
})
