import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const pianoCatalog = {
  release: 'model-suite-v1.0.0',
  source_commit: '1f7cf65ff9c58968bc3b605ee571db928d1ac37a',
  hf_commit: '2199df0a55953a0d2469d59ab2f23a8bef8eb314',
  active_bundle_id: 'model-suite-v1.0.0-gru-unrolled-fp32-origin',
  bundles: [{
    id: 'model-suite-v1.0.0-gru-unrolled-fp32-origin', release: 'model-suite-v1.0.0',
    precision: 'FP32', soc_version: 'Ascend310B4', complete: true,
    models: ['paper_ir', 'film_fdn', 'calibrated_ir', 'calibrated_film_ir'],
  }],
  models: [
    { id: 'paper_ir', name: 'Paper IR', architecture: 'paper', quality_status: 'quality_selection_pending', available: true, bundle_ids: ['model-suite-v1.0.0-gru-unrolled-fp32-origin'], n_harmonics: 96, n_noise_bands: 64, reverb_type: 'ir' },
    { id: 'film_fdn', name: 'FiLM FDN', architecture: 'configurable', quality_status: 'quality_selection_pending', available: true, bundle_ids: ['model-suite-v1.0.0-gru-unrolled-fp32-origin'], n_harmonics: 128, n_noise_bands: 96, reverb_type: 'fdn' },
    { id: 'calibrated_ir', name: 'Calibrated IR', architecture: 'configurable', quality_status: 'quality_selection_pending', available: true, bundle_ids: ['model-suite-v1.0.0-gru-unrolled-fp32-origin'], n_harmonics: 96, n_noise_bands: 64, reverb_type: 'ir' },
    { id: 'calibrated_film_ir', name: 'Calibrated FiLM IR', architecture: 'configurable', quality_status: 'quality_selection_pending', available: true, bundle_ids: ['model-suite-v1.0.0-gru-unrolled-fp32-origin'], n_harmonics: 96, n_noise_bands: 64, reverb_type: 'ir' },
  ],
  piano_years: [2004, 2006, 2008, 2009, 2011, 2013, 2014, 2015, 2017, 2018],
  io_contract: { dtype: 'FP32', max_polyphony: 16, frame_rate: 250 },
  latency_profiles: {
    low: { frames: 4, prebuffer_blocks: 1, audio_latency_ms: 15 },
    balanced: { frames: 8, prebuffer_blocks: 1, audio_latency_ms: 20 },
    safe: { frames: 16, prebuffer_blocks: 2, audio_latency_ms: 40 },
  },
  errors: [],
}

async function installPianoWebSocket(page: Page) {
  await page.addInitScript(() => {
    type Handler = ((event: { data?: string }) => void) | null
    class PianoSocket {
      static OPEN = 1
      readyState = 1
      url: string
      onopen: Handler = null
      onclose: Handler = null
      onmessage: Handler = null
      sent: string[] = []

      constructor(url: string) {
        this.url = String(url)
        const sockets = ((globalThis as unknown as { __testSockets?: PianoSocket[] }).__testSockets ??= [])
        sockets.push(this)
        setTimeout(() => this.onopen?.({}), 0)
      }

      send(payload: string) { this.sent.push(payload) }
      close() { this.readyState = 3; this.onclose?.({}) }
      emit(payload: object) { this.onmessage?.({ data: JSON.stringify(payload) }) }
    }
    Object.defineProperty(window, 'WebSocket', { value: PianoSocket })
  })
}

async function openPiano(page: Page) {
  await installPianoWebSocket(page)
  await page.route('**/api/v1/piano-ddsp/catalog', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(pianoCatalog) })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'Piano-DDSP' }).first().click()
  await expect(page.getByRole('heading', { name: '88 键实时演奏' })).toBeVisible()
}

for (const viewport of [
  { name: '1366x768', width: 1366, height: 768 },
  { name: '390x844', width: 390, height: 844 },
]) {
  test(`Piano-DDSP is stable at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await openPiano(page)
    await expect(page.getByLabel('88 键概览')).toBeVisible()
    await expect(page.getByText('当前没有可用的 Piano-DDSP FP32 OM bundle')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)

    const incoherentOverlap = await page.locator('.piano-session-bar').evaluate((bar) => {
      const children = Array.from(bar.children).map((child) => child.getBoundingClientRect())
      if (children.length < 2) return false
      return children[0].right > children[1].left + 1 && children[0].bottom > children[1].top + 1
    })
    expect(incoherentOverlap).toBe(false)

    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({
      path: `../reports/webui/screenshots/piano-ddsp-${viewport.name}.png`,
      fullPage: true,
    })
  })
}

test('Piano-DDSP reports switching, device loss, and socket timeout states', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await openPiano(page)
  await page.evaluate(() => {
    const sockets = (globalThis as unknown as { __testSockets: Array<{ url: string; emit: (value: object) => void }> }).__testSockets
    sockets.find((socket) => socket.url.includes('/piano-ddsp/events'))?.emit({
      event: 'status',
      data: {
        state: 'running', running: true,
        midi: { connected: true, active_notes: [], slot_notes: [], pedal: [0, 0, 0, 0], voice_steals: 0, last_velocity: 0 },
        audio: { device_lost: false },
      },
    })
  })
  await page.getByLabel('模型').selectOption('film_fdn')
  const parameterMessage = await page.evaluate(() => {
    const sockets = (globalThis as unknown as { __testSockets: Array<{ url: string; sent: string[] }> }).__testSockets
    return sockets.find((socket) => socket.url.includes('/piano-ddsp/events'))?.sent
      .map((item) => JSON.parse(item))
      .find((item) => item.event === 'parameters')
  })
  expect(parameterMessage).toMatchObject({ values: { model_id: 'film_fdn' } })

  await page.evaluate(() => {
    const sockets = (globalThis as unknown as { __testSockets: Array<{ url: string; emit: (value: object) => void; close: () => void }> }).__testSockets
    const socket = sockets.find((item) => item.url.includes('/piano-ddsp/events'))
    socket?.emit({ event: 'status', data: { state: 'switching', running: true, audio: { device_lost: true, error: 'USB output disconnected' } } })
  })
  await expect(page.getByText('SWITCHING')).toBeVisible()
  await expect(page.getByText('音频设备丢失')).toBeVisible()

  await page.evaluate(() => {
    const sockets = (globalThis as unknown as { __testSockets: Array<{ url: string; close: () => void }> }).__testSockets
    sockets.find((socket) => socket.url.includes('/piano-ddsp/events'))?.close()
  })
  await expect(page.getByText('offline', { exact: true })).toBeVisible()
  await expect(page.getByText('online', { exact: true })).toBeVisible({ timeout: 2500 })
})
