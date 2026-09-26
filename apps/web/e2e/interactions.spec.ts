import {test, expect, type Page, type Route} from '@playwright/test'
import {BODY, JOB, SOURCE, VERSION, WEB, cors, fulfillJson, mockProductApi, uploadBuffer} from './helpers'

async function defaultRuleParse(page: Page, options?: {status?: string; hold?: boolean; publishStatus?: number; deepOffered?: boolean}) {
  const pending: Route[] = []
  const posts: unknown[] = []
  const status = options?.status ?? 'succeeded'
  const publishStatus = options?.publishStatus ?? 201
  await mockProductApi(page, async (route, path) => {
    const request = route.request()
    if (path === '/rules' && request.method() === 'POST') {
      if (publishStatus >= 400) {
        await fulfillJson(route, {error: 'save_failed'}, publishStatus)
      } else {
        await fulfillJson(route, {
          id: 'saved-rule',
          title: '规则 A',
          kind: 'engine',
          type: '控制计划',
          created_at: '2026-09-23T00:00:00Z',
          body: BODY,
          source_file_id: SOURCE,
          source_file_version_id: VERSION,
        }, publishStatus)
      }
      return true
    }
    if (path === '/rules/saved-rule') {
      await fulfillJson(route, {
        id: 'saved-rule',
        title: '规则 A',
        kind: 'engine',
        type: '控制计划',
        created_at: '2026-09-23T00:00:00Z',
        body: BODY,
        source_file_id: SOURCE,
        source_file_version_id: VERSION,
      })
      return true
    }
    if (path === '/rules/parse') {
      posts.push(request.postDataJSON())
      if (options?.hold) {
        pending.push(route)
      } else {
        await fulfillJson(route, {id: (posts.at(-1) as {request_id: string}).request_id, status: 'queued'}, 202)
      }
      return true
    }
    if (path.startsWith('/rules/parse/')) {
      await fulfillJson(route, {
        id: path.split('/').at(-1),
        status,
        source_file_id: SOURCE,
        source_file_version_id: VERSION,
        document_type: '控制计划',
        result: status === 'succeeded' ? {title: '规则 A', body: BODY, source_file_version_id: VERSION} : null,
      })
      return true
    }
    return false
  }, {deepOffered: options?.deepOffered})
  return {pending, posts}
}

