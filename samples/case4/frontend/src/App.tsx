import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Camera,
  Check,
  ChevronRight,
  CircleHelp,
  Database,
  FileImage,
  ScanSearch,
  Fingerprint,
  HardDrive,
  Images,
  Info,
  LayoutDashboard,
  LoaderCircle,
  Menu,
  Monitor,
  RefreshCw,
  ScanLine,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  Upload,
  UserPlus,
  Wifi,
  X,
  Zap,
} from "lucide-react";
import { api, ApiError } from "./lib/api";
import type {
  BootstrapData,
  CandidateOption,
  CandidateRuntimeOption,
  CandidateRunResult,
  CameraDevice,
  DetectorOption,
  EnrollmentSession,
  HealthStatus,
  ModelOption,
  PageKey,
  RecognitionConfig,
  RecognitionHand,
  RecognitionResult,
  SystemItem,
  SystemStatus,
  TemplateItem,
} from "./lib/types";
import { Drawer } from "./components/Drawer";
import { ImageStage } from "./components/ImageStage";
import { LatencyChart } from "./components/LatencyChart";
import {
  Button,
  cx,
  MetricTile,
  Notice,
  Panel,
  SelectField,
  StatusPill,
  TextField,
  Toggle,
  toneForStatus,
} from "./components/ui";

// Never fabricate a model or candidate when the API is unavailable. This
// empty state keeps a disconnected browser from presenting a selectable model
// that has not been confirmed by the running board service.
const EMPTY_BOOTSTRAP: BootstrapData = {
  appVersion: "服务未连接",
  models: [],
  detectors: [],
  candidates: [],
  datasets: [],
  defaults: { modelId: "", detectorId: "", backend: "npu", precision: "mixed_fp16", threshold: 0.75, cameraResolution: "1280x720" },
};

const NAV_ITEMS: Array<{ key: PageKey; label: string; icon: typeof Activity; hint: string }> = [
  { key: "live", label: "实时识别", icon: ScanLine, hint: "上传或拍摄并匹配模板" },
  { key: "enrollment", label: "掌纹注册", icon: UserPlus, hint: "采集多张样本建立模板" },
  { key: "evaluation", label: "模型状态", icon: BarChart3, hint: "查看当前运行模型与人工测试状态" },
  { key: "system", label: "系统状态", icon: Activity, hint: "查看设备和资产健康度" },
];

const formatMs = (value?: number) => (value === undefined ? "--" : `${value.toFixed(1)} ms`);
const formatDate = (value?: string) => {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
};

// Keep preview requests serialized while avoiding the old ~2 FPS cadence.
// The board capture itself is typically 50-60 ms, so 80 ms gives roughly
// 7 FPS without allowing slow V4L2/JPEG requests to queue behind each other.
const CAMERA_PREVIEW_DELAY_MS = 80;
const AUTO_BASELINE_FRAMES = 6;
const AUTO_PALM_ENTER_DELTA = 12;
const AUTO_PALM_RELEASE_DELTA = 6;
const AUTO_STABLE_DELTA = 3.2;
const AUTO_STABLE_FRAMES = 3;
const AUTO_RELEASE_FRAMES = 3;

const pageFromHash = (): PageKey => {
  const value = window.location.hash.replace(/^#\/?/, "") as PageKey;
  return NAV_ITEMS.some((item) => item.key === value) ? value : "live";
};

const preferredCameraResolution = (camera?: CameraDevice): string => {
  if (!camera) return "1280x720";
  return ["1280x720", "1920x1080", "640x480", "320x240"].find((value) => camera.resolutions.includes(value))
    || camera.resolutions[0]
    || "1280x720";
};

const preferredLiveResolution = (camera?: CameraDevice): string => {
  if (!camera) return "320x240";
  return ["320x240", "640x480", "1280x720"].find((value) => camera.resolutions.includes(value))
    || camera.resolutions[0]
    || "320x240";
};

let cameraSessionSequence = 0;

/**
 * Poll one camera request at a time.  A fixed interval can create overlapping
 * V4L2 reads when a USB frame or JPEG encode is slow, which makes the browser
 * show stale frames several seconds behind the camera.  The next request is
 * scheduled only after the previous one has completed.
 */
function useCameraPreview(
  active: boolean,
  device: string,
  resolution: string,
  retryToken: number,
  announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void,
  onCaptureResolution?: (value?: string) => void,
): { frameUrl?: string; error?: string } {
  const [frameUrl, setFrameUrl] = useState<string>();
  const [error, setError] = useState<string>();
  const frameRef = useRef<string | undefined>(undefined);
  const reportedError = useRef(false);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | undefined;
    const session = `camera-${Date.now().toString(36)}-${(++cameraSessionSequence).toString(36)}`;
    reportedError.current = false;
    setError(undefined);

    const releaseFrame = () => {
      if (frameRef.current) URL.revokeObjectURL(frameRef.current);
      frameRef.current = undefined;
      setFrameUrl(undefined);
    };
    const wait = (milliseconds: number) => new Promise<void>((resolve) => {
      timer = window.setTimeout(resolve, milliseconds);
    });
    const poll = async () => {
      try {
        await api.openCamera(device, resolution, session);
        if (stopped) {
          await api.closeCamera(device, resolution, session).catch(() => undefined);
          return;
        }
      } catch (error) {
        if (!stopped) {
          reportedError.current = true;
          const message = error instanceof ApiError ? `摄像头打开失败：${error.message}` : "摄像头打开失败。";
          setError(message);
          announce(message, "danger");
        }
        return;
      }
      while (!stopped) {
        controller = new AbortController();
        try {
          const next = await api.fetchCameraFrame(device, resolution, controller.signal, session);
          if (stopped) {
            URL.revokeObjectURL(next.url);
            break;
          }
          const previous = frameRef.current;
          frameRef.current = next.url;
          setFrameUrl(next.url);
          onCaptureResolution?.(next.captureResolution);
          if (previous) URL.revokeObjectURL(previous);
          reportedError.current = false;
          setError(undefined);
          // Schedule the next request only after this one completes.  This
          // keeps the displayed frame current without building a backlog when
          // V4L2 or JPEG encoding is slower than the target cadence.
          await wait(CAMERA_PREVIEW_DELAY_MS);
        } catch (error) {
          if (stopped || (error instanceof DOMException && error.name === "AbortError")) break;
          const message = error instanceof ApiError ? `摄像头预览失败：${error.message}` : "摄像头预览失败。";
          setError(message);
          if (!reportedError.current) {
            reportedError.current = true;
            announce(message, "danger");
          }
          await api.closeCamera(device, resolution, session).catch(() => undefined);
          return;
        }
      }
    };

    if (active && device && resolution) void poll();
    return () => {
      stopped = true;
      controller?.abort();
      if (timer !== undefined) window.clearTimeout(timer);
      releaseFrame();
      onCaptureResolution?.(undefined);
      if (active && device) void api.closeCamera(device, resolution, session).catch(() => undefined);
    };
  }, [active, device, resolution, retryToken, announce, onCaptureResolution]);

  return { frameUrl, error };
}

type AutomaticRecognitionPhase = "inactive" | "calibrating" | "waiting_hand" | "stabilizing" | "recognizing" | "remove_hand";

const automaticRecognitionMessage = (phase: AutomaticRecognitionPhase): string => ({
  inactive: "等待摄像头",
  calibrating: "正在建立空画面参考，请先移开手掌",
  waiting_hand: "等待手掌进入画面",
  stabilizing: "已检测到掌心，请保持稳定",
  recognizing: "正在自动识别",
  remove_hand: "识别完成，请移开掌心后继续",
}[phase]);

const meanAbsoluteDifference = (first: Float32Array, second: Float32Array): number => {
  let total = 0;
  for (let index = 0; index < first.length; index += 1) total += Math.abs(first[index] - second[index]);
  return total / first.length;
};

/**
 * Watch full-frame temporal change to avoid invoking NPU on an empty scene.
 * The board detector remains the only source of palm location.
 */
