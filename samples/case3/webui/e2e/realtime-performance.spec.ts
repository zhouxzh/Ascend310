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
  recording: { active: false }, metrics: {}, diagnostics: {},
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
  await page.getByRole('button', { name: workspace }).first().click()
  await expect(page.getByRole('region', { name: workspace === '触控演奏' ? '触控实时演奏' : 'MIDI 键盘实时演奏' })).toBeVisible()
  const picker = workspace === '触控演奏' ? '.touch-patch-picker' : '.midi-patch-picker'
  await expect(page.locator(`${picker} summary`)).toBeVisible()
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
    const roll = page.getByRole('img', { name: '动态钢琴卷帘' })
    await expect(visualizerKeyboard).toHaveAttribute('data-key-count', '32')
    await expect(page.getByRole('button', { name: '使用 32 键' })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByLabel('实体 MIDI 输入')).toBeVisible()
    await expect(roll).toBeVisible()
    await expect(page.getByRole('slider', { name: '输出增益' })).toHaveValue('0')
    await expect(page.getByText('0.0 dB')).toBeVisible()
    const before = await visualizerKeyboard.boundingBox()
    expect(before).not.toBeNull()
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)

    await page.locator('.midi-patch-picker summary').click()
    await page.getByRole('tab', { name: '弦乐' }).click()
    await page.getByRole('button', { name: /Violin/ }).click()
    const after = await visualizerKeyboard.boundingBox()
    expect(after).not.toBeNull()
    expect(Math.abs(after!.height - before!.height)).toBeLessThanOrEqual(1)
    expect(Math.abs(after!.width - before!.width)).toBeLessThanOrEqual(1)

    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({
      path: `../reports/webui/screenshots/realtime-midi-keyboard-${viewport.name}.png`,
      fullPage: true,
    })

    if (viewport.width <= 600) {
      const drawerTab = page.getByRole('tab', { name: '录音监听' })
      await drawerTab.scrollIntoViewIfNeeded()
      await drawerTab.click()
      await expect(page.getByRole('button', { name: /开始录音/ })).toBeVisible()
      const [drawerBox, navBox] = await Promise.all([
        page.locator('.drawer-tabs').boundingBox(),
        page.locator('.bottom-nav').boundingBox(),
      ])
      expect(drawerBox).not.toBeNull()
      expect(navBox).not.toBeNull()
      expect(drawerBox!.y + drawerBox!.height).toBeLessThanOrEqual(navBox!.y + 1)
    }

  })
}

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
      page.locator('.patch-tile strong').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.getByRole('button', { name: '使用 25 键' }).boundingBox(),
      page.locator('.realtime-stage--touch .performance-control-bar label > span').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.realtime-stage--touch .performance-control-bar label > strong').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.realtime-stage--touch .performance-control-bar label').first().boundingBox(),
    ])
    expect(patchFontSize).toBeGreaterThanOrEqual(15)
    expect(keyButtonBox).not.toBeNull()
    expect(keyButtonBox!.height).toBeGreaterThanOrEqual(56)
    expect(controlLabelSize).toBeGreaterThanOrEqual(18)
    expect(controlValueSize).toBeGreaterThanOrEqual(20)
    expect(controlBox).not.toBeNull()
    expect(controlBox!.height).toBeGreaterThanOrEqual(64)

    await page.getByRole('button', { name: '向高音区移动一个八度' }).click()
    await expect(page.getByText('C4–C6')).toBeVisible()
    const piano = page.locator('.realtime-stage .piano')
    await expect(piano).toHaveAttribute('data-key-count', '25')
    await expect(piano).toHaveAttribute('data-white-key-count', '15')
    const [pianoBox, whiteKeyBox, blackKeyBox, footerBox, pianoStyleHeight, stageClass] = await Promise.all([
      piano.boundingBox(),
      piano.locator('.white-key').first().boundingBox(),
      piano.locator('.black-key').first().boundingBox(),
      page.locator('.status-footer').boundingBox(),
      piano.evaluate((element) => Number.parseFloat(getComputedStyle(element).height)),
      page.locator('.realtime-stage').getAttribute('class'),
    ])
    expect(pianoBox).not.toBeNull()
    expect(whiteKeyBox).not.toBeNull()
    expect(blackKeyBox).not.toBeNull()
    expect(footerBox).not.toBeNull()
    expect(stageClass).toContain('touch-keyboard-size--medium')
    expect(pianoStyleHeight).toBe(260)
    const stageBox = await page.locator('.keyboard-stage').boundingBox()
    expect(stageBox).not.toBeNull()
    expect(pianoBox!.width).toBeGreaterThanOrEqual(stageBox!.width - 24)
    expect(pianoBox!.height).toBeGreaterThanOrEqual(220)
    expect(pianoBox!.height).toBeLessThanOrEqual(310)
    expect(pianoBox!.y + pianoBox!.height).toBeLessThanOrEqual(footerBox!.y)
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
      page.locator('.realtime-stage--touch .performance-control-bar label > span').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.realtime-stage--touch .performance-control-bar label > strong').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.realtime-stage--touch .performance-control-bar label').first().boundingBox(),
    ])
    expect(narrowLabelSize).toBeGreaterThanOrEqual(18)
    expect(narrowValueSize).toBeGreaterThanOrEqual(20)
    expect(narrowControlBox).not.toBeNull()
    expect(narrowControlBox!.height).toBeGreaterThanOrEqual(64)
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

    const picker = page.locator('.touch-patch-picker')
    const pickerContent = page.locator('.touch-patch-picker-content')
    await expect(picker).toBeVisible()
    await expect(pickerContent).not.toBeVisible()
    await picker.locator('summary').click()
    await expect(pickerContent).toBeVisible()
    await picker.locator('summary').click()
    await expect(pickerContent).not.toBeVisible()

    const controls = page.locator('.realtime-stage--touch .performance-control-bar > *')
    await expect(controls).toHaveCount(5)
    const [viewport, footerBox, pianoBox, controlsBox, labelSize, valueSize, controlBoxes] = await Promise.all([
      page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight })),
      page.locator('.status-footer').boundingBox(),
      page.locator('.realtime-stage--touch .piano').boundingBox(),
      page.locator('.realtime-stage--touch .performance-control-bar').boundingBox(),
      page.locator('.realtime-stage--touch .performance-control-bar label > span').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.realtime-stage--touch .performance-control-bar label > strong').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      controls.evaluateAll((elements) => elements.map((element) => {
        const box = element.getBoundingClientRect()
        return { top: box.top, bottom: box.bottom, height: box.height }
      })),
    ])
    expect(viewport).toEqual({ width: 1920, height: 969 })
    expect(footerBox).not.toBeNull()
    expect(pianoBox).not.toBeNull()
    expect(controlsBox).not.toBeNull()
    expect(labelSize).toBeGreaterThanOrEqual(24)
    expect(valueSize).toBeGreaterThanOrEqual(28)
    expect(controlBoxes.every((box) => box.height >= 82)).toBe(true)
    expect(controlBoxes.every((box) => box.bottom <= footerBox!.y)).toBe(true)
    expect(pianoBox!.y + pianoBox!.height).toBeLessThanOrEqual(controlsBox!.y)
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1)
    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({ path: '../reports/webui/screenshots/realtime-touch-stage-1920x969.png', fullPage: false })
  } finally {
    await context.close()
  }
})

