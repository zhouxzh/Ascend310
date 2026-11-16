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

test("live recognition keeps input, model, and recognition readiness visible", () => {
  assert.match(app, /当前识别状态/);
  assert.match(app, /采集输入/);
  assert.match(app, /运行模型/);
  assert.match(app, /识别状态/);
  assert.match(app, /live-readiness/);
  assert.match(styles, /\.live-readiness\s*\{/);
  assert.match(styles, /\.live-readiness__item\s*\{/);
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
  assert.match(app, /等待手掌进入画面/);
  assert.match(app, /result\.hands\.map/);
  assert.match(app, /boxes=\{cameraActive \? liveDetection\?\.hands : result\?\.hands\}/);
  assert.doesNotMatch(app, /guideConfig=|guide=\{cameraActive\}/);
  assert.match(app, /识别完成，请移开掌心后继续/);
  assert.doesNotMatch(app, />拍摄并识别</);
});

test("live detector polls serially and laptop camera view preserves the full image", () => {
  assert.match(api, /async detectCamera/);
  assert.match(api, /\/detections/);
  assert.match(app, /await api\.detectCamera\(cameraDevice, resolution, config\.detectorId\)/);
  assert.match(app, /if \(!cancelled\) timer = setTimeout\(poll, 60\)/);
  assert.match(app, /const preferredLiveResolution/);
  assert.match(app, /const frameUrl = liveDetection\?\.previewUrl/);
  assert.doesNotMatch(app, /useCameraPreview\(cameraActive, cameraDevice, resolution, cameraRetryToken/);
  assert.match(app, /cameraActive \? liveDetection\?\.previewUrl \|\| frameUrl : preview/);
  assert.match(styles, /aspect-ratio:\s*16\s*\/\s*9/);
  assert.match(styles, /object-position:\s*center/);
  assert.match(styles, /\.image-stage__image--contain\s*\{\s*object-fit:\s*contain/);
  assert.match(styles, /@media \(max-width: 1500px\)/);
  assert.match(styles, /\.page--live \.live-layout\s*\{\s*grid-template-columns:\s*minmax\(0, 1fr\)/);
});

test("registration preview hands camera ownership over and cancels on page exit", () => {
  assert.match(app, /const previewActive = !busy && !running && !session/);
  assert.match(app, /await api\.closeCamera\(cameraDevice, resolution\)/);
  assert.match(app, /sessionRef\.current = session/);
  assert.match(app, /api\.deleteEnrollmentSession\(current\.id\)/);
  assert.doesNotMatch(app, /Local reset remains valid after a transport error/);
  assert.match(styles, /@media \(min-width: 900px\)/);
  assert.match(styles, /\.page, \.page--live\s*\{[^}]*overflow: hidden/);
  assert.match(styles, /\.hand-results\s*\{[^}]*overflow-y: auto/);
});

test("device discovery and capture errors remain distinct from API connection errors", () => {
  assert.match(app, /摄像头服务未连接/);
  assert.match(app, /已发现节点，取流失败/);
  assert.match(app, /模型服务未连接/);
  assert.match(app, /cameraError \? "摄像头取流失败"/);
  assert.match(app, /cameraError \? "取流失败"/);
  assert.match(app, /重试摄像头/);
});

test("camera paths always use automatic ROI localization", () => {
  assert.match(app, /assumeRoi:\s*false/);
  assert.match(api, /async recognizeCamera[\s\S]*?assume_roi:\s*false/);
  assert.match(api, /async createEnrollmentSession[\s\S]*?assume_roi:\s*false/);
  assert.match(api, /options\.file \? options\.assumeRoi : false/);
});

test("palm detector selection is separate from the palmprint embedding model", () => {
  assert.match(app, /手掌检测模型/);
  assert.match(app, /掌纹特征模型/);
  assert.match(app, /bootstrap\.detectors/);
  assert.match(app, /config\.detectorId/);
  assert.match(app, /直接拉伸到方形输入/);
  assert.match(api, /form\.append\("detector_id", config\.detectorId\)/);
  assert.match(api, /detector_id: config\.detectorId/);
  assert.match(api, /defaults\.detector_id/);
});

test("two-class detector boxes expose left and right palm labels", () => {
  assert.match(api, /detectedPalmSide: stringValue\(item\.detected_palm_side\)/);
  assert.match(app, /hand\.detectedPalmSide === "right" \? "右掌"/);
  assert.match(app, /boxes=\{session\?\.detectionBoxes \|\| enrollmentDetection\?\.hands\}/);
  assert.match(app, /result\.detectedPalmSide \|\| result\.palmSide \|\| best\?\.palmSide/);
});