test('batch upload keeps an uncertain version for read-only recovery', async ({page}) => {
  const names = ['first.xlsx', 'second.xlsx', 'P037.xlsx', 'fourth.xlsx']
  const created: string[] = []
  const blobs: string[] = []
  const finalized: string[] = []
  const reads: string[] = []
  const analyses: string[] = []
  const drafts: Array<{items: Array<{file_version_id: string; role: string}>}> = []
  const readResults = ['failed_read', 'staging', 'failed_read', 'ready', 'staging', 'staging']
  await mockProductApi(page, () => false)
  await page.route('**/api/files', async route => {
    created.push(route.request().postDataJSON().original_filename)
    await fulfillJson(route, {file_id: `file-${created.length}`, id: `version-${created.length}`}, 201)
  })
  await page.route('**/api/files/*/versions/*/blob', async route => {
    blobs.push(route.request().url())
    await fulfillJson(route, {})
  })
  await page.route('**/api/files/*/versions/*/finalize', async route => {
    finalized.push(route.request().url())
    if ([3, 4, 5].some(number => route.request().url().includes(`version-${number}`))) {
      await route.abort('failed')
    } else {
      await fulfillJson(route, {status: 'ready'})
    }
  })
  await page.route('**/api/files/*/versions/*', async route => {
    if (route.request().url().includes('/blob') || route.request().url().includes('/finalize')) {
      await route.fallback()
      return
    }
    reads.push(route.request().url())
    const result = readResults[reads.length - 1]
    if (result === 'failed_read') await route.abort('failed')
    else await fulfillJson(route, {status: result})
  })
  await page.route('**/api/drafts', route => fulfillJson(route, {id: 'draft-1'}, 201))
  await page.route('**/api/drafts/draft-1', async route => {
    if (route.request().method() === 'PUT') drafts.push(route.request().postDataJSON())
    await fulfillJson(route, {id: 'draft-1'})
  })
  await page.route('**/api/file-analyses', async route => {
    analyses.push(route.request().postDataJSON().source_file_version_id)
    await fulfillJson(route, {id: 'analysis-1', status: 'queued'})
  })
  await page.route('**/api/file-analyses/*', route => fulfillJson(route, {id: 'analysis-1', status: 'succeeded'}))
  await page.goto('/')
  await page.getByTestId('source-upload').setInputFiles(names.map(name => uploadBuffer(name, 'fixture')))
  await expect(page.getByTestId('draft-items')).toContainText('first.xlsx')
  await expect(page.getByTestId('draft-items')).toContainText('second.xlsx')
  await expect(page.getByRole('alert')).toContainText('上传结果未确认：P037.xlsx')
  const check = page.getByRole('button', {name: '核对上传结果'})
  await expect(check).toBeVisible()
  expect(await check.evaluate(button => button.closest('[role="alert"]')?.tagName)).toBe('DIV')
  await expect(page.getByText('fourth.xlsx')).toHaveCount(0)
  expect(created).toEqual(names.slice(0, 3))
  expect(blobs).toHaveLength(3)
  expect(finalized).toHaveLength(3)
  expect(drafts.map(item => item.items.at(-1)?.file_version_id)).toEqual(['version-1', 'version-2'])
  expect(await page.evaluate(() => {
    const event = new Event('beforeunload', {cancelable: true})
    window.dispatchEvent(event)
    return event.defaultPrevented
  })).toBe(true)
  await page.getByRole('link', {name: '规则库'}).click()
  const leave = page.getByRole('dialog')
  await expect(leave).toBeVisible()
  await expect(leave).toContainText('P037.xlsx')
  await expect(leave.getByRole('button', {name: '保存并离开'})).toHaveCount(0)
  await leave.getByRole('button', {name: '继续核对'}).click()
  await expect(leave).toHaveCount(0)
  await check.click()
  await expect(check).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('仍未就绪')
  await check.click()
  await expect(check).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('暂时无法读取')
  await check.click()
  await expect(page.getByTestId('draft-items')).toContainText('P037.xlsx')
  await expect(check).toHaveCount(0)
  expect(created).toEqual(names.slice(0, 3))
  expect(blobs).toHaveLength(3)
  expect(finalized).toHaveLength(3)
  expect(reads).toHaveLength(4)
  expect(analyses).toEqual(['version-1', 'version-2', 'version-3'])
  expect(drafts.at(-1)?.items.at(-1)?.file_version_id).toBe('version-3')
  await page.getByTestId('source-upload').setInputFiles(uploadBuffer('later.xlsx', 'fixture'))
  await expect(page.getByRole('button', {name: '核对上传结果'})).toBeVisible()
  await page.getByRole('button', {name: '暂不加入'}).click()
  await expect(page.getByRole('button', {name: '核对上传结果'})).toHaveCount(0)
  await expect(page.getByTestId('source-upload')).toBeEnabled()
  await expect(page.getByTestId('draft-items')).toContainText('P037.xlsx')
  await expect(page.getByTestId('draft-items')).not.toContainText('later.xlsx')
  expect(created).toEqual([...names.slice(0, 3), 'later.xlsx'])
  expect(drafts.at(-1)?.items.at(-1)?.file_version_id).toBe('version-3')
  await page.getByTestId('source-upload').setInputFiles(uploadBuffer('stuck.xlsx', 'fixture'))
  await expect(page.getByRole('button', {name: '核对上传结果'})).toBeVisible()
  await page.getByRole('checkbox', {name: '参与审核：first.xlsx'}).uncheck()
  await expect(page.getByTestId('save-draft')).toBeDisabled()
  const dialogs: string[] = []
  page.on('dialog', dialog => {
    dialogs.push(dialog.type())
    void dialog.dismiss()
  })
  await page.getByRole('link', {name: '规则库'}).click()
  const stuck = page.getByRole('dialog')
  await expect(stuck).toContainText('stuck.xlsx')
  await expect(stuck).toContainText('其他未保存修改也会放弃')
  await expect(stuck.getByRole('button', {name: '保存并离开'})).toHaveCount(0)
  await stuck.getByRole('button', {name: '暂不加入并离开'}).click()
  await expect(page.getByTestId('rule-versions')).toBeVisible()
  expect(dialogs).toEqual([])
  expect(drafts.at(-1)?.items.at(-1)?.file_version_id).toBe('version-3')
  expect(drafts.at(-1)?.items[0]?.role).toBe('target')
})

