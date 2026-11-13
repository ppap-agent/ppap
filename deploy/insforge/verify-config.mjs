#!/usr/bin/env node
import {spawnSync} from 'node:child_process'
import {readFileSync} from 'node:fs'
import {dirname, join} from 'node:path'
import {fileURLToPath} from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..', '..')
const insforge = here
const composeFile = join(insforge, 'compose.yaml')
const envFile = join(insforge, 'env.example')
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

function envOf(service) {
  return service?.environment && typeof service.environment === 'object' && !Array.isArray(service.environment)
    ? service.environment
    : {}
}

function commandText(service) {
  const command = service?.command
  if (Array.isArray(command)) return command.join('\n')
  return String(command || '')
}

function composeEnv(overrides) {
  const env = {...process.env, ...overrides}
  for (const key of Object.keys(env)) {
    if (
      key.startsWith('PPAP_') ||
      key.startsWith('FEISHU_') ||
      key.startsWith('LLM_') ||
      key.startsWith('OCR_') ||
      key.startsWith('PADDLE_')
    ) {
      delete env[key]
    }
  }
  return {...env, ...overrides}
}

function spawnCompose(args, env = process.env) {
  return spawnSync('docker', ['compose', ...args], {
    cwd: root,
    encoding: 'utf8',
    env,
    maxBuffer: 10 * 1024 * 1024,
  })
}

function missingComposeCli(result) {
  if (result.error?.code === 'ENOENT') {
    return 'Docker CLI not found. Static InsForge config needs the Docker Compose CLI (`docker compose config`); it does not start containers and does not need a Docker daemon or Buildx.'
  }
  const detail = String(result.stderr || result.stdout || result.error || '').trim()
  if (
    result.status !== 0 &&
    /compose/i.test(detail) &&
    /not a docker command|unknown command|plugin/i.test(detail)
  ) {
    return `Docker Compose CLI is required for static InsForge config (Compose CLI only; no daemon or Buildx):\n${detail}`
  }
  return null
}

function requireComposeCli() {
  const result = spawnCompose(['version'])
  const missing = missingComposeCli(result)
  if (missing) {
    fail(missing)
    return false
  }
  if (result.status !== 0) {
    fail(
      `Docker Compose CLI is required for static InsForge config (Compose CLI only; no daemon or Buildx):\n${result.stderr || result.stdout || result.error || 'docker compose version failed'}`,
    )
    return false
  }
  return true
}

function nativeComposeConfig(overrides) {
  const result = spawnCompose(
    ['--progress', 'quiet', '-f', composeFile, '--env-file', envFile, 'config', '--format', 'json'],
    composeEnv(overrides),
  )
  const missing = missingComposeCli(result)
  if (missing) {
    fail(missing)
    return null
  }
  if (result.status !== 0) {
    fail(`docker compose config failed:\n${result.stderr || result.stdout || result.error || 'unknown error'}`)
    return null
  }
  try {
    return JSON.parse(result.stdout)
  } catch (error) {
    fail(`docker compose config --format json was not JSON: ${error.message}`)
    return null
  }
}

const version = read(join(insforge, 'VERSION')).trim()
// Compose does not own the InsForge overlay release pin.
assertEqual(version, 'v2.3.2', 'VERSION')

const selected = {
  PPAP_BUILD_SHA: 'verify-config',
  PPAP_PG_HOST: 'selected-pg',
  PPAP_PG_PORT: '6543',
  PPAP_PG_API_PASSWORD: 'api-secret',
  PPAP_PG_WORKER_PASSWORD: 'worker-secret',
  PPAP_PG_BOOTSTRAP_PASSWORD: 'boot-secret',
  PPAP_PG_MIGRATOR_PASSWORD: 'mig-secret',
  PPAP_S3_ACCESS_KEY: 'objects',
  PPAP_S3_SECRET_KEY: 'objects-secret',
  PPAP_WEB_URL: 'https://app.example',
  PPAP_PUBLIC_API_URL: 'https://api.example',
  PPAP_MODEL_TEXT_BASE_URL: 'http://insforge:7130/v1',
  PPAP_MODEL_TEXT_MODEL: 'gateway-model',
  FEISHU_APP_ID: 'cli_prod',
  FEISHU_APP_SECRET: 'feishu-secret',
}

