import assert from 'node:assert/strict'
import {existsSync, readFileSync} from 'node:fs'
import {dirname, join} from 'node:path'
import test from 'node:test'
import {fileURLToPath} from 'node:url'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')

test('the product and addon documents boot the React entries', () => {
  const html = readFileSync(join(web, 'index.html'), 'utf8')
  assert.match(html, /id="app"/)
  assert.match(html, /type="module"/)
  assert.match(html, /src="\/src\/main\.tsx"/)
  assert.match(html, /h5-js-sdk/)
  const addon = readFileSync(join(web, 'addon.html'), 'utf8')
  assert.match(addon, /id="addon-root"/)
  assert.match(addon, /src="\/src\/addon\/main\.tsx"/)
  assert.doesNotMatch(addon, /audit-files\.html/)
})

test('docs-addon vendor client is the pinned Blockit export', () => {
  const vendor = readFileSync(join(web, 'block-docs-addon-api.js'), 'utf8')
  assert.match(vendor, /export \{/)
  assert.match(vendor, /BlockitClient/)
  assert.match(vendor, /@lark-opdev\/block-docs-addon-api@0\.0\.11/)
})

test('legacy page files are gone', () => {
  for (const name of ['app.js', 'audit-files.html', 'rules.html', 'usage.html', 'product-api.js']) {
    assert.equal(existsSync(join(web, name)), false)
  }
})