function useAutomaticPalmTrigger(
  active: boolean,
  frameUrl: string | undefined,
  sourceKey: string,
  busy: boolean,
  onReady: () => void,
): { phase: AutomaticRecognitionPhase; message: string } {
  const [phase, setPhase] = useState<AutomaticRecognitionPhase>(active ? "calibrating" : "inactive");
  const baselineFramesRef = useRef<Float32Array[]>([]);
  const baselineRef = useRef<Float32Array | undefined>(undefined);
  const previousRef = useRef<Float32Array | undefined>(undefined);
  const stableFramesRef = useRef(0);
  const releasedFramesRef = useRef(0);
  const triggeredRef = useRef(false);
  const onReadyRef = useRef(onReady);

  useEffect(() => { onReadyRef.current = onReady; }, [onReady]);
  useEffect(() => {
    baselineFramesRef.current = [];
    baselineRef.current = undefined;
    previousRef.current = undefined;
    stableFramesRef.current = 0;
    releasedFramesRef.current = 0;
    triggeredRef.current = false;
    setPhase(active ? "calibrating" : "inactive");
  }, [active, sourceKey]);

  useEffect(() => {
    if (!active || !frameUrl) return undefined;
    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (cancelled || !image.naturalWidth || !image.naturalHeight) return;
      const side = 72;
      const canvas = document.createElement("canvas");
      canvas.width = side;
      canvas.height = side;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) return;
      context.drawImage(image, 0, 0, image.naturalWidth, image.naturalHeight, 0, 0, side, side);
      const rgba = context.getImageData(0, 0, side, side).data;
      const current = new Float32Array(side * side);
      for (let pixel = 0; pixel < current.length; pixel += 1) {
        const offset = pixel * 4;
        current[pixel] = 0.299 * rgba[offset] + 0.587 * rgba[offset + 1] + 0.114 * rgba[offset + 2];
      }
      const baselineFrames = baselineFramesRef.current;
      if (!baselineRef.current) {
        baselineFrames.push(current);
        if (baselineFrames.length < AUTO_BASELINE_FRAMES) {
          setPhase("calibrating");
          return;
        }
        const baseline = new Float32Array(current.length);
        for (const frame of baselineFrames) for (let index = 0; index < baseline.length; index += 1) baseline[index] += frame[index] / baselineFrames.length;
        baselineRef.current = baseline;
        previousRef.current = current;
        setPhase("waiting_hand");
        return;
      }
      const baseline = baselineRef.current;
      const deltaFromBaseline = meanAbsoluteDifference(current, baseline);
      const deltaFromPrevious = previousRef.current ? meanAbsoluteDifference(current, previousRef.current) : Number.POSITIVE_INFINITY;
      previousRef.current = current;
      if (!triggeredRef.current) {
        if (deltaFromBaseline < AUTO_PALM_RELEASE_DELTA) {
          stableFramesRef.current = 0;
          for (let index = 0; index < baseline.length; index += 1) baseline[index] = baseline[index] * 0.96 + current[index] * 0.04;
          setPhase("waiting_hand");
          return;
        }
        if (deltaFromBaseline >= AUTO_PALM_ENTER_DELTA && deltaFromPrevious <= AUTO_STABLE_DELTA) stableFramesRef.current += 1;
        else stableFramesRef.current = 0;
        if (stableFramesRef.current >= AUTO_STABLE_FRAMES && !busy) {
          triggeredRef.current = true;
          releasedFramesRef.current = 0;
          setPhase("recognizing");
          onReadyRef.current();
        } else {
          setPhase("stabilizing");
        }
        return;
      }
      if (deltaFromBaseline < AUTO_PALM_RELEASE_DELTA) releasedFramesRef.current += 1;
      else releasedFramesRef.current = 0;
      if (releasedFramesRef.current >= AUTO_RELEASE_FRAMES) {
        triggeredRef.current = false;
        stableFramesRef.current = 0;
        setPhase("waiting_hand");
      } else {
        setPhase("remove_hand");
      }
    };
    image.src = frameUrl;
    return () => { cancelled = true; image.onload = null; image.onerror = null; };
  }, [active, busy, frameUrl]);

  return { phase, message: automaticRecognitionMessage(phase) };
}

function useToast() {
  const [toast, setToast] = useState<{ tone: "success" | "warning" | "danger" | "info"; message: string }>();
  const announce = useCallback((message: string, tone: "success" | "warning" | "danger" | "info" = "info") => {
    setToast({ message, tone });
    window.setTimeout(() => setToast(undefined), 4200);
  }, []);
  return { toast, announce };
}

