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
  Fingerprint,
  Gauge,
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
  CameraDevice,
  EnrollmentSession,
  HealthStatus,
  ModelOption,
  PageKey,
  RecognitionConfig,
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
  RangeField,
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
  candidates: [],
  datasets: [],
  defaults: { modelId: "", backend: "npu", precision: "mixed_fp16", threshold: 0.75, cameraResolution: "1280x720" },
};

const NAV_ITEMS: Array<{ key: PageKey; label: string; icon: typeof Activity; hint: string }> = [
  { key: "live", label: "实时识别", icon: ScanLine, hint: "上传或拍摄并匹配模板" },
  { key: "enrollment", label: "掌纹注册", icon: UserPlus, hint: "采集多张样本建立模板" },
  { key: "evaluation", label: "候选审计", icon: BarChart3, hint: "查看准入证据与离线评测边界" },
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

const pageFromHash = (): PageKey => {
  const value = window.location.hash.replace(/^#\/?/, "") as PageKey;
  return NAV_ITEMS.some((item) => item.key === value) ? value : "live";
};

const preferredCameraResolution = (camera?: CameraDevice): string => {
  if (!camera) return "1280x720";
  return ["1280x720", "1920x1080", "640x480"].find((value) => camera.resolutions.includes(value))
    || camera.resolutions[0]
    || "1280x720";
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
  announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void,
  onCaptureResolution?: (value?: string) => void,
): string | undefined {
  const [frameUrl, setFrameUrl] = useState<string>();
  const frameRef = useRef<string | undefined>(undefined);
  const reportedError = useRef(false);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | undefined;
    const session = `camera-${Date.now().toString(36)}-${(++cameraSessionSequence).toString(36)}`;
    reportedError.current = false;

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
          announce(error instanceof ApiError ? `摄像头打开失败：${error.message}` : "摄像头打开失败。", "danger");
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
          // Schedule the next request only after this one completes.  This
          // keeps the displayed frame current without building a backlog when
          // V4L2 or JPEG encoding is slower than the target cadence.
          await wait(CAMERA_PREVIEW_DELAY_MS);
        } catch (error) {
          if (stopped || (error instanceof DOMException && error.name === "AbortError")) break;
          if (!reportedError.current) {
            reportedError.current = true;
            announce(error instanceof ApiError ? `摄像头预览失败：${error.message}` : "摄像头预览失败。", "danger");
          }
          await wait(1000);
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
  }, [active, device, resolution, announce, onCaptureResolution]);

  return frameUrl;
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
  const [health, setHealth] = useState<HealthStatus>({ status: "unknown", message: "尚未连接服务" });
  const [cameras, setCameras] = useState<CameraDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [drawer, setDrawer] = useState<"config" | "templates" | "system" | null>(null);
  const [config, setConfig] = useState<RecognitionConfig>({
    modelId: EMPTY_BOOTSTRAP.defaults.modelId,
    backend: "npu",
    precision: EMPTY_BOOTSTRAP.defaults.precision,
    threshold: EMPTY_BOOTSTRAP.defaults.threshold,
    assumeRoi: true,
  });
  const { toast, announce } = useToast();

  const refresh = useCallback(async () => {
    setLoading(true);
    const [bootstrapResult, healthResult, camerasResult, candidatesResult] = await Promise.allSettled([api.bootstrap(), api.health(), api.cameras(), api.candidates()]);
    if (bootstrapResult.status === "fulfilled") {
      setBootstrap(bootstrapResult.value);
      setConfig((current) => ({
        ...current,
        modelId: (() => {
          const admitted = bootstrapResult.value.models.filter((model) => model.usableForRecognition !== false && model.availableBackends.includes("npu"));
          if (admitted.some((model) => model.id === current.modelId)) return current.modelId;
          return admitted.find((model) => model.id === bootstrapResult.value.defaults.modelId)?.id || admitted[0]?.id || "";
        })(),
        backend: "npu",
        precision: bootstrapResult.value.defaults.precision || current.precision,
        threshold: bootstrapResult.value.defaults.threshold || current.threshold,
      }));
    } else {
      setBootstrap(EMPTY_BOOTSTRAP);
      setConfig((current) => ({ ...current, modelId: "", backend: "npu", precision: "mixed_fp16" }));
    }
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    else setHealth({ status: "unknown", message: "无法连接工作台服务" });
    if (camerasResult.status === "fulfilled") setCameras(camerasResult.value);
    else setCameras([]);
    if (candidatesResult.status === "fulfilled") setCandidates(candidatesResult.value);
    else setCandidates([]);
    const failed = [bootstrapResult, healthResult, camerasResult, candidatesResult].some((result) => result.status === "rejected");
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
          {page === "live" ? <LivePage bootstrap={bootstrap} cameras={cameras} config={config} activeModel={activeModel} setConfigValue={setConfigValue} openConfig={() => setDrawer("config")} announce={announce} /> : null}
          {page === "enrollment" ? <EnrollmentPage bootstrap={bootstrap} cameras={cameras} config={config} setConfigValue={setConfigValue} announce={announce} openTemplates={() => setDrawer("templates")} /> : null}
          {page === "evaluation" ? <EvaluationPage candidates={candidates} /> : null}
          {page === "system" ? <SystemPage onRefresh={refresh} loading={loading} announce={announce} /> : null}
        </main>
      </div>
      <Drawer open={drawer === "config"} title="识别配置" description="配置只影响下一次识别，不会修改已保存模板。" onClose={() => setDrawer(null)} width="regular">
        <ConfigForm bootstrap={bootstrap} config={config} setConfigValue={setConfigValue} />
        {activeModel?.conversionOnly || activeModel?.usableForRecognition === false ? <Notice tone="warning">{activeModel.displayName} 仅用于转换验证，不能用于正式识别。</Notice> : null}
        {activeModel?.manualTestPending ? <Notice tone="warning">{activeModel.displayName} 处于人工测试阶段，尚未完成稳定性验收。</Notice> : null}
      </Drawer>
      <Drawer open={drawer === "templates"} title="模板管理" description="模板按模型和掌侧隔离保存。" onClose={() => setDrawer(null)} width="wide"><TemplateDrawer announce={announce} config={config} /></Drawer>
      <Drawer open={drawer === "system"} title="系统与资产" description="查看完整模型、数据集和摄像头清单。" onClose={() => setDrawer(null)} width="wide"><SystemDrawer announce={announce} /></Drawer>
      {toast ? <div className="toast-region" role="status"><Notice tone={toast.tone}>{toast.message}</Notice></div> : null}
    </div>
  );
}

function ConfigForm({ bootstrap, config, setConfigValue }: { bootstrap: BootstrapData; config: RecognitionConfig; setConfigValue: <K extends keyof RecognitionConfig>(key: K, value: RecognitionConfig[K]) => void }) {
  const admittedModels = bootstrap.models.filter((item) => item.usableForRecognition !== false && item.availableBackends.includes("npu"));
  const model = admittedModels.find((item) => item.id === config.modelId) || admittedModels[0];
  const changeModel = (modelId: string) => {
    const next = bootstrap.models.find((item) => item.id === modelId);
    setConfigValue("modelId", modelId);
    setConfigValue("backend", "npu");
    setConfigValue("precision", "mixed_fp16");
    if (next?.threshold !== undefined) setConfigValue("threshold", next.threshold);
  };
  return <div className="config-form">
    <SelectField label="识别模型" value={model?.id || ""} onChange={(event) => changeModel(event.target.value)} options={admittedModels.length ? admittedModels.map((item) => ({ value: item.id, label: item.displayName })) : [{ value: "", label: "暂无通过 NPU 准入的模型", disabled: true }]} />
    {!admittedModels.length ? <Notice tone="danger">当前没有通过 NPU 准入的模型。候选审计仍可在评测页查看，正式识别暂不可用。</Notice> : null}
    <div className="npu-lock"><Zap aria-hidden="true" /><div><strong>Ascend NPU</strong><span>生产服务固定使用 NPU，不提供 CPU fallback。</span></div></div>
    <div className="npu-lock"><Zap aria-hidden="true" /><div><strong>混合 FP16</strong><span>生产模型固定使用 NPU mixed-FP16 OM。</span></div></div>
    <RangeField label="接受阈值" min={0.4} max={0.98} step={0.01} value={config.threshold} onChange={(value) => setConfigValue("threshold", value)} />
    <Toggle label="输入已是 ROI" description="已有 128 × 128 掌纹时跳过定位" checked={config.assumeRoi} onChange={(checked) => setConfigValue("assumeRoi", checked)} />
  </div>;
}

function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return <div className="page-header"><div><p className="page-header__eyebrow">{eyebrow}</p><h2>{title}</h2><p>{description}</p></div>{action ? <div className="page-header__action">{action}</div> : null}</div>;
}

