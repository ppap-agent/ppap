import {defineConfig} from '@playwright/test'

const web = (process.env.PPAP_WEB_URL || 'http://127.0.0.1:4173').replace(/\/$/, '')

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: {timeout: 20_000},
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: web,
    viewport: {width: 1440, height: 900},
    trace: 'off',
  },
})
