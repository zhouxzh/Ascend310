const { chromium } = require("../samples/case3/webui/node_modules/playwright");

const baseUrl = "https://zhouxzh.github.io/Ascend310/";
const stamp = Date.now();

async function inspect(page, path, viewport, screenshot) {
  const consoleErrors = [];
  const pageErrors = [];
  const requestFailures = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("requestfailed", (request) => {
    requestFailures.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`);
  });

  await page.setViewportSize(viewport);
  await page.goto(`${baseUrl}${path}?verify=${stamp}`, {
    waitUntil: "domcontentloaded",
    timeout: 60_000,
  });
  await page.waitForTimeout(2_000);
  await page.locator("img").evaluateAll((images) => {
    for (const image of images) image.loading = "eager";
  });
  await page.evaluate(async () => {
    const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
    const step = Math.max(300, Math.floor(window.innerHeight * 0.75));
    for (let offset = 0; offset < document.documentElement.scrollHeight; offset += step) {
      window.scrollTo(0, offset);
      await pause(100);
    }
    window.scrollTo(0, document.documentElement.scrollHeight);
    await pause(500);
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(500);
  const result = await page.evaluate(() => {
    const images = [...document.images];
    const tablesOutsideViewport = [...document.querySelectorAll("table")].filter((table) => {
      const box = table.getBoundingClientRect();
      return box.left < 0 || box.right > document.documentElement.clientWidth;
    }).length;
    return {
      url: location.href,
      title: document.title,
      heading: document.querySelector("h1")?.textContent?.trim() ?? "",
      hasDdspContent: document.body.textContent.includes("DDSP-VST"),
      imageCount: images.length,
      brokenImages: images.filter((image) => !image.complete || image.naturalWidth === 0).map((image) => image.src),
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      tablesOutsideViewport,
    };
  });
  await page.screenshot({ path: screenshot, fullPage: false });
  return { ...result, consoleErrors, pageErrors, requestFailures };
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    proxy: { server: "http://127.0.0.1:7890" },
  });
  try {
    const desktop = await inspect(
      await browser.newPage(),
      "experiment/case3.html",
      { width: 1366, height: 768 },
      "tmp/live-case3-desktop.png",
    );
    const mobile = await inspect(
      await browser.newPage(),
      "experiment/case3.html",
      { width: 390, height: 844 },
      "tmp/live-case3-mobile.png",
    );
    const home = await inspect(
      await browser.newPage(),
      "",
      { width: 1366, height: 768 },
      "tmp/live-home-desktop.png",
    );
    const experiment = await inspect(
      await browser.newPage(),
      "experiment/",
      { width: 1366, height: 768 },
      "tmp/live-experiment-desktop.png",
    );
    const results = { desktop, mobile, home, experiment };
    console.log(JSON.stringify(results, null, 2));

    const failed = [desktop, mobile].some(
      (result) =>
        !result.hasDdspContent ||
        result.horizontalOverflow ||
        result.tablesOutsideViewport ||
        result.consoleErrors.length ||
        result.pageErrors.length ||
        result.requestFailures.length,
    ) || [home, experiment].some(
      (result) =>
        result.consoleErrors.length ||
        result.pageErrors.length ||
        result.requestFailures.length,
    );
    process.exitCode = failed ? 1 : 0;
  } finally {
    await browser.close();
  }
})();
