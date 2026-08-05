import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const environment = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process?.env
const liveBoardEnabled = Boolean(
  environment?.PLAYWRIGHT_BASE_URL
  && environment.CASE3_LIVE_BOARD_E2E === '1',
)

test.skip(
  !liveBoardEnabled,
  'Set PLAYWRIGHT_BASE_URL and CASE3_LIVE_BOARD_E2E=1 for the real-board suite.',
)

test('served DDSP-VST workspace reports the real board catalog', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 969 })
  const catalogResponse = page.waitForResponse((response) => (
    response.request().method() === 'GET'
    && response.url().endsWith('/api/v1/ddsp-vst-effect/catalog')
  ))
  await page.goto('/')
  await expect(page.locator('.primary-nav button')).toHaveCount(4)
  await page.getByRole('button', { name: 'DDSP-VST' }).first().click()
  const response = await catalogResponse
  const catalog = await response.json() as {
    available: boolean
    backend: string
    models: unknown[]
    audio_inputs: unknown[]
    audio_outputs: unknown[]
  }
  expect(catalog.backend).toBe('acl/om')
  expect(catalog.models).toHaveLength(11)
  await expect(page.getByRole('heading', { name: 'DDSP-VST' })).toBeVisible()
  await expect(page.getByText('FEATURE · ACL/OM')).toBeVisible()
  await expect(page.getByText('CONTROL · ACL/OM')).toBeVisible()
  await expect(page.getByRole('button', { name: '启动' })).toBeVisible()
  const metrics = await page.evaluate(() => {
    const content = document.querySelector<HTMLElement>('.content-area')
    const canvas = document.querySelector<HTMLCanvasElement>('.effect-trace')
    const context = canvas?.getContext('2d')
    let nonblank = 0
    if (canvas && context && canvas.width && canvas.height) {
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
      for (let index = 3; index < pixels.length; index += 64) {
        if (pixels[index] > 0) nonblank += 1
      }
    }
    return {
      contentClientHeight: content?.clientHeight ?? 0,
      contentScrollHeight: content?.scrollHeight ?? 0,
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      canvasNonblank: nonblank,
    }
  })
  expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1)
  expect(metrics.contentScrollHeight).toBeLessThanOrEqual(metrics.contentClientHeight + 1)
  expect(metrics.canvasNonblank).toBeGreaterThan(50)
  await mkdir('../reports/webui/screenshots', { recursive: true })
  await page.screenshot({
    path: process.env.DDSP_VST_LIVE_SCREENSHOT
      ?? '../reports/webui/screenshots/ddsp-vst-effect-live-1920x969.png',
    fullPage: false,
  })
  console.log(JSON.stringify({
    available: catalog.available,
    models: catalog.models.length,
    audioInputs: catalog.audio_inputs.length,
    audioOutputs: catalog.audio_outputs.length,
    metrics,
  }))
})
