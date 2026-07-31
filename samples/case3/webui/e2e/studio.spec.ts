import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const viewports = [
  { name: 'desktop', width: 1920, height: 1080 },
  { name: 'touch', width: 1024, height: 768 },
  { name: 'mobile', width: 390, height: 844 },
]

for (const viewport of viewports) {
  test(`${viewport.name} workspace is stable`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.goto('/')
    await expect(page.getByRole('button', { name: 'MIDI-DDSP' }).first()).toBeVisible({ timeout: 15_000 })
    await page.getByRole('button', { name: 'MIDI 键盘' }).first().click()
    await expect(page.getByRole('region', { name: 'MIDI 键盘实时演奏' })).toBeVisible()
    await expect(page.getByRole('img', { name: '动态钢琴卷帘' })).toBeVisible()
    await page.getByRole('button', { name: '触控演奏' }).first().click()
    await expect(page.getByRole('region', { name: '触控实时演奏' })).toBeVisible()
    await expect(page.locator('.realtime-stage .piano')).toBeVisible()

    const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(horizontalOverflow).toBeLessThanOrEqual(1)

    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({
      path: `../reports/webui/screenshots/studio-${viewport.name}.png`,
      fullPage: true,
    })

    await page.getByRole('button', { name: '设备' }).first().click()
    await expect(page.getByRole('heading', { name: '系统与设备' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '扬声器输出测试' })).toBeVisible()
    const devicesOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(devicesOverflow).toBeLessThanOrEqual(1)
    await page.screenshot({
      path: `../reports/webui/screenshots/devices-${viewport.name}.png`,
      fullPage: true,
    })
  })
}

test('all workspaces can be opened', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: 'MIDI-DDSP' }).first().click()
  await expect(page.getByRole('heading', { name: /MIDI-DDSP (音频库|新建渲染)/ })).toBeVisible()
  await page.getByRole('button', { name: '新建渲染' }).click()
  await expect(page.getByLabel('MIDI 声部音色分配')).toBeVisible()
  await expect(page.getByLabel('声部 1 音色')).toBeVisible({ timeout: 15_000 })
  await page.screenshot({ path: '../reports/webui/screenshots/studio-midi-ddsp.png', fullPage: true })
  await page.getByRole('button', { name: 'MIDI 键盘' }).first().click()
  await expect(page.getByRole('region', { name: 'MIDI 键盘实时演奏' })).toBeVisible()
  await expect(page.getByRole('tab', { name: /神经音色/ })).toHaveCount(0)
  await page.screenshot({ path: '../reports/webui/screenshots/studio-midi-keyboard.png', fullPage: true })
  await page.getByRole('button', { name: '触控演奏' }).first().click()
  await expect(page.getByRole('region', { name: '触控实时演奏' })).toBeVisible()
  await page.screenshot({ path: '../reports/webui/screenshots/studio-touch-performance.png', fullPage: true })
  await page.getByRole('button', { name: '设备' }).first().click()
  await expect(page.getByRole('heading', { name: '系统与设备' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '扬声器输出测试' })).toBeVisible()
  await expect(page.getByRole('button', { name: '扬声器' })).toHaveCount(0)
  await page.screenshot({ path: '../reports/webui/screenshots/studio-devices.png', fullPage: true })
})

test('MIDI-DDSP render controls fit a 1366x768 desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto('/')
  await page.getByRole('button', { name: 'MIDI-DDSP' }).first().click()
  await page.getByRole('button', { name: '新建渲染' }).click()
  await expect(page.getByLabel('MIDI 声部音色分配')).toBeVisible()
  await expect(page.getByLabel('声部 1 音色')).toBeVisible({ timeout: 15_000 })
  const startButton = page.getByTitle('开始渲染')
  await expect(startButton).toBeVisible()
  await expect(startButton).toBeInViewport()
  await expect(page.locator('.midi-settings')).toBeInViewport()
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(horizontalOverflow).toBeLessThanOrEqual(1)
  await page.screenshot({ path: '../reports/webui/screenshots/midi-render-1366x768.png', fullPage: true })
})

