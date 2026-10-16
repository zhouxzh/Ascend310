import type {
  BackendId,
  BootstrapData,
  CandidateOption,
  CandidateRuntimeOption,
  CandidateRunResult,
  CameraDevice,
  DatasetOption,
  EnrollmentSession,
  EvaluationRequest,
  EvaluationTask,
  HealthStatus,
  MatchItem,
  ModelOption,
  PrecisionId,
  RecognitionConfig,
  RecognitionResult,
  SystemItem,
  SystemStatus,
  TemplateItem,
} from "./types";

type JsonRecord = Record<string, unknown>;

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface CameraFrameResult {
  url: string;
  captureResolution?: string;
  previewResolution?: string;
}

const asRecord = (value: unknown): JsonRecord =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : {};

const asArray = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);

const stringValue = (value: unknown, fallback = ""): string =>
  typeof value === "string" || typeof value === "number" ? String(value) : fallback;

const numberValue = (value: unknown, fallback = 0): number => {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const boolValue = (value: unknown, fallback = false): boolean =>
  typeof value === "boolean" ? value : fallback;

const payload = (value: unknown): JsonRecord => {
  const object = asRecord(value);
  const nested = asRecord(object.data);
  return Object.keys(nested).length ? nested : object;
};

const list = (object: JsonRecord, ...keys: string[]): unknown[] => {
  for (const key of keys) {
    const candidate = asArray(object[key]);
    if (candidate.length || Array.isArray(object[key])) return candidate;
  }
  return [];
};

const request = async <T>(path: string, init?: RequestInit): Promise<T> => {
  let response: Response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError("无法连接工作台服务，请检查服务状态。", 0);
  }

  const text = await response.text();
  let body: unknown = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { message: text };
  }
  if (!response.ok) {
    const error = asRecord(body);
    throw new ApiError(
      stringValue(error.detail || error.message || error.error, `请求失败（${response.status}）`),
      response.status,
    );
  }
  return body as T;
};

const json = <T>(path: string, method = "GET", body?: unknown): Promise<T> =>
  request<T>(path, body === undefined ? { method } : { method, body: JSON.stringify(body) });

const urlForImage = (value: unknown): string | undefined => {
  const url = stringValue(value);
  if (!url) return undefined;
  return url.startsWith("data:") || url.startsWith("/") || url.startsWith("http") ? url : undefined;
};

const normalizeModel = (value: unknown): ModelOption => {
  const item = asRecord(value);
  const id = stringValue(item.id || item.model_id, "unknown");
  const backendSource = list(item, "available_backends", "backends").map(String);
  const taskType = stringValue(item.task_type || item.taskType) || undefined;
  return {
    id,
    displayName: stringValue(item.display_name || item.displayName || item.name, id),
    license: stringValue(item.license) || undefined,
    // The HTTP production surface is NPU-only. A malformed/legacy payload
    // must not reintroduce a CPU selector into the UI.
    availableBackends: ["npu"] as BackendId[],
    // Production recognition is mixed-FP16-only. A malformed or stale
    // bootstrap payload cannot recreate an unsupported precision selector.
    precisionOptions: ["mixed_fp16"] as PrecisionId[],
    conversionOnly: boolValue(item.conversion_only || item.conversionOnly),
    usableForRecognition:
      item.usable_for_recognition === undefined ? undefined : boolValue(item.usable_for_recognition),
    status: stringValue(item.status) || undefined,
    taskType: taskType as ModelOption["taskType"],
    modality: stringValue(item.modality) || undefined,
    featureDim: item.feature_dim === undefined ? undefined : numberValue(item.feature_dim),
    trainingDomains: list(item, "training_domains", "trainingDomains").map(String),
    weightStatus: stringValue(item.weight_status) || undefined,
    npuStatus: stringValue(item.npu_status) || undefined,
    eligibilityReason: stringValue(item.eligibility_reason) || undefined,
    threshold: item.threshold === undefined ? undefined : numberValue(item.threshold),
    manualTestPending: boolValue(item.manual_test_pending),
  };
};