function LivePage({ bootstrap, cameras, config, activeModel, setConfigValue, openConfig, announce }: { bootstrap: BootstrapData; cameras: CameraDevice[]; config: RecognitionConfig; activeModel?: ModelOption; setConfigValue: <K extends keyof RecognitionConfig>(key: K, value: RecognitionConfig[K]) => void; openConfig: () => void; announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void }) {
  const [file, setFile] = useState<File>();
  const [fileUrl, setFileUrl] = useState<string>();
  const [result, setResult] = useState<RecognitionResult>();
  const [busy, setBusy] = useState(false);
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraDevice, setCameraDevice] = useState(cameras[0]?.device || "");
  const [resolution, setResolution] = useState(preferredCameraResolution(cameras[0]));
  const [captureResolution, setCaptureResolution] = useState<string>();
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (!cameraDevice && cameras[0]) setCameraDevice(cameras[0].device); }, [cameraDevice, cameras]);
  useEffect(() => {
    if (!cameras.length || !cameraDevice) return;
    const selected = cameras.find((camera) => camera.device === cameraDevice) || cameras[0];
    if (!selected.resolutions.includes(resolution)) setResolution(preferredCameraResolution(selected));
  }, [cameraDevice, cameras, resolution]);
  useEffect(() => {
    if (cameraActive && (!cameras.length || !cameras.some((camera) => camera.device === cameraDevice))) {
      setCameraActive(false);
      const nextDevice = cameras[0]?.device || "";
      setCameraDevice(nextDevice);
      if (nextDevice) setResolution(preferredCameraResolution(cameras[0]));
      setCaptureResolution(undefined);
      announce("摄像头节点已变化，已停止旧预览；请重新打开新设备。", "warning");
    }
  }, [announce, cameraActive, cameraDevice, cameras]);
  const frameUrl = useCameraPreview(cameraActive, cameraDevice, resolution, announce, setCaptureResolution);
  useEffect(() => () => { if (fileUrl) URL.revokeObjectURL(fileUrl); }, [fileUrl]);
  const chooseFile = (next?: File) => { if (!next) return; if (fileUrl) URL.revokeObjectURL(fileUrl); setFile(next); setFileUrl(URL.createObjectURL(next)); setResult(undefined); };
  const runUpload = async () => {
    if (!activeModel || !config.modelId) { announce("当前没有通过 NPU 准入的模型。", "danger"); return; }
    if (!file) { announce("请先选择一张掌纹图像。", "warning"); return; }
    setBusy(true); try { setResult(await api.recognize(file, config)); announce("识别完成。", "success"); } catch (error) { announce(error instanceof ApiError ? error.message : "识别失败，请检查输入。", "danger"); } finally { setBusy(false); }
  };
  const runCamera = async () => {
    if (!activeModel || !config.modelId) { announce("当前没有通过 NPU 准入的模型。", "danger"); return; }
    if (!cameraDevice) { announce("未检测到板载摄像头。", "warning"); return; }
    setBusy(true); try { setResult(await api.recognizeCamera(cameraDevice, resolution, config)); announce("已完成当前帧识别。", "success"); } catch (error) { announce(error instanceof ApiError ? error.message : "摄像头识别失败。", "danger"); } finally { setBusy(false); }
  };
  const preview = result?.previewUrl || fileUrl;
  const selectedCamera = cameras.find((camera) => camera.device === cameraDevice);
  return <div className="page page--live">
    <PageHeader eyebrow="01 / 识别" title="实时识别" description="从上传图像或板载摄像头获取一帧，完成定位、质量检查和模板匹配。" action={<Button variant="secondary" icon={SlidersHorizontal} onClick={openConfig}>识别配置</Button>} />
    <div className="live-layout">
      <section className="live-main">
        <Panel title="输入画面" eyebrow="CAPTURE" action={<span className="panel-note">支持上传与 V4L2 摄像头</span>}>
          <div className="capture-strip">
            <input ref={inputRef} type="file" accept="image/*" className="visually-hidden" onChange={(event) => chooseFile(event.currentTarget.files?.[0])} />
            <Button variant="secondary" icon={Upload} onClick={() => inputRef.current?.click()}>选择图像</Button>
            <Button variant={cameraActive ? "primary" : "secondary"} icon={cameraActive ? Wifi : Camera} onClick={() => setCameraActive((value) => !value)}>{cameraActive ? "关闭预览" : "打开摄像头"}</Button>
            {file ? <span className="file-chip"><FileImage aria-hidden="true" />{file.name}</span> : <span className="capture-hint">建议使用正面、完整掌纹图像</span>}
          </div>
          {cameraActive ? <div className="camera-controls"><SelectField label="摄像头" value={cameraDevice} onChange={(event) => setCameraDevice(event.target.value)} options={cameras.length ? cameras.map((camera) => ({ value: camera.device, label: `${camera.name} · ${camera.device}` })) : [{ value: "", label: "未检测到摄像头", disabled: true }]} /><SelectField label="分辨率" value={resolution} onChange={(event) => setResolution(event.target.value)} options={(selectedCamera?.resolutions || ["1280x720", "1920x1080"]).map((item) => ({ value: item, label: item }))} /><span className="camera-effective-resolution">实际输入：{captureResolution || "等待首帧"}</span><Button variant="primary" icon={Camera} loading={busy} onClick={() => void runCamera()}>拍摄并识别</Button></div> : null}
          <ImageStage imageUrl={cameraActive ? frameUrl : preview} title="等待输入掌纹" description="选择本地图像，或打开摄像头查看实时画面。" active={cameraActive} objectFit="contain" onUpload={() => inputRef.current?.click()} onCamera={() => setCameraActive(true)} />
          {result?.roiUrl ? <div className="roi-strip"><div><span className="section-label">定位 ROI</span><strong>已生成 128 × 128 标准区域</strong></div><img src={result.roiUrl} alt="定位后的掌纹 ROI" /></div> : null}
        </Panel>
      </section>
      <aside className="live-inspector">
        <Panel title="识别结果" eyebrow="INSPECTOR" action={<StatusPill tone={result ? toneForStatus(result.accepted ? "accepted" : "拒识") : "neutral"}>{result ? (result.accepted ? "匹配通过" : "未通过") : "等待输入"}</StatusPill>}>
          {result ? <ResultSummary result={result} /> : <div className="empty-inspector"><div className="empty-inspector__icon"><CircleHelp /></div><strong>还没有识别结果</strong><p>上传图像或拍摄一帧后，结果会显示在这里。</p></div>}
          <div className="inspector-actions"><Button variant="primary" icon={ScanLine} loading={busy} onClick={() => void runUpload()} disabled={!file || !activeModel}>识别上传图像</Button><div className="inspector-meta"><span><Fingerprint aria-hidden="true" />{activeModel?.displayName || "未连接服务"}</span><span><Gauge aria-hidden="true" />阈值 {config.threshold.toFixed(2)}</span></div></div>
        </Panel>
        <div className="quick-status"><div className="quick-status__item"><span className="quick-status__label">推理设备</span><strong>Ascend NPU</strong></div><div className="quick-status__item"><span className="quick-status__label">输入契约</span><strong>{config.assumeRoi ? "128 × 128 ROI" : "自动定位"}</strong></div></div>
      </aside>
    </div>
  </div>;
}

