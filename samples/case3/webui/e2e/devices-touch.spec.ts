import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

test('devices workspace fits the physical 10-inch touch viewport', async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 1920, height: 969 },
    hasTouch: true,
  })
  const page = await context.newPage()

  try {
    await page.goto('/')
    await page.getByRole('button', { name: '设备' }).first().click()
    await expect(page.getByRole('heading', { name: '系统与设备' })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '开发板基本状态' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '触控与 MIDI 演奏' })).toBeVisible()
    await expect(page.getByRole('tab', { name: /设备概览/ })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('button', { name: /检查音频|查看输入|查看模型|查看环境/ })).toHaveCount(0)

    const layoutMetrics = () => page.evaluate(() => {
      const rectFor = (selector: string) => {
        const element = document.querySelector(selector)
        if (!element) {
          return null
        }
        const rect = element.getBoundingClientRect()
        return {
          x: rect.x,
          y: rect.y,
          width: rect.width,
          height: rect.height,
          bottom: rect.bottom,
        }
      }

      const tabButtons = [...document.querySelectorAll<HTMLElement>('.device-section-tabs button')]
      const readinessGrid = document.querySelector<HTMLElement>('.device-readiness-grid')
      const readinessCards = [...document.querySelectorAll<HTMLElement>('.device-readiness-card')]
      const fontSizeFor = (selector: string) => {
        const element = document.querySelector<HTMLElement>(selector)
        return element ? Number.parseFloat(getComputedStyle(element).fontSize) : 0
      }
      return {
        viewport: { width: window.innerWidth, height: window.innerHeight },
        document: {
          width: document.documentElement.scrollWidth,
          height: document.documentElement.scrollHeight,
        },
        contentArea: {
          clientHeight: document.querySelector<HTMLElement>('.content-area')?.clientHeight ?? 0,
          scrollHeight: document.querySelector<HTMLElement>('.content-area')?.scrollHeight ?? 0,
          scrollTop: document.querySelector<HTMLElement>('.content-area')?.scrollTop ?? 0,
        },
        overflowX: document.documentElement.scrollWidth - window.innerWidth,
        header: rectFor('.app-header'),
        tabs: rectFor('.device-section-tabs'),
        panel: rectFor('.device-section-panel'),
        boardStatus: rectFor('.device-board-status'),
        readinessGrid: rectFor('.device-readiness-grid'),
        audioLayout: rectFor('.device-audio-layout'),
        bluetoothPanel: rectFor('.device-audio-layout .bluetooth-panel'),
        ioPanel: rectFor('.device-audio-layout .device-io-panel'),
        speakerWorkspace: rectFor('.speaker-workspace--compact'),
        audioTestPanel: rectFor('.speaker-workspace--compact .audio-test-panel'),
        audioTestBody: rectFor('.speaker-workspace--compact .audio-test-body'),
        audioTestFeedback: rectFor('.speaker-workspace--compact .audio-test-feedback'),
        audioTestControls: rectFor('.speaker-workspace--compact .audio-test-controls'),
        speakerMonitor: rectFor('.speaker-workspace--compact .speaker-monitor'),
        runtimeSummary: rectFor('.runtime-summary-grid'),
        runtimeDetails: rectFor('.runtime-detail-grid'),
        readinessColumnCount: readinessGrid
          ? getComputedStyle(readinessGrid).gridTemplateColumns.split(' ').length
          : 0,
        readinessCardHeights: readinessCards.map((card) => card.getBoundingClientRect().height),
        overviewTypography: {
          boardTitle: fontSizeFor('.device-board-status-heading h2'),
          factLabel: fontSizeFor('.device-board-facts dt'),
          factValue: fontSizeFor('.device-board-facts dd'),
          cardTitle: fontSizeFor('.device-readiness-card h3'),
          cardLabel: fontSizeFor('.device-readiness-card dt'),
          cardValue: fontSizeFor('.device-readiness-card dd'),
          cardHint: fontSizeFor('.device-readiness-hint'),
        },
        tabHeights: tabButtons.map((button) => button.getBoundingClientRect().height),
        tabFontSizes: tabButtons.map((button) => Number.parseFloat(getComputedStyle(button).fontSize)),
      }
    })
    const metrics = await layoutMetrics()

    expect(metrics.viewport).toEqual({ width: 1920, height: 969 })
    expect(metrics.overflowX).toBeLessThanOrEqual(1)
    expect(metrics.header).not.toBeNull()
    expect(metrics.header!.height).toBeGreaterThanOrEqual(56)
    expect(metrics.tabs).not.toBeNull()
    expect(metrics.panel).not.toBeNull()
    expect(metrics.boardStatus).not.toBeNull()
    expect(metrics.readinessGrid).not.toBeNull()
    expect(metrics.contentArea.scrollHeight).toBeLessThanOrEqual(metrics.contentArea.clientHeight + 1)
    expect(metrics.boardStatus!.height).toBeLessThanOrEqual(110)
    expect(metrics.readinessColumnCount).toBe(2)
    expect(metrics.readinessCardHeights).toHaveLength(4)
    expect(Math.max(...metrics.readinessCardHeights) - Math.min(...metrics.readinessCardHeights)).toBeLessThanOrEqual(2)
    expect(metrics.overviewTypography.boardTitle).toBeGreaterThanOrEqual(22)
    expect(metrics.overviewTypography.factLabel).toBeGreaterThanOrEqual(16)
    expect(metrics.overviewTypography.factValue).toBeGreaterThanOrEqual(18)
    expect(metrics.overviewTypography.cardTitle).toBeGreaterThanOrEqual(20)
    expect(metrics.overviewTypography.cardLabel).toBeGreaterThanOrEqual(16)
    expect(metrics.overviewTypography.cardValue).toBeGreaterThanOrEqual(18)
    expect(metrics.overviewTypography.cardHint).toBeGreaterThanOrEqual(16)
    expect(metrics.tabs!.bottom).toBeLessThanOrEqual(metrics.panel!.y + 1)
    expect(Math.min(...metrics.tabHeights)).toBeGreaterThanOrEqual(52)

    await mkdir('../reports/webui/screenshots', { recursive: true })
    const screenshotName = process.env.DEVICES_SCREENSHOT_NAME ?? 'devices-touch-1920x969.png'
    await page.screenshot({
      path: `../reports/webui/screenshots/${screenshotName}`,
      fullPage: false,
    })

    const bluetoothLoaded = page.waitForResponse((response) => (
      response.request().method() === 'GET'
      && response.url().endsWith('/api/v1/bluetooth-audio')
    ))
    await page.getByRole('tab', { name: /音频设备/ }).click()
    await bluetoothLoaded
    await expect(page.getByRole('heading', { name: '蓝牙音频' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '音频设备测试' })).toBeVisible()
    await expect(page.getByLabel('输出测试参数')).toBeVisible()
    await expect(page.getByRole('heading', { name: '输出设置' })).toHaveCount(0)
    await expect(page.locator('.speaker-workspace > .panel')).toHaveCount(1)
    const audioMetrics = await layoutMetrics()
    await page.screenshot({
      path: '../reports/webui/screenshots/devices-audio-output-touch-1920x969.png',
      fullPage: false,
    })
    expect(audioMetrics.contentArea.scrollHeight).toBeLessThanOrEqual(audioMetrics.contentArea.clientHeight + 1)
    expect(audioMetrics.speakerWorkspace).not.toBeNull()
    expect(audioMetrics.audioLayout).not.toBeNull()
    expect(audioMetrics.bluetoothPanel).not.toBeNull()
    expect(audioMetrics.ioPanel).not.toBeNull()
    expect(audioMetrics.audioTestPanel).not.toBeNull()
    expect(audioMetrics.audioTestBody).not.toBeNull()
    expect(audioMetrics.audioTestFeedback).not.toBeNull()
    expect(audioMetrics.audioTestControls).not.toBeNull()
    expect(audioMetrics.speakerMonitor).not.toBeNull()
    expect(audioMetrics.speakerMonitor!.height).toBeLessThanOrEqual(120)
    expect(audioMetrics.audioTestPanel!.height).toBeLessThanOrEqual(440)
    expect(audioMetrics.audioTestFeedback!.x + audioMetrics.audioTestFeedback!.width).toBeLessThanOrEqual(audioMetrics.audioTestControls!.x + 1)
    expect(Math.abs(audioMetrics.bluetoothPanel!.width - audioMetrics.ioPanel!.width)).toBeLessThanOrEqual(2)
    expect(Math.abs(audioMetrics.bluetoothPanel!.y - audioMetrics.ioPanel!.y)).toBeLessThanOrEqual(1)
    expect(audioMetrics.speakerWorkspace!.y).toBeGreaterThanOrEqual(audioMetrics.bluetoothPanel!.bottom + 10)
    expect(audioMetrics.speakerWorkspace!.width).toBeGreaterThanOrEqual(audioMetrics.audioLayout!.width - 1)
    await page.getByRole('button', { name: '输入测试' }).click()
    await expect(page.getByRole('heading', { name: '音频设备测试' })).toBeVisible()
    await expect(page.getByLabel('输入测试参数')).toBeVisible()
    await expect(page.locator('.system-volume-readout')).toHaveCount(0)
    await expect(page.locator('.speaker-workspace > .panel')).toHaveCount(1)
    const inputTestMetrics = await layoutMetrics()
    expect(inputTestMetrics.contentArea.scrollHeight).toBeLessThanOrEqual(inputTestMetrics.contentArea.clientHeight + 1)
    expect(inputTestMetrics.audioTestPanel!.height).toBeLessThanOrEqual(440)
    await page.screenshot({
      path: '../reports/webui/screenshots/devices-audio-input-test-touch-1920x969.png',
      fullPage: false,
    })

    await page.getByRole('button', { name: /^输入 \d+$/ }).click()
    await page.screenshot({
      path: '../reports/webui/screenshots/devices-audio-input-touch-1920x969.png',
      fullPage: false,
    })

    await page.getByRole('button', { name: /^MIDI \d+$/ }).click()
    await page.screenshot({
      path: '../reports/webui/screenshots/devices-audio-midi-touch-1920x969.png',
      fullPage: false,
    })

    await page.getByRole('tab', { name: /运行环境/ }).click()
    await expect(page.getByRole('heading', { name: '运行依赖' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '模型资产' })).toBeVisible()
    await expect(page.locator('.runtime-summary-card')).toHaveCount(4)
    await expect(page.locator('.device-runtime-layout pre')).toHaveCount(0)
    const runtimeMetrics = await layoutMetrics()
    expect(runtimeMetrics.contentArea.scrollHeight).toBeLessThanOrEqual(runtimeMetrics.contentArea.clientHeight + 1)
    expect(runtimeMetrics.runtimeSummary).not.toBeNull()
    expect(runtimeMetrics.runtimeDetails).not.toBeNull()
    await page.screenshot({
      path: '../reports/webui/screenshots/devices-runtime-touch-1920x969.png',
      fullPage: false,
    })

    console.log(JSON.stringify({
      overview: { contentArea: metrics.contentArea, overflowX: metrics.overflowX },
      audio: {
        contentArea: audioMetrics.contentArea,
        overflowX: audioMetrics.overflowX,
        audioTestPanel: audioMetrics.audioTestPanel,
        audioTestBody: audioMetrics.audioTestBody,
        audioTestFeedback: audioMetrics.audioTestFeedback,
        audioTestControls: audioMetrics.audioTestControls,
        speakerMonitor: audioMetrics.speakerMonitor,
      },
      runtime: { contentArea: runtimeMetrics.contentArea, overflowX: runtimeMetrics.overflowX },
    }))
  } finally {
    await context.close()
  }
})

