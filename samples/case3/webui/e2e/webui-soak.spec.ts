import { expect, test, type Page } from '@playwright/test'
import { mkdir, rename, writeFile } from 'node:fs/promises'

const environment = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process?.env
const liveBoardEnabled = Boolean(
  environment?.PLAYWRIGHT_BASE_URL
  && environment.CASE3_LIVE_BOARD_E2E === '1',
)
const cycles = Math.max(1, Number.parseInt(environment?.CASE3_UI_SOAK_CYCLES ?? '100', 10))
const reportPath = environment?.CASE3_UI_SOAK_REPORT
  ?? '../reports/webui/stress/ui-soak.json'
const screenshotRoot = environment?.CASE3_UI_SCREENSHOT_DIR
  ?? '../reports/webui/screenshots/production-final'
const navigationActionLabels = [
  'realtime-touch',
  'realtime-midi',
  'workspace-midi-ddsp',
  'midi-ddsp-library',
  'midi-ddsp-render',
  'workspace-ddsp-vst',
  'ddsp-vst-timbre',
  'ddsp-vst-input-gate',
  'ddsp-vst-effect',
  'workspace-devices',
  'devices-overview',
  'devices-audio',
  'devices-audio-output',
  'devices-audio-input',
  'devices-midi',
  'devices-runtime',
] as const

test.skip(
  !liveBoardEnabled,
  'Set PLAYWRIGHT_BASE_URL and CASE3_LIVE_BOARD_E2E=1 for the real-board soak.',
)

type LayoutSample = {
  name: string
  viewport: { width: number; height: number }
  documentWidth: number
  contentClientHeight: number
  contentScrollHeight: number
  minimumCoreFontPx: number
  undersizedControls: string[]
  undersizedPrimaryActions: string[]
  canvasCount: number
  nonblankCanvases: number
}

type MemoryMetrics = {
  domNodes: number
  jsHeapUsedBytes: number
  garbageCollectorAvailable: boolean
}

function percentile(values: number[], quantile: number): number {
  const ordered = [...values].sort((left, right) => left - right)
  if (!ordered.length) return 0
  const position = (ordered.length - 1) * quantile
  const lower = Math.floor(position)
  const upper = Math.ceil(position)
  const weight = position - lower
  return ordered[lower] * (1 - weight) + ordered[upper] * weight
}

async function settle(page: Page) {
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  }))
}

async function layoutSample(page: Page, name: string): Promise<LayoutSample> {
  return page.evaluate((sampleName) => {
    const visible = (element: HTMLElement) => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
    }
    const label = (element: HTMLElement) => (
      element.getAttribute('aria-label')
      || element.getAttribute('title')
      || element.textContent?.trim().replace(/\s+/g, ' ').slice(0, 80)
      || element.tagName
    )
    const controlElements = [...document.querySelectorAll<HTMLElement>(
      'button:not(:disabled), select:not(:disabled), input:not(:disabled):not([type="range"]):not([type="checkbox"]):not([type="radio"])',
    )].filter((element) => visible(element) && !element.closest('.piano'))
    const undersizedControls = controlElements
      .filter((element) => element.getBoundingClientRect().height < 51.5)
      .map(label)
    const primaryActions = [...document.querySelectorAll<HTMLElement>(
      '.primary-button:not(:disabled), .danger-button:not(:disabled), .transport-primary:not(:disabled)',
    )].filter(visible)
    const undersizedPrimaryActions = primaryActions
      .filter((element) => element.getBoundingClientRect().height < 55.5)
      .map(label)
    const fontElements = [...document.querySelectorAll<HTMLElement>([
      '.content-area button',
      '.content-area select',
      '.content-area label',
      '.content-area .panel-header p',
      '.content-area .field > span',
      '.content-area .device-list-row small',
      '.content-area .inventory-row span',
      '.content-area .metric > span',
    ].join(','))].filter((element) => visible(element) && !element.closest('.piano'))
    const fontSizes = fontElements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize))
    const canvases = [...document.querySelectorAll<HTMLCanvasElement>('canvas')].filter(visible)
    let nonblankCanvases = 0
    for (const canvas of canvases) {
      const context = canvas.getContext('2d')
      if (!context || !canvas.width || !canvas.height) continue
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
      let nonblank = 0
      const stride = Math.max(4, Math.floor(pixels.length / 20_000 / 4) * 4)
      for (let index = 3; index < pixels.length; index += stride) {
        if (pixels[index] > 0) nonblank += 1
      }
      if (nonblank > 50) nonblankCanvases += 1
    }
    const content = document.querySelector<HTMLElement>('.content-area')
    return {
      name: sampleName,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      documentWidth: document.documentElement.scrollWidth,
      contentClientHeight: content?.clientHeight ?? 0,
      contentScrollHeight: content?.scrollHeight ?? 0,
      minimumCoreFontPx: fontSizes.length ? Math.min(...fontSizes) : 0,
      undersizedControls,
      undersizedPrimaryActions,
      canvasCount: canvases.length,
      nonblankCanvases,
    }
  }, name)
}

