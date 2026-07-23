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
    await page.goto('http://127.0.0.1:8765/')
    await expect(page.getByRole('heading', { name: '实时演奏' })).toBeVisible()
    await expect(page.locator('.piano')).toBeVisible()

    const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(horizontalOverflow).toBeLessThanOrEqual(1)

    await mkdir('../reports/webui/screenshots', { recursive: true })
    await page.screenshot({
      path: `../reports/webui/screenshots/studio-${viewport.name}.png`,
      fullPage: true,
    })
  })
}

test('all workspaces can be opened', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('http://127.0.0.1:8765/')
  await page.getByRole('button', { name: 'MIDI-DDSP' }).first().click()
  await expect(page.getByRole('heading', { name: 'MIDI-DDSP Player' })).toBeVisible()
  await page.screenshot({ path: '../reports/webui/screenshots/studio-midi-ddsp.png', fullPage: true })
  await page.getByRole('button', { name: '实验' }).first().click()
  await expect(page.getByRole('heading', { name: '模型实验' })).toBeVisible()
  await page.screenshot({ path: '../reports/webui/screenshots/studio-lab.png', fullPage: true })
  await page.getByRole('button', { name: '设备' }).first().click()
  await expect(page.getByRole('heading', { name: '系统与设备' })).toBeVisible()
  await page.screenshot({ path: '../reports/webui/screenshots/studio-devices.png', fullPage: true })
})
