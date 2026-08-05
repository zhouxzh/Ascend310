import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const models = Array.from({ length: 11 }, (_, index) => ({
  id: `effect-${index}`,
  name: `effect-${index}.om`,
  instrument: index === 0 ? 'Violin' : `Tone ${index + 1}`,
  backend: 'om',
  precision: 'mixed_float16',
  size_bytes: 2048,
  pitch_min_hz: 180,
  pitch_max_hz: 720,
  power_min_db: -60,
  power_max_db: -20,
}))

const parameters = Object.fromEntries([
  ['transpose', -24, 24, 0], ['input_pitch', -0.5, 0.5, 0],
  ['input_gain', -0.5, 0.5, 0], ['harmonic_gain', 0, 1, 1],
  ['noise_gain', 0, 1, 1], ['output_gain_db', -60, 6, -18],
  ['reverb_size', 0, 1, 0.4], ['reverb_damping', 0, 1, 0.1],
  ['reverb_wet', 0, 1, 0],
  ['gate_threshold_dbfs', -80, -20, -40], ['gate_hysteresis_db', 0, 18, 6],
  ['gate_hold_ms', 0, 1000, 160], ['gate_attack_ms', 1, 200, 10],
  ['gate_release_ms', 20, 2000, 180],
].map(([name, min, max, defaultValue]) => [name, { min, max, default: defaultValue }]))

const baseStatus = {
  state: 'stopped', running: false, error: null, backend: 'acl/om',
  feature_backend: 'acl/om', control_backend: 'acl/om',
  feature_model: 'ddsp_vst_feature_mixed_float16.om', config: {}, hashes: {},
  parameters: Object.fromEntries(Object.entries(parameters).map(([name, value]) => [name, value.default])),
  metrics: {
    frames: 0, f0_hz: 0, pw_db: -96, input_rms_dbfs: -96, input_peak_dbfs: -96,
    output_rms_dbfs: -96, output_peak_dbfs: -96, feature_ms: 0, feature_p95_ms: 0,
    control_ms: 0, control_p95_ms: 0, queue_latency_ms: 0, total_latency_ms: 0,
    capture_overflows: 0, playback_underruns: 0, clipped_samples: 0, safety_muted: false,
    gate_open: false, gate_gain: 0, gate_threshold_dbfs: -40,
    gate_close_threshold_dbfs: -46, gate_hold_frames: 0, gated_frames: 0,
    noise_floor_dbfs: -96, calibrating: false, calibration_progress: 0,
  },
}