for (const midiPortCount of [0, 1, 2]) {
  test(`MIDI status uses the available width with ${midiPortCount} ports`, async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 1920, height: 969 },
      hasTouch: true,
    })
    const page = await context.newPage()
    await page.route('**/api/v1/midi-ports', (route) => route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        available: true,
        ports: Array.from({ length: midiPortCount }, (_, index) => ({
          id: `midi-${index}`,
          index,
          name: `MIDI Controller ${index + 1}`,
          manufacturer: 'MIDIPLUS',
          model: 'TINY',
          key_count: 32,
          backend: 'rtmidi',
        })),
        error: null,
      }),
    }))

    try {
      await page.goto('/')
      await page.getByRole('button', { name: '设备' }).first().click()
      await page.getByRole('tab', { name: /音频设备/ }).click()
      await page.getByRole('button', { name: `MIDI ${midiPortCount}` }).click()
      await expect(page.getByRole('heading', { name: 'MIDI 输入状态' })).toBeVisible()

      const metrics = await page.evaluate(() => {
        const list = document.querySelector<HTMLElement>('.midi-device-status-list')
        const cards = [...document.querySelectorAll<HTMLElement>('.midi-device-status-card')]
        const empty = list?.querySelector<HTMLElement>('.empty-list')
        return {
          listWidth: list?.getBoundingClientRect().width ?? 0,
          contentWidth: list ? list.clientWidth
            - Number.parseFloat(getComputedStyle(list).paddingLeft)
            - Number.parseFloat(getComputedStyle(list).paddingRight) : 0,
          cardWidths: cards.map((card) => card.getBoundingClientRect().width),
          emptyWidth: empty?.getBoundingClientRect().width ?? 0,
          columns: list
            ? getComputedStyle(list).gridTemplateColumns
              .split(' ')
              .filter((track) => Number.parseFloat(track) > 1).length
            : 0,
        }
      })

      expect(metrics.listWidth).toBeGreaterThan(0)
      if (midiPortCount === 0) {
        expect(metrics.emptyWidth).toBeGreaterThanOrEqual(metrics.contentWidth - 2)
        expect(metrics.columns).toBe(1)
      } else if (midiPortCount === 1) {
        expect(metrics.cardWidths).toHaveLength(1)
        expect(metrics.cardWidths[0]).toBeGreaterThanOrEqual(metrics.contentWidth - 2)
        expect(metrics.columns).toBe(1)
      } else {
        expect(metrics.cardWidths).toHaveLength(2)
        expect(metrics.columns).toBe(2)
        expect(Math.abs(metrics.cardWidths[0] - metrics.cardWidths[1])).toBeLessThanOrEqual(2)
      }
    } finally {
      await context.close()
    }
  })
}

