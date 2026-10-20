#!/usr/bin/env node
import {spawnSync} from 'node:child_process'
import {dirname, join} from 'node:path'
import {fileURLToPath} from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const sha = 'verify-build'
const failures = []

function fail(message) {
  failures.push(message)
}

function nativeJson(command, args, env) {
  const result = spawnSync(command, args, {
    cwd: root,
    env,
    encoding: 'utf8',
    maxBuffer: 10 * 1024 * 1024,
  })
  if (result.status !== 0) {
    fail(command + ' ' + args.join(' ') + ' failed:\n' + String(result.stderr || result.stdout || result.error || 'unknown error'))
    return null
  }
  try {
    return JSON.parse(result.stdout)
  } catch (error) {
    fail(command + ' ' + args.join(' ') + ' did not return JSON: ' + error.message)
    return null
  }
}

function composeJson(file, envFile, overrides) {
  const args = ['compose', '--progress', 'quiet', '-f', file]
  if (envFile) args.push('--env-file', envFile)
  args.push('config', '--format', 'json')
  return nativeJson('docker', args, {...process.env, ...overrides})
}

function targetTag(bake, target) {
  const tags = bake && bake.target && bake.target[target] && bake.target[target].tags
  if (!Array.isArray(tags) || tags.length !== 1) {
    fail('bake target ' + target + ' must expose exactly one canonical tag')
    return ''
  }
  return tags[0]
}

function verifyCompose(name, config, expected) {
  if (!config) return
  const services = config.services || {}
  for (const [service, image] of Object.entries(expected)) {
    const rendered = services[service] || {}
    if (rendered.build) fail(name + ' ' + service + ' must consume an image, not define build')
    if (rendered.image !== image) {
      fail(name + ' ' + service + ' image: expected ' + image + ', got ' + String(rendered.image || '<missing>'))
    }
  }
}

const selected = {PPAP_BUILD_SHA: sha}
const bake = nativeJson('docker', ['buildx', 'bake', '--print'], {...process.env, ...selected})
const expected = bake
  ? {
      api: targetTag(bake, 'api'),
      worker: targetTag(bake, 'worker'),
      web: targetTag(bake, 'web'),
    }
  : {}

if (bake) {
  for (const [target, image] of Object.entries(expected)) {
    const definition = (bake.target && bake.target[target]) || {}
    if (!definition.context) fail('bake target ' + target + ' has no context')
    if (!definition.dockerfile) fail('bake target ' + target + ' has no dockerfile')
    if (!image.endsWith(':' + sha)) fail('bake target ' + target + ' tag is not keyed by PPAP_BUILD_SHA: ' + image)
  }
}

const release = nativeJson('docker', ['buildx', 'bake', 'release', '--print'], {
  ...process.env, ...selected, PPAP_IMAGE_PREFIX: 'ghcr.io/ppap-agent/ppap/',
})
if (release) {
  const targets = release.target || {}
  for (const target of ['api', 'worker', 'web', 'deep']) {
    if (targetTag(release, target) !== `ghcr.io/ppap-agent/ppap/${target}:${sha}`) fail(`release ${target} must use the fixed source SHA`)
    const labels = targets[target]?.labels || {}
    if (labels['org.opencontainers.image.revision'] !== sha || labels['org.opencontainers.image.source'] !== 'https://github.com/ppap-agent/ppap') fail(`release ${target} must identify its source`)
  }
  if (targets.deep?.contexts?.['ppap-worker'] !== 'target:worker') fail('Deep must consume the same Bake Worker build')
}

verifyCompose(
  'isolated-dev',
  composeJson('deploy/isolated-dev/compose.yaml', null, selected),
  expected,
)
verifyCompose(
  'production',
  composeJson('deploy/compose.yaml', 'deploy/compose.env.example', selected),
  expected,
)

const remoteEnv = {
  ...selected,
  PPAP_REMOTE_PG_DATABASE: 'ppap',
  PPAP_REMOTE_PG_API_USER: 'ppap_api',
  PPAP_REMOTE_PG_API_PASSWORD: 'secret',
  PPAP_REMOTE_TENANT_ALLOWLIST: '',
  PPAP_REMOTE_DEEP_CUSTOMER_ENABLED: 'false',
  PPAP_REMOTE_DEEP_EXECUTOR_REVISION: 'verify-deep',
  PPAP_REMOTE_S3_REGION: 'us-east-1',
  PPAP_REMOTE_S3_BUCKET: 'ppap-objects',
  PPAP_REMOTE_S3_ACCESS_KEY: 'access',
  PPAP_REMOTE_S3_SECRET_KEY: 'secret',
  PPAP_REMOTE_ONLYOFFICE_SECRET: 'secret',
}
verifyCompose(
  'remote-dev',
  composeJson('deploy/remote-dev/compose.yaml', null, remoteEnv),
  {api: expected.api, web: expected.web},
)

if (failures.length) {
  for (const message of failures) console.error(message)
  process.exit(1)
}
console.log('Canonical container build graph: ok')
