import {expect, type Page, type Route} from '@playwright/test'

export const WEB = (process.env.PPAP_WEB_URL || 'http://127.0.0.1:4173').replace(/\/$/, '')
export const RULE_A = '00000000-0000-4000-8000-0000000000a5'
export const JOB = '00000000-0000-4000-8000-000000000001'
export const SOURCE = '00000000-0000-4000-8000-000000000002'
export const VERSION = '00000000-0000-4000-8000-000000000003'
export const BODY = {
  kind: 'engine',
  type: '控制计划',
  rule_profile: 'v3',
  customers: [] as string[],
  models: [] as string[],
  parts: [] as string[],
  rules: [{id: '1', category: 'A', priority: null, rule_to_check: '检查 A', type_of_rule: 'table'}],
}

export const cors = {
  'Access-Control-Allow-Origin': WEB,
  'Access-Control-Allow-Credentials': 'true',
  'Access-Control-Allow-Headers': 'content-type',
}

export function apiPath(url: string) {
  const path = url.split('?')[0]
  const index = path.indexOf('/api')
  if (index >= 0) return path.slice(index + 4)
  return path.replace('http://127.0.0.1:8080', '')
}

export async function isolatedDevLogin(page: Page) {
  await page.goto('/dev')
  await page.getByTestId('isolated-dev-login').click()
  await page.getByTestId('session-user').waitFor({state: 'attached'})
}

export function uploadBuffer(name: string, body: string | Buffer, mimeType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet') {
  return {name, mimeType, buffer: typeof body === 'string' ? Buffer.from(body) : body}
}

export async function fulfillJson(route: Route, json: unknown, status = 200) {
  await route.fulfill({status, json, headers: cors})
}

export async function mockRuntimeConfig(page: Page, apiUrl = WEB + '/api') {
  await page.route('**/runtime-config.json', route => route.fulfill({json: {api_url: apiUrl}}))
}

export async function mockProductApi(page: Page, handle: (route: Route, path: string) => Promise<boolean> | boolean, options?: {deepOffered?: boolean}) {
  await mockRuntimeConfig(page)
  await page.route('**/api/**', async route => {
    const request = route.request()
    if (request.method() === 'OPTIONS') {
      await route.fulfill({status: 204, headers: cors})
      return
    }
    const path = apiPath(request.url())
    if (await handle(route, path)) return
    if (path === '/public-config') {
      await fulfillJson(route, {api_url: WEB + '/api', profile: 'isolated-dev', deep_offered: options?.deepOffered === true})
      return
    }
    if (path === '/me') {
      await fulfillJson(route, {display_name: 'alice', identity: {subject: 'fixture'}})
      return
    }
    if (path === '/rule-types') {
      await fulfillJson(route, {types: ['控制计划']})
      return
    }
    if (path === '/rules' && request.method() === 'GET') {
      await fulfillJson(route, {rules: [{id: 'version-b', kind: 'engine', title: '规则 B', type: '控制计划'}]})
      return
    }
    if (path === '/files') {
      await fulfillJson(route, {file_id: SOURCE, id: VERSION}, 201)
      return
    }
    if (path.endsWith('/blob') || path.endsWith('/finalize')) {
      await fulfillJson(route, {})
      return
    }
    await fulfillJson(route, {error: 'unexpected_test_request'}, 404)
  })
}

export async function waitForRound(page: Page) {
  await page.waitForFunction(() => new URL(window.location.href).searchParams.get('round'))
  return page.evaluate(() => new URL(window.location.href).searchParams.get('round'))
}

export {expect}