async function memoryMetrics(page: Page): Promise<MemoryMetrics> {
  return page.evaluate(() => {
    const runtime = globalThis as typeof globalThis & { gc?: () => void }
    runtime.gc?.()
    runtime.gc?.()
    const browserPerformance = performance as Performance & {
      memory?: { usedJSHeapSize?: number }
    }
    return {
      domNodes: document.getElementsByTagName('*').length,
      jsHeapUsedBytes: browserPerformance.memory?.usedJSHeapSize ?? 0,
      garbageCollectorAvailable: typeof runtime.gc === 'function',
    }
  })
}

async function timedAction(
  page: Page,
  timings: number[],
  action: () => Promise<void>,
  assertion: () => Promise<void>,
) {
  const started = performance.now()
  await action()
  await assertion()
  await settle(page)
  timings.push(performance.now() - started)
}

async function runWorkspaceCycle(page: Page, timings: number[]) {
  await timedAction(
    page,
    timings,
    () => page.getByRole('button', { name: '实时演奏' }).first().click(),
    () => expect(page.getByRole('region', { name: '触控实时演奏' })).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('tab', { name: 'MIDI 键盘', exact: true }).click(),
    () => expect(page.getByRole('region', { name: 'MIDI 键盘实时演奏' })).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('button', { name: 'MIDI-DDSP' }).first().click(),
    () => expect(page.getByRole('heading', { name: /MIDI-DDSP (音频库|新建渲染)/ })).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('button', { name: '音频库' }).click(),
    () => expect(page.getByRole('heading', { name: 'MIDI-DDSP 音频库' })).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('button', { name: '新建渲染' }).click(),
    () => expect(page.getByRole('heading', { name: 'MIDI-DDSP 新建渲染' })).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('button', { name: 'DDSP-VST' }).first().click(),
    () => expect(page.getByRole('heading', { name: 'DDSP-VST' })).toBeVisible(),
  )
  for (const tab of ['音色', '输入门', '效果']) {
    await timedAction(
      page,
      timings,
      () => page.getByRole('tab', { name: tab, exact: true }).click(),
      () => expect(page.getByRole('tab', { name: tab, exact: true })).toHaveAttribute('aria-selected', 'true'),
    )
  }
  await timedAction(
    page,
    timings,
    () => page.getByRole('button', { name: '设备' }).first().click(),
    () => expect(page.getByRole('tab', { name: /设备概览/ })).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('tab', { name: /设备概览/ }).click(),
    () => expect(page.getByRole('heading', { name: '开发板基本状态' })).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('tab', { name: /音频设备/ }).click(),
    () => expect(page.getByRole('heading', { name: '接口状态' })).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('button', { name: /^输出 \d+$/ }).click(),
    () => expect(page.getByLabel('输出测试参数')).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('button', { name: /^输入 \d+$/ }).click(),
    () => expect(page.getByLabel('输入测试参数')).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('button', { name: /^MIDI \d+$/ }).click(),
    () => expect(page.getByRole('heading', { name: 'MIDI 输入状态' })).toBeVisible(),
  )
  await timedAction(
    page,
    timings,
    () => page.getByRole('tab', { name: /运行环境/ }).click(),
    () => expect(page.getByRole('heading', { name: '运行依赖' })).toBeVisible(),
  )
}