for (const viewport of [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]) {
  test(`MIDI-DDSP progress stays readable on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.addInitScript(() => {
      class SilentWebSocket {
        onmessage: ((event: MessageEvent) => void) | null = null
        close() {}
      }
      Object.defineProperty(window, 'WebSocket', { value: SilentWebSocket })
    })
    const now = Date.now() / 1000
    await page.route('**/api/v1/jobs', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          jobs: [{
            id: 'progress-job',
            kind: 'midi-ddsp-play',
            state: 'running',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            progress: 0.43,
            progress_detail: {
              stage: 'pitch_context',
              stage_progress: 0.61,
              overall_progress: 0.43,
              completed: 79,
              total: 129,
              voice_batch_index: 1,
              voice_batch_count: 1,
              elapsed_seconds: 132,
              eta_seconds: 174,
              heartbeat_at: now,
            },
            message: '',
            exit_code: null,
            metadata: {},
            artifacts: [
              { id: 'progress-job--stem.wav', name: 'stem.wav', size_bytes: 10 },
              { id: 'progress-job--output.wav', name: 'output.wav', size_bytes: 20 },
            ],
          }],
        }),
      })
    })
    await page.route('**/api/v1/artifacts/**', async (route) => route.fulfill({ body: '' }))
    await page.goto('/')
    await page.getByRole('button', { name: 'MIDI-DDSP' }).first().click()
    await expect(page.getByLabel('MIDI-DDSP 渲染进度')).toBeVisible()
    await expect(page.getByLabel('MIDI 声部音色分配')).toBeVisible()
    await expect(page.getByLabel('声部 1 音色')).toBeVisible()
    await expect(page.getByText('音高与上下文', { exact: true }).last()).toBeVisible()
    await expect(page.getByText('61%', { exact: true })).toBeVisible()
    await expect(page.getByTitle('暂停')).toHaveCount(0)
    await expect(page.getByTitle('停止')).toBeVisible()
    await expect(page.getByTitle('下载 WAV')).toHaveAttribute('href', /output\.wav/)
    const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(horizontalOverflow).toBeLessThanOrEqual(1)
    if (viewport.name === 'mobile') {
      const lastVoice = page.locator('.voice-assignment-row:not(.voice-assignment-header)').last()
      const waveform = page.locator('.audio-preview').first()
      const [lastVoiceBox, waveformBox] = await Promise.all([
        lastVoice.boundingBox(),
        waveform.boundingBox(),
      ])
      expect(lastVoiceBox).not.toBeNull()
      expect(waveformBox).not.toBeNull()
      expect(waveformBox!.y).toBeGreaterThanOrEqual(lastVoiceBox!.y + lastVoiceBox!.height - 1)
    }
    await page.screenshot({
      path: `../reports/webui/screenshots/midi-progress-${viewport.name}.png`,
      fullPage: true,
    })
  })
}

test('generated WAV files can be selected and sent to the board output', async ({ page }) => {
  const now = new Date().toISOString()
  await page.route('**/api/v1/midi-ddsp/audio-devices', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        devices: [{
          id: 'alsa:onboard-headset',
          index: 0,
          name: '板载 3.5 mm',
          host_api: 'ALSA aplay',
          backend: 'alsa_mono',
          max_output_channels: 1,
          default_sample_rate: 48000,
          is_default: true,
          is_mono: true,
        }],
        error: null,
      }),
    })
  })
  await page.route('**/api/v1/jobs', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        jobs: [
          {
            id: 'new-recording',
            kind: 'midi-ddsp-play',
            state: 'succeeded',
            created_at: now,
            updated_at: now,
            progress: 1,
            message: '',
            exit_code: 0,
            metadata: { midi_name: 'canon.mid', instrument_id: 0, report: { duration_seconds: 245 } },
            artifacts: [{ id: 'new-recording--output.wav', name: 'output.wav', size_bytes: 2048 }],
          },
          {
            id: 'old-recording',
            kind: 'midi-ddsp-render',
            state: 'succeeded',
            created_at: now,
            updated_at: now,
            progress: 1,
            message: '',
            exit_code: 0,
            metadata: { midi_name: 'ddsp-test.mid', instrument_id: 4, report: { duration_seconds: 35 } },
            artifacts: [{ id: 'old-recording--output.wav', name: 'output.wav', size_bytes: 1024 }],
          },
        ],
      }),
    })
  })
  await page.route('**/api/v1/artifacts/**', async (route) => route.fulfill({ body: '' }))
  let replayRequest: { jobId: string; body: Record<string, unknown> } | null = null
  await page.route('**/api/v1/midi-ddsp/recordings/*/play', async (route) => {
    const match = route.request().url().match(/recordings\/([^/]+)\/play/)
    replayRequest = {
      jobId: match?.[1] ?? '',
      body: route.request().postDataJSON(),
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'replay-job', kind: 'midi-ddsp-wav-playback', state: 'queued',
        created_at: now, updated_at: now, progress: 0, message: '', exit_code: null,
        metadata: { source_job_id: match?.[1] }, artifacts: [],
      }),
    })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'MIDI-DDSP' }).first().click()
  await expect(page.getByRole('heading', { name: 'MIDI-DDSP 音频库' })).toBeVisible()
  await expect(page.locator('audio')).toHaveCount(0)
  await page.getByRole('button', { name: '当前浏览器' }).click()
  await expect(page.locator('audio')).toHaveAttribute('src', /new-recording--output\.wav/)
  await page.getByRole('button', { name: /ddsp-test\.mid/ }).click()
  await expect(page.locator('audio')).toHaveAttribute('src', /old-recording--output\.wav/)
  await expect(page.getByRole('button', { name: '开发板播放' })).toHaveCount(0)
  await page.getByRole('button', { name: '开发板喇叭' }).click()
  await page.getByRole('button', { name: '开发板播放' }).click()
  await expect.poll(() => replayRequest?.jobId).toBe('old-recording')
  expect(replayRequest?.body).toMatchObject({
    audio_device_id: 'alsa:onboard-headset',
    latency_ms: 40,
    output_gain_db: 0,
  })
  await page.screenshot({
    path: '../reports/webui/screenshots/midi-audio-library-desktop.png',
    fullPage: true,
  })

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { name: 'MIDI-DDSP 音频库' })).toBeVisible()
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(horizontalOverflow).toBeLessThanOrEqual(1)
  await page.screenshot({
    path: '../reports/webui/screenshots/midi-audio-library-mobile.png',
    fullPage: true,
  })
})
