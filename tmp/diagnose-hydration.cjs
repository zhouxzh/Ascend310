const { chromium } = require("../samples/case3/webui/node_modules/playwright");

const url = process.env.HYDRATION_URL ??
  `https://zhouxzh.github.io/Ascend310/experiment/?hydrate=${Date.now()}`;

function firstDifference(left, right) {
  const limit = Math.min(left.length, right.length);
  let index = 0;
  while (index < limit && left[index] === right[index]) index += 1;
  return {
    index,
    before: left.slice(Math.max(0, index - 250), index + 500),
    after: right.slice(Math.max(0, index - 250), index + 500),
  };
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(url.includes("127.0.0.1")
      ? {}
      : { proxy: { server: "http://127.0.0.1:7890" } }),
  });
  try {
    const staticContext = await browser.newContext({ javaScriptEnabled: false });
    const staticPage = await staticContext.newPage();
    await staticPage.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });

    const liveContext = await browser.newContext();
    const livePage = await liveContext.newPage();
    const consoleErrors = [];
    livePage.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await livePage.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });
    await livePage.waitForTimeout(3_000);

    const selectors = ["#app", "main", ".vp-page", ".theme-hope-content"];
    const comparisons = {};
    for (const selector of selectors) {
      const before = await staticPage.locator(selector).first().evaluate((node) => node.outerHTML).catch(() => "");
      const after = await livePage.locator(selector).first().evaluate((node) => node.outerHTML).catch(() => "");
      comparisons[selector] = {
        beforeLength: before.length,
        afterLength: after.length,
        ...firstDifference(before, after),
      };
    }
    console.log(JSON.stringify({ consoleErrors, comparisons }, null, 2));
  } finally {
    await browser.close();
  }
})();