function App() {
  const [page, setPage] = useState<PageKey>(() => pageFromHash());
  const [bootstrap, setBootstrap] = useState<BootstrapData>(EMPTY_BOOTSTRAP);
  const [candidates, setCandidates] = useState<CandidateOption[]>([]);
  const [candidateRuntimes, setCandidateRuntimes] = useState<CandidateRuntimeOption[]>([]);
  const [health, setHealth] = useState<HealthStatus>({ status: "unknown", message: "尚未连接服务" });
  const [cameras, setCameras] = useState<CameraDevice[]>([]);
  const [modelListReady, setModelListReady] = useState(false);
  const [cameraListReady, setCameraListReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [drawer, setDrawer] = useState<"config" | "templates" | "system" | null>(null);
  const [config, setConfig] = useState<RecognitionConfig>({
    modelId: EMPTY_BOOTSTRAP.defaults.modelId,
    detectorId: EMPTY_BOOTSTRAP.defaults.detectorId,
    backend: "npu",
    precision: EMPTY_BOOTSTRAP.defaults.precision,
    threshold: EMPTY_BOOTSTRAP.defaults.threshold,
    // Full camera frames always use palm detection. The pre-cropped
    // switch remains an opt-in only for uploaded 128 x 128 ROI.
    assumeRoi: false,
  });
  const { toast, announce } = useToast();

  const refresh = useCallback(async () => {
    setLoading(true);
    const [bootstrapResult, healthResult, camerasResult, candidatesResult, candidateRuntimesResult] = await Promise.allSettled([api.bootstrap(), api.health(), api.cameras(), api.candidates(), api.candidateRuntimes()]);
    setModelListReady(bootstrapResult.status === "fulfilled");
    setCameraListReady(camerasResult.status === "fulfilled");
    if (bootstrapResult.status === "fulfilled") {
      setBootstrap(bootstrapResult.value);
      setConfig((current) => ({
        ...current,
        modelId: (() => {
          const admitted = bootstrapResult.value.models.filter((model) => model.usableForRecognition !== false && model.availableBackends.includes("npu"));
          if (admitted.some((model) => model.id === current.modelId)) return current.modelId;
          return admitted.find((model) => model.id === bootstrapResult.value.defaults.modelId)?.id || admitted[0]?.id || "";
        })(),
        detectorId: (() => {
          const available = bootstrapResult.value.detectors.filter((detector) => detector.available);
          if (available.some((detector) => detector.id === current.detectorId)) return current.detectorId;
          return available.find((detector) => detector.id === bootstrapResult.value.defaults.detectorId)?.id || available[0]?.id || "";
        })(),
        backend: "npu",
        precision: bootstrapResult.value.defaults.precision || current.precision,
        threshold: bootstrapResult.value.defaults.threshold || current.threshold,
      }));
    } else {
      setBootstrap(EMPTY_BOOTSTRAP);
      setConfig((current) => ({ ...current, modelId: "", detectorId: "", backend: "npu", precision: "mixed_fp16" }));
    }
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    else setHealth({ status: "unknown", message: "无法连接工作台服务" });
    if (camerasResult.status === "fulfilled") setCameras(camerasResult.value);
    else setCameras([]);
    if (candidatesResult.status === "fulfilled") setCandidates(candidatesResult.value);
    else setCandidates([]);
    if (candidateRuntimesResult.status === "fulfilled") setCandidateRuntimes(candidateRuntimesResult.value);
    else setCandidateRuntimes([]);
    const failed = [bootstrapResult, healthResult, camerasResult, candidatesResult, candidateRuntimesResult].some((result) => result.status === "rejected");
    if (failed) announce("工作台服务不可用，未显示本地伪造模型。", "warning");
    setLoading(false);
  }, [announce]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const onHashChange = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHashChange);
    if (!window.location.hash) window.history.replaceState(null, "", "#live");
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const admittedModels = bootstrap.models.filter((model) => model.usableForRecognition !== false && model.availableBackends.includes("npu"));
  const activeModel = admittedModels.find((model) => model.id === config.modelId) || admittedModels[0];
  const availableDetectors = bootstrap.detectors.filter((detector) => detector.available);
  const activeDetector = availableDetectors.find((detector) => detector.id === config.detectorId) || availableDetectors[0];
  const setConfigValue = <K extends keyof RecognitionConfig>(key: K, value: RecognitionConfig[K]) => {
    setConfig((current) => ({ ...current, [key]: value }));
  };

  const openPage = (next: PageKey) => {
    setPage(next);
    if (window.location.hash !== `#${next}`) window.history.pushState(null, "", `#${next}`);
    setDrawer(null);
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><Fingerprint /></div>
          <div><p className="brand-kicker">PALMPRINT / NPU WORKBENCH</p><h1>掌纹识别工作台</h1></div>
        </div>
        <div className="topbar__right">
          <div className="device-indicator"><span className="device-indicator__dot" data-tone={toneForStatus(health.status)} /><span>Ascend 310B · 触控终端</span></div>
          <StatusPill tone={toneForStatus(health.status)}>{health.status === "ok" ? "服务正常" : health.message || "等待连接"}</StatusPill>
          <Button variant="ghost" icon={RefreshCw} loading={loading} onClick={() => void refresh()}>刷新状态</Button>
        </div>
      </header>
      <div className="workspace">
        <aside className="sidebar" aria-label="主导航">
          <div className="sidebar__caption">工作区</div>
          <nav className="sidebar__nav">
            {NAV_ITEMS.map(({ key, label, icon: Icon, hint }) => (
              <button type="button" className={cx("nav-item", page === key && "is-active")} key={key} onClick={() => openPage(key)}>
                <Icon aria-hidden="true" /><span><strong>{label}</strong><small>{hint}</small></span><ChevronRight aria-hidden="true" className="nav-item__arrow" />
              </button>
            ))}
          </nav>
          <div className="sidebar__bottom">
            <div className="sidebar-health"><div className="sidebar-health__icon"><ShieldCheck /></div><div><strong>设备状态</strong><span>{health.message || "健康检查已启用"}</span></div></div>
            <button type="button" className="sidebar-link" onClick={() => setDrawer("system")}><Settings2 aria-hidden="true" />系统与资产详情</button>
            <small className="sidebar-version">版本 {bootstrap.appVersion || "开发版"}</small>
          </div>
        </aside>
        <main className="main-area">
          <div className="mobile-nav">
            {NAV_ITEMS.map(({ key, label, icon: Icon }) => <button type="button" className={cx(page === key && "is-active")} key={key} onClick={() => openPage(key)}><Icon aria-hidden="true" /><span>{label}</span></button>)}
          </div>
          {page === "live" ? <LivePage bootstrap={bootstrap} cameras={cameras} modelListReady={modelListReady} cameraListReady={cameraListReady} config={config} activeModel={activeModel} activeDetector={activeDetector} setConfigValue={setConfigValue} openConfig={() => setDrawer("config")} announce={announce} /> : null}
          {page === "enrollment" ? <EnrollmentPage bootstrap={bootstrap} cameras={cameras} config={config} setConfigValue={setConfigValue} announce={announce} openTemplates={() => setDrawer("templates")} /> : null}
          {page === "evaluation" ? <EvaluationPage candidates={candidates} runtimeCandidates={candidateRuntimes} announce={announce} /> : null}
          {page === "system" ? <SystemPage onRefresh={refresh} loading={loading} announce={announce} /> : null}
        </main>
      </div>
      <Drawer open={drawer === "config"} title="识别配置" description="配置只影响下一次识别，不会修改已保存模板。" onClose={() => setDrawer(null)} width="regular">
        <ConfigForm bootstrap={bootstrap} modelListReady={modelListReady} config={config} setConfigValue={setConfigValue} />
        {activeDetector?.notice ? <Notice tone="warning">{activeDetector.displayName}：{activeDetector.notice}</Notice> : null}
        {activeModel?.conversionOnly || activeModel?.usableForRecognition === false ? <Notice tone="warning">{activeModel.displayName} 仅用于转换验证，不能用于正式识别。</Notice> : null}
        {activeModel?.manualTestPending ? <Notice tone="warning">{activeModel.displayName} 处于人工测试阶段，尚未完成稳定性验收。</Notice> : null}
      </Drawer>
      <Drawer open={drawer === "templates"} title="掌纹模板管理" description="按人员编号汇总左右掌；删除会同时移除关联采集图像。" onClose={() => setDrawer(null)} width="wide"><TemplateDrawer announce={announce} config={config} /></Drawer>
      <Drawer open={drawer === "system"} title="系统与资产" description="查看完整模型、数据集和摄像头清单。" onClose={() => setDrawer(null)} width="wide"><SystemDrawer announce={announce} /></Drawer>
      {toast ? <div className="toast-region" role="status"><Notice tone={toast.tone}>{toast.message}</Notice></div> : null}
    </div>
  );
}

function ConfigForm({ bootstrap, modelListReady, config, setConfigValue }: { bootstrap: BootstrapData; modelListReady: boolean; config: RecognitionConfig; setConfigValue: <K extends keyof RecognitionConfig>(key: K, value: RecognitionConfig[K]) => void }) {
  const admittedModels = bootstrap.models.filter((item) => item.usableForRecognition !== false && item.availableBackends.includes("npu"));
  const detectors = bootstrap.detectors.filter((item) => item.available);
  const detector = detectors.find((item) => item.id === config.detectorId) || detectors[0];
  const model = admittedModels.find((item) => item.id === config.modelId) || admittedModels[0];
  const changeModel = (modelId: string) => {
    const next = bootstrap.models.find((item) => item.id === modelId);
    setConfigValue("modelId", modelId);
    setConfigValue("backend", "npu");
    setConfigValue("precision", "mixed_fp16");
    if (next?.threshold !== undefined) setConfigValue("threshold", next.threshold);
  };
  return <div className="config-form">
    <SelectField label="手掌检测模型" value={detector?.id || ""} onChange={(event) => setConfigValue("detectorId", event.target.value)} options={detectors.length ? detectors.map((item) => ({ value: item.id, label: item.displayName })) : [{ value: "", label: modelListReady ? "暂无可用检测模型" : "模型服务未连接", disabled: true }]} />
    {detector ? <div className="detector-choice-meta"><span>{detector.family === "picodet" ? "PicoDet" : "NanoDet"} · {detector.inputShape[2] || "--"} × {detector.inputShape[3] || "--"}</span><span>直接拉伸到方形输入</span>{detector.taskMap !== undefined ? <span>离线mAP {detector.taskMap.toFixed(4)}</span> : null}{detector.rawNumericalGateOk === false ? <span className="stage-error">原始数值门未通过</span> : null}</div> : <Notice tone="danger">{modelListReady ? "当前没有可用的手掌检测模型。" : "模型服务未连接，请检查板端服务。"}</Notice>}
    <SelectField label="掌纹特征模型" value={model?.id || ""} onChange={(event) => changeModel(event.target.value)} options={admittedModels.length ? admittedModels.map((item) => ({ value: item.id, label: item.displayName })) : [{ value: "", label: modelListReady ? "暂无可用的 NPU 模型" : "模型服务未连接", disabled: true }]} />
    {!admittedModels.length ? <Notice tone="danger">{modelListReady ? "当前没有可用的 NPU 模型。" : "模型服务未连接，请检查板端服务。"}</Notice> : null}
    <div className="npu-lock"><ScanSearch aria-hidden="true" /><div><strong>手掌检测</strong><span>全图检测与掌纹特征检索是两个独立模型选择。</span></div></div>
    <div className="npu-lock"><Zap aria-hidden="true" /><div><strong>Ascend NPU</strong><span>生产服务固定使用 NPU，不提供 CPU fallback。</span></div></div>
    <div className="npu-lock"><Zap aria-hidden="true" /><div><strong>混合 FP16</strong><span>生产模型固定使用 NPU mixed-FP16 OM。</span></div></div>
    <Toggle label="上传文件已是 ROI" description="仅对明确的 128 × 128 上传图像跳过手掌检测" checked={config.assumeRoi} onChange={(checked) => setConfigValue("assumeRoi", checked)} />
  </div>;
}

function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return <div className="page-header"><div><p className="page-header__eyebrow">{eyebrow}</p><h2>{title}</h2><p>{description}</p></div>{action ? <div className="page-header__action">{action}</div> : null}</div>;
}