for (const viewport of [
  { name: 'board-1920x969', width: 1920, height: 969, touch: true },
  { name: 'desktop-1366x768', width: 1366, height: 768, touch: false },
  { name: 'tablet-1024x768', width: 1024, height: 768, touch: true },
  { name: 'mobile-390x844', width: 390, height: 844, touch: true },
]) {
  test(`DDSP-VST workspace is usable at ${viewport.name}`, async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      hasTouch: viewport.touch,
    })
    const page = await context.newPage()
    try {
      await page.addInitScript(() => {
        class QuietWebSocket {
          onmessage: ((event: MessageEvent) => void) | null = null
          constructor(_url: string) {}
          close() {}
        }
        Object.defineProperty(window, 'WebSocket', { configurable: true, value: QuietWebSocket })
      })
      await page.route('**/api/v1/ddsp-vst-effect/catalog', (route) => route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          available: true, error: null, backend: 'acl/om',
          feature_model: { name: 'ddsp_vst_feature_mixed_float16.om', sha256: 'a'.repeat(64), available: true },
          models,
          audio_inputs: [{
            id: 'pulse:ugreen', index: 1, name: 'UGREEN Camera 1080P', host_api: 'PulseAudio',
            backend: 'pulse', type: 'capture', max_input_channels: 2,
            default_sample_rate: 48000, state: 'running', available: true,
          }],
          audio_outputs: [{
            id: 'pulse:edifier', index: 2, name: 'EDIFIER M16 Pro', host_api: 'PulseAudio',
            backend: 'pulse', max_output_channels: 2, default_sample_rate: 48000,
          }],
          default_model_id: 'effect-0', default_audio_input_id: 'pulse:ugreen',
          default_audio_output_id: 'pulse:edifier', parameters,
        }),
      }))
      await page.route('**/api/v1/ddsp-vst-effect/catalog/refresh', (route) => route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          available: true, error: null, backend: 'acl/om',
          feature_model: { name: 'ddsp_vst_feature_mixed_float16.om', sha256: 'a'.repeat(64), available: true },
          models,
          audio_inputs: [{
            id: 'pulse:ugreen', index: 1, name: 'UGREEN Camera 1080P', host_api: 'PulseAudio',
            backend: 'pulse', type: 'capture', max_input_channels: 2,
            default_sample_rate: 48000, state: 'running', available: true,
          }],
          audio_outputs: [{
            id: 'pulse:edifier', index: 2, name: 'EDIFIER M16 Pro', host_api: 'PulseAudio',
            backend: 'pulse', max_output_channels: 2, default_sample_rate: 48000,
          }],
          default_model_id: 'effect-0', default_audio_input_id: 'pulse:ugreen',
          default_audio_output_id: 'pulse:edifier', parameters,
        }),
      }))
      await page.route('**/api/v1/ddsp-vst-effect/status', (route) => route.fulfill({
        contentType: 'application/json', body: JSON.stringify(baseStatus),
      }))
      await page.route('**/api/v1/ddsp-vst-effect/start', (route) => route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          ...baseStatus, state: 'running', running: true,
          config: {
            model_id: 'effect-0', audio_input_id: 'pulse:ugreen', audio_output_id: 'pulse:edifier',
            input_device_name: 'UGREEN Camera 1080P', output_device_name: 'EDIFIER M16 Pro',
          },
          metrics: { ...baseStatus.metrics, frames: 16, f0_hz: 220, input_rms_dbfs: -24, total_latency_ms: 128 },
        }),
      }))
      await page.goto('/')
      await expect(page.locator('.primary-nav button')).toHaveCount(4)
      await page.getByRole('button', { name: 'DDSP-VST' }).first().click()
      await expect(page.getByRole('heading', { name: 'DDSP-VST' })).toBeVisible()
      await expect(page.getByLabel('DDSP-VST 音色').locator('option:checked')).toHaveText('小提琴 · 混合半精度')
      await expect(page.getByRole('option')).toHaveCount(13)
      const canvas = page.getByRole('img', { name: 'DDSP-VST 音高与响度轨迹' })
      await expect(canvas).toBeVisible()
      await expect.poll(() => canvas.evaluate((element) => {
        const target = element as HTMLCanvasElement
        const context2d = target.getContext('2d')
        if (!context2d || target.width === 0 || target.height === 0) return 0
        const pixels = context2d.getImageData(0, 0, target.width, target.height).data
        let nonblank = 0
        for (let index = 3; index < pixels.length; index += 64) {
          if (pixels[index] > 0) nonblank += 1
        }
        return nonblank
      })).toBeGreaterThan(50)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      expect(overflow).toBeLessThanOrEqual(1)
      if (viewport.name === 'board-1920x969') {
        await page.getByRole('button', { name: '刷新已发布 OM 音色' }).click()
        const canvasBox = await canvas.boundingBox()
        expect(canvasBox).not.toBeNull()
        if (canvasBox) {
          await page.mouse.move(canvasBox.x + canvasBox.width * 0.51, canvasBox.y + canvasBox.height * 0.5)
          await page.mouse.down()
          await page.mouse.move(canvasBox.x + canvasBox.width * 0.56, canvasBox.y + canvasBox.height * 0.44)
          await page.mouse.up()
          expect(Number(await page.getByRole('slider', { name: '音高校准' }).inputValue())).toBeGreaterThan(0)
          expect(Number(await page.getByRole('slider', { name: '力度校准' }).inputValue())).toBeGreaterThan(0)
        }
        const fit = await page.evaluate(() => {
          const content = document.querySelector<HTMLElement>('.content-area')
          const start = document.querySelector<HTMLElement>('.effect-run-button')
          return {
            scrollHeight: content?.scrollHeight ?? 0,
            clientHeight: content?.clientHeight ?? 0,
            startHeight: start?.getBoundingClientRect().height ?? 0,
          }
        })
        expect(fit.scrollHeight).toBeLessThanOrEqual(fit.clientHeight + 1)
        expect(fit.startHeight).toBeGreaterThanOrEqual(52)
      }
      await page.getByRole('button', { name: '启动' }).click()
      await expect(page.getByRole('button', { name: '停止' })).toBeVisible()
      await mkdir('../reports/webui/screenshots', { recursive: true })
      await page.screenshot({
        path: `../reports/webui/screenshots/ddsp-vst-effect-${viewport.name}.png`,
        fullPage: false,
      })
    } finally {
      await context.close()
    }
  })
}