const normalizeCandidate = (value: unknown): CandidateOption => {
  const item = asRecord(value);
  const metrics = asRecord(item.metrics);
  const performance = asRecord(item.performance);
  return {
    id: stringValue(item.id || item.candidate_id || item.model_id, "unknown"),
    displayName: stringValue(item.display_name || item.displayName || item.name, "未命名候选"),
    source: stringValue(item.source) || undefined,
    revision: stringValue(item.revision) || undefined,
    taskType: (stringValue(item.task_type || item.taskType, "embedding") as CandidateOption["taskType"]),
    modality: stringValue(item.modality) || undefined,
    license: stringValue(item.license || item.weight_license) || undefined,
    weightStatus: stringValue(item.weight_status) || undefined,
    weightSha256: stringValue(item.weight_sha256) || undefined,
    exportStatus: stringValue(item.export_status) || undefined,
    omStatus: stringValue(item.om_status) || undefined,
    boardNpuStatus: stringValue(item.board_npu_status) || undefined,
    onnxSha256: stringValue(item.onnx_sha256) || undefined,
    onnxBytes: item.onnx_bytes === undefined ? undefined : numberValue(item.onnx_bytes),
    npuStatus: stringValue(item.npu_status) || undefined,
    metricsStatus: stringValue(item.metrics_status) || undefined,
    usableForRecognition: item.usable_for_recognition === undefined ? undefined : boolValue(item.usable_for_recognition),
    eligibilityReason: stringValue(item.eligibility_reason) || undefined,
    reproducible: item.reproducible === undefined ? undefined : boolValue(item.reproducible),
    reproducibilityReason: stringValue(item.reproducibility_reason) || undefined,
    trainingDomains: list(item, "training_domains", "trainingDomains").map(String),
    metrics: Object.fromEntries(Object.entries(metrics).map(([key, metric]) => [key, metric === null ? null : typeof metric === "number" ? metric : stringValue(metric)])),
    performance: Object.fromEntries(Object.entries(performance).map(([key, metric]) => [key, metric === null ? null : typeof metric === "number" ? metric : stringValue(metric)])),
    naReasons: Object.fromEntries(Object.entries(asRecord(item.na_reasons)).map(([key, reason]) => [key, stringValue(reason)])),
    manualTestPending: boolValue(item.manual_test_pending),
    diagnosticStatus: stringValue(item.diagnostic_status) || undefined,
    diagnosticBlockingCategories: list(item, "diagnostic_blocking_categories").map(String),
  };
};

const normalizeCandidateRuntime = (value: unknown): CandidateRuntimeOption => {
  const item = asRecord(value);
  const inputShape = item.input_shape;
  const normalizedShape = Array.isArray(inputShape)
    ? inputShape.map((shape) => Array.isArray(shape) ? shape.map((dimension) => numberValue(dimension)) : numberValue(shape))
    : [];
  return {
    id: stringValue(item.id || item.candidate_id, "unknown"),
    displayName: stringValue(item.display_name || item.displayName || item.name, "未命名候选"),
    taskType: stringValue(item.task_type || item.taskType, "unknown"),
    modality: stringValue(item.modality) || undefined,
    inputKind: stringValue(item.input_kind || item.inputKind),
    inputShape: normalizedShape as CandidateRuntimeOption["inputShape"],
    outputKind: stringValue(item.output_kind || item.outputKind),
    omPath: stringValue(item.om_path || item.omPath),
    bytes: numberValue(item.bytes),
    sha256: stringValue(item.sha256),
    available: boolValue(item.available),
    reason: stringValue(item.reason) || undefined,
  };
};

