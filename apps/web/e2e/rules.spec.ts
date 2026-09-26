import {test, expect} from '@playwright/test'
import {isolatedDevLogin, uploadBuffer} from './helpers'

test('import, review, publish, and revisit a customer rule', async ({page, request}) => {
  const title = '规则导入 ' + crypto.randomUUID().slice(0, 8)
  const source = {
    kind: 'engine',
    type: '控制计划',
    rule_profile: 'v3',
    customers: ['测试客户'],
    models: [],
    parts: [],
    rules: [{id: '1', category: '基本信息', priority: null, rule_to_check: '必须填写零件号', type_of_rule: 'table'}],
  }
  await isolatedDevLogin(page)
  await page.getByTestId('pc-shell').getByTestId('nav-rules').click()
  await page.waitForURL('**/rules')
  await page.getByTestId('new-rule').click()
  await page.getByTestId('rule-file').setInputFiles(uploadBuffer('控制计划规则.json', Buffer.from(JSON.stringify(source)), 'application/json'))
  await expect(page.getByTestId('rule-title')).toBeVisible({timeout: 30_000})
  await expect(page.getByTestId('rule-message')).toContainText('解析完成')
  await expect(page.getByTestId('rule-items').locator('article')).toHaveCount(1)
  await page.getByTestId('rule-title').fill(title)
  await page.locator('[data-field="rule_to_check"]').fill('必须填写零件号和版本')
  await page.getByTestId('publish-rule').click()
  await expect(page.getByText('已保存规则')).toBeVisible()
  await page.goto('/rules')
  const row = page.getByTestId('rule-versions').locator('tr', {hasText: title})
  await expect(row).toHaveCount(1)
  await row.getByRole('link', {name: title}).click()
  await expect(page.locator('[data-field="rule_to_check"]')).toHaveCount(0)
  await expect(page.getByTestId('rule-items')).toContainText('必须填写零件号和版本')
  await expect(page.locator('#rule-metadata')).toContainText('测试客户')
  await expect(page.locator('#rule-origin')).toContainText('版本')
  const href = await page.locator('#rule-source').getAttribute('href')
  expect(href).toBeTruthy()
  const downloaded = await request.get(href!)
  expect(downloaded.status()).toBe(200)
  expect(await downloaded.json()).toEqual(source)
})