function ResultSummary({ result }: { result: RecognitionResult }) {
  const best = result.matches[0];
  const resultName = result.userName || best?.userName || (result.matches.length ? "未通过匹配" : "未找到模板");
  const totalMs = result.timing["total ms"];
  return <div className="result-summary"><div className={cx("result-banner", result.accepted ? "result-banner--success" : "result-banner--warning")}><div><span className="result-banner__label">{result.accepted ? "最佳匹配" : "建议复核"}</span><strong>{resultName}</strong>{result.palmSide || best?.palmSide ? <span>{result.palmSide || best?.palmSide}</span> : null}</div><strong className="result-banner__score">{(result.score ?? best?.score ?? 0).toFixed(3)}</strong></div><div className="result-facts"><div><span>质量</span><strong>{String(result.quality?.passed ?? result.quality?.score ?? "--")}</strong></div><div><span>匹配数</span><strong>{result.matches.length}</strong></div><div><span>总耗时</span><strong>{formatMs(totalMs)}</strong></div></div><div className="match-list"><div className="section-label">TOP-K 候选</div>{result.matches.slice(0, 3).map((match, index) => <div className="match-row" key={`${match.userId || match.userName}-${index}`}><span className="match-rank">{index + 1}</span><span className="match-name">{match.userName}<small>{match.palmSide || "掌侧未标注"}</small></span><strong>{match.score.toFixed(3)}</strong></div>)}</div></div>;
}