const normalizeCandidateRun = (value: unknown): CandidateRunResult => {
  const item = payload(value);
  const topK = list(item, "top_k").map((entry) => {
    const top = asRecord(entry);
    return { classId: numberValue(top.class_id || top.classId), logit: numberValue(top.logit) };
  });
  return {
    candidateId: stringValue(item.candidate_id),
    displayName: stringValue(item.display_name),
    taskType: stringValue(item.task_type),
    modality: stringValue(item.modality) || undefined,
    backend: "npu",
    precision: "mixed_fp16",
    omPath: stringValue(item.om_path),
    omBytes: numberValue(item.om_bytes),
    omSha256: stringValue(item.om_sha256),
    inputShapes: list(item, "input_shapes").map((shape) => Array.isArray(shape) ? shape.map((dimension) => numberValue(dimension)) : []),
    outputShapes: list(item, "output_shapes").map((shape) => Array.isArray(shape) ? shape.map((dimension) => numberValue(dimension)) : []),
    outputDtypes: list(item, "output_dtypes").map(String),
    outputFinite: boolValue(item.output_finite),
    inferenceMs: numberValue(item.inference_ms),
    totalMs: numberValue(item.total_ms),
    topK: topK.length ? topK : undefined,
    theta: list(item, "theta").map((value) => numberValue(value)),
    embeddingDim: item.embedding_dim === undefined ? undefined : numberValue(item.embedding_dim),
    embeddingL2Norm: item.embedding_l2_norm === undefined ? undefined : numberValue(item.embedding_l2_norm),
    embeddingDims: list(item, "embedding_dims").map((value) => numberValue(value)),
    cosineSimilarity: item.cosine_similarity === undefined ? undefined : numberValue(item.cosine_similarity),
  };
};

const normalizeDataset = (value: unknown): DatasetOption => {
  const item = asRecord(value);
  const id = stringValue(item.id || item.dataset_id, "unknown");
  return {
    id,
    displayName: stringValue(item.display_name || item.displayName || item.name, id),
    status: stringValue(item.status) || undefined,
    description: stringValue(item.description || item.reason) || undefined,
  };
};

const normalizeCamera = (value: unknown): CameraDevice => {
  const item = asRecord(value);
  const resolutions = list(item, "resolutions", "available_resolutions").map(String);
  return {
    device: stringValue(item.device || item.path || item.id),
    name: stringValue(item.name || item.label || item.device, "V4L2 摄像头"),
    resolutions: resolutions.length ? resolutions : ["1280x720", "1920x1080", "640x480"],
    state: stringValue(item.state || item.status) || undefined,
    physicalId: stringValue(item.physical_id) || undefined,
    aliases: list(item, "aliases").map(String),
    actualResolution: stringValue(item.actual_resolution) || undefined,
    frameFormat: stringValue(item.frame_format) || undefined,
  };
};

const normalizeRecognition = (value: unknown): RecognitionResult => {
  const item = payload(value);
  const matches = list(item, "matches", "top_k").map((entry): MatchItem => {
    const match = asRecord(entry);
    return {
      palmId: stringValue(match.palm_id || match.user_id || match.id) || undefined,
      personId: stringValue(match.person_id) || undefined,
      displayName: stringValue(match.display_name || match.user_name || match.name) || undefined,
      userId: stringValue(match.palm_id || match.user_id || match.id) || undefined,
      userName: stringValue(match.display_name || match.user_name || match.name, "未知模板"),
      palmSide: stringValue(match.palm_side || match.side) || undefined,
      score: numberValue(match.score),
      samples: numberValue(match.samples, 0) || undefined,
    };
  });
  const timingSource = asRecord(item.timing);
  const qualitySource = asRecord(item.quality);
  const calibration = asRecord(item.calibration);
  const templateSummary = asRecord(item.template_summary);
  return {
    accepted: item.accepted === undefined ? undefined : item.accepted === null ? null : boolValue(item.accepted),
    outcome: stringValue(item.outcome) || undefined,
    score: item.score === undefined ? undefined : numberValue(item.score),
    palmId: stringValue(item.palm_id || item.user_id) || undefined,
    personId: stringValue(item.person_id) || undefined,
    displayName: stringValue(item.display_name || item.user_name || item.userName) || undefined,
    userName: stringValue(item.display_name || item.user_name || item.userName) || undefined,
    palmSide: stringValue(item.palm_side || item.palmSide) || undefined,
    status: stringValue(item.status || item.message, "等待识别"),
    previewUrl: urlForImage(item.preview_url || item.preview),
    roiUrl: urlForImage(item.roi_url || item.roi),
    quality: qualitySource as RecognitionResult["quality"],
    geometry: asRecord(item.geometry) as RecognitionResult["geometry"],
    roiPipelineId: stringValue(item.roi_pipeline_id) || undefined,
    templateSummary: Object.keys(templateSummary).length ? {
      palmCount: numberValue(templateSummary.palm_count),
      personCount: numberValue(templateSummary.person_count),
      sampleCount: numberValue(templateSummary.sample_count),
      roiPipelineId: stringValue(templateSummary.roi_pipeline_id) || undefined,
    } : undefined,
    calibrationReady: calibration.ready === undefined ? undefined : boolValue(calibration.ready),
    matches,
    timing: Object.fromEntries(
      Object.entries(timingSource).map(([key, timing]) => [key, numberValue(timing)]),
    ),
  };
};

