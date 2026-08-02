import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const viewports = [
  { name: 'desktop', width: 1920, height: 1080 },
  { name: 'touch', width: 1024, height: 768 },
  { name: 'mobile', width: 390, height: 844 },
]

function wavFixture(durationSeconds = 2, sampleRate = 8_000): Buffer {
  const sampleCount = durationSeconds * sampleRate
  const dataBytes = sampleCount * 2
  const wav = Buffer.alloc(44 + dataBytes)
  wav.write('RIFF', 0)
  wav.writeUInt32LE(36 + dataBytes, 4)
  wav.write('WAVEfmt ', 8)
  wav.writeUInt32LE(16, 16)
  wav.writeUInt16LE(1, 20)
  wav.writeUInt16LE(1, 22)
  wav.writeUInt32LE(sampleRate, 24)
  wav.writeUInt32LE(sampleRate * 2, 28)
  wav.writeUInt16LE(2, 32)
  wav.writeUInt16LE(16, 34)
  wav.write('data', 36)
  wav.writeUInt32LE(dataBytes, 40)
  for (let index = 0; index < sampleCount; index += 1) {
    wav.writeInt16LE(Math.round(Math.sin(index * Math.PI * 2 * 220 / sampleRate) * 3_000), 44 + index * 2)
  }
  return wav
}

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
    const [deviceTabsBox, devicePanelBox] = await Promise.all([
      page.getByRole('tablist', { name: '设备页面分区' }).boundingBox(),
      page.getByRole('tabpanel').boundingBox(),
    ])
    expect(deviceTabsBox).not.toBeNull()
    expect(devicePanelBox).not.toBeNull()
    expect(deviceTabsBox!.height).toBeLessThanOrEqual(70)
    expect(deviceTabsBox!.width).toBeGreaterThanOrEqual(devicePanelBox!.width - 1)
    expect(deviceTabsBox!.y + deviceTabsBox!.height).toBeLessThanOrEqual(devicePanelBox!.y + 1)
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

test('physical 10-inch MIDI-DDSP uses one piano-roll visualizer', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 969 })
  await page.goto('/')
  await page.getByRole('button', { name: 'MIDI-DDSP' }).first().click()
  await expect(page.getByRole('heading', { name: /MIDI-DDSP (音频库|新建渲染)/ })).toBeVisible()
  const roll = page.getByRole('region', { name: 'MIDI 文件钢琴卷帘' })
  await expect(roll).toBeVisible()
  await expect(roll.locator('.midi-file-roll-title span')).toHaveText(/\d+ 音符 · \d+ 声部/, { timeout: 15_000 })
  await expect(roll.locator('canvas')).toHaveCount(3)
  await expect(page.locator('.audio-preview, .waveform')).toHaveCount(0)
  await expect(roll).toBeInViewport()
  const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(horizontalOverflow).toBeLessThanOrEqual(1)
  await page.screenshot({
    path: '../reports/webui/screenshots/midi-ddsp-touch-1920x969.png',
    fullPage: false,
  })
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
      await page.getByTitle('展开卷帘').click()
      const lastVoice = page.locator('.voice-assignment-row:not(.voice-assignment-header)').last()
      const roll = page.getByRole('img', { name: 'MIDI 文件音符时间轴' })
      const [lastVoiceBox, rollBox] = await Promise.all([lastVoice.boundingBox(), roll.boundingBox()])
      expect(lastVoiceBox).not.toBeNull()
      expect(rollBox).not.toBeNull()
      expect(rollBox!.y + rollBox!.height).toBeLessThanOrEqual(lastVoiceBox!.y + 1)
    }
    await expect(page.locator('.audio-preview')).toHaveCount(0)
    await page.screenshot({
      path: `../reports/webui/screenshots/midi-progress-${viewport.name}.png`,
      fullPage: true,
    })
  })
}

test('generated WAV files can be selected and sent to the board output', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 969 })
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
            metadata: { midi_id: 'midi-new', midi_name: 'canon.mid', instrument_id: 0, report: { duration_seconds: 245 } },
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
            metadata: { midi_id: 'midi-old', midi_name: 'ddsp-test.mid', instrument_id: 4, report: { duration_seconds: 35 } },
            artifacts: [{ id: 'old-recording--output.wav', name: 'output.wav', size_bytes: 1024 }],
          },
        ],
      }),
    })
  })
  await page.route('**/api/v1/midi-ddsp/library', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ tracks: [] }) })
  })
  await page.route('**/api/v1/midi-files/*/piano-roll', async (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      midi_id: 'midi-fixture',
      midi_sha256: 'a'.repeat(64),
      midi_name: 'fixture.mid',
      duration_seconds: 2,
      note_count: 3,
      pitch_min: 60,
      pitch_max: 67,
      timing: {
        ticks_per_beat: 480,
        tempo_changes: [{ tick: 0, time_seconds: 0, bpm: 120 }],
        time_signatures: [{ tick: 0, time_seconds: 0, numerator: 4, denominator: 4 }],
      },
      voices: [{
        id: 'voice-1', track_index: 0, track_name: 'Piano', channel: 1, program: 0,
        suggested_instrument_id: 0,
        notes: [
          { start_seconds: 0, duration_seconds: 0.7, pitch: 60, velocity: 90 },
          { start_seconds: 0.7, duration_seconds: 0.7, pitch: 64, velocity: 90 },
          { start_seconds: 1.4, duration_seconds: 0.6, pitch: 67, velocity: 90 },
        ],
      }],
    }),
  }))
  await page.route('**/api/v1/artifacts/**', async (route) => route.fulfill({
    contentType: 'audio/wav',
    body: wavFixture(),
  }))
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
  await expect(page.getByText('3 音符 · 1 声部')).toBeVisible()
  const browserPlay = page.getByRole('button', { name: '浏览器播放', exact: true })
  await expect(browserPlay).toBeEnabled()
  await expect(page.getByRole('slider', { name: '浏览器播放位置' })).toBeEnabled()
  await page.getByRole('button', { name: '循环播放' }).click()
  await expect(page.getByRole('button', { name: '关闭循环播放' })).toHaveAttribute('aria-pressed', 'true')
  await browserPlay.click()
  await expect(page.getByText(/播放中 · 3 音符/)).toBeVisible()
  await expect(page.getByText('4×')).toBeVisible()
  await expect(page.locator('.midi-roll-transport-time')).not.toHaveText('0:00 / 0:02', { timeout: 5_000 })
  await page.screenshot({
    path: '../reports/webui/screenshots/midi-browser-transport-advanced.png',
    fullPage: false,
  })
  await page.getByRole('button', { name: '停止浏览器播放' }).click()

  await page.setViewportSize({ width: 390, height: 844 })
  const browserOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(browserOverflow).toBeLessThanOrEqual(1)
  await page.screenshot({
    path: '../reports/webui/screenshots/midi-browser-transport-advanced-mobile.png',
    fullPage: false,
  })

  await page.setViewportSize({ width: 1920, height: 969 })
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