test('draft network failure has a Chinese message', async ({page}) => {
  await mockProductApi(page, () => false)
  await page.route('**/api/drafts?*', route => route.abort('failed'))
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('网络连接失败，请检查网络后重试。')
  expect(await page.getByRole('alert').innerText()).not.toContain('Failed to fetch')
})

test('draft analysis read failure is local and retry does not reanalyze', async ({page}) => {
  const items = [
    {file_id: 'reference', file_version_id: 'ref-version', original_filename: 'flow.xlsx', role: 'reference', rule_version_id: null},
    {file_id: SOURCE, file_version_id: VERSION, original_filename: 'control.xlsx', role: 'target', rule_version_id: 'version-b'},
  ]
  const reads: string[] = []
  const writes: string[] = []
  await mockProductApi(page, () => false)
  page.on('request', request => {
    if (['POST', 'PUT'].includes(request.method())) writes.push(request.url())
  })
  await page.route('**/api/drafts/' + JOB, route => fulfillJson(route, {id: JOB, status: 'open', items}))
  await page.route('**/api/files/*/versions/*/analysis', async route => {
    reads.push(route.request().url())
    if (route.request().url().includes('/reference/') && reads.filter(url => url.includes('/reference/')).length === 1) {
      await route.abort('failed')
      return
    }
    const reference = route.request().url().includes('/reference/')
    await fulfillJson(route, {analysis: {id: reference ? 'ref-analysis' : 'target-analysis', status: reference ? 'failed' : 'succeeded'}})
  })
  await page.route('**/api/file-analyses/ref-analysis', route => fulfillJson(route, {id: 'ref-analysis', status: 'failed'}))
  await page.route('**/api/file-analyses/target-analysis', route => fulfillJson(route, {
    id: 'target-analysis',
    status: 'succeeded',
    result: {
      metadata: {customer: 'CETUS', model: null, part: null},
      document_type: '控制计划',
      matched_rule_id: 'version-b',
      matching_status: 'matched',
      notices: [],
    },
  }))
  await page.goto('/?draft=' + JOB)
  await expect(page.getByRole('button', {name: '重新读取 flow.xlsx 的识别结果'})).toBeVisible()
  await expect(page.getByText('CETUS', {exact: true})).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await expect(page.getByTestId('item-rule')).toHaveValue('version-b')
  await page.getByRole('button', {name: '重新读取 flow.xlsx 的识别结果'}).click()
  await expect(page.getByText('识别未完成，请核对原件', {exact: true})).toBeVisible()
  await expect(page.getByRole('button', {name: '重新读取 flow.xlsx 的识别结果'})).toHaveCount(0)
  await expect(page.getByText('匹配失败，请手动选择', {exact: true})).toHaveCount(0)
  expect(reads).toHaveLength(3)
  expect(writes).toEqual([])
})

for (const [readback, finalizeResponse] of [
  ['ready', 'lost'],
  ['ready', 'bad_json'],
  ['staging', 'lost'],
  ['failed_read', 'lost'],
] as const) {
  test(`lost or invalid finalize (${readback}/${finalizeResponse}) reads the version once`, async ({page}) => {
    const createdPosts: string[] = []
    const finalizePosts: string[] = []
    const versionReads: string[] = []
    await mockProductApi(page, () => false)
    page.on('request', request => {
      if (request.method() === 'POST' && request.url().endsWith('/api/files')) createdPosts.push(request.method())
    })
    await page.route('**/api/files/*/versions/*/finalize', async route => {
      finalizePosts.push(route.request().method())
      if (finalizeResponse === 'bad_json') {
        await route.fulfill({status: 200, body: '{', contentType: 'application/json', headers: cors})
      } else {
        await route.abort('failed')
      }
    })
    await page.route('**/api/files/*/versions/*', async route => {
      if (route.request().url().includes('/blob') || route.request().url().includes('/finalize')) {
        await route.fallback()
        return
      }
      versionReads.push(route.request().method())
      if (readback === 'failed_read') await route.abort('failed')
      else await fulfillJson(route, {status: readback})
    })
    await page.route('**/api/drafts', route => fulfillJson(route, {id: JOB}, 201))
    await page.route('**/api/drafts/' + JOB, route => fulfillJson(route, {id: JOB, status: 'open', items: []}))
    await page.goto('/')
    await page.getByTestId('source-upload').setInputFiles(uploadBuffer('尺寸检验报告-example.xlsm', 'fixture', 'application/vnd.ms-excel.sheet.macroEnabled.12'))
    if (readback === 'ready') {
      await expect(page.getByTestId('draft-items')).toContainText('尺寸检验报告-example.xlsm')
      await expect(page.getByRole('alert')).toHaveCount(0)
      await expect(page.getByTestId('source-upload')).toBeEnabled()
    } else {
      await expect(page.getByRole('alert')).toContainText('上传结果未确认：尺寸检验报告-example.xlsm')
      await expect(page.getByTestId('draft-items')).toHaveCount(0)
      await expect(page.getByRole('button', {name: '核对上传结果'})).toBeVisible()
      await expect(page.getByTestId('source-upload')).toBeDisabled()
    }
    await expect(page.getByText('正在上传 尺寸检验报告-example.xlsm', {exact: true})).toHaveCount(0)
    expect(createdPosts).toEqual(['POST'])
    expect(finalizePosts).toEqual(['POST'])
    expect(versionReads).toEqual(['GET'])
  })
}