const normalizeSession = (value: unknown): EnrollmentSession => {
  const item = payload(value);
  const sampleSource = list(item, "samples", "sample_urls");
  return {
    id: stringValue(item.id || item.session_id),
    status: stringValue(item.status || item.message) || undefined,
    phase: stringValue(item.phase) || undefined,
    guide: stringValue(item.guide || item.status || item.message) || undefined,
    mode: stringValue(item.mode) || undefined,
    reviewActionsEnabled: boolValue(item.review_actions_enabled || item.reviewActionsEnabled),
    personId: stringValue(item.person_id) || undefined,
    displayName: stringValue(item.display_name) || undefined,
    palmSide: stringValue(item.palm_side) || undefined,
    samples: sampleSource.map((sample, index) => {
      const record = asRecord(sample);
      return {
        id: stringValue(record.id || record.sample_id, String(index)),
        captureId: stringValue(record.capture_id) || undefined,
        previewUrl: urlForImage(record.preview_url || record.preview),
        roiUrl: urlForImage(record.roi_url || record.roi),
        originalUrl: urlForImage(record.original_url),
        quality: asRecord(record.quality) as EnrollmentSession["quality"],
        geometry: asRecord(record.geometry) as EnrollmentSession["geometry"],
      };
    }),
    maxSamples: numberValue(item.max_samples || item.maxSamples, 5),
    minSamples: numberValue(item.min_samples || item.minSamples, 3),
    previewUrl: urlForImage(item.preview_url || item.preview),
    roiUrl: urlForImage(item.roi_url || item.roi),
    quality: asRecord(item.quality) as EnrollmentSession["quality"],
    geometry: asRecord(item.geometry) as EnrollmentSession["geometry"],
    rejections: Object.fromEntries(Object.entries(asRecord(item.rejections)).map(([key, count]) => [key, numberValue(count)])),
    lastError: stringValue(item.last_error) || undefined,
    actualResolution: stringValue(item.actual_resolution) || undefined,
  };
};

const normalizeTemplates = (value: unknown): TemplateItem[] => {
  const item = payload(value);
  return list(item, "items", "templates", "users").map((entry): TemplateItem => {
    const template = asRecord(entry);
    return {
      id: stringValue(template.id || template.palm_id || template.user_id),
      palmId: stringValue(template.palm_id || template.id || template.user_id) || undefined,
      personId: stringValue(template.person_id) || undefined,
      displayName: stringValue(template.display_name || template.user_name || template.name) || undefined,
      userName: stringValue(template.display_name || template.user_name || template.name, "未命名"),
      palmSide: stringValue(template.palm_side || template.side) || undefined,
      samples: numberValue(template.samples, 0) || undefined,
      modelId: stringValue(template.model_id) || undefined,
      updatedAt: stringValue(template.updated_at || template.updatedAt) || undefined,
    };
  });
};

const normalizeEvaluation = (value: unknown): EvaluationTask => {
  const item = payload(value);
  const performanceSource = asRecord(item.performance);
  const metrics = asRecord(item.metrics);
  const pureModel = asRecord(performanceSource.pure_model);
  const pipeline = asRecord(performanceSource.pipeline);
  return {
    id: stringValue(item.id || item.evaluation_id),
    status: stringValue(item.status, "queued"),
    progress: Math.min(1, Math.max(0, numberValue(item.progress, 0))),
    message: stringValue(item.message || item.detail) || undefined,
    metrics: Object.fromEntries(
      Object.entries(metrics).map(([key, metric]) => [key, typeof metric === "number" ? metric : stringValue(metric)]),
    ),
    performance: {
      pureModelP50Ms: numberValue(performanceSource.pure_model_p50_ms || pureModel.p50_ms, 0) || undefined,
      pipelineP50Ms: numberValue(performanceSource.pipeline_p50_ms || pipeline.p50_ms, 0) || undefined,
    },
    reportUrl: urlForImage(item.report_url) || (stringValue(item.report_path) ? `/api/evaluations/${encodeURIComponent(stringValue(item.id || item.evaluation_id))}/report` : undefined),
  };
};

