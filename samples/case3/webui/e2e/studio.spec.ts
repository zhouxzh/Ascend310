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
    await expect(page.getByText('MIDI-DDSP Player')).toBeVisible({ timeout: 15_000 })
    await page.getByRole('button', { name: 'DDSP-VST' }).first().click()
    await expect(page.locator('.piano')).toBeVisible()

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
  await expect(page.getByRole('heading', { name: 'MIDI-DDSP Player' })).toBeVisible()
  await page.screenshot({ path: '../reports/webui/screenshots/studio-midi-ddsp.png', fullPage: true })
  await page.getByRole('button', { name: 'DDSP-VST' }).first().click()
  await expect(page.getByRole('heading', { name: 'DDSP-VST Synth' })).toBeVisible()
  await page.screenshot({ path: '../reports/webui/screenshots/studio-ddsp-vst.png', fullPage: true })
  await page.getByRole('button', { name: '实验' }).first().click()
  await expect(page.getByRole('heading', { name: '模型实验' })).toBeVisible()
  await page.screenshot({ path: '../reports/webui/screenshots/studio-lab.png', fullPage: true })
  await page.getByRole('button', { name: '设备' }).first().click()
  await expect(page.getByRole('heading', { name: '系统与设备' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '扬声器输出测试' })).toBeVisible()
  await expect(page.getByRole('button', { name: '扬声器' })).toHaveCount(0)
  await page.screenshot({ path: '../reports/webui/screenshots/studio-devices.png', fullPage: true })
})
