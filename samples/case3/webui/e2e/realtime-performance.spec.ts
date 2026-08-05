import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const pianoPatch = {
  patch_id: 'piano.paper-ir', name: 'Concert Grand', category: 'piano', available: true,
  pitch_min: 21, pitch_max: 108, polyphony: 16, compatible_audio_device_ids: ['usb-audio'],
  parameters: {
    velocity_curve: { min: 0.25, max: 2, default: 1 },
    transpose: { min: -24, max: 24, default: 0 },
    output_gain_db: { min: -60, max: 6, default: 0 },
    reverb: { min: 0, max: 1, default: 1 },
    piano_year: { options: [2017, 2018], default: 2018 },
  },
  details: { engine: 'piano-ddsp', architecture: 'gru-unrolled' },
}
const violinPatch = {
  patch_id: 'neural.violin', name: 'Violin', category: 'strings', available: true,
  pitch_min: 55, pitch_max: 88, polyphony: 4, compatible_audio_device_ids: ['usb-audio'],
  parameters: {
    velocity_curve: { min: 0.25, max: 2, default: 0.55 },
    transpose: { min: -24, max: 24, default: 0 },
    output_gain_db: { min: -60, max: 6, default: 0 },
    reverb: { min: 0, max: 1, default: 0.15 },
    harmonic_gain: { min: 0, max: 1, default: 1 },
    noise_gain: { min: 0, max: 1, default: 1 },
  },
  details: { engine: 'ddsp-vst', precision: 'origin' },
}
const realtimeCatalog = {
  schema_version: 1,
  patches: [pianoPatch, violinPatch],
  audio_devices: [{
    id: 'usb-audio', index: 2, name: 'EDIFIER M16 Pro', host_api: 'ALSA', backend: 'portaudio',
    max_output_channels: 2, default_sample_rate: 48000, is_default: true,
    compatible_patch_ids: [pianoPatch.patch_id, violinPatch.patch_id],
  }],
  midi_ports: [], midi_error: null,
  midi_files: [{
    id: 'midi-demo', name: 'demo.mid', size_bytes: 32, uploaded: false, note_count: 4,
    track_count: 1, max_polyphony: 1, voice_count: 1, duration_seconds: 60,
    monophonic: true, midi_ddsp_mode: 'monophonic', midi_ddsp_supported: true,
    unsupported_code: null, unsupported_reason: null, programs: [], tracks: [],
  }],
  latency_profiles: ['low', 'balanced', 'safe'],
}
const stopped = {
  state: 'stopped', running: false, patch_id: null, patch: null, active_notes: [],
  recording: { active: false }, metrics: { midi_to_pcm_p95_ms: 18.4, npu_p95_ms: 11.3 }, diagnostics: {},
}

async function installRealtimeWebSocket(page: Page) {
  await page.addInitScript(() => {
    type Handler = ((event: { data?: string }) => void) | null
    class RealtimeSocket {
      static OPEN = 1
      readyState = 1
      url: string
      onopen: Handler = null
      onclose: Handler = null
      onmessage: Handler = null
      sent: string[] = []

      constructor(url: string) {
        this.url = String(url)
        const sockets = ((globalThis as unknown as { __testSockets?: RealtimeSocket[] }).__testSockets ??= [])
        sockets.push(this)
        setTimeout(() => this.onopen?.({}), 0)
      }

      send(payload: string) { this.sent.push(payload) }
      close() { this.readyState = 3 }
      emit(payload: object) { this.onmessage?.({ data: JSON.stringify(payload) }) }
    }
    Object.defineProperty(window, 'WebSocket', { value: RealtimeSocket })
  })
}

