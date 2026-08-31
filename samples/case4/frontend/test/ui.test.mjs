import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const [app, styles, api, ui] = await Promise.all([
  readFile(join(root, "src", "App.tsx"), "utf8"),
  readFile(join(root, "src", "styles.css"), "utf8"),
  readFile(join(root, "src", "lib", "api.ts"), "utf8"),
  readFile(join(root, "src", "components", "ui.tsx"), "utf8"),
]);

test("four task pages and primary API actions are present", () => {
  for (const label of ["实时识别", "掌纹注册", "模型候选审计", "系统状态"]) assert.match(app, new RegExp(label));
  for (const route of ["/api/bootstrap", "/api/candidates", "/api/recognitions", "/api/enrollment-sessions", "/api/evaluations", "/api/system-status"]) assert.match(api, new RegExp(route.replaceAll("/", "\\/")));
});

test("production UI is NPU-only and keeps audit candidates visible", () => {
  assert.match(app, /生产服务固定使用 NPU/);
  assert.match(app, /候选矩阵/);
  assert.match(app, /任务类型/);
  assert.match(app, /模态/);
  assert.match(app, /NPU 状态/);
  assert.match(app, /eligibilityReason/);
  assert.doesNotMatch(app, /label="计算后端"/);
  assert.doesNotMatch(app, /ARM CPU/);
  assert.match(api, /availableBackends:\s*\["npu"\]/);
  assert.match(app, /availableBackends\.includes\("npu"\)/);
});

test("candidate audit is read-only and never calls the blocked comparison API", () => {
  assert.match(app, /READ-ONLY AUDIT/);
  assert.match(app, /python -m tools\.offline\.benchmark/);
  assert.match(app, /candidate-audit-list/);
  assert.doesNotMatch(app, /startComparison|api\.comparison|ComparisonTask|ComparisonRequest/);
  assert.doesNotMatch(api, /\/api\/comparisons/);
});

test("recognition result distinguishes an empty template store and uses explicit total timing", () => {
  assert.match(app, /未通过匹配/);
  assert.match(app, /timing\["total ms"\]/);
});

test("known board alarm remains a warning in the status palette", () => {
  assert.match(ui, /only an actual runtime failure/);
  assert.match(ui, /warning/);
});

test("touch sizing and desktop no-root-scroll layout are encoded in CSS", () => {
  assert.match(styles, /\.app-shell\s*\{[^}]*height:\s*100dvh/);
  assert.match(styles, /body\s*\{[^}]*overflow-x:\s*hidden/);
  assert.match(styles, /\.button\s*\{[^}]*min-height:\s*var\(--touch-min\)/);
  assert.match(styles, /--font-body:\s*22px/);
  assert.match(styles, /--font-aux:\s*18px/);
  assert.match(styles, /\.field__control\s*\{[^}]*font-size:\s*var\(--font-body\)/);
  assert.match(styles, /\.field__label\s*\{[^}]*font-size:\s*var\(--font-body\)/);
  assert.match(styles, /@media\s*\(max-width:\s*1199px\)/);
  assert.match(styles, /\.candidate-audit-list\s*\{/);
  assert.match(styles, /\.candidate-audit-row\s*\{/);
});

test("camera preview is sequential, low-bandwidth, and exposes full-HD mode", () => {
  assert.match(app, /useCameraPreview/);
  assert.doesNotMatch(app, /const update = \(\) => \{ if \(active\) setFrameUrl/);
  assert.match(app, /CAMERA_PREVIEW_DELAY_MS\s*=\s*80/);
  assert.match(api, /preview:\s*"true"/);
  assert.match(api, /max_width:\s*"960"/);
  assert.match(api, /openCamera\(device: string, resolution: string, session: string\)/);
  assert.match(api, /closeCamera\(device: string, resolution\?: string, session\?: string\)/);
  assert.match(api, /session\)/);
  assert.match(app, /openCamera\(device, resolution, session\)/);
  assert.match(app, /closeCamera\(device, resolution, session\)/);
  assert.match(app, /摄像头节点已变化/);
  assert.match(api, /1920x1080/);
});