function EnrollmentPage({ bootstrap, cameras, config, setConfigValue, announce, openTemplates }: { bootstrap: BootstrapData; cameras: CameraDevice[]; config: RecognitionConfig; setConfigValue: <K extends keyof RecognitionConfig>(key: K, value: RecognitionConfig[K]) => void; announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void; openTemplates: () => void }) {
  const [name, setName] = useState("");
  const [palmSide, setPalmSide] = useState("left");
  const [session, setSession] = useState<EnrollmentSession>();
  const [file, setFile] = useState<File>();
  const [fileUrl, setFileUrl] = useState<string>();
  const [cameraDevice, setCameraDevice] = useState(cameras[0]?.device || "");
  const [resolution, setResolution] = useState(preferredCameraResolution(cameras[0]));
  const [cameraActive, setCameraActive] = useState(false);
  const [captureResolution, setCaptureResolution] = useState<string>();
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (!cameraDevice && cameras[0]) setCameraDevice(cameras[0].device); }, [cameraDevice, cameras]);
  useEffect(() => {
    if (!cameras.length || !cameraDevice) return;
    const selected = cameras.find((camera) => camera.device === cameraDevice) || cameras[0];
    if (!selected.resolutions.includes(resolution)) setResolution(preferredCameraResolution(selected));
  }, [cameraDevice, cameras, resolution]);
  useEffect(() => {
    if (cameraActive && (!cameras.length || !cameras.some((camera) => camera.device === cameraDevice))) {
      setCameraActive(false);
      const nextDevice = cameras[0]?.device || "";
      setCameraDevice(nextDevice);
      if (nextDevice) setResolution(preferredCameraResolution(cameras[0]));
      setCaptureResolution(undefined);
      announce("摄像头节点已变化，已停止旧预览；请重新打开新设备。", "warning");
    }
  }, [announce, cameraActive, cameraDevice, cameras]);
  const frameUrl = useCameraPreview(cameraActive, cameraDevice, resolution, announce, setCaptureResolution);
  useEffect(() => () => { if (fileUrl) URL.revokeObjectURL(fileUrl); }, [fileUrl]);
  const chooseFile = (next?: File) => { if (!next) return; if (fileUrl) URL.revokeObjectURL(fileUrl); setFile(next); setFileUrl(URL.createObjectURL(next)); };
  const ensureSession = async () => { if (session) return session; const created = await api.createEnrollmentSession(config); setSession(created); return created; };
  const addSample = async () => {
    if (!file && (!cameraDevice || !cameraActive)) { announce("请上传样本，或先打开摄像头预览。", "warning"); return; }
    setBusy(true); try { const current = await ensureSession(); const next = await api.addEnrollmentSample(current.id, { file, cameraDevice: file ? undefined : cameraDevice, resolution, assumeRoi: config.assumeRoi }); setSession(next); setFile(undefined); if (fileUrl) URL.revokeObjectURL(fileUrl); setFileUrl(undefined); announce(`样本已加入（${next.samples.length}/${next.maxSamples}）。`, "success"); } catch (error) { announce(error instanceof ApiError ? error.message : "样本采集失败。", "danger"); } finally { setBusy(false); }
  };
  const commit = async () => { if (!name.trim()) { announce("请先填写姓名或编号。", "warning"); return; } if (!session || session.samples.length < session.minSamples) { announce(`至少需要 ${session?.minSamples || 3} 个合格样本。`, "warning"); return; } setBusy(true); try { await api.commitEnrollmentSession(session.id, { name: name.trim(), palmSide, config }); announce("模板已保存。", "success"); setSession(undefined); setName(""); } catch (error) { announce(error instanceof ApiError ? error.message : "模板保存失败。", "danger"); } finally { setBusy(false); } };
  const reset = async () => { if (session?.id) { try { await api.deleteEnrollmentSession(session.id); } catch { /* local reset still useful */ } } setSession(undefined); setFile(undefined); if (fileUrl) URL.revokeObjectURL(fileUrl); setFileUrl(undefined); announce("本次采集已清空。", "info"); };
  const count = session?.samples.length || 0;
  const changeEnrollmentModel = (modelId: string) => {
    const next = bootstrap.models.find((item) => item.id === modelId);
    setConfigValue("modelId", modelId);
    setConfigValue("backend", "npu");
    setConfigValue("precision", "mixed_fp16");
    if (next?.threshold !== undefined) setConfigValue("threshold", next.threshold);
  };
  return <div className="page page--enrollment">
    <PageHeader eyebrow="02 / 注册" title="掌纹注册" description="为同一人员的一只手采集 3 至 5 个质量合格样本，保存为独立模板。" action={<Button variant="secondary" icon={Images} onClick={openTemplates}>模板管理</Button>} />
    <div className="enrollment-layout">
      <section className="enrollment-capture"><Panel title="采集画面" eyebrow="SAMPLE COLLECTION"><input ref={inputRef} type="file" accept="image/*" className="visually-hidden" onChange={(event) => chooseFile(event.currentTarget.files?.[0])} /><div className="capture-strip"><Button variant="secondary" icon={Upload} onClick={() => inputRef.current?.click()}>选择样本</Button><Button variant={cameraActive ? "primary" : "secondary"} icon={Camera} onClick={() => setCameraActive((value) => !value)}>{cameraActive ? "关闭预览" : "使用摄像头"}</Button>{file ? <span className="file-chip"><FileImage aria-hidden="true" />{file.name}</span> : <span className="capture-hint">每个样本都应保持掌心完整、清晰</span>}</div>{cameraActive ? <div className="camera-controls camera-controls--compact"><SelectField label="摄像头" value={cameraDevice} onChange={(event) => setCameraDevice(event.target.value)} options={cameras.length ? cameras.map((camera) => ({ value: camera.device, label: `${camera.name} · ${camera.device}` })) : [{ value: "", label: "未检测到摄像头", disabled: true }]} /><SelectField label="分辨率" value={resolution} onChange={(event) => setResolution(event.target.value)} options={(cameras.find((camera) => camera.device === cameraDevice)?.resolutions || ["1280x720", "1920x1080"]).map((item) => ({ value: item, label: item }))} /><span className="camera-effective-resolution">实际输入：{captureResolution || "等待首帧"}</span></div> : null}<ImageStage imageUrl={cameraActive ? frameUrl : fileUrl} title="等待采集样本" description="上传一张图像，或打开摄像头实时取样。" active={cameraActive} onUpload={() => inputRef.current?.click()} onCamera={() => setCameraActive(true)} /><div className="sample-actions"><Button variant="primary" icon={UserPlus} loading={busy} onClick={() => void addSample()}>添加样本</Button><Button variant="quiet" icon={RefreshCw} onClick={() => void reset()}>清空本次采集</Button></div></Panel></section>
      <aside className="enrollment-inspector"><Panel title="注册信息" eyebrow="ENROLLMENT"><div className="identity-form"><TextField label="姓名或编号" placeholder="例如：P-001" value={name} onChange={(event) => setName(event.target.value)} /><div className="field"><span className="field__label">掌侧</span><div className="segmented"><button type="button" className={cx(palmSide === "left" && "is-active")} onClick={() => setPalmSide("left")}>左掌</button><button type="button" className={cx(palmSide === "right" && "is-active")} onClick={() => setPalmSide("right")}>右掌</button></div></div><SelectField label="模板模型" value={config.modelId} onChange={(event) => changeEnrollmentModel(event.target.value)} options={bootstrap.models.filter((model) => model.usableForRecognition !== false && model.availableBackends.includes("npu")).map((model) => ({ value: model.id, label: model.displayName }))} /></div><div className="sample-progress"><div className="sample-progress__header"><span>样本进度</span><strong>{count} / {session?.maxSamples || 5}</strong></div><div className="progress-track"><span style={{ width: `${Math.min(100, (count / (session?.maxSamples || 5)) * 100)}%` }} /></div><p>至少 {session?.minSamples || 3} 张，建议 5 张</p></div><div className="sample-gallery">{Array.from({ length: session?.maxSamples || 5 }).map((_, index) => <div className={cx("sample-slot", index < count && "is-filled")} key={index}>{index < count ? <img src={session?.samples[index]} alt={`第 ${index + 1} 个样本`} /> : <span>{index + 1}</span>}</div>)}</div><Button variant="primary" icon={Check} loading={busy} disabled={!session || count < (session?.minSamples || 3)} onClick={() => void commit()} className="save-template-button">保存模板</Button><Notice tone="info">模板会按“人员 + 掌侧 + 模型”隔离，保存后可在模板管理中删除。</Notice></Panel></aside>
    </div>
  </div>;
}

