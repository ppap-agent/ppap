#!/usr/bin/env node
import {readFileSync} from 'node:fs'
import {dirname, join} from 'node:path'
import {fileURLToPath} from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..', '..')
const insforge = here
const siloImage =
  'docker.io/pgsty/silo:RELEASE.2026-09-16T00-00-00Z@sha256:635197cb9f36d01bee221d34d1c7d7960f6a95c48b0b6c01d99cd13bdae51a46'
const failures = []

function fail(message) {
  failures.push(message)
}

function read(path) {
  return readFileSync(path, 'utf8')
}

function assertEqual(actual, expected, message) {
  if (actual !== expected) fail(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
}

function assert(condition, message) {
  if (!condition) fail(message)
}

function composeServices(compose) {
  const services = {}
  let current = ''
  let inServices = false
  for (const line of compose.split('\n')) {
    if (line.startsWith('services:')) {
      inServices = true
      continue
    }
    if (!inServices) continue
    if (line.startsWith('  ') && !line.startsWith('    ') && line.trim().endsWith(':')) {
      current = line.trim().replace(/:$/, '')
      services[current] = []
      continue
    }
    if (current) services[current].push(line)
  }
  return Object.fromEntries(Object.entries(services).map(([name, body]) => [name, body.join('\n')]))
}

function parseDotenv(path) {
  const values = {}
  for (const line of read(path).split('\n')) {
    const raw = line.trim()
    if (!raw || raw.startsWith('#') || !raw.includes('=')) continue
    const index = raw.indexOf('=')
    values[raw.slice(0, index)] = raw.slice(index + 1)
  }
  return values
}

function serviceEnvironment(body) {
  const env = {}
  let inEnv = false
  for (const line of body.split('\n')) {
    const stripped = line.trim()
    if (stripped === 'environment:') {
      inEnv = true
      continue
    }
    if (!inEnv) continue
    if (line.startsWith('    ') && !line.startsWith('      ') && stripped.endsWith(':')) break
    if (!stripped.includes(':')) continue
    const index = stripped.indexOf(':')
    env[stripped.slice(0, index)] = stripped.slice(index + 1).trim().replace(/^["']|["']$/g, '')
  }
  return env
}

function interpolate(value, values) {
  return value.replace(/\$\{([A-Z][A-Z0-9_]*)(:-([^}]*))?\}/g, (_, name, hasDefault, fallback) => {
    const held = values[name]
    if (held) return held
    if (hasDefault !== undefined) return fallback || ''
    return held || ''
  })
}

function interpolatedEnvironment(body, values) {
  return Object.fromEntries(Object.entries(serviceEnvironment(body)).map(([key, raw]) => [key, interpolate(raw, values)]))
}

const version = read(join(insforge, 'VERSION')).trim()
assertEqual(version, 'v2.3.2', 'VERSION')
const readme = read(join(insforge, 'README.md'))
for (const marker of ['v2.3.2', 'docker.sock', 'host-root-equivalent', 'Custom Compute', 'Edge Function', 'Do not import legacy ledgers', 'compose.yaml down', 'without `-v`']) {
  assert(readme.includes(marker), `README missing ${marker}`)
}
assert(read(join(root, 'deploy', 'README.md')).includes('down -v'), 'deploy README must mention down -v')

const compose = read(join(insforge, 'compose.yaml'))
assert(compose.includes('insforge-network'), 'compose joins insforge-network')
assert(compose.includes('external: true'), 'compose uses external network')
assert(!compose.includes('\n  postgres:\n'), 'compose must not bundle postgres')
assert(!compose.includes('ghcr.io/insforge/postgres'), 'compose must not pull InsForge postgres')
assert(!compose.includes('denoland/deno'), 'compose must not use Deno')
assert(!compose.includes('postgrest/postgrest'), 'compose must not use PostgREST')
assert(!compose.toLowerCase().includes('edge function'), 'compose must not mention edge function')
assert(compose.includes('bootstrap.sql'), 'compose runs bootstrap.sql')
assert(compose.includes('apply.sql'), 'compose runs apply.sql')
assert(!compose.includes('python tools/'), 'compose must not call python tools')
assert(compose.includes(siloImage), 'compose pins the Silo image digest')
assert(compose.includes('command: ["server", "/data", "--console-address", ":9001"]'), 'Silo command')
assert(compose.includes('MINIO_ROOT_USER'), 'Silo uses MINIO_ROOT_USER')
assert(!compose.includes('minio/minio:'), 'compose must not use minio/minio')
assert(!compose.includes('ppap-minio:'), 'compose must not use ppap-minio')
assert(!compose.includes('distroless'), 'compose must not use distroless Silo')
assert(compose.includes('name: ppap-silo-data'), 'named object volume')

const services = composeServices(compose)
assertEqual(JSON.stringify(Object.keys(services).sort()), JSON.stringify(['api', 'init-bucket', 'init-db', 'silo', 'web', 'worker'].sort()), 'compose services')
assert(!services['init-db'].includes('depends_on:'), 'init-db has no depends_on')
assert(!services.api.includes('env_file:'), 'api has no env_file')
assert(!services.worker.includes('env_file:'), 'worker has no env_file')
assert(services.api.includes('PPAP_PG_HOST: ${PPAP_PG_HOST}'), 'api interpolates PPAP_PG_HOST')
assert(services.worker.includes('PPAP_PG_HOST: ${PPAP_PG_HOST}'), 'worker interpolates PPAP_PG_HOST')
assert(!services.api.includes('PPAP_PG_HOST: postgres'), 'api must not hardcode postgres host')
assert(services.api.includes('FEISHU_APP_ID'), 'api has Feishu app id')
assert(!services.worker.includes('FEISHU_APP_ID'), 'worker must not have Feishu app id')
assert(services.api.includes('PPAP_PG_API_PASSWORD'), 'api has API db password')
assert(!services.api.includes('PPAP_PG_BOOTSTRAP_PASSWORD'), 'api must not have bootstrap password')
assert(!services.api.includes('PPAP_PG_MIGRATOR_PASSWORD'), 'api must not have migrator password')
assert(!services.api.includes('PPAP_PG_WORKER_PASSWORD'), 'api must not have worker password')
assert(services.worker.includes('PPAP_PG_WORKER_PASSWORD'), 'worker has worker password')
assert(!services.worker.includes('PPAP_PG_API_PASSWORD'), 'worker must not have API password')
assert(!services.api.includes('PADDLE_TOKEN'), 'api must not have PADDLE_TOKEN')
assert(services['init-db'].includes('PPAP_PG_BOOTSTRAP_PASSWORD'), 'init-db has bootstrap password')
assert(services['init-db'].includes('PGCONNECT_TIMEOUT'), 'init-db times out')
assert(services['init-db'].includes('silo-data:/data'), 'init-db can archive silo-data')
assert(services['init-db'].includes('image: postgres:16.10-bookworm'), 'init-db uses postgres client image')
assert(services.silo.includes('minio/health/live'), 'silo healthcheck')
assert(services['init-bucket'].includes('condition: service_healthy'), 'init-bucket waits for healthy silo')
assert(services.silo.includes('- silo-data:/data'), 'silo mounts named volume')
assert(!services.silo.includes('bind:'), 'silo must not bind-mount host paths')

const selected = {
  PPAP_PG_HOST: 'postgres',
  PPAP_PG_PORT: '5432',
  PPAP_PG_DATABASE: 'ppap',
  PPAP_PG_API_USER: 'ppap_api',
  PPAP_PG_API_PASSWORD: 'api-secret',
  PPAP_PG_WORKER_USER: 'ppap_worker',
  PPAP_PG_WORKER_PASSWORD: 'worker-secret',
  PPAP_S3_ENDPOINT: 'http://silo:9000',
  PPAP_S3_REGION: 'us-east-1',
  PPAP_S3_BUCKET: 'ppap-objects',
  PPAP_S3_ACCESS_KEY: 'objects',
  PPAP_S3_SECRET_KEY: 'objects-secret',
  PPAP_WEB_URL: 'https://app.example',
  PPAP_PUBLIC_API_URL: 'https://api.example',
  LLM_BASE_URL: 'http://insforge:7130/v1',
  LLM_MODEL: 'gateway-model',
  FEISHU_APP_ID: 'cli_prod',
  FEISHU_APP_SECRET: 'feishu-secret',
}
const apiEnv = interpolatedEnvironment(services.api, selected)
const workerEnv = interpolatedEnvironment(services.worker, selected)
const initDbEnv = interpolatedEnvironment(services['init-db'], selected)
assertEqual(apiEnv.PPAP_PG_HOST, 'postgres', 'api PPAP_PG_HOST')
assertEqual(workerEnv.PPAP_PG_HOST, 'postgres', 'worker PPAP_PG_HOST')
assertEqual(apiEnv.PPAP_PG_API_PASSWORD, 'api-secret', 'api password interpolation')
assertEqual(workerEnv.PPAP_PG_WORKER_PASSWORD, 'worker-secret', 'worker password interpolation')
assertEqual(apiEnv.FEISHU_APP_ID, 'cli_prod', 'api Feishu interpolation')
assertEqual(initDbEnv.PGHOST, 'postgres', 'init-db PGHOST')
assert(!('PPAP_PG_BOOTSTRAP_PASSWORD' in apiEnv), 'api env has no bootstrap password')
assert(!('PPAP_PG_MIGRATOR_PASSWORD' in apiEnv), 'api env has no migrator password')
assert(!('PPAP_PG_WORKER_PASSWORD' in apiEnv), 'api env has no worker password')
assert(!('PPAP_PG_API_PASSWORD' in workerEnv), 'worker env has no API password')

const catalog = parseDotenv(join(insforge, 'env.example'))
assertEqual(catalog.PPAP_PG_HOST, 'postgres', 'env.example PPAP_PG_HOST')
assertEqual(catalog.PPAP_PG_BOOTSTRAP_USER, 'postgres', 'env.example bootstrap user')
assertEqual(catalog.PPAP_PG_DATABASE, 'ppap', 'env.example database')
assertEqual(catalog.PPAP_S3_ENDPOINT, 'http://silo:9000', 'env.example S3 endpoint')
assert(!catalog.PPAP_S3_ENDPOINT.includes('isolated-dev'), 'env.example must not use isolated-dev S3')

const api = JSON.parse(read(join(insforge, 'compute', 'api.json')))
const worker = JSON.parse(read(join(insforge, 'compute', 'worker.json')))
assertEqual(api.kind, 'custom-compute', 'api compute kind')
assertEqual(worker.kind, 'custom-compute', 'worker compute kind')
assertEqual(api.ingress, 'port', 'api ingress')
assertEqual(worker.ingress, 'none', 'worker ingress')
assertEqual(api.port, 8080, 'api port')
assert(!('port' in worker), 'worker has no public port')
assert(String(api.image).includes('ppap-api:'), 'api image')
assert(String(worker.image).includes('ppap-worker:'), 'worker image')
assertEqual(api.environment.PPAP_SERVICE, 'api', 'api service name')
assertEqual(worker.environment.PPAP_SERVICE, 'worker', 'worker service name')
assert(!('PPAP_PG_BOOTSTRAP_PASSWORD' in api.environment), 'api spec has no bootstrap password')
assert(!('PPAP_PG_MIGRATOR_PASSWORD' in api.environment), 'api spec has no migrator password')
assert(!('PPAP_PG_WORKER_PASSWORD' in api.environment), 'api spec has no worker password')
assert('FEISHU_APP_SECRET' in api.environment, 'api spec has Feishu secret')
assert(!('FEISHU_APP_SECRET' in worker.environment), 'worker spec has no Feishu secret')
assert(!('PPAP_PG_API_PASSWORD' in worker.environment), 'worker spec has no API password')
assert('PADDLE_TOKEN' in worker.environment, 'worker spec has PADDLE_TOKEN')
assert(!('PADDLE_TOKEN' in api.environment), 'api spec has no PADDLE_TOKEN')
for (const spec of [api, worker]) {
  const blob = JSON.stringify(spec)
  assert(!blob.includes('edge-function'), `${spec.name} is not an edge function`)
  assert(!blob.toLowerCase().includes('deno'), `${spec.name} is not Deno`)
}

if (failures.length) {
  for (const message of failures) console.error(message)
  process.exit(1)
}
console.log('InsForge config contract: ok')
