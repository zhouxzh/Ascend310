import { expect, test } from "@playwright/test";

test("fake API supports the main Case 1 workflow", async ({ page }) => {
  await page.goto("/?fake=1");
  await expect(page.getByRole("heading", { name: "人脸考勤" })).toBeVisible();
  await expect(page.getByText("今日最近记录")).toBeVisible();

  await page.getByRole("button", { name: /用户管理/ }).click();
  await page.getByLabel("姓名").fill("浏览器测试");
  await page.getByRole("tab", { name: /设备摄像头/ }).click();
  await page.getByRole("button", { name: "抓取画面" }).click();
  await page.getByRole("button", { name: "注册用户" }).click();
  await expect(page.getByText("用户注册成功")).toBeVisible();
  await expect(page.getByText("浏览器测试")).toBeVisible();
});
