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
  for (const label of ["实时识别", "掌纹注册", "模型状态", "系统状态"]) assert.match(app, new RegExp(label));
  for (const route of ["/api/bootstrap", "/api/candidates", "/api/candidate-runtimes", "/api/candidate-runs", "/api/recognitions", "/api/enrollment-sessions", "/api/evaluations", "/api/system-status"]) assert.match(api, new RegExp(route.replaceAll("/", "\\/")));
});

test("production UI is NPU-only and exposes only active runtime candidates", () => {
  assert.match(app, /生产服务固定使用 NPU/);
  assert.match(app, /运行模型/);
  assert.match(app, /任务类型/);
  assert.match(app, /模态/);
  assert.match(app, /NPU 状态/);
  assert.match(app, /eligibilityReason/);
  assert.match(app, /诊断阻断/);
  assert.doesNotMatch(app, /label="计算后端"/);
  assert.doesNotMatch(app, /ARM CPU/);
  assert.match(api, /availableBackends:\s*\["npu"\]/);
  assert.match(app, /availableBackends\.includes\("npu"\)/);
});

test("runtime model status is read-only and never calls the blocked comparison API", () => {
  assert.match(app, /RUNTIME MODELS/);
  assert.match(app, /完整候选审计记录/);
  assert.match(app, /candidate-audit-list/);
  assert.doesNotMatch(app, /startComparison|api\.comparison|ComparisonTask|ComparisonRequest/);
  assert.doesNotMatch(api, /\/api\/comparisons/);
});

test("heterogeneous candidate experiments remain separate from production selectors", () => {
  assert.match(app, /候选模型实验/);
  assert.match(app, /运行候选模型/);
  assert.match(app, /inputKind === "rgb_pair"/);
  assert.match(app, /candidateRuntimes/);
  assert.match(api, /runCandidate\(candidateId: string/);
  assert.match(api, /candidate-runs/);
});

test("recognition result keeps uncalibrated results in candidate-only mode and uses explicit total timing", () => {
  assert.match(app, /候选检索/);
  assert.match(app, /不判定身份是否通过/);
  assert.match(app, /timing\["total ms"\]/);
});

test("enrollment assigns person IDs automatically and preserves an ID for the other palm", () => {
  assert.match(app, /P-000001/);
  assert.match(app, /开始采集后自动分配/);
  assert.match(app, /登记同一人另一只掌/);
  assert.match(app, /登记下一位人员/);
  assert.doesNotMatch(app, /请填写人员编号/);
  assert.match(api, /personId\?: string/);
});

test("administrator review uses the server session mode and makes review status explicit", () => {
  assert.match(app, /const sessionMode = session\?\.mode === "assisted"/);
  assert.match(app, /管理员审阅：可剔除样本或确认保存/);
  assert.match(app, /开始管理员采集/);
  assert.match(api, /review_actions_enabled/);
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

test("camera recognition opens automatically and waits for a stable palm without a shutter button", () => {
  assert.match(app, /setCameraActive\(true\)/);
  assert.match(app, /useAutomaticPalmTrigger/);
  assert.match(app, /AUTO_BASELINE_FRAMES/);
  assert.match(app, /请将掌心放入采集框/);
  assert.match(app, /识别完成，请移开掌心后继续/);
  assert.doesNotMatch(app, />拍摄并识别</);
});

test("camera paths always use automatic ROI localization", () => {
  assert.match(app, /assumeRoi:\s*false/);
  assert.match(api, /async recognizeCamera[\s\S]*?assume_roi:\s*false/);
  assert.match(api, /async createEnrollmentSession[\s\S]*?assume_roi:\s*false/);
  assert.match(api, /options\.file \? options\.assumeRoi : false/);
});