const normalizeSystemItem = (value: unknown): SystemItem => {
  const item = asRecord(value);
  const nestedFields = asRecord(item.fields);
  const sourceEntries = Object.keys(nestedFields).length
    ? Object.entries(nestedFields)
    : Object.entries(item).filter(([key]) => !["id", "title", "name", "display_name", "status", "description", "reason", "fields"].includes(key));
  const fields = Object.fromEntries(
    sourceEntries.map(([key, fieldValue]) => [
      key,
      fieldValue && typeof fieldValue === "object" ? JSON.stringify(fieldValue) : fieldValue,
    ]),
  ) as SystemItem["fields"];
  return {
    id: stringValue(item.id || item.device || item.path) || undefined,
    title: stringValue(item.title || item.display_name || item.name || item.id || item.device, "未命名项目"),
    status: stringValue(item.status) || undefined,
    description: stringValue(item.description || item.reason || item.state) || undefined,
    fields,
  };
};

export const api = {
  async bootstrap(): Promise<BootstrapData> {
    const item = payload(await json<unknown>("/api/bootstrap"));
    const defaults = asRecord(item.defaults);
    return {
      appVersion: stringValue(item.app_version || item.version) || undefined,
      models: list(item, "models").map(normalizeModel),
      datasets: list(item, "datasets").map(normalizeDataset),
      candidates: list(item, "candidates").map(normalizeCandidate),
      defaults: {
        modelId: stringValue(defaults.model_id || defaults.modelId),
        backend: "npu",
        precision: stringValue(defaults.precision, "mixed_fp16") as PrecisionId,
        threshold: numberValue(defaults.threshold, 0.75),
        cameraResolution: stringValue(defaults.camera_resolution || defaults.cameraResolution, "1280x720"),
      },
    };
  },

  async health(): Promise<HealthStatus> {
    const item = payload(await json<unknown>("/api/health"));
    return {
      status: stringValue(item.status, "unknown"),
      message: stringValue(item.message || item.detail) || undefined,
      transportReady: item.transport_ready === undefined ? undefined : boolValue(item.transport_ready),
      runtimeImportable: item.runtime_importable === undefined ? undefined : boolValue(item.runtime_importable),
      modelReady: item.model_ready === undefined ? undefined : boolValue(item.model_ready),
      admittedModelIds: list(item, "admitted_model_ids").map(String),
    };
  },

  async cameras(): Promise<CameraDevice[]> {
    const item = payload(await json<unknown>("/api/cameras"));
    return list(item, "items", "cameras", "devices").map(normalizeCamera).filter((camera) => Boolean(camera.device));
  },

  cameraFrameUrl(device: string, resolution: string, session?: string): string {
    const query = new URLSearchParams({
      resolution,
      // The preview is deliberately smaller than the capture mode.  The
      // server still captures at the selected mode (including 1920x1080),
      // but avoids pushing a multi-megapixel JPEG through the touch browser.
      preview: "true",
      max_width: "960",
      quality: "72",
      t: String(Date.now()),
    });
    if (session) query.set("session", session);
    return `/api/cameras/${encodeURIComponent(device)}/frame?${query.toString()}`;
  },

  async fetchCameraFrame(
    device: string,
    resolution: string,
    signal?: AbortSignal,
    session?: string,
  ): Promise<CameraFrameResult> {
    const response = await fetch(api.cameraFrameUrl(device, resolution, session), {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
    if (!response.ok) {
      let detail = `摄像头请求失败（${response.status}）`;
      try {
        const body = (await response.json()) as Record<string, unknown>;
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        // Keep the status fallback when the board returned a non-JSON error.
      }
      throw new ApiError(detail, response.status);
    }
    const blob = await response.blob();
    return {
      url: URL.createObjectURL(blob),
      captureResolution: response.headers.get("X-Capture-Resolution") || undefined,
      previewResolution: response.headers.get("X-Preview-Resolution") || undefined,
    };
  },

  async recognize(file: File, config: RecognitionConfig): Promise<RecognitionResult> {
    const form = new FormData();
    form.append("image", file, file.name || "palm-image.png");
    form.append("model_id", config.modelId);
    form.append("backend", config.backend);
    form.append("precision", config.precision);
    form.append("threshold", String(config.threshold));
    form.append("assume_roi", String(config.assumeRoi));
    return normalizeRecognition(await request<unknown>("/api/recognitions", { method: "POST", body: form }));
  },

  async recognizeCamera(device: string, resolution: string, config: RecognitionConfig): Promise<RecognitionResult> {
    return normalizeRecognition(
      await json<unknown>(`/api/cameras/${encodeURIComponent(device)}/recognitions`, "POST", {
        model_id: config.modelId,
        backend: config.backend,
        precision: config.precision,
        threshold: config.threshold,
        // V4L2 supplies a full frame.  The server repeats this guard for
        // direct or cached clients, but send the correct contract as well.
        assume_roi: false,
        resolution,
      }),
    );
  },

  async openCamera(device: string, resolution: string, session: string): Promise<void> {
    const query = new URLSearchParams({ resolution, session });
    await json<unknown>(`/api/cameras/${encodeURIComponent(device)}/open?${query.toString()}`, "POST");
  },

  async closeCamera(device: string, resolution?: string, session?: string): Promise<void> {
    const query = new URLSearchParams();
    if (resolution) query.set("resolution", resolution);
    if (session) query.set("session", session);
    const suffix = query.size ? `?${query.toString()}` : "";
    await json<unknown>(`/api/cameras/${encodeURIComponent(device)}/close${suffix}`, "POST");
  },

  async createEnrollmentSession(
    config: RecognitionConfig,
    identity: { continueSessionId?: string; displayName?: string; palmSide: string; mode: "self_service" | "assisted" },
  ): Promise<EnrollmentSession> {
    return normalizeSession(
      await json<unknown>("/api/enrollment-sessions", "POST", {
        model_id: config.modelId,
        backend: config.backend,
        precision: config.precision,
        // Automatic enrollment always starts from full camera frames.
        assume_roi: false,
        continue_session_id: identity.continueSessionId,
        display_name: identity.displayName || undefined,
        palm_side: identity.palmSide,
        mode: identity.mode,
      }),
    );
  },

  async roiGuide(device: string, resolution: string): Promise<{center_x: number; center_y: number; side_ratio: number}> {
    const item = payload(await json<unknown>(`/api/cameras/${encodeURIComponent(device)}/roi-guide?resolution=${encodeURIComponent(resolution)}`));
    return asRecord(item.config) as {center_x: number; center_y: number; side_ratio: number};
  },

  async saveRoiGuide(device: string, resolution: string, config: {center_x: number; center_y: number; side_ratio: number}): Promise<void> {
    await json<unknown>(`/api/cameras/${encodeURIComponent(device)}/roi-guide?resolution=${encodeURIComponent(resolution)}`, "PUT", config);
  },

  async enrollmentSession(sessionId: string): Promise<EnrollmentSession> {
    return normalizeSession(await json<unknown>(`/api/enrollment-sessions/${encodeURIComponent(sessionId)}`));
  },

  async startEnrollmentAutoCapture(sessionId: string, device: string, resolution: string): Promise<EnrollmentSession> {
    return normalizeSession(
      await json<unknown>(`/api/enrollment-sessions/${encodeURIComponent(sessionId)}/auto-capture`, "POST", {
        device,
        resolution,
      }),
    );
  },

  async discardEnrollmentSample(sessionId: string, sampleId: string): Promise<EnrollmentSession> {
    return normalizeSession(
      await json<unknown>(`/api/enrollment-sessions/${encodeURIComponent(sessionId)}/samples/${encodeURIComponent(sampleId)}`, "DELETE"),
    );
  },

  async addEnrollmentSample(
    sessionId: string,
    options: { file?: File; cameraDevice?: string; resolution?: string; assumeRoi: boolean },
  ): Promise<EnrollmentSession> {
    const form = new FormData();
    if (options.file) form.append("image", options.file, options.file.name || "palm-image.png");
    form.append("source", options.file ? "upload" : "camera");
    // A camera sample must be localized.  The opt-in bypass is only for an
    // administrator-uploaded, pre-cropped ROI image.
    form.append("assume_roi", String(options.file ? options.assumeRoi : false));
    if (options.cameraDevice) form.append("camera_device", options.cameraDevice);
    if (options.resolution) form.append("resolution", options.resolution);
    return normalizeSession(
      await request<unknown>(`/api/enrollment-sessions/${encodeURIComponent(sessionId)}/samples`, {
        method: "POST",
        body: form,
      }),
    );
  },

  async commitEnrollmentSession(
    sessionId: string,
    requestBody: { personId?: string; displayName?: string; palmSide?: string; config: RecognitionConfig },
  ): Promise<EnrollmentSession> {
    return normalizeSession(
      await json<unknown>(`/api/enrollment-sessions/${encodeURIComponent(sessionId)}/commit`, "POST", {
        person_id: requestBody.personId,
        display_name: requestBody.displayName,
        palm_side: requestBody.palmSide,
        model_id: requestBody.config.modelId,
        backend: requestBody.config.backend,
        precision: requestBody.config.precision,
      }),
    );
  },

  deleteEnrollmentSession(sessionId: string): Promise<void> {
    return json<void>(`/api/enrollment-sessions/${encodeURIComponent(sessionId)}`, "DELETE");
  },

  async templates(config: Partial<RecognitionConfig> = {}): Promise<TemplateItem[]> {
    const query = new URLSearchParams();
    if (config.modelId) query.set("model_id", config.modelId);
    if (config.backend) query.set("backend", config.backend);
    if (config.precision) query.set("precision", config.precision);
    const suffix = query.size ? `?${query.toString()}` : "";
    return normalizeTemplates(await json<unknown>(`/api/templates${suffix}`));
  },

  deleteTemplate(templateId: string, config: Partial<RecognitionConfig> = {}): Promise<void> {
    const query = new URLSearchParams();
    if (config.modelId) query.set("model_id", config.modelId);
    if (config.backend) query.set("backend", config.backend);
    if (config.precision) query.set("precision", config.precision);
    const suffix = query.size ? `?${query.toString()}` : "";
    return json<void>(`/api/templates/${encodeURIComponent(templateId)}${suffix}`, "DELETE");
  },

  async startEvaluation(requestBody: EvaluationRequest): Promise<EvaluationTask> {
    return normalizeEvaluation(
      await json<unknown>("/api/evaluations", "POST", {
        dataset_id: requestBody.datasetId,
        spectrum: requestBody.spectrum,
        model_id: requestBody.modelId,
        backend: requestBody.backend,
        precision: requestBody.precision,
        threshold: requestBody.threshold,
        assume_roi: requestBody.assumeRoi,
        max_identities: requestBody.maxIdentities,
      }),
    );
  },

  async evaluation(taskId: string): Promise<EvaluationTask> {
    return normalizeEvaluation(await json<unknown>(`/api/evaluations/${encodeURIComponent(taskId)}`));
  },

  evaluationReportUrl(taskId: string): string {
    return `/api/evaluations/${encodeURIComponent(taskId)}/report`;
  },

  async systemStatus(): Promise<SystemStatus> {
    const item = payload(await json<unknown>("/api/system-status"));
    return {
      summary: asRecord(item.summary) as SystemStatus["summary"],
      models: list(item, "models").map(normalizeSystemItem),
      datasets: list(item, "datasets").map(normalizeSystemItem),
      cameras: list(item, "cameras").map(normalizeSystemItem),
    };
  },

  async candidates(): Promise<CandidateOption[]> {
    const item = payload(await json<unknown>("/api/candidates"));
    return list(item, "items", "candidates").map(normalizeCandidate);
  },

  async candidateRuntimes(): Promise<CandidateRuntimeOption[]> {
    const item = payload(await json<unknown>("/api/candidate-runtimes"));
    return list(item, "items", "candidates").map(normalizeCandidateRuntime);
  },

  async runCandidate(candidateId: string, file: File, referenceFile?: File): Promise<CandidateRunResult> {
    const form = new FormData();
    form.append("candidate_id", candidateId);
    form.append("image", file, file.name || "candidate-image.png");
    if (referenceFile) form.append("reference_image", referenceFile, referenceFile.name || "candidate-reference.png");
    return normalizeCandidateRun(await request<unknown>("/api/candidate-runs", { method: "POST", body: form }));
  },

};
