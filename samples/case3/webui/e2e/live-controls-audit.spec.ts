import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'

const environment = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process?.env
const liveBoardEnabled = Boolean(
  environment?.PLAYWRIGHT_BASE_URL
  && environment.CASE3_LIVE_BOARD_E2E === '1',
)
const reportPath = environment?.CASE3_CONTROLS_AUDIT_REPORT
  ?? '../reports/webui/stress/live-controls-audit.json'
const screenshotRoot = environment?.CASE3_CONTROLS_AUDIT_SCREENSHOT_DIR
  ?? '../reports/webui/screenshots/live-controls-audit'

test.skip(
  !liveBoardEnabled,
  'Set PLAYWRIGHT_BASE_URL and CASE3_LIVE_BOARD_E2E=1 for the real-board control audit.',
)

type AuditStep = {
  name: string
  passed: boolean
  duration_ms: number
  detail?: string
  screenshot?: string
}

type ControlInventory = {
  page: string
  enabled: string[]
  disabled: string[]
  sliders: string[]
  selects: string[]
}

function messageOf(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause)
}

async function settle(page: Page, milliseconds = 80) {
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  }))
  if (milliseconds > 0) await page.waitForTimeout(milliseconds)
}

async function visibleButton(page: Page, name: string | RegExp) {
  const button = page.getByRole('button', { name }).filter({ visible: true }).first()
  await expect(button).toBeVisible()
  return button
}

async function selectAlternative(select: Locator) {
  const original = await select.inputValue()
  const values = await select.locator('option:not(:disabled)').evaluateAll((options) => (
    options.map((option) => (option as HTMLOptionElement).value).filter(Boolean)
  ))
  const alternative = values.find((value) => value !== original)
  if (alternative) {
    await select.selectOption(alternative)
    const currentValues = await select.locator('option').evaluateAll((options) => (
      options.map((option) => (option as HTMLOptionElement).value)
    ))
    if (currentValues.includes(original)) await select.selectOption(original)
  }
}

async function moveAndRestore(range: Locator) {
  const original = await range.inputValue()
  const bounds = await range.evaluate((element) => {
    const input = element as HTMLInputElement
    return {
      min: Number(input.min || 0),
      max: Number(input.max || 100),
      step: Number(input.step || 1),
      value: Number(input.value),
    }
  })
  const candidate = Number(
    Math.min(bounds.max, Math.max(bounds.min, bounds.value + bounds.step)).toFixed(6),
  )
  if (candidate !== bounds.value) {
    await range.fill(String(candidate))
    await range.fill(original)
  }
}

async function cleanup(request: APIRequestContext) {
  const workflows = [
    ['/api/v1/realtime/status', '/api/v1/realtime/stop'],
    ['/api/v1/ddsp-vst-effect/status', '/api/v1/ddsp-vst-effect/stop'],
    ['/api/v1/speaker-test/status', '/api/v1/speaker-test/stop'],
    ['/api/v1/audio-input-test/status', '/api/v1/audio-input-test/stop'],
  ] as const
  for (const [statusPath, stopPath] of workflows) {
    try {
      const response = await request.get(statusPath)
      if (!response.ok()) continue
      const status = await response.json() as { running?: boolean }
      if (status.running) await request.post(stopPath)
    } catch {
      // The audit report records browser-visible failures; cleanup remains best effort.
    }
  }
}

async function inventory(page: Page, pageName: string): Promise<ControlInventory> {
  return page.evaluate((name) => {
    const visible = (element: HTMLElement) => {
      const style = getComputedStyle(element)
      const box = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' && box.width > 0 && box.height > 0
    }
    const label = (element: HTMLElement) => (
      element.getAttribute('aria-label')
      || element.getAttribute('title')
      || element.textContent?.trim().replace(/\s+/g, ' ')
      || element.tagName
    ).slice(0, 120)
    const buttons = [...document.querySelectorAll<HTMLButtonElement>('button')].filter(visible)
    const sliders = [...document.querySelectorAll<HTMLInputElement>('input[type="range"]')].filter(visible)
    const selects = [...document.querySelectorAll<HTMLSelectElement>('select')].filter(visible)
    return {
      page: name,
      enabled: buttons.filter((button) => !button.disabled).map(label),
      disabled: buttons.filter((button) => button.disabled).map(label),
      sliders: sliders.map(label),
      selects: selects.map(label),
    }
  }, pageName)
}