async function openRealtimeStage(page: Page, workspace: '触控演奏' | 'MIDI 键盘' = 'MIDI 键盘') {
  await installRealtimeWebSocket(page)
  await page.route('**/api/v1/realtime/catalog', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(realtimeCatalog) }))
  await page.route('**/api/v1/realtime/status', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(stopped) }))
  await page.route('**/api/v1/realtime/start', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ ...stopped, state: 'running', running: true, patch_id: pianoPatch.patch_id, patch: pianoPatch, audio_device_id: 'usb-audio' }),
  }))
  await page.route('**/api/v1/realtime/switch', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ ...stopped, state: 'running', running: true, patch_id: violinPatch.patch_id, patch: violinPatch, audio_device_id: 'usb-audio', last_switch: { ok: true, rolled_back: false, duration_ms: 238 } }),
  }))
  await page.goto('/')
  await page.getByRole('button', { name: '实时演奏' }).first().click()
  const mode = page.getByRole('tab', { name: workspace === '触控演奏' ? '触摸屏' : 'MIDI 键盘', exact: true })
  if (await mode.getAttribute('aria-selected') !== 'true') await mode.click()
  await expect(page.getByRole('region', { name: workspace === '触控演奏' ? '触控实时演奏' : 'MIDI 键盘实时演奏' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '当前音色' })).toBeVisible()
}

