import assert from 'node:assert/strict'
import {existsSync, readFileSync, readdirSync} from 'node:fs'
import {dirname, join} from 'node:path'
import test from 'node:test'
import {fileURLToPath} from 'node:url'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const repo = join(web, '..', '..')

function filesUnder(root: string): string[] {
  const files: string[] = []
  for (const entry of readdirSync(root, {withFileTypes: true})) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) files.push(...filesUnder(path))
    else files.push(path)
  }
  return files
}

function shippedSources() {
  return filesUnder(join(web, 'src'))
    .filter(path => (path.endsWith('.ts') || path.endsWith('.tsx')) && !path.endsWith('.test.ts') && !path.endsWith('.test.tsx'))
    .map(path => readFileSync(path, 'utf8'))
    .join('\n')
}

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

test('docs-addon client comes from the package manager', () => {
  const pkg = JSON.parse(readFileSync(join(web, 'package.json'), 'utf8'))
  assert.equal(pkg.dependencies['@lark-opdev/block-docs-addon-api'], '0.0.11')
  assert.equal(existsSync(join(web, 'block-docs-addon-api.js')), false)
  const addon = readFileSync(join(web, 'src/addon/main.tsx'), 'utf8')
  assert.match(addon, /@lark-opdev\/block-docs-addon-api/)
  assert.match(addon, /BlockitClient/)
  assert.doesNotMatch(addon, /block-docs-addon-api\.js/)
})

test('legacy page files are gone', () => {
  for (const name of ['app.js', 'audit-files.html', 'rules.html', 'usage.html', 'product-api.js']) {
    assert.equal(existsSync(join(web, name)), false)
  }
})

test('package.json does not declare InsForge SDK or PostgREST clients', () => {
  const pkg = JSON.parse(readFileSync(join(web, 'package.json'), 'utf8'))
  const declared = {...pkg.dependencies, ...pkg.devDependencies}
  for (const name of Object.keys(declared)) {
    assert.equal(name.startsWith('@insforge/'), false, name)
    assert.equal(/postgrest/i.test(name), false, name)
  }
})

test('the web client stays behind the Product API boundary', () => {
  const sources = shippedSources()
  for (const forbidden of [
    '/uac/',
    'transformer/file-audit',
    '/rest/v1',
    'ppap_documents',
    'ppap_jobs',
    'insforge.dev',
    'X-Token',
    '仅海岸线',
    '/v2',
  ]) {
    assert.equal(sources.includes(forbidden), false, `forbidden web dependency: ${forbidden}`)
  }
  assert.equal(filesUnder(join(web, 'src')).some(path => path.endsWith('.vue')), false)
  assert.match(readFileSync(join(repo, 'deploy', 'web', 'Dockerfile'), 'utf8'), /COPY --from=web-build \/app\/dist/)
})

test('Web wire schemas are generated from FastAPI OpenAPI', () => {
  const generated = readFileSync(join(web, 'src', 'generated', 'openapi.ts'), 'utf8')
  const api = readFileSync(join(web, 'src', 'api.ts'), 'utf8')
  const pkg = JSON.parse(readFileSync(join(web, 'package.json'), 'utf8'))

  assert.equal(pkg.devDependencies['openapi-typescript'], '7.13.0')
  assert.match(pkg.scripts['api-types'], /app\.openapi\(\)/)
  assert.match(pkg.scripts['api-types'], /openapi-typescript --alphabetize/)
  assert.match(pkg.scripts['api-types:check'], /--check/)
  assert.equal(existsSync(join(repo, 'scripts', 'generate_web_contract.py')), false)
  assert.match(api, /from ['"]\.\/generated\/openapi['"]/)
  assert.match(api, /type MaterialWire = Schema<'MaterialWire'>/)
  assert.match(api, /export type RuleBody = Schema<'EngineRuleBody'>/)

  for (const legacy of [
    /export interface Rule\b/,
    /export interface Analysis\b/,
    /export interface Finding\b/,
    /export interface Round\b/,
    /export interface Draft\b/,
    /export interface QuotaEstimate\b/,
    /export interface Job\b/,
  ]) {
    assert.doesNotMatch(api, legacy)
  }

  for (const behavior of ['fetch(', 'setTimeout(', 'navigator.', '请求失败', '请先登录']) {
    assert.equal(generated.includes(behavior), false, `generated OpenAPI artifact contains frontend behavior: ${behavior}`)
  }
})

test('usage stays read-only and the client does not own audit pricing', () => {
  const sources = shippedSources()
  assert.match(sources, /\/usage\?/)
  assert.match(sources, /\/integrations\/feishu\/usage\/quota/)
  assert.doesNotMatch(sources, /\/integrations\/feishu\/usage\/report/)
  assert.doesNotMatch(sources, /AUDIT_COST_PER_FILE/)
  assert.doesNotMatch(sources, /\*\s*500/)
  assert.match(sources, /target_count/)
  assert.match(sources, /accept_allowed/)
})
