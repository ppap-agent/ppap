import {test, expect} from '@playwright/test'
import {BODY, JOB, SOURCE, VERSION, WEB, fulfillJson, mockProductApi, mockRuntimeConfig} from './helpers'

test('keyboard focus, viewports, and reduced motion on the product shell', async ({page}) => {
  await page.emulateMedia({reducedMotion: 'reduce'})
  await mockRuntimeConfig(page)
  await page.route('**/api/public-config', route => fulfillJson(route, {api_url: WEB + '/api', profile: 'isolated-dev'}))
  await page.route('**/api/me', route => fulfillJson(route, {error: 'unauthenticated'}, 401))
  const session = await page.context().newCDPSession(page)
  await session.send('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 1})
  await page.goto('/dev')
  await page.waitForSelector('[data-testid="isolated-dev-login"]')
  await expect(page.getByTestId('pc-shell')).toBeVisible()
  await expect(page.locator('.mobile-tab-bar')).toBeHidden()
  await page.keyboard.press('Tab')
  const focused = await page.evaluate(() => {
    const el = document.activeElement
    const style = el ? getComputedStyle(el) : null
    return {text: el?.textContent, outline: style?.outlineStyle, width: style ? parseFloat(style.outlineWidth) : 0}
  })
  expect(focused.text).toContain('跳到主要内容')
  expect(focused.outline).toBe('solid')
  expect(focused.width).toBeGreaterThanOrEqual(2)
  const media = await page.evaluate(() => ({
    pointer: matchMedia('(pointer: coarse)').matches,
    motion: matchMedia('(prefers-reduced-motion: reduce)').matches,
  }))
  expect(media.pointer).toBe(true)
  expect(media.motion).toBe(true)
  await page.setViewportSize({width: 390, height: 844})
  await expect(page.locator('.mobile-tab-bar')).toBeVisible()
  const box = await page.locator('.mobile-tab-bar a').first.boundingBox()
  expect(box).toBeTruthy()
  expect(box!.height).toBeGreaterThanOrEqual(44)
  expect(await page.locator('h1').count()).toBe(1)
})

test('rule editor keyboard, leave dialog, and a single heading', async ({page}) => {
  await mockProductApi(page, async (route, path) => {
    if (path.startsWith('/rules/parse/')) {
      await fulfillJson(route, {
        id: JOB,
        status: 'succeeded',
        source_file_id: SOURCE,
        source_file_version_id: VERSION,
        document_type: '控制计划',
        result: {title: '规则 A', body: BODY, source_file_version_id: VERSION},
      })
      return true
    }
    return false
  })
  await page.goto('/rules?parse=' + JOB)
  await expect(page.getByTestId('rule-title')).toHaveValue('规则 A')
  expect(await page.locator('h1').count()).toBe(1)
  await page.getByTestId('rule-title').focus()
  expect(await page.evaluate(() => document.activeElement?.id)).toBe('rule-title')
  await page.keyboard.press('Tab')
  const moved = await page.evaluate(() => {
    const active = document.activeElement
    return Boolean(active && active.id !== 'rule-title' && active.closest('#main-content, main'))
  })
  expect(moved).toBe(true)
  await page.getByTestId('rule-back').click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  const inside = await page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')))
  expect(inside).toBe(true)
  expect(await page.locator('h1').count()).toBe(1)
})
