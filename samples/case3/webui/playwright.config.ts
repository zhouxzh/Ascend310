import { defineConfig } from '@playwright/test'

const externalBaseURL = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process?.env?.PLAYWRIGHT_BASE_URL

export default defineConfig({
  testDir: './e2e',
  outputDir: '../reports/webui/playwright',
  use: {
    baseURL: externalBaseURL ?? 'http://127.0.0.1:18765',
    browserName: 'chromium',
    channel: 'msedge',
  },
  webServer: externalBaseURL
    ? undefined
    : {
        command:
          'python -m uvicorn midi_ddsp_webui.app:app --app-dir .. --host 127.0.0.1 --port 18765',
        url: 'http://127.0.0.1:18765',
        reuseExistingServer: false,
      },
})