test('failed round reaudit preserves inputs and deep mode without starting a job', async ({page}) => {
  const draftId = '00000000-0000-4000-8000-000000000004'
  const items = [
    {file_id: SOURCE, file_version_id: VERSION, original_filename: 'control-plan.xlsx', role: 'target', rule_version_id: 'version-b'},
    {file_id: 'reference-file', file_version_id: 'reference-version', original_filename: 'process-flow.xlsx', role: 'reference', rule_version_id: null},
  ]
  const posts: unknown[] = []
  const saves: unknown[] = []
  const accepted: string[] = []
  await mockProductApi(page, () => false, {deepOffered: true})
  await page.route('**/api/public-config', route => fulfillJson(route, {api_url: WEB + '/api', profile: 'isolated-dev', deep_offered: true}))
  await page.route('**/api/rounds/' + JOB, route => fulfillJson(route, {
    id: JOB,
    execution: 'failed',
    analysis_mode: 'deep',
    inputs: items,
    findings: [],
    failure: {code: 'deep_budget_exhausted', message: '执行额度已用完。', retryable: false},
  }))
  await page.route('**/api/drafts', async route => {
    posts.push(route.request().postDataJSON())
    await fulfillJson(route, {id: draftId}, 201)
  })
  await page.route('**/api/drafts/' + draftId, async route => {
    if (route.request().method() === 'PUT') saves.push(route.request().postDataJSON())
    await fulfillJson(route, {id: draftId, status: 'open', items, analysis_mode: 'deep'})
  })
  await page.route('**/api/drafts/*/accept', async route => {
    accepted.push(route.request().method())
    await fulfillJson(route, {}, 500)
  })
  await page.route('**/api/integrations/feishu/usage/quota?*', route => fulfillJson(route, {accept_allowed: true}))
  await page.goto('/?round=' + JOB)
  await page.getByTestId('prepare-again').click()
  await expect(page.locator('#audit-preparation')).toBeVisible()
  await expect(page.getByTestId('draft-items')).toContainText('control-plan.xlsx')
  await expect(page.getByTestId('draft-items')).toContainText('process-flow.xlsx')
  expect(posts).toEqual([{predecessor_id: JOB}])
  expect(saves).toEqual([{
    items: items.map(item => ({file_version_id: item.file_version_id, role: item.role, rule_version_id: item.rule_version_id})),
    analysis_mode: 'deep',
  }])
  expect(accepted).toEqual([])
  await expect(page.getByTestId('analysis-mode')).toBeVisible()
})

test('deep mode control is absent when public-config does not offer it', async ({page}) => {
  await mockProductApi(page, () => false)
  await page.route('**/api/drafts', route => fulfillJson(route, {id: JOB}, 201))
  await page.route('**/api/drafts/' + JOB, route => fulfillJson(route, {id: JOB, status: 'open', items: []}))
  await page.goto('/')
  await page.getByTestId('source-upload').setInputFiles(uploadBuffer('control-plan.xlsx', 'fixture'))
  await expect(page.getByTestId('draft-items')).toContainText('control-plan.xlsx')
  await expect(page.getByTestId('analysis-mode')).toHaveCount(0)
})

