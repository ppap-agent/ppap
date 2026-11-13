import assert from 'node:assert/strict'
import {readFileSync, readdirSync} from 'node:fs'
import {dirname, join} from 'node:path'
import test from 'node:test'
import {fileURLToPath} from 'node:url'

const src = dirname(fileURLToPath(import.meta.url))

function filesUnder(root: string): string[] {
  return readdirSync(root, {withFileTypes: true}).flatMap(entry => entry.isDirectory() ? filesUnder(join(root, entry.name)) : [join(root, entry.name)])
}

test('the web client reaches data only through the Product API, never the database REST endpoint', () => {
  const sources = filesUnder(src)
    .filter(path => /\.tsx?$/.test(path) && !/\.test\.tsx?$/.test(path))
    .map(path => readFileSync(path, 'utf8'))
    .join('\n')
  assert.equal(sources.includes('/rest/v1'), false)
})
