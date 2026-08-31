import { defineConfig } from "@playwright/test";
var externalBaseUrl = process.env.PLAYWRIGHT_BASE_URL;
var baseURL = externalBaseUrl !== null && externalBaseUrl !== void 0 ? externalBaseUrl : "http://127.0.0.1:4173";
export default defineConfig({
    testDir: "./e2e",
    outputDir: "./test-results/playwright",
    timeout: 30000,
    expect: { timeout: 5000 },
    fullyParallel: true,
    use: {
        baseURL: baseURL,
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