test('physical 10-inch MIDI stage keeps controller controls and visualizer readable', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1920, height: 969 }, hasTouch: true })
  const page = await context.newPage()
  try {
    await openRealtimeStage(page, 'MIDI 键盘')

    const picker = page.locator('.midi-patch-picker')
    const pickerContent = page.locator('.midi-patch-picker-content')
    await expect(picker).toBeVisible()
    await expect(pickerContent).not.toBeVisible()
    await expect(page.locator('.patch-library')).toHaveCount(0)
    await picker.locator('summary').click()
    await expect(pickerContent).toBeVisible()
    await picker.locator('summary').click()
    await expect(pickerContent).not.toBeVisible()

    const [inputLabelSize, inputSelectBox, keyButtons, rollBox, visualizerBox, drawerBox, navigationFontSize, navigationBox] = await Promise.all([
      page.locator('.realtime-stage--midi .midi-input-control span').evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.getByLabel('实体 MIDI 输入').boundingBox(),
      page.locator('.realtime-stage--midi .key-count-control button').evaluateAll((elements) => elements.map((element) => element.getBoundingClientRect().toJSON())),
      page.locator('.realtime-stage--midi .live-piano-roll').boundingBox(),
      page.getByRole('img', { name: '32 键钢琴可视化' }).boundingBox(),
      page.locator('.realtime-stage--midi .drawer-tabs').boundingBox(),
      page.locator('.primary-nav button').first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
      page.locator('.primary-nav button').first().boundingBox(),
    ])
    expect(inputLabelSize).toBeGreaterThanOrEqual(18)
    expect(inputSelectBox).not.toBeNull()
    expect(inputSelectBox!.height).toBeGreaterThanOrEqual(42)
    expect(keyButtons.every((box) => box.height >= 56)).toBe(true)
    expect(rollBox).not.toBeNull()
    expect(rollBox!.height).toBeGreaterThanOrEqual(220)
    expect(visualizerBox).not.toBeNull()
    expect(visualizerBox!.height).toBeGreaterThanOrEqual(96)
    expect(drawerBox).not.toBeNull()
    expect(visualizerBox!.y + visualizerBox!.height).toBeLessThanOrEqual(drawerBox!.y)
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

test('unified session starts, switches patch, and releases notes on blur', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  let switchBody: Record<string, unknown> | null = null
  await openRealtimeStage(page)
  await page.unroute('**/api/v1/realtime/switch')
  await page.route('**/api/v1/realtime/switch', async (route) => {
    switchBody = route.request().postDataJSON()
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ ...stopped, state: 'running', running: true, patch_id: violinPatch.patch_id, patch: violinPatch, audio_device_id: 'usb-audio', last_switch: { ok: true, rolled_back: false, duration_ms: 220 } }),
    })
  })

  await page.getByRole('button', { name: /开始演奏/ }).click()
  await expect(page.getByText('演奏中')).toBeVisible()
  await page.locator('.midi-patch-picker summary').click()
  await page.getByRole('tab', { name: '弦乐' }).click()
  await page.getByRole('button', { name: /Violin/ }).click()
  await expect.poll(() => switchBody).toMatchObject({ patch_id: 'neural.violin', audio_device_id: 'usb-audio' })

  await page.evaluate(() => window.dispatchEvent(new Event('blur')))
  const allNotesOff = await page.evaluate(() => {
    const sockets = (globalThis as unknown as { __testSockets: Array<{ url: string; sent: string[] }> }).__testSockets
    return sockets.find((socket) => socket.url.includes('/realtime/events'))?.sent
      .map((item) => JSON.parse(item))
      .some((item) => item.event === 'all_notes_off')
  })
  expect(allNotesOff).toBe(true)
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