for (const [entry, firstRead] of [
  ['submit', 'connectionreset'],
  ['deep_link', 'connectionreset'],
  ['submit', 'bad_json'],
  ['deep_link', 'bad_json'],
] as const) {
  test(`round read failure (${entry}/${firstRead}) recovers without resubmitting`, async ({page}) => {
    const draftId = '00000000-0000-4000-8000-000000000004'
    const item = {file_id: SOURCE, file_version_id: VERSION, original_filename: 'control-plan.xlsx', role: 'target', rule_version_id: 'version-b'}
    const accepted = {id: JOB, execution: 'queued', analysis_mode: 'standard', inputs: [item], findings: []}
    const posts: string[] = []
    const reads: string[] = []
    await mockProductApi(page, () => false)
    await page.route('**/api/drafts/' + draftId, route => fulfillJson(route, {id: draftId, status: 'open', items: [item]}))
    await page.route('**/api/files/*/versions/*/analysis', route => fulfillJson(route, {analysis: null}))
    await page.route('**/api/integrations/feishu/usage/quota?*', route => fulfillJson(route, {accept_allowed: true}))
    await page.route('**/api/drafts/' + draftId + '/accept', async route => {
      posts.push(route.request().method())
      await fulfillJson(route, accepted, 201)
    })
    await page.route('**/api/rounds/' + JOB, async route => {
      reads.push(route.request().method())
      if (reads.length === 1) {
        if (firstRead === 'connectionreset') await route.abort('connectionreset')
        else await route.fulfill({status: 200, body: '{', contentType: 'application/json', headers: cors})
      } else {
        await fulfillJson(route, {...accepted, execution: 'completed'})
      }
    })
    await page.goto(entry === 'submit' ? '/?draft=' + draftId : '/?round=' + JOB)
    if (entry === 'submit') await page.getByTestId('submit-audit').click()
    await expect(page.getByText('暂时无法读取审核状态，正在重新连接。请勿重复提交。', {exact: true})).toBeVisible()
    await expect(page.locator('#audit-preparation')).toHaveCount(0)
    await expect(page.getByTestId('submit-audit')).toHaveCount(0)
    await expect(page.getByText('检查完成', {exact: true}).first).toBeVisible({timeout: 10_000})
    await expect(page.getByText('暂时无法读取审核状态，正在重新连接。请勿重复提交。', {exact: true})).toHaveCount(0)
    expect(posts).toEqual(entry === 'submit' ? ['POST'] : [])
    expect(reads).toEqual(['GET', 'GET'])
  })
}

test('delayed parse cannot be replaced by new or open other rule', async ({page}) => {
  const {pending, posts} = await defaultRuleParse(page, {hold: true})
  await page.goto('/rules')
  await expect(page.getByTestId('rule-versions')).toContainText('规则 B')
  await page.getByTestId('new-rule').click()
  const parse = page.waitForRequest('**/api/rules/parse')
  await page.getByTestId('rule-file').setInputFiles(uploadBuffer('A.json', '{}', 'application/json'))
  await parse
  await expect(page.getByTestId('rule-back')).toBeDisabled()
  await expect(page.getByTestId('rule-file')).toBeDisabled()
  await page.evaluate(() => { document.querySelector('[data-testid=rule-back]')?.dispatchEvent(new MouseEvent('click', {bubbles: true})) })
  expect(pending).toHaveLength(1)
  const posted = posts[0] as {request_id: string}
  await pending[0].fulfill({
    status: 202,
    json: {id: posted.request_id, status: 'queued'},
    headers: cors,
  })
  await page.waitForTimeout(100)
  await page.route('**/api/rules/parse/' + posted.request_id, route => fulfillJson(route, {
    id: posted.request_id,
    status: 'succeeded',
    source_file_id: SOURCE,
    source_file_version_id: VERSION,
    document_type: '控制计划',
    result: {title: '规则 A', body: BODY, source_file_version_id: VERSION},
  }))
  await expect(page.getByTestId('rule-title')).toHaveValue('规则 A')
  await expect(page.locator('#rule-origin')).toContainText('新规则')
  await expect(page.getByTestId('rule-back')).toBeEnabled()
  expect(posts).toHaveLength(1)
})