function LivePage({ bootstrap, cameras, modelListReady, cameraListReady, config, activeModel, activeDetector, setConfigValue, openConfig, announce }: { bootstrap: BootstrapData; cameras: CameraDevice[]; modelListReady: boolean; cameraListReady: boolean; config: RecognitionConfig; activeModel?: ModelOption; activeDetector?: DetectorOption; setConfigValue: <K extends keyof RecognitionConfig>(key: K, value: RecognitionConfig[K]) => void; openConfig: () => void; announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void }) {
  const [file, setFile] = useState<File>();
  const [fileUrl, setFileUrl] = useState<string>();
  const [result, setResult] = useState<RecognitionResult>();
  const [busy, setBusy] = useState(false);
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraRetryToken, setCameraRetryToken] = useState(0);
  const [cameraDevice, setCameraDevice] = useState(cameras[0]?.device || "");
  const [resolution, setResolution] = useState(preferredLiveResolution(cameras[0]));
  const [captureResolution, setCaptureResolution] = useState<string>();
  const inputRef = useRef<HTMLInputElement>(null);
  const [liveDetection, setLiveDetection] = useState<RecognitionResult>();
  const [detectionError, setDetectionError] = useState<string>();
  const frameUrl = liveDetection?.previewUrl;
  const cameraError = detectionError;
  useEffect(() => { if (!cameraDevice && cameras[0]) setCameraDevice(cameras[0].device); }, [cameraDevice, cameras]);
  useEffect(() => {
    if (cameraDevice && cameras.some((camera) => camera.device === cameraDevice)) setCameraActive(true);
  }, [cameraDevice, cameras]);
  useEffect(() => {
    if (!cameras.length || !cameraDevice) return;
    const selected = cameras.find((camera) => camera.device === cameraDevice) || cameras[0];
    if (!selected.resolutions.includes(resolution)) setResolution(preferredLiveResolution(selected));
  }, [cameraDevice, cameras, resolution]);
  useEffect(() => {
    if (cameraActive && (!cameras.length || !cameras.some((camera) => camera.device === cameraDevice))) {
      setCameraActive(false);
      const nextDevice = cameras[0]?.device || "";
      setCameraDevice(nextDevice);
      if (nextDevice) setResolution(preferredLiveResolution(cameras[0]));
      setCaptureResolution(undefined);
      announce("摄像头节点已变化，已停止旧预览；请重新打开新设备。", "warning");
    }
  }, [announce, cameraActive, cameraDevice, cameras]);
  const detectionEnabled = cameraActive && Boolean(cameraDevice) && !busy;
  useEffect(() => {
    setLiveDetection(undefined);
    setDetectionError(undefined);
  }, [cameraDevice, resolution, cameraRetryToken, config.detectorId]);
  useEffect(() => {
    if (!detectionEnabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api.detectCamera(cameraDevice, resolution, config.detectorId);
        if (!cancelled) {
          setLiveDetection(next);
          setCaptureResolution(next.captureResolution);
          setDetectionError(undefined);
        }
      } catch (error) {
        if (!cancelled) setDetectionError(error instanceof Error ? error.message : "手掌检测失败");
      } finally {
        if (!cancelled) timer = setTimeout(poll, 60);
      }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [detectionEnabled, cameraDevice, resolution, cameraRetryToken, config.detectorId]);
  useEffect(() => () => {
    if (cameraDevice) void api.closeCamera(cameraDevice, resolution).catch(() => undefined);
  }, [cameraDevice, resolution]);
  useEffect(() => () => { if (fileUrl) URL.revokeObjectURL(fileUrl); }, [fileUrl]);
  const chooseFile = (next?: File) => { if (!next) return; if (fileUrl) URL.revokeObjectURL(fileUrl); setFile(next); setFileUrl(URL.createObjectURL(next)); setResult(undefined); };
  const runUpload = async () => {
    if (!activeModel || !config.modelId) { announce("当前没有通过 NPU 准入的模型。", "danger"); return; }
    if (!activeDetector || !config.detectorId) { announce("当前没有可用的手掌检测模型。", "danger"); return; }
    if (!file) { announce("请先选择一张掌纹图像。", "warning"); return; }
    setBusy(true); try { setResult(await api.recognize(file, config)); announce("识别完成。", "success"); } catch (error) { announce(error instanceof ApiError ? error.message : "识别失败，请检查输入。", "danger"); } finally { setBusy(false); }
  };
  const runCamera = useCallback(async () => {
    if (!activeModel || !config.modelId) { announce("当前没有通过 NPU 准入的模型。", "danger"); return; }
    if (!cameraDevice) { announce("未检测到板载摄像头。", "warning"); return; }
    if (busy) return;
    setBusy(true); try { setResult(await api.recognizeCamera(cameraDevice, resolution, config)); announce("已完成当前帧识别。", "success"); } catch (error) { announce(error instanceof ApiError ? error.message : "摄像头识别失败。", "danger"); } finally { setBusy(false); }
  }, [activeModel, announce, busy, cameraDevice, config, resolution]);
  const autoRecognition = useAutomaticPalmTrigger(
    cameraActive && Boolean(activeModel && activeDetector && config.modelId && config.detectorId && cameraDevice),
    frameUrl,
    `${cameraDevice}:${resolution}`,
    busy,
    () => { void runCamera(); },
  );
  const preview = result?.previewUrl || fileUrl;
  const selectedCamera = cameras.find((camera) => camera.device === cameraDevice);
  const inputState = !cameraListReady
    ? "摄像头服务未连接"
    : cameraError
    ? "已发现节点，取流失败"
    : cameraActive
    ? `${selectedCamera?.name || cameraDevice} ${captureResolution ? `· ${captureResolution}` : "· 正在取流"}`
    : cameras.length
      ? "已发现摄像头，等待取流"
      : "未发现可采集节点";
  const inputTone = !cameraListReady || cameraError ? "blocked" : cameraActive ? "ready" : cameras.length ? "waiting" : "blocked";
  const recognitionState = cameraError ? "摄像头取流失败" : busy ? "正在处理" : result
    ? `检测到 ${result.detectionCount} 只手掌`
    : file ? "上传图像待识别" : autoRecognition.message;
  return <div className="page page--live">
    <PageHeader eyebrow="01 / 识别" title="实时识别" description="检测到摄像头后自动开启，对画面中的每只手掌逐一检索。" action={<Button variant="secondary" icon={SlidersHorizontal} onClick={openConfig}>识别配置</Button>} />
    <section className="live-readiness" aria-label="当前识别状态" aria-live="polite">
      <div className="live-readiness__item" data-tone={inputTone}><Camera aria-hidden="true" /><span><small>采集输入</small><strong>{inputState}</strong></span></div>
      <div className="live-readiness__item" data-tone={activeDetector ? "ready" : "blocked"}><ScanSearch aria-hidden="true" /><span><small>手掌检测</small><strong>{activeDetector?.displayName || (modelListReady ? "暂无可用检测模型" : "模型服务未连接")}</strong></span></div>
      <div className="live-readiness__item" data-tone={activeModel ? "ready" : "blocked"}><Fingerprint aria-hidden="true" /><span><small>掌纹特征</small><strong>{activeModel?.displayName || (modelListReady ? "暂无可用模型" : "模型服务未连接")}</strong></span></div>
      <div className="live-readiness__item" data-tone={busy ? "working" : result ? "ready" : "waiting"}><Activity aria-hidden="true" /><span><small>识别状态</small><strong>{recognitionState}</strong></span></div>
    </section>
    <div className="live-layout">
      <section className="live-main">
        <Panel title="输入画面" eyebrow="CAPTURE" action={<span className="panel-note">支持上传与 V4L2 摄像头</span>}>
          <div className="capture-strip">
            <input ref={inputRef} type="file" accept="image/*" className="visually-hidden" onChange={(event) => chooseFile(event.currentTarget.files?.[0])} />
            <Button variant="secondary" icon={Upload} onClick={() => inputRef.current?.click()}>选择图像</Button>
            {file ? <span className="file-chip"><FileImage aria-hidden="true" />{file.name}</span> : <span className="capture-hint">{cameraError ? "摄像头已发现，暂无可用画面" : cameraActive ? "摄像头已自动开启，无需手动拍照" : "可上传图像进行识别"}</span>}
          </div>
          {cameraActive ? <div className="camera-controls">
            <SelectField label="摄像头" value={cameraDevice} onChange={(event) => setCameraDevice(event.target.value)} options={cameras.length ? cameras.map((camera) => ({ value: camera.device, label: `${camera.name} · ${camera.device}` })) : [{ value: "", label: "未检测到摄像头", disabled: true }]} />
            <SelectField label="分辨率" value={resolution} onChange={(event) => setResolution(event.target.value)} options={(selectedCamera?.resolutions || ["1280x720", "1920x1080"]).map((item) => ({ value: item, label: item }))} />
            <span className="camera-effective-resolution">实际输入：{captureResolution || "等待首帧"}</span>
            <span className="auto-recognition-state" data-phase={cameraError ? "inactive" : autoRecognition.phase}><span className="live-dot" aria-hidden="true" />{cameraError ? "取流失败" : detectionError || (liveDetection ? `实时检测：${liveDetection.detectionCount} 只手掌` : "正在检测手掌")}</span>
            {cameraError ? <Button variant="secondary" icon={RefreshCw} onClick={() => setCameraRetryToken((value) => value + 1)}>重试摄像头</Button> : null}
          </div> : null}
          <ImageStage imageUrl={cameraActive ? liveDetection?.previewUrl || frameUrl : preview} boxes={cameraActive ? liveDetection?.hands : result?.hands} title={cameraError ? "摄像头取流失败" : "正在连接摄像头"} description={cameraError || "检测到摄像头后会自动打开。"} active={cameraActive && !cameraError} error={Boolean(cameraError)} objectFit="contain" onUpload={() => inputRef.current?.click()} />
        </Panel>
      </section>
      <aside className="live-inspector">
        <Panel title="识别结果" eyebrow="INSPECTOR" action={<StatusPill tone={result ? "info" : "neutral"}>{result ? `${result.detectionCount} 只手掌` : "等待输入"}</StatusPill>}>
          {result ? result.hands.length ? <div className="hand-results">{result.hands.map((hand) => <section className="hand-result" key={hand.index}><div className="hand-result__heading"><strong>{hand.detectedPalmSide === "right" ? "右掌" : hand.detectedPalmSide === "left" ? "左掌" : `手掌 ${hand.index}`}</strong><span>{hand.detectionScore === undefined ? "已裁剪 ROI" : `检测分数 ${hand.detectionScore.toFixed(3)}`}</span></div>{hand.roiUrl ? <img src={hand.roiUrl} alt={`${hand.detectedPalmSide === "right" ? "右掌" : hand.detectedPalmSide === "left" ? "左掌" : `手掌 ${hand.index}`} 的 ROI`} /> : null}<ResultSummary result={hand} /></section>)}</div> : <div className="empty-inspector"><div className="empty-inspector__icon"><CircleHelp /></div><strong>未检测到手掌</strong></div> : <div className="empty-inspector"><div className="empty-inspector__icon"><CircleHelp /></div><strong>还没有识别结果</strong><p>上传图像或拍摄一帧后，结果会显示在这里。</p></div>}
          <div className="inspector-actions"><Button variant="primary" icon={ScanLine} loading={busy} onClick={() => void runUpload()} disabled={!file || !activeModel}>识别上传图像</Button><div className="inspector-meta"><span><Fingerprint aria-hidden="true" />{activeModel?.displayName || "未连接服务"}</span></div></div>
        </Panel>
        <div className="quick-status"><div className="quick-status__item"><span className="quick-status__label">推理设备</span><strong>NPU · mixed-FP16</strong></div><div className="quick-status__item"><span className="quick-status__label">采集区域</span><strong>检测手掌后逐框裁剪</strong></div></div>
      </aside>
    </div>
  </div>;
}

