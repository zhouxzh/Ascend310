import { defineConfig } from '@playwright/test'

const externalBaseURL = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process?.env?.PLAYWRIGHT_BASE_URL
const liveBoardEnabled = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process?.env?.CASE3_LIVE_BOARD_E2E === '1'

export default defineConfig({
  testDir: './e2e',
  outputDir: '../reports/webui/playwright',
  use: {
    baseURL: externalBaseURL ?? 'http://127.0.0.1:8765',
    browserName: 'chromium',
    channel: 'msedge',
    launchOptions: liveBoardEnabled ? { args: ['--js-flags=--expose-gc'] } : undefined,
  },
  webServer: externalBaseURL
    ? undefined
    : {
        command:
          'python -m uvicorn midi_ddsp_webui.app:app --app-dir .. --host 127.0.0.1 --port 8765',
        url: 'http://127.0.0.1:8765',
        reuseExistingServer: true,
      },
})