for (const viewport of [
  { name: 'desktop-1366x768', width: 1366, height: 768, touch: false },
  { name: 'tablet-1024x768', width: 1024, height: 768, touch: true },
  { name: 'mobile-390x844', width: 390, height: 844, touch: true },
]) {
  test(`audio input test stays readable at ${viewport.name}`, async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
      hasTouch: viewport.touch,
    })
    const page = await context.newPage()
    try {
      await page.goto('/')
      await page.getByRole('button', { name: '设备' }).first().click()
      await page.getByRole('tab', { name: /音频设备/ }).click()
      await page.getByRole('button', { name: '输入测试' }).click()
      await expect(page.getByRole('heading', { name: '音频设备测试' })).toBeVisible()
      await expect(page.getByLabel('输入测试参数')).toBeVisible()
      await expect(page.getByRole('button', { name: '开始输入测试' })).toBeVisible()

      const metrics = await page.evaluate(() => {
        const startButton = document.querySelector<HTMLElement>('.audio-input-test-panel .primary-button')
        const panel = document.querySelector<HTMLElement>('.audio-input-test-panel')
        const settings = document.querySelector<HTMLElement>('.audio-input-test-controls')
        return {
          viewportWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          startButtonHeight: startButton?.getBoundingClientRect().height ?? 0,
          panelWidth: panel?.getBoundingClientRect().width ?? 0,
          panelScrollWidth: panel?.scrollWidth ?? 0,
          settingsWidth: settings?.getBoundingClientRect().width ?? 0,
          settingsScrollWidth: settings?.scrollWidth ?? 0,
        }
      })
      expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1)
      expect(metrics.panelScrollWidth).toBeLessThanOrEqual(metrics.panelWidth + 1)
      expect(metrics.settingsScrollWidth).toBeLessThanOrEqual(metrics.settingsWidth + 1)
      expect(metrics.startButtonHeight).toBeGreaterThanOrEqual(viewport.touch ? 48 : 40)

      await page.screenshot({
        path: `../reports/webui/screenshots/devices-audio-input-test-${viewport.name}.png`,
        fullPage: false,
      })
    } finally {
      await context.close()
    }
  })
}