function ResultSummary({ result }: { result: RecognitionHand }) {
  const best = result.matches[0];
  const resultName = result.outcome === "roi_rejected" ? result.status : result.displayName || result.userName || best?.displayName || best?.userName || (result.matches.length ? "候选待复核" : "未找到模板");
  const totalMs = result.timing["total ms"];
  const isCalibrated = result.calibrationReady === true;
  return <div className="result-summary">
    <div className={cx("result-banner", result.outcome === "matched" ? "result-banner--success" : "result-banner--warning")}>
      <div>
        <span className="result-banner__label">{result.outcome === "candidates_only" ? "候选检索" : result.outcome === "matched" ? "最佳匹配" : "需要处理"}</span>
        <strong>{resultName}</strong>
        {result.detectedPalmSide || result.palmSide || best?.palmSide ? <span>{(result.detectedPalmSide || result.palmSide || best?.palmSide) === "right" ? "右掌" : (result.detectedPalmSide || result.palmSide || best?.palmSide) === "left" ? "左掌" : (result.detectedPalmSide || result.palmSide || best?.palmSide)}{best?.personId ? ` · ${best.personId}` : ""}</span> : null}
      </div>
      <strong className="result-banner__score">{result.outcome === "roi_rejected" || result.outcome === "no_templates" ? "--" : (result.score ?? best?.score ?? 0).toFixed(3)}</strong>
    </div>
    {!isCalibrated && result.outcome === "candidates_only" ? <Notice tone="info">本机可见光阈值尚未校准，当前仅提供 Top-K 候选与分数，不判定身份是否通过。</Notice> : null}
    <div className="result-facts">
      <div><span>模板库</span><strong>{result.templateSummary ? `${result.templateSummary.palmCount} 掌 / ${result.templateSummary.personCount} 人` : "--"}</strong></div>
      <div><span>候选数</span><strong>{result.matches.length}</strong></div>
      <div><span>总耗时</span><strong>{formatMs(totalMs)}</strong></div>
    </div>
    <div className="match-list">
      <div className="section-label">TOP-K 候选</div>
      {result.matches.map((match, index) => <div className="match-row" key={`${match.palmId || match.userId || match.userName}-${index}`}>
        <span className="match-rank">{index + 1}</span>
        <span className="match-name">{match.displayName || match.userName}<small>{match.personId || "未标注编号"} · {match.palmSide === "right" ? "右掌" : match.palmSide === "left" ? "左掌" : match.palmSide || "掌侧未标注"}</small></span>
        <strong>{match.score.toFixed(3)}</strong>
      </div>)}
    </div>
  </div>;
}

