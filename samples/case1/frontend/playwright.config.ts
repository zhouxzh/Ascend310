import { defineConfig } from "@playwright/test";

const externalBaseUrl = process.env.PLAYWRIGHT_BASE_URL;
const baseURL = externalBaseUrl ?? "http://127.0.0.1:4173";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results/playwright",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  use: {
    baseURL,
    browserName: "chromium",
    headless: true,
    trace: "retain-on-failure",
  },
  webServer: externalBaseUrl
    ? undefined
    : {
        command: "npm run dev -- --host 127.0.0.1 --port 4173",
        url: baseURL,
        reuseExistingServer: !process.env.CI,
      },
});