for (const viewport of [
  { name: '1366x768', width: 1366, height: 768 },
  { name: '1024x600', width: 1024, height: 600 },
  { name: '390x844', width: 390, height: 844 },
]) {
  test(`MIDI keyboard workspace is stable at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await openRealtimeStage(page)

    const visualizerKeyboard = page.getByRole('img', { name: '32 键钢琴可视化' })
    const roll = page.locator('.realtime-stage--midi .live-piano-roll')
    await expect(visualizerKeyboard).toHaveAttribute('data-key-count', '32')
    await expect(page.getByRole('button', { name: '使用 32 键' })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByLabel('实体 MIDI 输入')).toBeVisible()
    await expect(roll).toBeVisible()
    await expect(page.getByRole('slider', { name: '输出增益' })).toHaveValue('0')
    await expect(page.getByText('0.0 dB')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)

    await expect(page.getByRole('combobox', { name: '当前音色' }).locator('option')).toHaveCount(1)
    await expect(page.getByRole('option', { name: 'Violin' })).toHaveCount(0)

    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({
      path: `../reports/webui/screenshots/realtime-midi-keyboard-${viewport.name}.png`,
      fullPage: true,
    })

    if (viewport.width <= 600) {
      const recordButton = page.getByRole('button', { name: '录音' })
      await recordButton.scrollIntoViewIfNeeded()
      await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' }))
      await expect(recordButton).toBeVisible()
      const [recordBox, navBox] = await Promise.all([
        recordButton.boundingBox(),
        page.locator('.bottom-nav').boundingBox(),
      ])
      expect(recordBox).not.toBeNull()
      expect(navBox).not.toBeNull()
      expect(recordBox!.y + recordBox!.height).toBeLessThanOrEqual(navBox!.y + 1)
    }

  })
}

test('switching realtime input modes reuses the loaded Piano-DDSP catalog', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 969 })
  await installRealtimeWebSocket(page)
  let catalogRequests = 0
  await page.route('**/api/v1/realtime/catalog', async (route) => {
    catalogRequests += 1
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(realtimeCatalog) })
  })
  await page.route('**/api/v1/realtime/status', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify(stopped),
  }))

  await page.goto('/')
  await expect(page.getByRole('region', { name: '触控实时演奏' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '当前音色' })).toHaveValue('piano.paper-ir')
  expect(catalogRequests).toBe(1)

  await page.getByRole('tab', { name: 'MIDI 键盘', exact: true }).click()
  await expect(page.getByRole('region', { name: 'MIDI 键盘实时演奏' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '当前音色' })).toHaveValue('piano.paper-ir', { timeout: 500 })
  expect(catalogRequests).toBe(1)
  await expect(page.getByText('正在加载统一音色库')).toHaveCount(0)
})

test('10-inch touch layout opens a readable two-octave performance keyboard by default', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, hasTouch: true })
  const page = await context.newPage()
  try {
    await openRealtimeStage(page, '触控演奏')

    await expect(page.getByRole('button', { name: '使用 25 键' })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('.realtime-stage .piano')).toHaveAttribute('data-key-count', '25')
    await expect(page.getByRole('img', { name: '25 键钢琴可视化' })).toHaveCount(0)
    await expect(page.getByRole('img', { name: '动态钢琴卷帘' })).toBeVisible()
    await expect(page.getByRole('button', { name: '使用 32 键' })).toHaveCount(0)
    await expect(page.getByText('C3–C5')).toBeVisible()
    const [patchFontSize, keyButtonBox, controlLabelSize, controlValueSize, controlBox] = await Promise.all([
      page.getByRole('combobox', { name: '当前音色' }).evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.getByRole('button', { name: '使用 25 键' }).boundingBox(),
      page.locator('.touch-shaping-controls label > span:first-child').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.touch-shaping-controls label > strong').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.touch-shaping-controls label').first().boundingBox(),
    ])
    expect(patchFontSize).toBeGreaterThanOrEqual(15)
    expect(keyButtonBox).not.toBeNull()
    expect(keyButtonBox!.height).toBeGreaterThanOrEqual(56)
    expect(controlLabelSize).toBeGreaterThanOrEqual(16)
    expect(controlValueSize).toBeGreaterThanOrEqual(17)
    expect(controlBox).not.toBeNull()
    expect(controlBox!.height).toBeGreaterThanOrEqual(88)

    await page.getByRole('button', { name: '向高音区移动一个八度' }).click()
    await expect(page.getByText('C4–C6')).toBeVisible()
    const piano = page.locator('.realtime-stage .piano')
    await expect(piano).toHaveAttribute('data-key-count', '25')
    await expect(piano).toHaveAttribute('data-white-key-count', '15')
    const [pianoBox, frameBox, whiteKeyBox, blackKeyBox, pianoStyleHeight, stageClass, frameBorder] = await Promise.all([
      piano.boundingBox(),
      page.locator('.keyboard-frame--touch').boundingBox(),
      piano.locator('.white-key').first().boundingBox(),
      piano.locator('.black-key').first().boundingBox(),
      piano.evaluate((element) => Number.parseFloat(getComputedStyle(element).height)),
      page.locator('.realtime-stage').getAttribute('class'),
      page.locator('.keyboard-frame--touch').evaluate((element) => getComputedStyle(element).borderBottomWidth),
    ])
    expect(pianoBox).not.toBeNull()
    expect(frameBox).not.toBeNull()
    expect(whiteKeyBox).not.toBeNull()
    expect(blackKeyBox).not.toBeNull()
    await expect(page.locator('.realtime-stage--touch .stage-drawer')).toHaveCount(0)
    await expect(page.getByRole('tab', { name: '触摸屏' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('tab', { name: 'MIDI 键盘', exact: true })).toHaveAttribute('aria-selected', 'false')
    await expect(page.locator('.status-footer')).toHaveCount(0)
    expect(stageClass).toContain('touch-keyboard-size--medium')
    expect(pianoStyleHeight).toBe(190)
    const stageBox = await page.locator('.keyboard-stage').boundingBox()
    expect(stageBox).not.toBeNull()
    expect(pianoBox!.width).toBeGreaterThanOrEqual(stageBox!.width - 48)
    expect(pianoBox!.height).toBeGreaterThanOrEqual(180)
    expect(pianoBox!.height).toBeLessThanOrEqual(310)
    expect(frameBorder).toBe('1px')
    expect(frameBox!.y + frameBox!.height).toBeGreaterThanOrEqual(788)
    expect(frameBox!.y + frameBox!.height).toBeLessThanOrEqual(796)
    expect(frameBox!.y + frameBox!.height - (pianoBox!.y + pianoBox!.height)).toBeGreaterThanOrEqual(9)
    expect(pianoBox!.height / whiteKeyBox!.width).toBeLessThan(4)
    expect(blackKeyBox!.width).toBeLessThan(whiteKeyBox!.width)
    expect(blackKeyBox!.height).toBeLessThan(pianoBox!.height)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)

    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({ path: '../reports/webui/screenshots/realtime-touch-10inch-1280x800.png', fullPage: true })
    await page.locator('.keyboard-stage').screenshot({ path: '../reports/webui/screenshots/realtime-touch-stage-10inch.png' })
    await piano.screenshot({ path: '../reports/webui/screenshots/realtime-touch-piano-25keys.png' })

    await page.getByRole('button', { name: '使用 13 键' }).click()
    await expect(page.getByText('C4–C5')).toBeVisible()
    await expect(piano).toHaveAttribute('data-key-count', '13')
    await expect(piano).toHaveAttribute('data-white-key-count', '8')
    const [largePianoBox, largeWhiteKeyBox] = await Promise.all([
      piano.boundingBox(),
      piano.locator('.white-key').first().boundingBox(),
    ])
    expect(largePianoBox).not.toBeNull()
    expect(largeWhiteKeyBox).not.toBeNull()
    expect(largeWhiteKeyBox!.width).toBeGreaterThan(140)
    expect(largePianoBox!.x).toBeLessThan(40)
    await piano.screenshot({ path: '../reports/webui/screenshots/realtime-touch-piano-13keys.png' })

    await page.setViewportSize({ width: 600, height: 400 })
    const [narrowLabelSize, narrowValueSize, narrowControlBox] = await Promise.all([
      page.locator('.touch-shaping-controls label > span:first-child').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.touch-shaping-controls label > strong').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.touch-shaping-controls label').first().boundingBox(),
    ])
    expect(narrowLabelSize).toBeGreaterThanOrEqual(16)
    expect(narrowValueSize).toBeGreaterThanOrEqual(17)
    expect(narrowControlBox).not.toBeNull()
    expect(narrowControlBox!.height).toBeGreaterThanOrEqual(88)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)
  } finally {
    await context.close()
  }
})

test('physical 10-inch touch stage keeps performance controls in the first viewport', async ({ browser }) => {
  // The 1920x1080 panel leaves a 1920x969 browser content area below XFCE and Firefox chrome.
  const context = await browser.newContext({ viewport: { width: 1920, height: 969 }, hasTouch: true })
  const page = await context.newPage()
  try {
    await openRealtimeStage(page, '触控演奏')

    const pianoSelector = page.getByRole('combobox', { name: '当前音色' })
    await expect(pianoSelector).toBeVisible()
    await expect(pianoSelector.locator('option')).toHaveCount(1)
    await expect(page.getByRole('option', { name: /小提琴/ })).toHaveCount(0)
    await expect(page.locator('.touch-patch-picker')).toHaveCount(0)

    const controls = page.locator('.touch-shaping-controls > *')
    await expect(controls).toHaveCount(6)
    const [viewport, sessionBox, pianoBox, frameBox, deckBox, keyboardBox, rollBox, labelSize, valueSize, controlBoxes] = await Promise.all([
      page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight })),
      page.locator('.realtime-stage--touch .stage-session-bar').boundingBox(),
      page.locator('.realtime-stage--touch .piano').boundingBox(),
      page.locator('.keyboard-frame--touch').boundingBox(),
      page.locator('.touch-control-deck').boundingBox(),
      page.locator('.realtime-stage--touch .keyboard-stage').boundingBox(),
      page.locator('.realtime-stage--touch .live-piano-roll').boundingBox(),
      page.locator('.touch-shaping-controls label > span:first-child').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.touch-shaping-controls label > strong').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      controls.evaluateAll((elements) => elements.map((element) => {
        const box = element.getBoundingClientRect()
        return { top: box.top, bottom: box.bottom, height: box.height }
      })),
    ])
    expect(viewport).toEqual({ width: 1920, height: 969 })
    await expect(page.locator('.status-footer')).toHaveCount(0)
    expect(sessionBox).not.toBeNull()
    expect(sessionBox!.height).toBeLessThanOrEqual(68)
    expect(pianoBox).not.toBeNull()
    expect(frameBox).not.toBeNull()
    expect(deckBox).not.toBeNull()
    await expect(page.locator('.realtime-stage--touch .stage-drawer')).toHaveCount(0)
    expect(keyboardBox).not.toBeNull()
    expect(rollBox).not.toBeNull()
    expect(labelSize).toBeGreaterThanOrEqual(16)
    expect(valueSize).toBeGreaterThanOrEqual(17)
    expect(controlBoxes.every((box) => box.height >= 88)).toBe(true)
    expect(controlBoxes.every((box) => box.bottom <= viewport.height)).toBe(true)
    expect(deckBox!.y + deckBox!.height).toBeLessThanOrEqual(keyboardBox!.y)
    expect(rollBox!.height).toBeGreaterThanOrEqual(220)
    expect(frameBox!.y + frameBox!.height).toBeGreaterThanOrEqual(viewport.height - 12)
    expect(frameBox!.y + frameBox!.height).toBeLessThanOrEqual(viewport.height - 6)
    expect(frameBox!.y + frameBox!.height - (pianoBox!.y + pianoBox!.height)).toBeGreaterThanOrEqual(9)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)
    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({ path: '../reports/webui/screenshots/realtime-touch-stage-1920x969.png', fullPage: false })
  } finally {
    await context.close()
  }
})

test('touch workbench keeps sound, routing, performance, and recording controls out of the piano roll', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1920, height: 969 }, hasTouch: true })
  const page = await context.newPage()
  try {
    await openRealtimeStage(page, '触控演奏')

    await expect(page.locator('.realtime-stage--touch .stage-drawer')).toHaveCount(0)
    await expect(page.getByRole('tab', { name: '录音监听' })).toHaveCount(0)
    await expect(page.getByRole('tab', { name: '音色参数' })).toHaveCount(0)
    await expect(page.getByRole('tab', { name: '连接设置' })).toHaveCount(0)
    await expect(page.getByRole('tab', { name: '性能' })).toHaveCount(0)
    await expect(page.getByRole('combobox', { name: '当前音色' })).toBeVisible()
    await expect(page.getByRole('combobox', { name: '音频输出' })).toBeVisible()
    await expect(page.getByRole('combobox', { name: '延时档位' })).toBeVisible()
    await expect(page.getByLabel('会话状态')).toBeVisible()
    const runtimeMetrics = page.getByLabel('实时性能')
    await expect(runtimeMetrics).toBeVisible()
    await expect(runtimeMetrics.locator(':scope > span')).toHaveCount(5)
    await expect(runtimeMetrics.getByText('按键 P95')).toBeVisible()
    await expect(runtimeMetrics.getByText('18.4 ms')).toBeVisible()
    await expect(runtimeMetrics.getByText('NPU P95')).toBeVisible()
    await expect(runtimeMetrics.getByText('监听丢弃')).toBeVisible()
    const [sessionLabelSizes, sessionValueSizes, metricLabelSizes, metricValueSizes] = await Promise.all([
      page.locator('.touch-session-field > span, .touch-session-routing label > span').evaluateAll((elements) => (
        elements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize))
      )),
      page.locator('.touch-session-field select, .touch-session-routing select').evaluateAll((elements) => (
        elements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize))
      )),
      runtimeMetrics.locator(':scope > span > span').evaluateAll((elements) => (
        elements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize))
      )),
      runtimeMetrics.locator(':scope > span > strong').evaluateAll((elements) => (
        elements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize))
      )),
    ])
    expect(sessionLabelSizes.every((size) => size >= 16)).toBe(true)
    expect(sessionValueSizes.every((size) => size >= 18)).toBe(true)
    expect(metricLabelSizes.every((size) => size >= 16)).toBe(true)
    expect(metricValueSizes.every((size) => size >= 18)).toBe(true)
    await expect(page.getByRole('button', { name: '录音' })).toBeVisible()
    await expect(page.getByRole('button', { name: '监听' })).toBeVisible()
    await expect(page.getByRole('slider', { name: '力度曲线' })).toBeVisible()
    await expect(page.getByRole('combobox', { name: '钢琴年份' })).toBeVisible()

    const [toolbarBox, rollBox, pianoBox] = await Promise.all([
      page.locator('.realtime-stage--touch .roll-toolbar').boundingBox(),
      page.locator('.realtime-stage--touch .live-piano-roll').boundingBox(),
      page.locator('.keyboard-frame--touch').boundingBox(),
    ])
    expect(toolbarBox).not.toBeNull()
    expect(rollBox).not.toBeNull()
    expect(pianoBox).not.toBeNull()
    expect(toolbarBox!.y + toolbarBox!.height).toBeLessThanOrEqual(rollBox!.y)
    expect(rollBox!.y + rollBox!.height).toBeLessThanOrEqual(pianoBox!.y)

    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({ path: '../reports/webui/screenshots/realtime-touch-compact-workbench-1920x969.png', fullPage: false })
  } finally {
    await context.close()
  }
})

test('physical 10-inch MIDI stage keeps controller controls and visualizer readable', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1920, height: 969 }, hasTouch: true })
  const page = await context.newPage()
  try {
    await openRealtimeStage(page, 'MIDI 键盘')

    const soundSelector = page.getByRole('combobox', { name: '当前音色' })
    await expect(soundSelector).toBeVisible()
    await expect(soundSelector.locator('option')).toHaveCount(1)
    await expect(page.getByRole('option', { name: 'Violin' })).toHaveCount(0)
    await expect(page.locator('.touch-control-deck')).toBeVisible()
    await expect(page.getByLabel('实时性能')).toBeVisible()
    await expect(page.getByRole('button', { name: '录音' })).toBeVisible()
    await expect(page.getByRole('button', { name: '监听' })).toBeVisible()
    await expect(page.locator('.realtime-stage--midi .stage-drawer')).toHaveCount(0)

    const [inputLabelSize, inputSelectBox, rangeBox, keyButtons, rollBox, visualizerBox, frameBox, navigationFontSize, navigationBox] = await Promise.all([
      page.locator('.midi-keyboard-port-control > span').evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.getByLabel('实体 MIDI 输入').boundingBox(),
      page.locator('.realtime-stage--midi .keyboard-range-bar').boundingBox(),
      page.locator('.realtime-stage--midi .key-count-control button').evaluateAll((elements) => elements.map((element) => element.getBoundingClientRect().toJSON())),
      page.locator('.realtime-stage--midi .live-piano-roll').boundingBox(),
      page.getByRole('img', { name: '32 键钢琴可视化' }).boundingBox(),
      page.locator('.keyboard-frame--midi').boundingBox(),
      page.locator('.primary-nav button').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.primary-nav button').first().boundingBox(),
    ])
    expect(inputLabelSize).toBeGreaterThanOrEqual(16)
    expect(inputSelectBox).not.toBeNull()
    expect(inputSelectBox!.height).toBeGreaterThanOrEqual(48)
    expect(rangeBox).not.toBeNull()
    expect(inputSelectBox!.y).toBeGreaterThanOrEqual(rangeBox!.y)
    expect(inputSelectBox!.y + inputSelectBox!.height).toBeLessThanOrEqual(rangeBox!.y + rangeBox!.height)
    expect(keyButtons.every((box) => box.height >= 56)).toBe(true)
    expect(rollBox).not.toBeNull()
    expect(rollBox!.height).toBeGreaterThanOrEqual(220)
    expect(visualizerBox).not.toBeNull()
    expect(frameBox).not.toBeNull()
    expect(visualizerBox!.height).toBeGreaterThanOrEqual(96)
    expect(frameBox!.y + frameBox!.height).toBeGreaterThanOrEqual(957)
    expect(frameBox!.y + frameBox!.height).toBeLessThanOrEqual(963)
    expect(frameBox!.y + frameBox!.height - (visualizerBox!.y + visualizerBox!.height)).toBeGreaterThanOrEqual(9)
    expect(navigationFontSize).toBeGreaterThanOrEqual(22)
    expect(navigationBox).not.toBeNull()
    expect(navigationBox!.height).toBeGreaterThanOrEqual(80)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)
    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({ path: '../reports/webui/screenshots/realtime-midi-stage-1920x969.png', fullPage: false })
  } finally {
    await context.close()
  }
})

test('unified session starts and releases notes on blur', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await openRealtimeStage(page)

  await page.getByRole('button', { name: /开始演奏/ }).click()
  await expect(page.getByText('演奏中')).toBeVisible()

  await page.evaluate(() => window.dispatchEvent(new Event('blur')))
  await expect.poll(() => page.evaluate(() => {
    const sockets = (globalThis as unknown as { __testSockets: Array<{ url: string; sent: string[] }> }).__testSockets
    return sockets
      .filter((socket) => socket.url.includes('/realtime/events'))
      .some((socket) => socket.sent
        .map((item) => JSON.parse(item))
        .some((item) => item.event === 'all_notes_off'))
  })).toBe(true)
})

test('touch keyboard sends independent note edges for two simultaneous contacts', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 969 })
  await openRealtimeStage(page, '触控演奏')
  await page.getByRole('button', { name: /开始演奏/ }).click()
  await expect(page.getByText('演奏中')).toBeVisible()
  await expect(page.getByText('已连接', { exact: true })).toBeVisible()

  const c4 = page.locator('.piano-key[data-note="60"]')
  const d4 = page.locator('.piano-key[data-note="62"]')
  const e4 = page.locator('.piano-key[data-note="64"]')
  await expect(c4).toBeEnabled()
  await expect(d4).toBeEnabled()
  await expect(e4).toBeEnabled()
  const touchStartResult = await page.locator('.piano').evaluate((piano, keys) => {
    const changedTouches = keys.map(({ selector, identifier }) => {
      const key = piano.querySelector<HTMLElement>(selector)
      if (!key) throw new Error(`Missing piano key ${selector}`)
      const bounds = key.getBoundingClientRect()
      return {
        identifier,
        clientX: bounds.x + bounds.width / 2,
        clientY: bounds.y + bounds.height * 0.8,
      }
    })
    const event = new Event('touchstart', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'changedTouches', { value: changedTouches })
    const dispatched = piano.dispatchEvent(event)
    return { dispatched, defaultPrevented: event.defaultPrevented }
  }, [
    { selector: '.piano-key[data-note="60"]', identifier: 31 },
    { selector: '.piano-key[data-note="64"]', identifier: 32 },
  ])
  expect(touchStartResult).toEqual({ dispatched: false, defaultPrevented: true })

  const sentNotes = () => page.evaluate(() => {
    const sockets = (globalThis as unknown as { __testSockets: Array<{ url: string; sent: string[] }> }).__testSockets
    return sockets
      .filter((socket) => socket.url.includes('/realtime/events'))
      .flatMap((socket) => socket.sent.map((payload) => JSON.parse(payload)))
      .filter((message) => message.event === 'note_on' || message.event === 'note_off')
      .map((message) => `${message.event}:${message.note}`)
  })
  await expect.poll(sentNotes).toEqual(['note_on:60', 'note_on:64'])

  const touchMoveResult = await page.locator('.piano').evaluate((piano) => {
    const key = piano.querySelector<HTMLElement>('.piano-key[data-note="62"]')
    if (!key) throw new Error('Missing piano key 62')
    const bounds = key.getBoundingClientRect()
    const event = new Event('touchmove', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'changedTouches', {
      value: [{ identifier: 31, clientX: bounds.x + bounds.width / 2, clientY: bounds.y + bounds.height * 0.8 }],
    })
    const dispatched = piano.dispatchEvent(event)
    return { dispatched, defaultPrevented: event.defaultPrevented }
  })
  expect(touchMoveResult).toEqual({ dispatched: false, defaultPrevented: true })
  await expect.poll(sentNotes).toEqual(['note_on:60', 'note_on:64', 'note_off:60', 'note_on:62'])
  await expect(c4).not.toHaveClass(/is-(pressed|active)/)
  await expect(d4).toHaveClass(/is-(pressed|active)/)
  await expect(e4).toHaveClass(/is-(pressed|active)/)

  const releaseTouch = (identifier: number) => page.locator('.piano').evaluate((piano, touchIdentifier) => {
    const event = new Event('touchend', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'changedTouches', { value: [{ identifier: touchIdentifier }] })
    piano.dispatchEvent(event)
  }, identifier)
  await releaseTouch(31)
  await expect.poll(sentNotes).toEqual(['note_on:60', 'note_on:64', 'note_off:60', 'note_on:62', 'note_off:62'])
  await releaseTouch(32)
  await expect.poll(sentNotes).toEqual(['note_on:60', 'note_on:64', 'note_off:60', 'note_on:62', 'note_off:62', 'note_off:64'])
})

test('live piano roll paints websocket note activity', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await openRealtimeStage(page)
  await page.getByRole('button', { name: /开始演奏/ }).click()
  await page.evaluate((status) => {
    const sockets = (globalThis as unknown as {
      __testSockets: Array<{ url: string; emit: (payload: object) => void }>
    }).__testSockets
    sockets.find((socket) => socket.url.includes('/realtime/events'))?.emit({ event: 'status', data: status })
  }, {
    ...stopped,
    state: 'running',
    running: true,
    patch_id: pianoPatch.patch_id,
    patch: pianoPatch,
    audio_device_id: 'usb-audio',
    active_notes: [60, 64, 67],
  })
  await expect.poll(async () => page.getByRole('img', { name: '动态钢琴卷帘' }).evaluate((element) => {
    const canvas = element as HTMLCanvasElement
    const context = canvas.getContext('2d')
    if (!context || canvas.width === 0 || canvas.height === 0) return 0
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
    let count = 0
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index + 2] > 120 && pixels[index + 2] > pixels[index] * 1.25) count += 1
    }
    return count
  }), { timeout: 1_000 }).toBeGreaterThan(10)
  await mkdir('../reports/webui/screenshots', { recursive: true })
  await page.screenshot({ path: '../reports/webui/screenshots/realtime-roll-active-1366x768.png', fullPage: true })
})

test('live piano roll keeps a note that starts and ends within one browser frame', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  await openRealtimeStage(page)
  await page.getByRole('button', { name: /开始演奏/ }).click()
  await page.evaluate(() => {
    const canvas = document.querySelector<HTMLCanvasElement>('.roll-trails')
    const context = canvas?.getContext('2d')
    if (!context) return
    const clearRect = context.clearRect.bind(context)
    let draws = 0
    context.clearRect = (...args: Parameters<CanvasRenderingContext2D['clearRect']>) => {
      draws += 1
      clearRect(...args)
    }
    Object.assign(globalThis, { __rollDrawCount: () => draws })
  })
  await page.evaluate(() => {
    const sockets = (globalThis as unknown as {
      __testSockets: Array<{ url: string; emit: (payload: object) => void }>
    }).__testSockets
    const socket = sockets.find((item) => item.url.includes('/realtime/events'))
    for (let index = 0; index < 12; index += 1) {
      socket?.emit({ event: 'note', note: 60 + (index % 4), on: true })
      socket?.emit({ event: 'note', note: 60 + (index % 4), on: false })
    }
  })
  await page.waitForTimeout(120)

  const paintedPixels = await page.getByRole('img', { name: '动态钢琴卷帘' }).evaluate((element) => {
    const canvas = element as HTMLCanvasElement
    const context = canvas.getContext('2d')
    if (!context || canvas.width === 0 || canvas.height === 0) return 0
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
    let count = 0
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index + 2] > 120 && pixels[index + 2] > pixels[index] * 1.25) count += 1
    }
    return count
  })
  expect(paintedPixels).toBeGreaterThan(10)
  const drawCount = await page.evaluate(() => (
    (globalThis as unknown as { __rollDrawCount?: () => number }).__rollDrawCount?.() ?? 0
  ))
  expect(drawCount).toBeLessThanOrEqual(6)
})