function EnrollmentPage({ bootstrap, cameras, config, setConfigValue, announce, openTemplates }: { bootstrap: BootstrapData; cameras: CameraDevice[]; config: RecognitionConfig; setConfigValue: <K extends keyof RecognitionConfig>(key: K, value: RecognitionConfig[K]) => void; announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void; openTemplates: () => void }) {
  const [personId, setPersonId] = useState("");
  const [continueSessionId, setContinueSessionId] = useState<string>();
  const [displayName, setDisplayName] = useState("");
  const [palmSide, setPalmSide] = useState("left");
  const [mode, setMode] = useState<"self_service" | "assisted">("self_service");
  const [session, setSession] = useState<EnrollmentSession>();
  const [cameraDevice, setCameraDevice] = useState(cameras[0]?.device || "");
  const [resolution, setResolution] = useState(preferredCameraResolution(cameras[0]));
  const [busy, setBusy] = useState(false);
  const [reviewOriginal, setReviewOriginal] = useState(false);
  const [reviewSampleId, setReviewSampleId] = useState<string>();
  const terminalNotice = useRef<string>();
  const running = Boolean(session && ["camera_opening", "countdown", "waiting_position", "stabilizing", "collecting", "saving"].includes(session.phase || ""));
  // Once the server creates a session, it owns the workflow mode. This keeps
  // a local selector state from making a self-service session look reviewed.
  const sessionMode = session?.mode === "assisted" ? "assisted" : session ? "self_service" : mode;
  const isAssistedSession = sessionMode === "assisted";
  const reviewActionsEnabled = isAssistedSession && session?.reviewActionsEnabled === true;
  const count = session?.samples.length || 0;
  const selectedCamera = cameras.find((camera) => camera.device === cameraDevice) || cameras[0];
  const sessionRef = useRef<EnrollmentSession>();
  sessionRef.current = session;
  const previewActive = !busy && !running && !session;
  const {frameUrl: enrollmentFrame, error: enrollmentCameraError} = useCameraPreview(
    previewActive, cameraDevice, resolution, 0, announce,
  );
  const [enrollmentDetection, setEnrollmentDetection] = useState<RecognitionResult>();
  const [enrollmentDetectionError, setEnrollmentDetectionError] = useState<string>();
  const detectBeforeEnrollment = previewActive && Boolean(enrollmentFrame) && !enrollmentCameraError;
  useEffect(() => {
    setEnrollmentDetection(undefined);
    if (!detectBeforeEnrollment || !cameraDevice) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api.detectCamera(cameraDevice, resolution, config.detectorId);
        if (!cancelled) { setEnrollmentDetection(next); setEnrollmentDetectionError(undefined); }
      } catch (error) {
        if (!cancelled) setEnrollmentDetectionError(error instanceof Error ? error.message : "检测失败");
      } finally { if (!cancelled) timer = setTimeout(poll, 350); }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [detectBeforeEnrollment, cameraDevice, resolution, config.detectorId]);
  useEffect(() => () => {
    const current = sessionRef.current;
    if (current && !["completed", "cancelled"].includes(current.phase || "")) {
      void api.deleteEnrollmentSession(current.id).catch(() => undefined);
    }
  }, []);

  useEffect(() => { if (!cameraDevice && cameras[0]) setCameraDevice(cameras[0].device); }, [cameraDevice, cameras]);
  useEffect(() => {
    if (!selectedCamera) return;
    if (!selectedCamera.resolutions.includes(resolution)) setResolution(preferredCameraResolution(selectedCamera));
  }, [resolution, selectedCamera]);
  useEffect(() => {
    if (!session?.id || !running) return undefined;
    let alive = true;
    const poll = async () => {
      try {
        const next = await api.enrollmentSession(session.id);
        if (!alive) return;
        setSession(next);
        if (["completed", "failed"].includes(next.phase || "") && terminalNotice.current !== `${next.id}:${next.phase}`) {
          terminalNotice.current = `${next.id}:${next.phase}`;
          announce(next.guide || (next.phase === "completed" ? "模板已保存。" : "注册失败。"), next.phase === "completed" ? "success" : "danger");
        }
      } catch (error) {
        if (alive) announce(error instanceof ApiError ? error.message : "注册状态读取失败。", "danger");
      }
    };
    void poll();
    let timer: ReturnType<typeof setTimeout>;
    const nextPoll = async () => {
      await poll();
      if (alive) timer = setTimeout(nextPoll, 500);
    };
    // Do not overlap status requests while a board worker is busy.
    timer = setTimeout(nextPoll, 500);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [announce, running, session?.id]);

  const changeEnrollmentModel = (modelId: string) => {
    const next = bootstrap.models.find((item) => item.id === modelId);
    setConfigValue("modelId", modelId);
    setConfigValue("backend", "npu");
    setConfigValue("precision", "mixed_fp16");
    if (next?.threshold !== undefined) setConfigValue("threshold", next.threshold);
  };
  const start = async () => {
    if (!cameraDevice) { announce("未检测到可用摄像头。", "warning"); return; }
    setBusy(true);
    try {
      terminalNotice.current = undefined;
      await api.closeCamera(cameraDevice, resolution);
      const created = await api.createEnrollmentSession(config, { continueSessionId, displayName: displayName.trim() || undefined, palmSide, mode });
      setSession(created);
      setPersonId(created.personId || personId);
      let started: EnrollmentSession;
      try {
        started = await api.startEnrollmentAutoCapture(created.id, cameraDevice, resolution);
      } catch (error) {
        await api.deleteEnrollmentSession(created.id);
        setSession(undefined);
        throw error;
      }
      setMode(started.mode === "assisted" ? "assisted" : "self_service");
      setSession(started);
      announce(
        started.mode === "assisted"
          ? "已启动管理员审阅采集，采满 5 张后可剔除样本或确认保存。"
          : "已启动自助自动采集，请在画面中保持一只手掌。",
        "info",
      );
    } catch (error) {
      announce(error instanceof ApiError ? error.message : "无法启动自动采集。", "danger");
    } finally { setBusy(false); }
  };
  const cancel = async () => {
    if (session?.id) {
      try { await api.deleteEnrollmentSession(session.id); }
      catch (error) { announce(error instanceof Error ? error.message : "取消失败，请重试", "danger"); return; }
    }
    terminalNotice.current = undefined;
    setSession(undefined);
    setPersonId("");
    setContinueSessionId(undefined);
    setDisplayName("");
    setPalmSide("left");
    announce("本次注册已取消，已删除采集图像。", "info");
  };
  const discard = async (sampleId: string) => {
    if (!session?.id) return;
    setBusy(true);
    try {
      setSession(await api.discardEnrollmentSample(session.id, sampleId));
      announce("样本已删除，系统会自动补采。", "info");
    } catch (error) { announce(error instanceof ApiError ? error.message : "无法删除样本。", "danger"); } finally { setBusy(false); }
  };
  const commit = async () => {
    if (!session?.id) return;
    setBusy(true);
    try {
      const completed = await api.commitEnrollmentSession(session.id, { personId: session.personId || personId, displayName: displayName.trim() || undefined, palmSide, config });
      setSession(completed);
      announce(completed.guide || "模板已保存。", "success");
    } catch (error) { announce(error instanceof ApiError ? error.message : "模板保存失败。", "danger"); } finally { setBusy(false); }
  };
  const latestSample = session?.samples.length ? session.samples[session.samples.length - 1] : undefined;
  const reviewSample = session?.samples.find((sample) => sample.id === reviewSampleId) || latestSample;
  const previewUrl = session?.phase === "reviewing" ? reviewOriginal ? reviewSample?.originalUrl : reviewSample?.roiUrl : session?.previewUrl || latestSample?.previewUrl || enrollmentDetection?.previewUrl || enrollmentFrame;
  const canStart = !session || ["failed", "cancelled"].includes(session.phase || "");
  const activePersonId = session?.personId || personId;
  const continueSamePerson = () => {
    terminalNotice.current = undefined;
    setPersonId(activePersonId);
    setContinueSessionId(session?.id);
    setPalmSide((current) => current === "left" ? "right" : "left");
    setSession(undefined);
  };
  const startNextPerson = () => {
    terminalNotice.current = undefined;
    setPersonId("");
    setContinueSessionId(undefined);
    setDisplayName("");
    setPalmSide("left");
    setSession(undefined);
  };
  return <div className="page page--enrollment">
    <PageHeader eyebrow="02 / 注册" title="掌纹注册" description="系统自动分配人员编号，左右掌是独立 1:N 模板。画面中保持一只手掌，自动采集 5 张合格样本。" action={<Button variant="secondary" icon={Images} onClick={openTemplates}>掌纹模板管理</Button>} />
    <div className="enrollment-layout">
      <section className="enrollment-capture"><Panel title="自动采集画面" eyebrow="AUTO COLLECTION" action={<StatusPill tone={running ? "info" : session?.phase === "completed" ? "success" : session?.phase === "failed" ? "danger" : "neutral"}>{session?.phase || "ready"}</StatusPill>}>
        <div className="camera-controls camera-controls--compact"><SelectField label="摄像头" value={cameraDevice} onChange={(event) => setCameraDevice(event.target.value)} options={cameras.length ? cameras.map((camera) => ({ value: camera.device, label: `${camera.name} · ${camera.device}` })) : [{ value: "", label: "未检测到摄像头", disabled: true }]} /><SelectField label="分辨率" value={resolution} onChange={(event) => setResolution(event.target.value)} options={(selectedCamera?.resolutions || ["1280x720", "1920x1080"]).map((item) => ({ value: item, label: item }))} /></div>
        <ImageStage imageUrl={previewUrl} title={enrollmentCameraError || session?.guide || "摄像头预览"} description={enrollmentCameraError || session?.guide || ""} active={running || previewActive} error={Boolean(enrollmentCameraError)} objectFit="contain" boxes={session?.detectionBoxes || enrollmentDetection?.hands} countdown={session?.countdownRemaining} palmSideLabel={palmSide === "right" ? "右掌注册" : "左掌注册"} />
        {session?.phase === "reviewing" ? <div className="review-view"><div className="segmented"><button className={!reviewOriginal ? "is-active" : ""} onClick={() => setReviewOriginal(false)}>ROI</button><button className={reviewOriginal ? "is-active" : ""} onClick={() => setReviewOriginal(true)}>原图</button></div><SelectField label="审阅样本" value={reviewSample?.id || ""} onChange={(event) => setReviewSampleId(event.target.value)} options={session.samples.map((sample, index) => ({value: sample.id, label: `样本 ${index + 1}`}))} /></div> : null}
        <div className="enrollment-guide"><strong>{session?.guide || "自助模式采满 5 张后自动保存；管理员辅助模式支持审阅和剔除。"}</strong><small className="enrollment-mode">当前方式：{isAssistedSession ? reviewActionsEnabled ? "管理员审阅：可剔除样本或确认保存" : "管理员审阅（第 5 张后不会自动保存）" : "自助自动保存"}</small>{session?.lastError ? <span>{session.lastError}</span> : null}{session?.rejections && Object.keys(session.rejections).length ? <small>拒绝统计：{Object.entries(session.rejections).map(([reason, value]) => `${reason} ${value}`).join(" · ")}</small> : null}</div>
        <div className="sample-actions">{canStart ? <Button variant="primary" icon={Camera} loading={busy} onClick={() => void start()}>{mode === "assisted" ? "开始管理员采集" : "开始自动采集"}</Button> : null}{session?.phase === "completed" ? <><Button variant="secondary" icon={RefreshCw} disabled={busy} onClick={continueSamePerson}>登记同一人另一只掌</Button><Button variant="quiet" icon={RefreshCw} disabled={busy} onClick={startNextPerson}>登记下一位人员</Button></> : <Button variant="quiet" icon={RefreshCw} disabled={!session || busy} onClick={() => void cancel()}>{running ? "取消采集" : "清空本次注册"}</Button>}</div>
      </Panel></section>
      <aside className="enrollment-inspector"><Panel title="注册信息" eyebrow="PALM IDENTITY"><div className="identity-form"><div className="field identity-auto-id"><span className="field__label">人员编号</span><strong>{activePersonId || "开始采集后自动分配"}</strong><small>系统按 P-000001 格式自动分配</small></div><TextField label="显示名（可选）" placeholder="例如：张三" value={displayName} disabled={running || session?.phase === "completed"} onChange={(event) => setDisplayName(event.target.value)} /><div className="field"><span className="field__label">掌侧</span><div className="segmented"><button type="button" disabled={running || session?.phase === "completed"} className={cx(palmSide === "left" && "is-active")} onClick={() => setPalmSide("left")}>左掌</button><button type="button" disabled={running || session?.phase === "completed"} className={cx(palmSide === "right" && "is-active")} onClick={() => setPalmSide("right")}>右掌</button></div></div><div className="field"><span className="field__label">注册方式</span><div className="segmented"><button type="button" disabled={running || Boolean(session)} className={cx(mode === "self_service" && "is-active")} onClick={() => setMode("self_service")}>自助自动保存</button><button type="button" disabled={running || Boolean(session)} className={cx(mode === "assisted" && "is-active")} onClick={() => setMode("assisted")}>管理员审阅</button></div></div><SelectField label="手掌检测模型" value={config.detectorId} onChange={(event) => setConfigValue("detectorId", event.target.value)} options={bootstrap.detectors.filter((detector) => detector.available).map((detector) => ({ value: detector.id, label: detector.displayName }))} /><SelectField label="掌纹特征模型" value={config.modelId} onChange={(event) => changeEnrollmentModel(event.target.value)} options={bootstrap.models.filter((model) => model.usableForRecognition !== false && model.availableBackends.includes("npu")).map((model) => ({ value: model.id, label: model.displayName }))} /></div><div className="sample-progress"><div className="sample-progress__header"><span>样本进度</span><strong>{count} / 5</strong></div><div className="progress-track"><span style={{ width: `${count * 20}%` }} /></div><p>只保存最终入选样本；拒绝帧不会落盘。</p></div><div className="sample-gallery">{Array.from({ length: 5 }).map((_, index) => { const sample = session?.samples[index]; return <div className={cx("sample-slot", sample && "is-filled")} key={sample?.id || index}>{sample ? <><img src={sample.roiUrl || sample.previewUrl} alt={`第 ${index + 1} 个掌纹样本`} />{mode === "assisted" && session?.phase === "reviewing" ? <button type="button" className="sample-remove" title="剔除该样本" onClick={() => void discard(sample.id)}><Trash2 aria-hidden="true" /></button> : null}</> : <span>{index + 1}</span>}</div>; })}</div>{mode === "assisted" ? <Button variant="primary" icon={Check} loading={busy} disabled={session?.phase !== "reviewing" || count !== 5} onClick={() => void commit()} className="save-template-button">确认并保存 5 张样本</Button> : null}<Notice tone="info">系统为新人员自动分配编号。完成一只掌后可继续登记同一人的另一只掌；左右掌分别保存为独立模板。</Notice></Panel></aside>
    </div>
  </div>;
}

function EvaluationPage({ candidates, runtimeCandidates, announce }: { candidates: CandidateOption[]; runtimeCandidates: CandidateRuntimeOption[]; announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void }) {
  const [taskFilter, setTaskFilter] = useState("all");
  const [modalityFilter, setModalityFilter] = useState("all");
  const [npuFilter, setNpuFilter] = useState("all");
  const [runtimeId, setRuntimeId] = useState("");
  const [runtimeFile, setRuntimeFile] = useState<File>();
  const [referenceFile, setReferenceFile] = useState<File>();
  const [runtimeResult, setRuntimeResult] = useState<CandidateRunResult>();
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const runtimeInputRef = useRef<HTMLInputElement>(null);
  const referenceInputRef = useRef<HTMLInputElement>(null);
  const selectedRuntime = runtimeCandidates.find((candidate) => candidate.id === runtimeId) || runtimeCandidates[0];
  useEffect(() => {
    if (selectedRuntime && selectedRuntime.id !== runtimeId) setRuntimeId(selectedRuntime.id);
  }, [runtimeId, selectedRuntime]);
  const filteredCandidates = candidates.filter((candidate) =>
    (taskFilter === "all" || candidate.taskType === taskFilter)
    && (modalityFilter === "all" || candidate.modality === modalityFilter)
    && (npuFilter === "all" || candidate.npuStatus === npuFilter),
  );
  const taskOptions = Array.from(new Set(candidates.map((candidate) => candidate.taskType))).sort();
  const modalityOptions = Array.from(new Set(candidates.map((candidate) => candidate.modality).filter(Boolean) as string[])).sort();
  const npuOptions = Array.from(new Set(candidates.map((candidate) => candidate.npuStatus).filter(Boolean) as string[])).sort();
  const runCandidate = async () => {
    if (!selectedRuntime || !selectedRuntime.available) { announce("当前候选没有可用的校验通过 OM。", "warning"); return; }
    if (!runtimeFile) { announce("请先选择一张候选模型输入图像。", "warning"); return; }
    if (selectedRuntime.inputKind === "rgb_pair" && !referenceFile) { announce("双输入候选需要同时选择参考图像。", "warning"); return; }
    setRuntimeBusy(true);
    try {
      setRuntimeResult(await api.runCandidate(selectedRuntime.id, runtimeFile, referenceFile));
      announce("候选模型 NPU 推理完成。", "success");
    } catch (error) {
      announce(error instanceof ApiError ? error.message : "候选模型运行失败。", "danger");
    } finally {
      setRuntimeBusy(false);
    }
  };
  const shapeLabel = (shape: number[] | number[][]) => Array.isArray(shape[0])
    ? (shape as number[][]).map((item) => `[${item.join(", ")}]`).join(" + ")
    : `[${(shape as number[]).join(", ")}]`;
  return <div className="page page--evaluation">
    <PageHeader eyebrow="03 / 模型状态" title="当前运行模型" description="这里仅显示当前发行版实际保留的 NPU 模型。完整候选审计记录仍由离线工具维护。" />
    <Notice tone="info">生产默认使用 CCNet；CompNet 可在 manual_test profile 下人工测试，完成验收前不会自动升级为生产模型。</Notice>
    <Panel title="候选模型实验" eyebrow="CANDIDATE RUNTIME">
      <div className="candidate-runtime-intro"><p>候选模型按各自的任务契约在 NPU 上运行：分类器返回 logits，ROI 模型返回变换参数，双输入模型返回两路特征，掌静脉模型使用灰度输入。</p><StatusPill tone="info">不进入生产模板</StatusPill></div>
      <div className="candidate-runtime-controls">
        <SelectField label="候选模型" value={selectedRuntime?.id || ""} onChange={(event) => { setRuntimeId(event.target.value); setRuntimeResult(undefined); }} options={runtimeCandidates.length ? runtimeCandidates.map((item) => ({ value: item.id, label: `${item.displayName}${item.available ? "" : " · 资产不可用"}`, disabled: !item.available })) : [{ value: "", label: "暂无候选运行时资产", disabled: true }]} />
        {selectedRuntime ? <div className="candidate-runtime-contract"><strong>{selectedRuntime.taskType} · {selectedRuntime.modality || "未声明模态"}</strong><span>输入 {shapeLabel(selectedRuntime.inputShape)} · 输出 {selectedRuntime.outputKind}</span><small>{selectedRuntime.available ? `OM ${selectedRuntime.bytes.toLocaleString()} bytes · ${selectedRuntime.sha256.slice(0, 12)}…` : selectedRuntime.reason || "OM 资产不可用"}</small></div> : null}
      </div>
      <div className="candidate-runtime-files">
        <input ref={runtimeInputRef} type="file" accept="image/*" className="visually-hidden" onChange={(event) => { setRuntimeFile(event.currentTarget.files?.[0]); setRuntimeResult(undefined); }} />
        <Button variant="secondary" icon={Upload} onClick={() => runtimeInputRef.current?.click()}>选择输入图像</Button>
        {selectedRuntime?.inputKind === "rgb_pair" ? <><input ref={referenceInputRef} type="file" accept="image/*" className="visually-hidden" onChange={(event) => { setReferenceFile(event.currentTarget.files?.[0]); setRuntimeResult(undefined); }} /><Button variant="secondary" icon={Images} onClick={() => referenceInputRef.current?.click()}>选择参考图像</Button></> : null}
        <span className="file-chip">{runtimeFile ? <><FileImage aria-hidden="true" />{runtimeFile.name}</> : "未选择输入图像"}</span>
        {selectedRuntime?.inputKind === "rgb_pair" ? <span className="file-chip">{referenceFile ? <><FileImage aria-hidden="true" />{referenceFile.name}</> : "未选择参考图像"}</span> : null}
        <Button variant="primary" icon={Zap} loading={runtimeBusy} disabled={!selectedRuntime?.available} onClick={() => void runCandidate()}>运行候选模型</Button>
      </div>
      {runtimeResult ? <div className="candidate-runtime-result"><div><strong>{runtimeResult.displayName}</strong><span>{runtimeResult.inferenceMs.toFixed(1)} ms 推理 · {runtimeResult.outputFinite ? "输出有限" : "输出异常"}</span></div><div className="candidate-runtime-metrics"><span>输出 {runtimeResult.outputShapes.map((shape) => `[${shape.join(", ")}]`).join(" · ")}</span>{runtimeResult.embeddingDim ? <span>特征维度 {runtimeResult.embeddingDim}</span> : null}{runtimeResult.cosineSimilarity !== undefined ? <span>两路余弦 {runtimeResult.cosineSimilarity.toFixed(4)}</span> : null}{runtimeResult.topK?.length ? <span>Top-1 类别 {runtimeResult.topK[0].classId}</span> : null}{runtimeResult.theta?.length ? <span>ROI 参数 {runtimeResult.theta.map((value) => value.toFixed(3)).join(", ")}</span> : null}</div></div> : null}
    </Panel>
    <Panel title="运行模型" eyebrow="RUNTIME MODELS" className="candidate-matrix-panel"><div className="candidate-filters"><SelectField label="任务类型" value={taskFilter} onChange={(event) => setTaskFilter(event.target.value)} options={[{ value: "all", label: "全部任务" }, ...taskOptions.map((value) => ({ value, label: value }))]} /><SelectField label="模态" value={modalityFilter} onChange={(event) => setModalityFilter(event.target.value)} options={[{ value: "all", label: "全部模态" }, ...modalityOptions.map((value) => ({ value, label: value }))]} /><SelectField label="NPU 状态" value={npuFilter} onChange={(event) => setNpuFilter(event.target.value)} options={[{ value: "all", label: "全部状态" }, ...npuOptions.map((value) => ({ value, label: value }))]} /><span className="candidate-filter-count">显示 {filteredCandidates.length} / {candidates.length}</span></div><div className="candidate-audit-list" role="list">{filteredCandidates.map((candidate) => { const diagnosticBlocked = candidate.diagnosticStatus === "blocked_faults"; return <article className={cx("candidate-audit-row", (candidate.usableForRecognition === false || diagnosticBlocked) && "is-audit-only")} key={candidate.id} role="listitem"><div className="candidate-audit-row__summary"><strong>{candidate.displayName}</strong><small>{candidate.taskType} · {candidate.modality || "未声明模态"}{diagnosticBlocked ? " · 设备诊断阻断" : candidate.manualTestPending ? " · 人工测试通道" : " · 生产模型"}{candidate.reproducible === false ? ` · ${candidate.reproducibilityReason || "来源 revision 未固定"}` : ""}</small></div><StatusPill tone={diagnosticBlocked ? "danger" : toneForStatus(candidate.manualTestPending ? "info" : candidate.npuStatus || "pending")}>{diagnosticBlocked ? "诊断阻断" : candidate.manualTestPending ? "人工测试" : "生产可用"}</StatusPill><p>{diagnosticBlocked ? "ACL smoke 已通过，但板端诊断存在阻断事件，当前不能作为生产模型。" : candidate.manualTestPending ? "OM 与 NPU 运行路径已保留，等待人工功能验证。" : (candidate.eligibilityReason || "已在当前运行时注册。")}</p></article>; })}</div>{filteredCandidates.length === 0 ? <div className="empty-state candidate-empty"><BarChart3 /><strong>没有符合筛选条件的模型</strong></div> : null}</Panel>
  </div>;
}

function SystemPage({ onRefresh, loading, announce }: { onRefresh: () => Promise<void>; loading: boolean; announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void }) {
  const [status, setStatus] = useState<SystemStatus>();
  const load = useCallback(async () => { try { setStatus(await api.systemStatus()); } catch (error) { announce(error instanceof ApiError ? error.message : "系统状态读取失败。", "danger"); } }, [announce]);
  useEffect(() => { void load(); }, [load]);
  const summary = status?.summary || {};
  return <div className="page page--system"><PageHeader eyebrow="04 / 状态" title="系统状态" description="设备、运行时、模型资产和摄像头的统一健康摘要。" action={<Button variant="secondary" icon={RefreshCw} loading={loading} onClick={() => { void onRefresh(); void load(); }}>刷新检查</Button>} /><div className="health-hero"><div className="health-hero__icon"><ShieldCheck /></div><div><span className="section-label">设备摘要</span><h3>{String(summary.status || summary.health || "等待检查")}</h3><p>{String(summary.message || summary.detail || "查看下方资产状态，确认识别链路可用。")}</p></div><StatusPill tone={toneForStatus(String(summary.status || "neutral"))}>{String(summary.status || "unknown")}</StatusPill></div><div className="system-summary-grid"><MetricTile label="CANN" value={String(summary.cann ?? summary.cann_version ?? "--")} tone={toneForStatus(String(summary.cann_status || "neutral"))} /><MetricTile label="NPU" value={String(summary.npu ?? summary.npu_health ?? "--")} tone={toneForStatus(String(summary.npu_status || summary.npu_health || "neutral"))} /><MetricTile label="摄像头" value={String(summary.camera ?? summary.camera_count ?? "--")} tone={toneForStatus(String(summary.camera_status || "neutral"))} /><MetricTile label="模板" value={String(summary.templates ?? summary.template_count ?? "--")} /></div><div className="system-columns"><AssetList title="模型资产" icon={Fingerprint} items={status?.models || []} /><AssetList title="数据集" icon={Database} items={status?.datasets || []} /><AssetList title="摄像头" icon={Camera} items={status?.cameras || []} /></div></div>;
}

function AssetList({ title, icon: Icon, items }: { title: string; icon: typeof Fingerprint; items: SystemItem[] }) {
  return <Panel title={title} eyebrow="ASSET" action={<span className="panel-count">{items.length}</span>} className="asset-panel"><div className="asset-list">{items.length ? items.map((item, index) => <div className="asset-row" key={item.id || `${item.title}-${index}`}><div className="asset-row__icon"><Icon aria-hidden="true" /></div><div className="asset-row__copy"><strong>{item.title}</strong><span>{item.description || "无附加说明"}</span>{item.fields && Object.keys(item.fields).length ? <small>{Object.entries(item.fields).slice(0, 2).map(([key, value]) => `${key}: ${String(value)}`).join(" · ")}</small> : null}</div><StatusPill tone={toneForStatus(item.status)} icon={false}>{item.status || "未知"}</StatusPill></div>) : <div className="asset-empty">暂无资产记录</div>}</div></Panel>;
}

function TemplateDrawer({ announce, config }: { announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void; config: RecognitionConfig }) {
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => { setLoading(true); try { setTemplates(await api.templates(config)); } catch (error) { announce(error instanceof ApiError ? error.message : "模板读取失败。", "danger"); } finally { setLoading(false); } }, [announce, config]);
  useEffect(() => { void load(); }, [load]);
  const remove = async (item: TemplateItem) => { if (!window.confirm(`删除 ${item.personId || item.userName} 的${item.palmSide === "left" ? "左掌" : "右掌"}模板？关联采集图像也会被删除。`)) return; try { await api.deleteTemplate(item.id, config); setTemplates((current) => current.filter((candidate) => candidate.id !== item.id)); announce("模板及关联采集图像已删除。", "success"); } catch (error) { announce(error instanceof ApiError ? error.message : "模板删除失败。", "danger"); } };
  const groups = Object.values(templates.reduce<Record<string, TemplateItem[]>>((result, item) => { const key = item.personId || item.userName; (result[key] ||= []).push(item); return result; }, {}));
  return <div className="drawer-section"><div className="drawer-toolbar"><span>{templates.length} 个独立掌纹</span><Button variant="quiet" icon={RefreshCw} loading={loading} onClick={() => void load()}>刷新</Button></div><div className="template-list">{groups.length ? groups.map((group) => <section className="template-person-group" key={group[0].personId || group[0].userName}><h3>{group[0].personId || group[0].userName}<small>{group[0].displayName && group[0].displayName !== group[0].personId ? group[0].displayName : ""}</small></h3>{group.map((item) => <div className="template-row" key={item.id}><div className="template-avatar"><Fingerprint /></div><div><strong>{item.palmSide === "left" ? "左掌" : item.palmSide === "right" ? "右掌" : "掌侧未标注"}</strong><span>{item.samples || 0} 个样本 · {item.palmId || item.id}</span><small>{item.modelId || "模型未标注"} · {formatDate(item.updatedAt)}</small></div><button type="button" className="danger-icon-button" aria-label={`删除 ${item.userName}`} title="删除模板" onClick={() => void remove(item)}><Trash2 aria-hidden="true" /></button></div>)}</section>) : <div className="empty-state empty-state--small"><HardDrive /><strong>暂无模板</strong><p>完成注册后，模板会显示在这里。</p></div>}</div></div>;
}

function SystemDrawer({ announce }: { announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void }) {
  const [status, setStatus] = useState<SystemStatus>();
  const [loading, setLoading] = useState(false);
  const load = async () => { setLoading(true); try { setStatus(await api.systemStatus()); } catch (error) { announce(error instanceof ApiError ? error.message : "系统状态读取失败。", "danger"); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  return <div className="drawer-section"><div className="drawer-toolbar"><span>资产详情</span><Button variant="quiet" icon={RefreshCw} loading={loading} onClick={() => void load()}>刷新</Button></div><div className="drawer-asset-groups">{([ ["模型", status?.models || []], ["数据集", status?.datasets || []], ["摄像头", status?.cameras || []] ] as Array<[string, SystemItem[]]>).map(([title, items]) => <div className="drawer-asset-group" key={title}><h3>{title}</h3>{items.length ? items.map((item, index) => <div className="drawer-detail-row" key={item.id || `${item.title}-${index}`}><strong>{item.title}</strong><StatusPill tone={toneForStatus(item.status)} icon={false}>{item.status || "未知"}</StatusPill><span>{item.description || "--"}</span></div>) : <p className="muted">暂无记录</p>}</div>)}</div></div>;
}

export default App;
