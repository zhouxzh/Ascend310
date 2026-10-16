export type PageKey = "live" | "enrollment" | "evaluation" | "system";
/** Production UI is deliberately NPU-only. CPU remains an offline CLI concern. */
export type BackendId = "npu";
export type PrecisionId = "mixed_fp16";

export type CandidateTaskType =
  | "embedding"
  | "roi"
  | "detector"
  | "segmentation"
  | "classifier"
  | "pair_comparison"
  | "vein_classifier"
  | "vein_embedding"
  | "sdk"
  | "code"
  | "audit";

export type CandidateStatus = "ready" | "pending" | "audit-only" | "failed" | string;

export interface ModelOption {
  id: string;
  displayName: string;
  license?: string;
  availableBackends: BackendId[];
  precisionOptions: PrecisionId[];
  conversionOnly?: boolean;
  usableForRecognition?: boolean;
  status?: string;
  taskType?: CandidateTaskType;
  modality?: string;
  featureDim?: number;
  trainingDomains?: string[];
  weightStatus?: string;
  npuStatus?: string;
  eligibilityReason?: string;
  threshold?: number;
  manualTestPending?: boolean;
}

export interface DatasetOption {
  id: string;
  displayName: string;
  status?: string;
  description?: string;
}

export interface CameraDevice {
  device: string;
  name: string;
  resolutions: string[];
  state?: string;
  physicalId?: string;
  aliases?: string[];
  actualResolution?: string;
  frameFormat?: string;
}

export interface RoiGuideConfig {
  center_x: number;
  center_y: number;
  side_ratio: number;
}

export interface BootstrapData {
  appVersion?: string;
  models: ModelOption[];
  datasets: DatasetOption[];
  candidates?: CandidateOption[];
  defaults: {
    modelId: string;
    backend: BackendId;
    precision: PrecisionId;
    threshold: number;
    cameraResolution: string;
  };
}

export interface CandidateOption {
  id: string;
  displayName: string;
  source?: string;
  revision?: string;
  taskType: CandidateTaskType;
  modality?: string;
  license?: string;
  weightStatus?: string;
  weightSha256?: string;
  exportStatus?: string;
  omStatus?: string;
  boardNpuStatus?: string;
  onnxSha256?: string;
  onnxBytes?: number;
  npuStatus?: string;
  metricsStatus?: string;
  usableForRecognition?: boolean;
  eligibilityReason?: string;
  reproducible?: boolean;
  reproducibilityReason?: string;
  trainingDomains?: string[];
  metrics?: Record<string, number | string | null>;
  performance?: Record<string, number | string | null>;
  naReasons?: Record<string, string>;
  manualTestPending?: boolean;
  diagnosticStatus?: string;
  diagnosticBlockingCategories?: string[];
}

export interface CandidateRuntimeOption {
  id: string;
  displayName: string;
  taskType: CandidateTaskType | string;
  modality?: string;
  inputKind: string;
  inputShape: number[] | number[][];
  outputKind: string;
  omPath: string;
  bytes: number;
  sha256: string;
  available: boolean;
  reason?: string;
}

export interface CandidateRunResult {
  candidateId: string;
  displayName: string;
  taskType: string;
  modality?: string;
  backend: BackendId;
  precision: PrecisionId;
  omPath: string;
  omBytes: number;
  omSha256: string;
  inputShapes: number[][];
  outputShapes: number[][];
  outputDtypes: string[];
  outputFinite: boolean;
  inferenceMs: number;
  totalMs: number;
  topK?: Array<{ classId: number; logit: number }>;
  theta?: number[];
  embeddingDim?: number;
  embeddingL2Norm?: number;
  embeddingDims?: number[];
  cosineSimilarity?: number;
}

export interface RecognitionConfig {
  modelId: string;
  backend: BackendId;
  precision: PrecisionId;
  threshold: number;
  assumeRoi: boolean;
}

export interface MatchItem {
  palmId?: string;
  personId?: string;
  displayName?: string;
  userId?: string;
  userName: string;
  palmSide?: string;
  score: number;
  samples?: number;
}

export interface RecognitionResult {
  accepted?: boolean | null;
  outcome?: "roi_rejected" | "no_templates" | "candidates_only" | "below_threshold" | "matched" | string;
  score?: number;
  palmId?: string;
  personId?: string;
  displayName?: string;
  userName?: string;
  palmSide?: string;
  status: string;
  previewUrl?: string;
  roiUrl?: string;
  quality: Record<string, number | string | boolean>;
  matches: MatchItem[];
  timing: Record<string, number>;
  geometry?: Record<string, number | string | boolean | unknown[]>;
  roiPipelineId?: string;
  templateSummary?: { palmCount: number; personCount: number; sampleCount: number; roiPipelineId?: string };
  calibrationReady?: boolean;
}

export interface EnrollmentSample {
  id: string;
  captureId?: string;
  previewUrl?: string;
  roiUrl?: string;
  originalUrl?: string;
  quality?: Record<string, number | string | boolean>;
  geometry?: Record<string, number | string | boolean | unknown[]>;
}

export interface EnrollmentSession {
  id: string;
  status?: string;
  phase?: string;
  guide?: string;
  mode?: "self_service" | "assisted" | string;
  reviewActionsEnabled?: boolean;
  personId?: string;
  displayName?: string;
  palmSide?: string;
  samples: EnrollmentSample[];
  maxSamples: number;
  minSamples: number;
  previewUrl?: string;
  roiUrl?: string;
  quality?: Record<string, number | string | boolean>;
  geometry?: Record<string, number | string | boolean | unknown[]>;
  rejections?: Record<string, number>;
  lastError?: string;
  actualResolution?: string;
}

export interface TemplateItem {
  id: string;
  palmId?: string;
  personId?: string;
  displayName?: string;
  userName: string;
  palmSide?: string;
  samples?: number;
  modelId?: string;
  updatedAt?: string;
}

export interface EvaluationRequest extends RecognitionConfig {
  datasetId: string;
  spectrum: string;
  maxIdentities: number;
}

export interface EvaluationTask {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  progress: number;
  message?: string;
  metrics: Record<string, number | string>;
  performance: {
    pureModelP50Ms?: number;
    pipelineP50Ms?: number;
    [key: string]: number | undefined;
  };
  reportUrl?: string;
}

export interface HealthStatus {
  status: "ok" | "warning" | "error" | "unknown" | string;
  message?: string;
  transportReady?: boolean;
  runtimeImportable?: boolean;
  modelReady?: boolean;
  admittedModelIds?: string[];
}

export interface SystemItem {
  id?: string;
  title: string;
  status?: string;
  description?: string;
  fields?: Record<string, string | number | boolean | undefined>;
}

export interface SystemStatus {
  summary: Record<string, string | number | boolean | undefined>;
  models: SystemItem[];
  datasets: SystemItem[];
  cameras: SystemItem[];
}