const config = requireComposeCli() ? nativeComposeConfig(selected) : null
if (config) {
  const services = config.services || {}
  assertEqual(
    JSON.stringify(Object.keys(services).sort()),
    JSON.stringify(['api', 'init-bucket', 'init-db', 'silo', 'web', 'worker'].sort()),
    'compose services',
  )
  assert(!('postgres' in services), 'compose must not bundle postgres')

  const network = config.networks?.default || {}
  assertEqual(network.name, 'insforge-network', 'compose joins insforge-network')
  assertEqual(network.external, true, 'compose uses external network')
  assertEqual(config.volumes?.['silo-data']?.name, 'ppap-silo-data', 'named object volume')

  const images = Object.values(services).map(service => String(service.image || ''))
  assert(
    images.every(image => !image.includes('ghcr.io/insforge/postgres')),
    'compose must not pull InsForge postgres',
  )
  assert(
    images.every(image => !image.includes('denoland/deno')),
    'compose must not use Deno',
  )
  assert(
    images.every(image => !image.includes('postgrest/postgrest')),
    'compose must not use PostgREST',
  )
  assert(
    images.every(image => !image.includes('minio/minio:')),
    'compose must not use minio/minio',
  )
  assert(
    images.every(image => !image.includes('ppap-minio:')),
    'compose must not use ppap-minio',
  )
  assert(
    images.every(image => !image.toLowerCase().includes('distroless')),
    'compose must not use distroless Silo',
  )

  const api = services.api || {}
  const worker = services.worker || {}
  const web = services.web || {}
  const initDb = services['init-db'] || {}
  const initBucket = services['init-bucket'] || {}
  const silo = services.silo || {}
  assertEqual(api.image, 'ppap-api:verify-config', 'api canonical image')
  assertEqual(worker.image, 'ppap-worker:verify-config', 'worker canonical image')
  assertEqual(web.image, 'ppap-web:verify-config', 'web canonical image')
  assert(!api.build, 'api compose does not define a build graph')
  assert(!worker.build, 'worker compose does not define a build graph')
  assert(!web.build, 'web compose does not define a build graph')

  const apiEnv = envOf(api)
  const workerEnv = envOf(worker)
  const initDbEnv = envOf(initDb)
  const initDbCommand = commandText(initDb)

  assert(!api.env_file, 'api has no env_file')
  assert(!worker.env_file, 'worker has no env_file')
  assert(!initDb.depends_on, 'init-db has no depends_on')
  assert(initDbCommand.includes('bootstrap.sql'), 'compose runs bootstrap.sql')
  assert(initDbCommand.includes('sh /migrations/apply.sh'), 'compose runs canonical migration runner')
  assert(!initDbCommand.includes('PGUSER='), 'compose delegates migrator user mapping to apply.sh')
  assert(!initDbCommand.includes('PGPASSWORD='), 'compose delegates migrator password mapping to apply.sh')
  assert(!initDbCommand.includes('apply.sql'), 'compose must not use an ordering mirror')
  assert(!initDbCommand.includes('python tools/'), 'compose must not call python tools')
  assertEqual(initDb.image, 'postgres:16.10-bookworm', 'init-db uses postgres client image')
  assertEqual(initDbEnv.PGCONNECT_TIMEOUT, '5', 'init-db times out')
  assert(
    (initDb.volumes || []).some(volume => volume.source === 'silo-data' && volume.target === '/data'),
    'init-db can archive silo-data',
  )

  assertEqual(silo.image, siloImage, 'compose pins the Silo image digest')
  assertEqual(JSON.stringify(silo.command), JSON.stringify(['server', '/data', '--console-address', ':9001']), 'Silo command')
  assert('MINIO_ROOT_USER' in envOf(silo), 'Silo uses MINIO_ROOT_USER')
  assert(
    JSON.stringify(silo.healthcheck?.test || []).includes('minio/health/live'),
    'silo healthcheck',
  )
  assertEqual(
    initBucket.depends_on?.silo?.condition,
    'service_healthy',
    'init-bucket waits for healthy silo',
  )
  assert(
    (silo.volumes || []).some(volume => volume.type === 'volume' && volume.source === 'silo-data' && volume.target === '/data'),
    'silo mounts named volume',
  )
  assert(
    !(silo.volumes || []).some(volume => volume.type === 'bind'),
    'silo must not bind-mount host paths',
  )

  assertEqual(apiEnv.PPAP_PG_HOST, 'selected-pg', 'api PPAP_PG_HOST')
  assertEqual(workerEnv.PPAP_PG_HOST, 'selected-pg', 'worker PPAP_PG_HOST')
  assertEqual(apiEnv.PPAP_PG_PORT, '6543', 'api PPAP_PG_PORT')
  assertEqual(apiEnv.PPAP_PG_API_PASSWORD, 'api-secret', 'api password interpolation')
  assertEqual(workerEnv.PPAP_PG_WORKER_PASSWORD, 'worker-secret', 'worker password interpolation')
  assertEqual(apiEnv.FEISHU_APP_ID, 'cli_prod', 'api Feishu interpolation')
  assertEqual(initDbEnv.PGHOST, 'selected-pg', 'init-db PGHOST')
  assertEqual(initDbEnv.PGPASSWORD, 'boot-secret', 'init-db bootstrap password')
  assertEqual(apiEnv.PPAP_S3_ENDPOINT, 'http://silo:9000', 'api S3 endpoint')
  assert('FEISHU_APP_ID' in apiEnv, 'api has Feishu app id')
  assert(!('FEISHU_APP_ID' in workerEnv), 'worker must not have Feishu app id')
  assert('PPAP_PG_API_PASSWORD' in apiEnv, 'api has API db password')
  assert('PGPASSWORD' in initDbEnv, 'init-db has bootstrap password')
  assert(!('PPAP_PG_BOOTSTRAP_PASSWORD' in apiEnv), 'api must not have bootstrap password')
  assert(!('PPAP_PG_MIGRATOR_PASSWORD' in apiEnv), 'api must not have migrator password')
  assert(!('PPAP_PG_WORKER_PASSWORD' in apiEnv), 'api must not have worker password')
  assert('PPAP_PG_WORKER_PASSWORD' in workerEnv, 'worker has worker password')
  assert(!('PPAP_PG_API_PASSWORD' in workerEnv), 'worker must not have API password')
  assert(!('PPAP_PADDLE_TOKEN' in apiEnv), 'api must not have PPAP_PADDLE_TOKEN')
  assert('PPAP_PADDLE_TOKEN' in workerEnv, 'worker has PPAP_PADDLE_TOKEN')
}

// Defaults come from env.example through `docker compose config`, not from
// scanning that file as text.
const defaults = requireComposeCli() ? nativeComposeConfig({}) : null
if (defaults) {
  const defaultApi = envOf(defaults.services?.api)
  const defaultWorker = envOf(defaults.services?.worker)
  assertEqual(defaultApi.PPAP_PG_HOST, 'postgres', 'env file default PG host')
  assertEqual(defaultApi.PPAP_PG_PORT, '5432', 'env file default PG port')
  assertEqual(defaultApi.PPAP_PG_DATABASE, 'ppap', 'env file default database')
  assertEqual(defaultApi.PPAP_S3_ENDPOINT, 'http://silo:9000', 'env file default S3 endpoint')
  assert(!('FEISHU_APP_ID' in defaultWorker), 'worker must not receive Feishu app id from defaults')
}

// @insforge/cli@0.2.8 has no offline compute-spec validator. `compute *` talks
// to a live backend; `config plan/apply` is insforge.toml, not these files.
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
assert('PPAP_PADDLE_TOKEN' in worker.environment, 'worker spec has PPAP_PADDLE_TOKEN')
assert(!('PPAP_PADDLE_TOKEN' in api.environment), 'api spec has no PPAP_PADDLE_TOKEN')
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