test('real board UI survives repeated touch-workspace navigation', async ({ browser }) => {
  test.setTimeout(Math.max(15 * 60_000, cycles * 20_000))
  const context = await browser.newContext({
    viewport: { width: 1920, height: 969 },
    hasTouch: true,
  })
  const page = await context.newPage()
  page.setDefaultTimeout(10_000)
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  const requestFailures: string[] = []
  const failedResponses: string[] = []
  const timings: number[] = []
  const warmupTimings: number[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('requestfailed', (request) => requestFailures.push(
    `${request.method()} ${request.url()} ${request.failure()?.errorText ?? 'failed'}`,
  ))
  page.on('response', (response) => {
    if (response.status() >= 400) failedResponses.push(
      `${response.status()} ${response.request().method()} ${response.url()}`,
    )
  })

  try {
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.primary-nav button')).toHaveCount(4)
    await expect(page.getByRole('button', { name: '实时演奏' }).first()).toBeVisible({ timeout: 15_000 })
    const warmupCycles = Math.min(5, Math.max(0, cycles - 1))
    for (let index = 0; index < warmupCycles; index += 1) {
      await runWorkspaceCycle(page, warmupTimings)
    }
    await page.getByRole('button', { name: '实时演奏' }).first().click()
    await expect(page.getByRole('region', { name: '触控实时演奏' })).toBeVisible()
    await settle(page)
    const baselineMemory = await memoryMetrics(page)

    for (let index = warmupCycles; index < cycles; index += 1) {
      await runWorkspaceCycle(page, timings)
    }
    await page.getByRole('button', { name: '实时演奏' }).first().click()
    await expect(page.getByRole('region', { name: '触控实时演奏' })).toBeVisible()
    await settle(page)
    const finalMemory = await memoryMetrics(page)

    await mkdir(screenshotRoot, { recursive: true })
    const samples: LayoutSample[] = []
    const capture = async (name: string) => {
      console.log(`[capture] ${name}`)
      await settle(page)
      const sample = await layoutSample(page, name)
      samples.push(sample)
      await page.screenshot({ path: `${screenshotRoot}/${name}.png`, fullPage: false })
    }

    await capture('touch-performance-1920x969')
    const touchFrame = page.locator('.keyboard-frame--touch')
    const touchFrameBottomGap = await touchFrame.evaluate((element) => (
      window.innerHeight - element.getBoundingClientRect().bottom
    ))
    const pianoFrameInset = await page.locator('.piano').evaluate((element) => (
      element.parentElement!.getBoundingClientRect().bottom - element.getBoundingClientRect().bottom
    ))
    expect(touchFrameBottomGap).toBeGreaterThanOrEqual(6)
    expect(touchFrameBottomGap).toBeLessThanOrEqual(12)
    expect(pianoFrameInset).toBeGreaterThanOrEqual(9)

    console.log('[navigate] MIDI keyboard screenshots')
    await page.getByRole('tab', { name: 'MIDI 键盘', exact: true }).click()
    await expect(page.getByRole('region', { name: 'MIDI 键盘实时演奏' })).toBeVisible()
    await capture('midi-keyboard-1920x969')
    const midiFrame = page.locator('.keyboard-frame--midi')
    expect(await midiFrame.evaluate((element) => (
      window.innerHeight - element.getBoundingClientRect().bottom
    ))).toBeGreaterThanOrEqual(6)
    expect(await page.locator('.visualizer-keyboard').evaluate((element) => (
      element.parentElement!.getBoundingClientRect().bottom - element.getBoundingClientRect().bottom
    ))).toBeGreaterThanOrEqual(9)

    console.log('[navigate] MIDI-DDSP screenshots')
    await page.getByRole('button', { name: 'MIDI-DDSP' }).first().click()
    await page.getByRole('button', { name: '音频库' }).click()
    await capture('midi-ddsp-library-1920x969')
    await page.getByRole('button', { name: '新建渲染' }).click()
    await capture('midi-ddsp-render-1920x969')
    expect(await page.locator('.midi-file-roll:visible')).toHaveCount(1)

    console.log('[navigate] DDSP-VST screenshots')
    await page.getByRole('button', { name: 'DDSP-VST' }).first().click()
    for (const [tab, name] of [
      ['音色', 'ddsp-vst-timbre-1920x969'],
      ['输入门', 'ddsp-vst-input-gate-1920x969'],
      ['效果', 'ddsp-vst-effect-1920x969'],
    ] as const) {
      await page.getByRole('tab', { name: tab, exact: true }).click()
      await capture(name)
    }
    const ddspContent = await page.locator('.content-area').evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
    }))
    expect(ddspContent.scrollHeight).toBeLessThanOrEqual(ddspContent.clientHeight + 1)

    console.log('[navigate] device screenshots')
    await page.getByRole('button', { name: '设备' }).first().click()
    await page.getByRole('tab', { name: /设备概览/ }).click()
    await capture('devices-overview-1920x969')
    await page.getByRole('tab', { name: /音频设备/ }).click()
    await page.getByRole('button', { name: /^输出 \d+$/ }).click()
    await capture('devices-audio-output-1920x969')
    await page.getByRole('button', { name: /^输入 \d+$/ }).click()
    await capture('devices-audio-input-1920x969')
    await page.getByRole('button', { name: /^MIDI \d+$/ }).click()
    await capture('devices-midi-1920x969')
    await page.getByRole('tab', { name: /运行环境/ }).click()
    await capture('devices-runtime-1920x969')

    const p95Ms = percentile(timings, 0.95)
    const p99Ms = percentile(timings, 0.99)
    const timingsByAction = Object.fromEntries(navigationActionLabels.map((label, actionIndex) => {
      const values = timings.filter((_, index) => index % navigationActionLabels.length === actionIndex)
      return [label, {
        samples: values.length,
        p50_ms: percentile(values, 0.50),
        p95_ms: percentile(values, 0.95),
        p99_ms: percentile(values, 0.99),
        maximum_ms: Math.max(...values),
      }]
    }))
    const domGrowth = baselineMemory.domNodes
      ? (finalMemory.domNodes - baselineMemory.domNodes) / baselineMemory.domNodes : 0
    const heapGrowth = baselineMemory.jsHeapUsedBytes
      ? (finalMemory.jsHeapUsedBytes - baselineMemory.jsHeapUsedBytes) / baselineMemory.jsHeapUsedBytes : 0
    const checks = {
      console_errors_zero: consoleErrors.length === 0,
      page_errors_zero: pageErrors.length === 0,
      request_failures_zero: requestFailures.length === 0,
      failed_responses_zero: failedResponses.length === 0,
      navigation_p95_under_250_ms: p95Ms < 250,
      navigation_p99_under_500_ms: p99Ms < 500,
      dom_growth_under_10_percent: domGrowth <= 0.10,
      heap_growth_under_20_percent: heapGrowth <= 0.20,
      browser_gc_available: baselineMemory.garbageCollectorAvailable && finalMemory.garbageCollectorAvailable,
      no_horizontal_overflow: samples.every((sample) => sample.documentWidth <= sample.viewport.width + 1),
      core_text_at_least_14_px: samples.every((sample) => sample.minimumCoreFontPx >= 14),
      controls_at_least_52_px: samples.every((sample) => sample.undersizedControls.length === 0),
      primary_actions_at_least_56_px: samples.every((sample) => sample.undersizedPrimaryActions.length === 0),
      canvases_nonblank: samples.every((sample) => (
        sample.canvasCount === 0 || sample.nonblankCanvases >= 1
      )),
    }
    const report = {
      schema: 'case3-webui-touch-soak/v1',
      completed_at: new Date().toISOString(),
      base_url: environment?.PLAYWRIGHT_BASE_URL,
      configuration: {
        cycles,
        warmup_cycles: warmupCycles,
        viewport: { width: 1920, height: 969 },
        has_touch: true,
      },
      navigation: {
        samples: timings.length,
        p50_ms: percentile(timings, 0.50),
        p95_ms: p95Ms,
        p99_ms: p99Ms,
        maximum_ms: Math.max(...timings),
        by_action: timingsByAction,
      },
      memory: { baseline: baselineMemory, final: finalMemory, dom_growth: domGrowth, heap_growth: heapGrowth },
      errors: { consoleErrors, pageErrors, requestFailures, failedResponses },
      layouts: samples,
      checks,
      passed: Object.values(checks).every(Boolean),
    }
    await mkdir(reportPath.replace(/[\\/][^\\/]+$/, ''), { recursive: true })
    const temporary = `${reportPath}.part`
    await writeFile(temporary, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
    await rename(temporary, reportPath)

    expect(checks, JSON.stringify(report, null, 2)).toEqual(
      Object.fromEntries(Object.keys(checks).map((key) => [key, true])),
    )
  } finally {
    await context.close()
  }
})