test('refresh reads unknown without submitting and warns before explicit rerun', async ({page}) => {
  const {posts} = await defaultRuleParse(page, {status: 'unknown'})
  await page.goto('/rules?parse=' + JOB)
  await expect(page.locator('#rule-message')).toContainText('再次消耗模型费用')
  await expect(page.locator('#parse-type')).toHaveValue('控制计划')
  expect(posts).toEqual([])
  await page.reload()
  await expect(page.locator('#rule-message')).toContainText('原任务可能仍会完成')
  expect(posts).toEqual([])
  await page.getByTestId('rerun-rule').click()
  await expect(page.locator('#rule-message')).toContainText('解析结果暂不确定')
  expect(posts).toHaveLength(1)
  expect((posts[0] as {request_id: string}).request_id).not.toBe(JOB)
  expect((posts[0] as {document_type: string}).document_type).toBe('控制计划')
})

test('failed task can be retried with selected type without changing original', async ({page}) => {
  const {posts} = await defaultRuleParse(page)
  const originalRequests: string[] = []
  await page.route('**/api/rules/parse/' + JOB, async route => {
    originalRequests.push(route.request().method())
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({status: 204, headers: cors})
      return
    }
    await fulfillJson(route, {
      id: JOB,
      status: 'failed',
      source_file_id: SOURCE,
      source_file_version_id: VERSION,
      document_type: '',
      result: {error: 'rule_type_required'},
    })
  })
  await page.goto('/rules?parse=' + JOB)
  await expect(page.locator('#rule-message')).toContainText('请选择文档类型')
  await expect(page.locator('#parse-type')).toHaveValue('')
  expect(posts).toEqual([])
  await page.locator('#parse-type').selectOption('控制计划')
  await page.getByTestId('rerun-rule').click()
  await expect(page.getByTestId('rule-title')).toHaveValue('规则 A')
  expect(posts).toHaveLength(1)
  const posted = posts[0] as {request_id: string; source_file_id: string; source_file_version_id: string; document_type: string}
  expect(posted.request_id).not.toBe(JOB)
  expect(posted.source_file_id).toBe(SOURCE)
  expect(posted.source_file_version_id).toBe(VERSION)
  expect(posted.document_type).toBe('控制计划')
  expect(originalRequests.filter(method => method !== 'OPTIONS')).toEqual(['GET'])
})

test('save success clears the leave guard before navigation', async ({page}) => {
  await defaultRuleParse(page)
  const dialogs: string[] = []
  page.on('dialog', dialog => {
    dialogs.push(dialog.type())
    void dialog.dismiss()
  })
  await page.goto('/rules?parse=' + JOB)
  await expect(page.getByTestId('rule-title')).toHaveValue('规则 A')
  await page.getByTestId('publish-rule').click()
  await page.waitForFunction(() => location.search.includes('saved=1') || (document.querySelector('[data-testid=publish-rule]') || {} as HTMLElement).textContent?.includes('打开已保存规则'))
  expect(dialogs).toEqual([])
  if (!page.url().includes('saved=1')) {
    await expect(page.getByTestId('publish-rule')).toHaveText('打开已保存规则')
    await expect(page.getByTestId('publish-rule')).toBeEnabled()
  }
})

test('save failure returns to the editable candidate', async ({page}) => {
  await defaultRuleParse(page, {publishStatus: 500})
  await page.goto('/rules?parse=' + JOB)
  await expect(page.getByTestId('rule-title')).toHaveValue('规则 A')
  await page.getByTestId('publish-rule').click()
  await expect(page.locator('#rule-message')).toContainText('请求失败')
  await expect(page.getByTestId('publish-rule')).toHaveText('保存规则')
  await expect(page.getByTestId('publish-rule')).toBeEnabled()
  await expect(page.getByTestId('rule-title')).toBeEnabled()
})

test('confirmed abandon leaves without a second prompt', async ({page}) => {
  await defaultRuleParse(page)
  const dialogs: string[] = []
  page.on('dialog', dialog => {
    dialogs.push(dialog.type())
    void dialog.dismiss()
  })
  await page.goto('/rules?parse=' + JOB)
  await expect(page.getByTestId('rule-title')).toHaveValue('规则 A')
  await page.getByTestId('rule-back').click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', {name: '放弃修改'}).click()
  await page.waitForFunction(() => location.pathname.endsWith('/rules') && !location.search)
  expect(dialogs).toEqual([])
})
