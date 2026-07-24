import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  outputDir: '../reports/webui/playwright',
  use: {
    baseURL: 'http://127.0.0.1:8765',
    browserName: 'chromium',
    channel: 'msedge',
  },
  webServer: {
    command: 'python ../scripts/run_webui.py',
    url: 'http://127.0.0.1:8765',
    reuseExistingServer: true,
  },
})