test('real board controls execute without UI, request, or resource leaks', async ({ browser }) => {
  test.setTimeout(12 * 60_000)
  await mkdir(screenshotRoot, { recursive: true })
  await mkdir(reportPath.replace(/[\\/][^\\/]+$/, ''), { recursive: true })

  const context = await browser.newContext({
    viewport: { width: 1920, height: 969 },
    hasTouch: true,
  })
  const page = await context.newPage()
  page.setDefaultTimeout(12_000)
  const steps: AuditStep[] = []
  const inventories: ControlInventory[] = []
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  const requestFailures: string[] = []
  const expectedMediaAborts: string[] = []
  const failedResponses: string[] = []

  page.on('console', (entry) => {
    if (entry.type() === 'error') consoleErrors.push(entry.text())
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('requestfailed', (request) => {
    const entry = `${request.method()} ${request.url()} ${request.failure()?.errorText ?? 'failed'}`
    if (
      request.failure()?.errorText === 'net::ERR_ABORTED'
      && request.url().includes('/api/v1/artifacts/')
    ) expectedMediaAborts.push(entry)
    else requestFailures.push(entry)
  })
  page.on('response', (response) => {
    if (response.status() >= 400) failedResponses.push(
      `${response.status()} ${response.request().method()} ${response.url()}`,
    )
  })

  const runStep = async (name: string, action: () => Promise<void>) => {
    const started = performance.now()
    try {
      await action()
      await settle(page)
      await page.waitForLoadState('networkidle', { timeout: 3_000 }).catch(() => undefined)
      steps.push({ name, passed: true, duration_ms: performance.now() - started })
    } catch (cause) {
      const screenshot = `${screenshotRoot}/${String(steps.length + 1).padStart(2, '0')}-failed.png`
      await page.screenshot({ path: screenshot, fullPage: false }).catch(() => undefined)
      steps.push({
        name,
        passed: false,
        duration_ms: performance.now() - started,
        detail: messageOf(cause),
        screenshot,
      })
      await cleanup(page.request)
    }
  }

  try {
    await cleanup(page.request)

    await runStep('应用启动与全局刷新', async () => {
      await page.goto('/')
      await expect(page.getByRole('region', { name: '触控实时演奏' })).toBeVisible()
      await expect(page.locator('.primary-nav button')).toHaveCount(4)
      const refresh = await visibleButton(page, '刷新')
      await Promise.all([
        page.waitForResponse((response) => response.url().endsWith('/api/v1/status') && response.ok()),
        refresh.click(),
      ])
      inventories.push(await inventory(page, '触控演奏'))
    })

    await runStep('触控演奏：四个钢琴预设', async () => {
      const bank = page.getByRole('group', { name: '钢琴音色' })
      await expect(bank).toBeVisible()
      const presets = bank.getByRole('button')
      const count = await presets.count()
      expect(count).toBeGreaterThanOrEqual(1)
      for (let index = 0; index < count; index += 1) {
        await presets.nth(index).click()
        await expect(presets.nth(index)).toHaveAttribute('aria-pressed', 'true')
      }
      await expect(page.getByRole('button', { name: /小提琴|长笛|萨克斯|小号/ })).toHaveCount(0)
    })

    await runStep('触控演奏：滑块、卷帘与键盘布局', async () => {
      for (const label of ['输出增益', '混响', '触控力度', '移调']) {
        await moveAndRestore(page.getByRole('slider', { name: label }))
      }
      for (const seconds of [2, 4, 8]) {
        const button = await visibleButton(page, `${seconds} 秒时间窗`)
        await button.click()
        await expect(button).toHaveAttribute('aria-pressed', 'true')
      }
      for (const count of [13, 25]) {
        const button = await visibleButton(page, `使用 ${count} 键`)
        await button.click()
        await expect(button).toHaveAttribute('aria-pressed', 'true')
      }
      for (const size of ['小', '中', '大']) {
        const button = await visibleButton(page, `使用${size}键盘`)
        await button.click()
        await expect(button).toHaveAttribute('aria-pressed', 'true')
      }
      await (await visibleButton(page, '向低音区移动一个八度')).click()
      await (await visibleButton(page, '向高音区移动一个八度')).click()
    })

    await runStep('触控演奏：抽屉页签', async () => {
      for (const name of ['录音监听', '音色参数', '连接设置', '性能']) {
        const tab = page.getByRole('tab', { name })
        await tab.click()
        await expect(tab).toHaveAttribute('aria-selected', 'true')
        await expect(page.locator('.realtime-stage--touch .drawer-content')).toBeVisible()
      }
    })

    await runStep('触控演奏：启动、琴键、延音、监听、Panic 与停止', async () => {
      try {
        await (await visibleButton(page, /开始演奏/)).click()
        await expect(page.getByText('演奏中', { exact: true })).toBeVisible({ timeout: 20_000 })
        const c4 = page.locator('.realtime-stage--touch .piano-key[data-note="60"]')
        await c4.dispatchEvent('pointerdown', { pointerId: 101, pointerType: 'touch', isPrimary: true })
        await page.waitForTimeout(80)
        await c4.dispatchEvent('pointerup', { pointerId: 101, pointerType: 'touch', isPrimary: true })
        const sustain = page.locator('.touch-sustain-control')
        await expect(sustain).toBeEnabled()
        await sustain.click()
        await expect(sustain).toHaveAttribute('aria-pressed', 'true')
        await sustain.click()
        const recordingTab = page.getByRole('tab', { name: '录音监听' })
        await recordingTab.click()
        await expect(page.getByRole('button', { name: '开始录音' })).toBeEnabled()
        const monitor = page.getByRole('button', { name: '浏览器监听' })
        await monitor.click()
        await expect(page.getByRole('button', { name: '关闭监听' })).toBeVisible()
        await page.getByRole('button', { name: '关闭监听' }).click()
        await (await visibleButton(page, 'Panic / 全部停音')).click()
      } finally {
        const stop = page.getByRole('button', { name: '停止', exact: true })
        if (await stop.isVisible()) await stop.click()
        else await cleanup(page.request)
      }
      await expect(page.getByRole('button', { name: /开始演奏/ })).toBeVisible({ timeout: 20_000 })
    })

    await runStep('MIDI 键盘：音色库分类与音色选择', async () => {
      await page.getByRole('tab', { name: 'MIDI 键盘', exact: true }).click()
      await expect(page.getByRole('region', { name: 'MIDI 键盘实时演奏' })).toBeVisible()
      const picker = page.locator('.midi-patch-picker')
      const summary = picker.locator('summary')
      for (const category of ['最近使用', '钢琴', '弦乐', '木管', '铜管', '其他']) {
        if (await picker.getAttribute('open') === null) await summary.click()
        const tab = page.getByRole('tab', { name: category })
        await tab.click()
        await expect(tab).toHaveAttribute('aria-selected', 'true')
        const firstPatch = picker.locator('.patch-tile:not(:disabled)').first()
        if (await firstPatch.count()) {
          await firstPatch.click()
        }
      }
      if (await picker.getAttribute('open') === null) await summary.click()
      await page.getByRole('tab', { name: '钢琴' }).click()
      const pianoPatch = picker.locator('.patch-tile:not(:disabled)').first()
      if (await pianoPatch.count()) await pianoPatch.click()
      if (await picker.getAttribute('open') !== null) await summary.click()
      inventories.push(await inventory(page, 'MIDI 键盘'))
    })

    await runStep('MIDI 键盘：卷帘、键数、八度与设置抽屉', async () => {
      if (await page.getByRole('tab', { name: 'MIDI 键盘', exact: true }).getAttribute('aria-selected') !== 'true') {
        await page.getByRole('tab', { name: 'MIDI 键盘', exact: true }).click()
      }
      const picker = page.locator('.midi-patch-picker')
      if (await picker.getAttribute('open') !== null) await picker.locator('summary').click()
      for (const seconds of [2, 4, 8]) await (await visibleButton(page, `${seconds} 秒时间窗`)).click()
      for (const count of [32, 49, 61, 88]) {
        const button = await visibleButton(page, `使用 ${count} 键`)
        await button.click()
        await expect(button).toHaveAttribute('aria-pressed', 'true')
      }
      await (await visibleButton(page, '使用 32 键')).click()
      await (await visibleButton(page, '向低音区移动一个八度')).click()
      await (await visibleButton(page, '向高音区移动一个八度')).click()
      await moveAndRestore(page.getByRole('slider', { name: '输出增益' }))
      for (const name of ['MIDI 文件', '录音监听', '音色参数', '连接设置', '性能']) {
        const tab = page.getByRole('tab', { name })
        await tab.click()
        await expect(tab).toHaveAttribute('aria-selected', 'true')
      }
    })

    await runStep('MIDI 键盘：实时会话与 MIDI 文件播放控制', async () => {
      if (await page.getByRole('tab', { name: 'MIDI 键盘', exact: true }).getAttribute('aria-selected') !== 'true') {
        await page.getByRole('tab', { name: 'MIDI 键盘', exact: true }).click()
      }
      const picker = page.locator('.midi-patch-picker')
      if (await picker.getAttribute('open') !== null) await picker.locator('summary').click()
      try {
        await page.getByRole('tab', { name: 'MIDI 文件' }).click()
        const midiSelect = page.locator('select[aria-label="MIDI 文件"]')
        const midiValues = await midiSelect.locator('option').evaluateAll((options) => (
          options.map((option) => (option as HTMLOptionElement).value).filter(Boolean)
        ))
        expect(midiValues.length).toBeGreaterThan(0)
        await midiSelect.selectOption(midiValues[0])
        await (await visibleButton(page, /开始演奏/)).click()
        await expect(page.getByText('演奏中', { exact: true })).toBeVisible({ timeout: 20_000 })
        const play = await visibleButton(page, '播放')
        await play.click()
        await page.waitForTimeout(500)
        const pause = page.getByTitle('暂停')
        if (await pause.isVisible()) await pause.click()
        const stopPlayer = page.getByTitle('停止并回到开头')
        if (await stopPlayer.isEnabled()) await stopPlayer.click()
      } finally {
        const stop = page.getByRole('button', { name: '停止', exact: true })
        if (await stop.isVisible()) await stop.click()
        else await cleanup(page.request)
      }
      await expect(page.getByRole('button', { name: /开始演奏/ })).toBeVisible({ timeout: 20_000 })
    })

    await runStep('MIDI-DDSP：音频库、版本与卷帘控制', async () => {
      await (await visibleButton(page, 'MIDI-DDSP')).click()
      await expect(page.getByRole('heading', { name: /MIDI-DDSP (音频库|新建渲染)/ })).toBeVisible()
      await (await visibleButton(page, '音频库')).click()
      const tracks = page.locator('.recording-row')
      const trackCount = await tracks.count()
      expect(trackCount).toBeGreaterThan(0)
      for (let index = 0; index < Math.min(trackCount, 6); index += 1) await tracks.nth(index).click()
      const versionSelect = page.getByLabel('渲染版本')
      if (await versionSelect.count()) await selectAlternative(versionSelect)
      for (const title of ['放大时间轴', '缩小时间轴', '开启自动跟随', '显示完整曲目', '折叠卷帘']) {
        const control = page.getByTitle(title)
        if (await control.isVisible() && await control.isEnabled()) await control.click()
      }
      const expand = page.getByTitle('展开卷帘')
      if (await expand.isVisible()) await expand.click()
      inventories.push(await inventory(page, 'MIDI-DDSP 音频库'))
    })

    await runStep('MIDI-DDSP：浏览器与开发板播放控制', async () => {
      await (await visibleButton(page, 'MIDI-DDSP')).click()
      await (await visibleButton(page, '音频库')).click()
      try {
        await (await visibleButton(page, '当前浏览器')).click()
        const loop = page.getByRole('button', { name: '循环播放' })
        if (await loop.isEnabled()) {
          await loop.click()
          await page.getByRole('button', { name: '关闭循环播放' }).click()
        }
        const browserPlay = page.getByRole('button', { name: '浏览器播放', exact: true })
        if (await browserPlay.isEnabled()) {
          await browserPlay.click()
          await page.waitForTimeout(350)
          await page.getByRole('button', { name: '停止浏览器播放' }).click()
        }
        await (await visibleButton(page, '开发板喇叭')).click()
        const boardPlay = page.getByRole('button', { name: '开发板播放' })
        if (await boardPlay.isEnabled()) {
          await boardPlay.click()
          await expect(page.getByTitle('停止')).toBeVisible({ timeout: 15_000 })
          await page.getByTitle('停止').click()
        }
      } finally {
        await cleanup(page.request)
      }
    })

    await runStep('MIDI-DDSP：新建渲染配置与上传选择器', async () => {
      await (await visibleButton(page, '新建渲染')).click()
      await expect(page.getByRole('heading', { name: 'MIDI-DDSP 新建渲染' })).toBeVisible()
      const uploadButton = page.getByTitle('上传 MIDI')
      const chooserPromise = page.waitForEvent('filechooser')
      await uploadButton.click()
      await chooserPromise
      const schemes = page.locator('.midi-settings select')
      for (let index = 0; index < await schemes.count(); index += 1) await selectAlternative(schemes.nth(index))
      const ranges = page.locator('.midi-output-settings input[type="range"]')
      for (let index = 0; index < await ranges.count(); index += 1) await moveAndRestore(ranges.nth(index))
      const reset = page.getByTitle('恢复自动建议')
      if (await reset.isEnabled()) await reset.click()
      await expect(page.getByTitle('开始渲染')).toBeVisible()
      inventories.push(await inventory(page, 'MIDI-DDSP 新建渲染'))
    })

    await runStep('DDSP-VST：目录、设备、音色与全部参数页签', async () => {
      await (await visibleButton(page, 'DDSP-VST')).click()
      await expect(page.getByRole('heading', { name: 'DDSP-VST' })).toBeVisible()
      await Promise.all([
        page.waitForResponse((response) => (
          response.url().endsWith('/api/v1/ddsp-vst-effect/catalog/refresh')
          && response.ok()
        )),
        (await visibleButton(page, '刷新已发布 OM 音色')).click(),
      ])
      for (const label of ['音频输入', '音频输出', 'DDSP-VST 音色']) {
        await selectAlternative(page.getByLabel(label))
      }
      for (const group of ['音色', '输入门', '效果']) {
        const tab = page.getByRole('tab', { name: group, exact: true })
        await tab.click()
        await expect(tab).toHaveAttribute('aria-selected', 'true')
        const ranges = page.locator('.effect-parameter-grid input[type="range"]')
        for (let index = 0; index < await ranges.count(); index += 1) await moveAndRestore(ranges.nth(index))
      }
      inventories.push(await inventory(page, 'DDSP-VST'))
    })

    await runStep('DDSP-VST：OM 启动、校准与停止', async () => {
      await cleanup(page.request)
      await page.reload()
      await (await visibleButton(page, 'DDSP-VST')).click()
      try {
        await (await visibleButton(page, '启动')).click()
        await expect(page.getByRole('button', { name: '停止' })).toBeVisible({ timeout: 30_000 })
        await page.getByRole('tab', { name: '输入门', exact: true }).click()
        const calibrate = page.getByRole('button', { name: '重新校准' })
        await expect(calibrate).toBeEnabled()
        await calibrate.click()
        await page.waitForTimeout(1400)
      } finally {
        const stop = page.getByRole('button', { name: '停止', exact: true })
        if (await stop.isVisible()) await stop.click()
        else await cleanup(page.request)
      }
      await expect(page.getByRole('button', { name: '启动' })).toBeVisible({ timeout: 20_000 })
    })

    await runStep('设备：概览、运行环境与全局页签', async () => {
      await (await visibleButton(page, '设备')).click()
      for (const name of [/设备概览/, /音频设备/, /运行环境/]) {
        const tab = page.getByRole('tab', { name })
        await tab.click()
        await expect(tab).toHaveAttribute('aria-selected', 'true')
      }
      inventories.push(await inventory(page, '设备运行环境'))
    })

    await runStep('设备：蓝牙刷新、扫描和接口子页', async () => {
      await (await visibleButton(page, '设备')).click()
      await page.getByRole('tab', { name: /音频设备/ }).click()
      await page.getByTitle('刷新蓝牙设备').click()
      const scan = page.getByTitle('扫描蓝牙设备')
      await expect(scan).toBeVisible()
      await scan.click()
      await expect(scan).toBeEnabled({ timeout: 20_000 })
      for (const name of [/^输出 \d+$/, /^输入 \d+$/, /^MIDI \d+$/]) {
        const button = await visibleButton(page, name)
        await button.click()
      }
      inventories.push(await inventory(page, '设备 MIDI'))
    })

    await runStep('设备：扬声器设置、声道、步进器与短测试', async () => {
      await cleanup(page.request)
      await (await visibleButton(page, '刷新')).click()
      await (await visibleButton(page, '设备')).click()
      await page.getByRole('tab', { name: /音频设备/ }).click()
      await (await visibleButton(page, /^输出 \d+$/)).click()
      await (await visibleButton(page, '输出测试')).click()
      for (const channel of ['左声道', '双声道', '右声道']) {
        const button = page.getByRole('button', { name: channel, exact: true })
        if (await button.isVisible() && await button.isEnabled()) await button.click()
      }
      const ranges = page.getByLabel('输出测试参数').locator('input[type="range"]')
      for (let index = 0; index < await ranges.count(); index += 1) await moveAndRestore(ranges.nth(index))
      const decrease = page.getByTitle('减小')
      const increase = page.getByTitle('增大')
      if (await decrease.isEnabled()) await decrease.click()
      if (await increase.isEnabled()) await increase.click()
      try {
        await (await visibleButton(page, '开始测试')).click()
        await expect(page.getByRole('button', { name: '立即停止' })).toBeVisible({ timeout: 10_000 })
      } finally {
        const stop = page.getByRole('button', { name: '立即停止' })
        if (await stop.isVisible()) await stop.click()
        else await cleanup(page.request)
      }
      await expect(page.getByRole('button', { name: '开始测试' })).toBeVisible({ timeout: 10_000 })
    })

    await runStep('设备：麦克风设置、步进器与短测试', async () => {
      await cleanup(page.request)
      await (await visibleButton(page, '刷新')).click()
      await (await visibleButton(page, '设备')).click()
      await page.getByRole('tab', { name: /音频设备/ }).click()
      await (await visibleButton(page, '输入测试')).click()
      const ranges = page.getByLabel('输入测试参数').locator('input[type="range"]')
      for (let index = 0; index < await ranges.count(); index += 1) await moveAndRestore(ranges.nth(index))
      const decrease = page.getByTitle('减小')
      const increase = page.getByTitle('增大')
      if (await decrease.isEnabled()) await decrease.click()
      if (await increase.isEnabled()) await increase.click()
      try {
        await (await visibleButton(page, '开始输入测试')).click()
        await expect(page.getByRole('button', { name: '立即停止' })).toBeVisible({ timeout: 10_000 })
      } finally {
        const stop = page.getByRole('button', { name: '立即停止' })
        if (await stop.isVisible()) await stop.click()
        else await cleanup(page.request)
      }
      await expect(page.getByRole('button', { name: '开始输入测试' })).toBeVisible({ timeout: 10_000 })
      inventories.push(await inventory(page, '设备音频输入'))
    })
  } finally {
    await cleanup(page.request)
    const statusResponse = await page.request.get('/api/v1/status').catch(() => null)
    const finalStatus = statusResponse?.ok()
      ? await statusResponse.json() as { active_owner?: string | null }
      : null
    const report = {
      schema: 'case3-webui-live-controls-audit/v1',
      completed_at: new Date().toISOString(),
      base_url: environment?.PLAYWRIGHT_BASE_URL,
      viewport: { width: 1920, height: 969, touch: true },
      summary: {
        steps: steps.length,
        passed: steps.filter((step) => step.passed).length,
        failed: steps.filter((step) => !step.passed).length,
      },
      steps,
      inventories,
      errors: { consoleErrors, pageErrors, requestFailures, failedResponses },
      expected_media_aborts: expectedMediaAborts,
      final_active_owner: finalStatus?.active_owner ?? null,
      intentionally_not_activated: [
        '蓝牙连接、断开、配对与信任关系修改',
        'MIDI-DDSP 开始渲染（会创建新的长期任务和历史版本）',
        '实时录音（会创建新的 WAV 资产）',
        '下载链接（会在测试电脑生成文件）',
      ],
    }
    await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
    await context.close()
  }

  const failedSteps = steps.filter((step) => !step.passed)
  expect(failedSteps, JSON.stringify(failedSteps, null, 2)).toEqual([])
  expect(consoleErrors, JSON.stringify(consoleErrors, null, 2)).toEqual([])
  expect(pageErrors, JSON.stringify(pageErrors, null, 2)).toEqual([])
  expect(requestFailures, JSON.stringify(requestFailures, null, 2)).toEqual([])
  expect(failedResponses, JSON.stringify(failedResponses, null, 2)).toEqual([])
})