function EvaluationPage({ candidates }: { candidates: CandidateOption[] }) {
  const [taskFilter, setTaskFilter] = useState("all");
  const [modalityFilter, setModalityFilter] = useState("all");
  const [npuFilter, setNpuFilter] = useState("all");
  const filteredCandidates = candidates.filter((candidate) =>
    (taskFilter === "all" || candidate.taskType === taskFilter)
    && (modalityFilter === "all" || candidate.modality === modalityFilter)
    && (npuFilter === "all" || candidate.npuStatus === npuFilter),
  );
  const taskOptions = Array.from(new Set(candidates.map((candidate) => candidate.taskType))).sort();
  const modalityOptions = Array.from(new Set(candidates.map((candidate) => candidate.modality).filter(Boolean) as string[])).sort();
  const npuOptions = Array.from(new Set(candidates.map((candidate) => candidate.npuStatus).filter(Boolean) as string[])).sort();
  return <div className="page page--evaluation">
    <PageHeader eyebrow="03 / 候选审计" title="模型候选审计" description="在线服务只展示候选的准入证据；未准入模型不会从此页面启动推理或评测。" />
    <Notice tone="info">候选比较仅在离线研究环境执行：<code>python -m tools.offline.benchmark</code></Notice>
    <Panel title="候选矩阵" eyebrow="READ-ONLY AUDIT" className="candidate-matrix-panel"><div className="candidate-filters"><SelectField label="任务类型" value={taskFilter} onChange={(event) => setTaskFilter(event.target.value)} options={[{ value: "all", label: "全部任务" }, ...taskOptions.map((value) => ({ value, label: value }))]} /><SelectField label="模态" value={modalityFilter} onChange={(event) => setModalityFilter(event.target.value)} options={[{ value: "all", label: "全部模态" }, ...modalityOptions.map((value) => ({ value, label: value }))]} /><SelectField label="NPU 状态" value={npuFilter} onChange={(event) => setNpuFilter(event.target.value)} options={[{ value: "all", label: "全部状态" }, ...npuOptions.map((value) => ({ value, label: value }))]} /><span className="candidate-filter-count">显示 {filteredCandidates.length} / {candidates.length}</span></div><div className="candidate-audit-list" role="list">{filteredCandidates.map((candidate) => <article className={cx("candidate-audit-row", candidate.usableForRecognition === false && "is-audit-only")} key={candidate.id} role="listitem"><div className="candidate-audit-row__summary"><strong>{candidate.displayName}</strong><small>{candidate.taskType} · {candidate.modality || "未声明模态"}{candidate.manualTestPending ? " · 人工测试待验收" : ""}{candidate.reproducible === false ? ` · ${candidate.reproducibilityReason || "来源 revision 未固定"}` : ""}</small></div><StatusPill tone={toneForStatus(candidate.manualTestPending ? "pending" : candidate.npuStatus || "pending")}>{candidate.manualTestPending ? "manual_test_pending" : candidate.npuStatus || "pending"}</StatusPill><p>{candidate.eligibilityReason || candidate.naReasons?.identity_metrics || "候选状态由 manifest 审计记录提供。"}</p></article>)}</div>{filteredCandidates.length === 0 ? <div className="empty-state candidate-empty"><BarChart3 /><strong>没有符合筛选条件的候选</strong></div> : null}</Panel>
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
  const remove = async (item: TemplateItem) => { try { await api.deleteTemplate(item.id, config); setTemplates((current) => current.filter((candidate) => candidate.id !== item.id)); announce("模板已删除。", "success"); } catch (error) { announce(error instanceof ApiError ? error.message : "模板删除失败。", "danger"); } };
  return <div className="drawer-section"><div className="drawer-toolbar"><span>{templates.length} 个模板</span><Button variant="quiet" icon={RefreshCw} loading={loading} onClick={() => void load()}>刷新</Button></div><div className="template-list">{templates.length ? templates.map((item) => <div className="template-row" key={item.id}><div className="template-avatar"><Fingerprint /></div><div><strong>{item.userName}</strong><span>{item.palmSide || "掌侧未标注"} · {item.samples || 0} 个样本</span><small>{item.modelId || "模型未标注"} · {formatDate(item.updatedAt)}</small></div><button type="button" className="danger-icon-button" aria-label={`删除 ${item.userName}`} title="删除模板" onClick={() => void remove(item)}><Trash2 aria-hidden="true" /></button></div>) : <div className="empty-state empty-state--small"><HardDrive /><strong>暂无模板</strong><p>完成注册后，模板会显示在这里。</p></div>}</div></div>;
}

function SystemDrawer({ announce }: { announce: (message: string, tone?: "success" | "warning" | "danger" | "info") => void }) {
  const [status, setStatus] = useState<SystemStatus>();
  const [loading, setLoading] = useState(false);
  const load = async () => { setLoading(true); try { setStatus(await api.systemStatus()); } catch (error) { announce(error instanceof ApiError ? error.message : "系统状态读取失败。", "danger"); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  return <div className="drawer-section"><div className="drawer-toolbar"><span>资产详情</span><Button variant="quiet" icon={RefreshCw} loading={loading} onClick={() => void load()}>刷新</Button></div><div className="drawer-asset-groups">{([ ["模型", status?.models || []], ["数据集", status?.datasets || []], ["摄像头", status?.cameras || []] ] as Array<[string, SystemItem[]]>).map(([title, items]) => <div className="drawer-asset-group" key={title}><h3>{title}</h3>{items.length ? items.map((item, index) => <div className="drawer-detail-row" key={item.id || `${item.title}-${index}`}><strong>{item.title}</strong><StatusPill tone={toneForStatus(item.status)} icon={false}>{item.status || "未知"}</StatusPill><span>{item.description || "--"}</span></div>) : <p className="muted">暂无记录</p>}</div>)}</div></div>;
}

export default App;
