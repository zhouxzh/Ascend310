const elements = {
  model: document.querySelector("#model"),
  modelInput: document.querySelector("#modelInput"),
  source: document.querySelector("#source"),
  resolution: document.querySelector("#resolution"),
  fps: document.querySelector("#fps"),
  bitrateKbps: document.querySelector("#bitrateKbps"),
  cameraBackend: document.querySelector("#cameraBackend"),
  cameraFourcc: document.querySelector("#cameraFourcc"),
  encoderStatus: document.querySelector("#encoderStatus"),
  conf: document.querySelector("#conf"),
  iou: document.querySelector("#iou"),
  inferEvery: document.querySelector("#inferEvery"),
  start: document.querySelector("#start"),
  stop: document.querySelector("#stop"),
  clearLog: document.querySelector("#clearLog"),
  remoteVideo: document.querySelector("#remoteVideo"),
  videoDimensions: document.querySelector("#videoDimensions"),
  serverStatus: document.querySelector("#serverStatus"),
  runtimeStatus: document.querySelector("#runtimeStatus"),
  pipelineStatus: document.querySelector("#pipelineStatus"),
  peerStatus: document.querySelector("#peerStatus"),
  bitrateStatus: document.querySelector("#bitrateStatus"),
  npuStatus: document.querySelector("#npuStatus"),
  inferStatus: document.querySelector("#inferStatus"),
  detectionStatus: document.querySelector("#detectionStatus"),
  trackFpsStatus: document.querySelector("#trackFpsStatus"),
  fpsOverlay: document.querySelector("#fpsOverlay"),
  fpsStatus: document.querySelector("#fpsStatus"),
  codecStatus: document.querySelector("#codecStatus"),
  logOutput: document.querySelector("#logOutput"),
};

let modelInfo = new Map();
let activeConnection = null;
let activeAttemptId = 0;
let pendingOfferController = null;
let statsTimer = null;
let serverStatsTimer = null;
let lastStats = null;
let lastFpsStats = null;
let lastServerErrorText = "";
let startInProgress = false;
let fpsTrackingId = null;
let fpsTimestamps = [];

function log(message) {
  const timestamp = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  elements.logOutput.textContent += `[${timestamp}] ${message}\n`;
  elements.logOutput.scrollTop = elements.logOutput.scrollHeight;
}

function setText(element, text) {
  element.textContent = text;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function parseResolution(value) {
  const [width, height] = value.split("x").map(Number);
  return { width, height };
}

function positiveInteger(value, name) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} 必须是正整数。`);
  }
  return parsed;
}

function numericRange(value, name, lower, upper) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < lower || parsed > upper) {
    throw new Error(`${name} 必须在 ${lower} 到 ${upper} 之间。`);
  }
  return parsed;
}

function parseBitrateKbps(value) {
  if (!value || !String(value).trim()) {
    return null;
  }
  return positiveInteger(value, "H.264 码率");
}

function updateModelInput() {
  const item = modelInfo.get(elements.model.value);
  elements.modelInput.value = item?.input ?? "-";
}

function setSelectValue(select, value, label = null) {
  const stringValue = String(value);
  const exists = Array.from(select.options).some((option) => option.value === stringValue);
  if (!exists) {
    const option = document.createElement("option");
    option.value = stringValue;
    option.textContent = label ?? stringValue;
    select.append(option);
  }
  select.value = stringValue;
}

async function loadModels() {
  const data = await fetchJson("/models");
  const models = data.models ?? [];
  modelInfo = new Map(models.map((item) => [item.name, item]));
  elements.model.innerHTML = "";

  for (const item of models) {
    const option = document.createElement("option");
    option.value = item.name;
    option.textContent = item.name;
    elements.model.append(option);
  }

  if (models.length === 0) {
    const option = document.createElement("option");
    option.value = "YOLOv10n_gestures.om";
    option.textContent = "YOLOv10n_gestures.om";
    elements.model.append(option);
    log("没有在 models/ 下找到 .om 文件。");
  } else {
    elements.model.value = data.default_model ?? models[0].name;
  }
  updateModelInput();
}

async function checkHealth() {
  const data = await fetchJson("/health");
  setText(elements.serverStatus, data.status === "ok" ? "在线" : "异常");
  setText(elements.runtimeStatus, `${data.runtime_target ?? "unknown"} / ${data.transport ?? "unknown"}`);
  setText(elements.codecStatus, (data.video_codec ?? "h264").toUpperCase());
  elements.encoderStatus.value = data.encoder ?? "-";
  setText(
    elements.pipelineStatus,
    `${data.defaults?.camera_backend ?? "opencv"} / ${data.encoder ?? "unknown"}`
  );
  if (data.default_source) {
    elements.source.value = data.default_source;
  }
  const defaults = data.defaults ?? {};
  if (defaults.width && defaults.height) {
    const resolution = `${defaults.width}x${defaults.height}`;
    setSelectValue(elements.resolution, resolution, `${defaults.width} x ${defaults.height}`);
  }
  if (defaults.fps) {
    setSelectValue(elements.fps, defaults.fps, `${defaults.fps} fps`);
  }
  if (defaults.infer_every_n) {
    elements.inferEvery.value = defaults.infer_every_n;
  }
  if (defaults.conf) {
    elements.conf.value = defaults.conf;
  }
  if (defaults.iou) {
    elements.iou.value = defaults.iou;
  }
  if (defaults.bitrate_kbps) {
    setSelectValue(elements.bitrateKbps, defaults.bitrate_kbps, `${defaults.bitrate_kbps} kbps`);
  }
  if (defaults.camera_backend) {
    setSelectValue(elements.cameraBackend, defaults.camera_backend);
  }
  if (defaults.camera_fourcc) {
    setSelectValue(elements.cameraFourcc, defaults.camera_fourcc);
  }
}

function setControlsBusy(isBusy) {
  elements.start.disabled = isBusy;
  elements.model.disabled = isBusy;
  elements.source.disabled = isBusy;
  elements.resolution.disabled = isBusy;
  elements.fps.disabled = isBusy;
  elements.bitrateKbps.disabled = isBusy;
  elements.cameraBackend.disabled = isBusy;
  elements.cameraFourcc.disabled = isBusy;
  elements.conf.disabled = isBusy;
  elements.iou.disabled = isBusy;
  elements.inferEvery.disabled = isBusy;
}

function resetRemoteVideoSize() {
  elements.videoDimensions.textContent = "-";
}

function updateRemoteVideoSize() {
  const sourceWidth = elements.remoteVideo.videoWidth;
  const sourceHeight = elements.remoteVideo.videoHeight;
  if (!sourceWidth || !sourceHeight) {
    return;
  }
  const displayWidth = Math.round(elements.remoteVideo.getBoundingClientRect().width);
  const displayHeight = Math.round(elements.remoteVideo.getBoundingClientRect().height);
  elements.videoDimensions.textContent = `${sourceWidth}x${sourceHeight} / ${displayWidth}x${displayHeight}`;
}

function setFpsDisplay(fps) {
  const text = `${fps} fps`;
  elements.fpsOverlay.textContent = text;
  elements.fpsStatus.textContent = text;
}

function resetFpsDisplay() {
  elements.fpsOverlay.textContent = "- fps";
  elements.fpsStatus.textContent = "-";
}

function formatNumber(value, digits = 1) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "-";
  }
  return number.toFixed(digits);
}

function rvfcCallback() {
  if (fpsTrackingId === null) return;
  const now = performance.now();
  fpsTimestamps.push(now);
  while (fpsTimestamps.length > 0 && fpsTimestamps[0] <= now - 1000) {
    fpsTimestamps.shift();
  }
  setFpsDisplay(fpsTimestamps.length);
  fpsTrackingId = elements.remoteVideo.requestVideoFrameCallback(rvfcCallback);
}

function startFpsTracking() {
  stopFpsTracking();
  fpsTimestamps = [];
  lastFpsStats = null;
  if (typeof elements.remoteVideo.requestVideoFrameCallback === "function") {
    fpsTrackingId = elements.remoteVideo.requestVideoFrameCallback(rvfcCallback);
  }
}

function stopFpsTracking() {
  if (fpsTrackingId !== null) {
    try {
      elements.remoteVideo.cancelVideoFrameCallback(fpsTrackingId);
    } catch (_) {}
    fpsTrackingId = null;
  }
  fpsTimestamps = [];
  resetFpsDisplay();
}

function closeConnection(connection) {
  try {
    connection.getReceivers().forEach((receiver) => {
      if (receiver.track) {
        receiver.track.stop();
      }
    });
    connection.close();
  } catch (error) {
    log(`关闭连接警告: ${error.message}`);
  }
}

function stopStats() {
  if (statsTimer) {
    clearInterval(statsTimer);
    statsTimer = null;
  }
  if (serverStatsTimer) {
    clearInterval(serverStatsTimer);
    serverStatsTimer = null;
  }
  lastStats = null;
  lastFpsStats = null;
  lastServerErrorText = "";
  setText(elements.bitrateStatus, "-");
  setText(elements.npuStatus, "-");
  setText(elements.inferStatus, "-");
  setText(elements.detectionStatus, "-");
  setText(elements.trackFpsStatus, "-");
}

function cancelPendingOffer() {
  if (pendingOfferController) {
    pendingOfferController.abort();
    pendingOfferController = null;
  }
}

function teardownActiveConnection() {
  cancelPendingOffer();
  stopStats();
  stopFpsTracking();
  if (activeConnection) {
    closeConnection(activeConnection);
    activeConnection = null;
  }
  elements.remoteVideo.srcObject = null;
  resetRemoteVideoSize();
  setText(elements.peerStatus, "未建立");
}

function stopConnection({ logMessage = true } = {}) {
  const hadActiveWork = Boolean(activeConnection || pendingOfferController || startInProgress);
  activeAttemptId += 1;
  startInProgress = false;
  setControlsBusy(false);
  teardownActiveConnection();
  if (logMessage && hadActiveWork) {
    log("连接已关闭。");
  }
}

function isActiveAttempt(connection, attemptId) {
  return activeConnection === connection && activeAttemptId === attemptId;
}

function assertActiveAttempt(connection, attemptId) {
  if (!isActiveAttempt(connection, attemptId)) {
    const error = new Error("Connection attempt was superseded.");
    error.name = "AbortError";
    throw error;
  }
}

async function readInboundStats(connection) {
  if (activeConnection !== connection) {
    return;
  }

  const stats = await connection.getStats();
  for (const report of stats.values()) {
    if (report.type !== "inbound-rtp" || report.kind !== "video") {
      continue;
    }

    if (!lastStats) {
      lastStats = {
        bytesReceived: report.bytesReceived,
        timestamp: report.timestamp,
      };
      lastFpsStats = {
        framesDecoded: report.framesDecoded ?? report.framesReceived ?? 0,
        timestamp: report.timestamp,
      };
      return;
    }

    const bytesDelta = report.bytesReceived - lastStats.bytesReceived;
    const timeDeltaMs = report.timestamp - lastStats.timestamp;
    if (timeDeltaMs > 0) {
      const bitrateKbps = ((bytesDelta * 8) / timeDeltaMs).toFixed(1);
      setText(elements.bitrateStatus, `${bitrateKbps} kbps`);
    }

    const framesDecoded = report.framesDecoded ?? report.framesReceived ?? 0;
    const framesDelta = framesDecoded - lastFpsStats.framesDecoded;
    const fpsTimeDeltaMs = report.timestamp - lastFpsStats.timestamp;
    if (fpsTimeDeltaMs > 0 && framesDelta > 0) {
      setFpsDisplay(Math.round((framesDelta * 1000) / fpsTimeDeltaMs));
    }

    lastStats = {
      bytesReceived: report.bytesReceived,
      timestamp: report.timestamp,
    };
    lastFpsStats = {
      framesDecoded,
      timestamp: report.timestamp,
    };
    return;
  }
}

function startStats(connection) {
  stopStats();
  statsTimer = window.setInterval(() => {
    readInboundStats(connection).catch((error) => log(`Stats failed: ${error.message}`));
  }, 1000);
  serverStatsTimer = window.setInterval(() => {
    readServerStats().catch((error) => log(`Server stats failed: ${error.message}`));
  }, 500);
  readServerStats().catch((error) => log(`Server stats failed: ${error.message}`));
}

async function readServerStats() {
  if (!activeConnection) {
    return;
  }
  const data = await fetchJson("/stats");
  if (!data || Object.keys(data).length === 0) {
    return;
  }
  const serverErrors = [
    data.capture_error ? `capture: ${data.capture_error}` : "",
    data.render_error ? `render: ${data.render_error}` : "",
    data.infer_error ? `infer: ${data.infer_error}` : "",
  ].filter(Boolean).join(" · ");
  if (serverErrors) {
    if (serverErrors !== lastServerErrorText) {
      log(`服务端错误: ${serverErrors}`);
      lastServerErrorText = serverErrors;
    }
    setText(elements.pipelineStatus, serverErrors);
  } else {
    lastServerErrorText = "";
  }
  if (Number.isFinite(Number(data.npu_latency_ms))) {
    setText(elements.npuStatus, `${Number(data.npu_latency_ms).toFixed(1)} ms`);
  }
  if (Number.isFinite(Number(data.track_fps))) {
    setText(elements.trackFpsStatus, `${Number(data.track_fps).toFixed(1)} fps`);
  }
  const npuMs = formatNumber(data.npu_latency_ms);
  const totalMs = formatNumber(data.infer_total_ms);
  const inferFps = formatNumber(data.infer_fps);
  setText(
    elements.inferStatus,
    `1/${data.infer_every_n ?? "?"} · NPU ${npuMs} ms · total ${totalMs} ms · ${inferFps} fps`
  );
  setText(elements.detectionStatus, `${data.detections ?? 0}`);
  const captureFps = formatNumber(data.capture_fps);
  const captureMs = formatNumber(data.capture_ms);
  const pipelineMs = formatNumber(data.pipeline_ms);
  const nv12Ms = formatNumber(data.nv12_ms);
  const fourcc = data.actual_fourcc || data.camera_fourcc || "?";
  if (!serverErrors) {
    setText(
      elements.pipelineStatus,
      `${data.camera_backend ?? "?"}/${fourcc} · cap ${captureFps} fps/${captureMs} ms · nv12 ${nv12Ms} ms · send ${pipelineMs} ms · ${data.encoder ?? elements.encoderStatus.value}`
    );
  }
}

function bindConnectionEvents(connection, attemptId) {
  connection.ontrack = (event) => {
    if (!isActiveAttempt(connection, attemptId)) {
      return;
    }
    elements.remoteVideo.srcObject = event.streams[0];
    startFpsTracking();
    log(`收到远端视频轨: ${event.track.id}`);
  };

  connection.onconnectionstatechange = () => {
    if (!isActiveAttempt(connection, attemptId)) {
      return;
    }
    setText(elements.peerStatus, connection.connectionState);
    log(`PeerConnection: ${connection.connectionState}`);
  };

  connection.oniceconnectionstatechange = () => {
    if (!isActiveAttempt(connection, attemptId)) {
      return;
    }
    log(`ICE: ${connection.iceConnectionState}`);
  };
}

function bindVideoEvents() {
  elements.remoteVideo.addEventListener("loadedmetadata", () => {
    updateRemoteVideoSize();
    log(`视频尺寸: ${elements.remoteVideo.videoWidth}x${elements.remoteVideo.videoHeight}`);
  });
  elements.remoteVideo.addEventListener("resize", updateRemoteVideoSize);
  elements.remoteVideo.addEventListener("error", () => {
    const error = elements.remoteVideo.error;
    log(`Video error: code=${error?.code ?? "unknown"} message=${error?.message ?? ""}`);
  });
}

function getReceiverCodecs(mimeType) {
  const capabilities = RTCRtpReceiver.getCapabilities?.("video");
  return (capabilities?.codecs ?? []).filter(
    (codec) => codec.mimeType.toLowerCase() === mimeType.toLowerCase()
  );
}

function applyH264Preference(transceiver) {
  const h264Codecs = getReceiverCodecs("video/H264");
  if (!h264Codecs.length) {
    throw new Error("当前浏览器没有 video/H264 WebRTC 接收能力。");
  }
  transceiver.setCodecPreferences(h264Codecs);
}

function logAppliedSourceSettings(sourceSettings) {
  if (!sourceSettings?.applied) {
    return;
  }
  const applied = sourceSettings.applied;
  const bitrate = applied.bitrate_kbps ? `${applied.bitrate_kbps} kbps` : "auto";
  log(
    `服务端: model=${sourceSettings.model ?? "unknown"} source=${sourceSettings.source ?? "unknown"} capture=${applied.width ?? "?"}x${applied.height ?? "?"}@${applied.fps ?? "?"} input=${sourceSettings.model_input ?? "?"} bitrate=${bitrate} backend=${applied.camera_backend ?? "?"} infer=1/${applied.infer_every_n ?? "?"} fourcc=${applied.actual_fourcc || applied.camera_fourcc || "?"}`
  );
  setText(
    elements.pipelineStatus,
    `${applied.camera_backend ?? "?"}/${applied.actual_fourcc || applied.camera_fourcc || "?"} / ${sourceSettings.encoder ?? elements.encoderStatus.value}`
  );
}

async function startConnection() {
  if (startInProgress) {
    log("连接正在建立。");
    return;
  }

  startInProgress = true;
  setControlsBusy(true);
  const attemptId = activeAttemptId + 1;
  activeAttemptId = attemptId;
  teardownActiveConnection();

  const { width, height } = parseResolution(elements.resolution.value);
  const fps = positiveInteger(elements.fps.value, "采集帧率");
  const bitrateKbps = parseBitrateKbps(elements.bitrateKbps.value);
  const inferEvery = positiveInteger(elements.inferEvery.value, "推理间隔");
  const conf = numericRange(elements.conf.value, "Conf", 0.01, 0.99);
  const iou = numericRange(elements.iou.value, "IoU", 0.01, 0.99);

  let connection = null;
  try {
    connection = new RTCPeerConnection();
    activeConnection = connection;
    const transceiver = connection.addTransceiver("video", { direction: "recvonly" });
    applyH264Preference(transceiver);
    bindConnectionEvents(connection, attemptId);

    const offer = await connection.createOffer();
    assertActiveAttempt(connection, attemptId);
    await connection.setLocalDescription(offer);
    assertActiveAttempt(connection, attemptId);

    pendingOfferController = new AbortController();
    const answer = await fetchJson("/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: pendingOfferController.signal,
      body: JSON.stringify({
        sdp: connection.localDescription.sdp,
        type: connection.localDescription.type,
        model: elements.model.value,
        source: elements.source.value,
        width,
        height,
        fps,
        bitrate_kbps: bitrateKbps,
        camera_backend: elements.cameraBackend.value,
        camera_fourcc: elements.cameraFourcc.value,
        infer_every_n: inferEvery,
        conf,
        iou,
      }),
    });
    pendingOfferController = null;
    assertActiveAttempt(connection, attemptId);

    await connection.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
    assertActiveAttempt(connection, attemptId);

    startStats(connection);
    log(`WebRTC offer 已发送: ${width}x${height}@${fps} H.264 bitrate=${bitrateKbps ?? "auto"}`);
    logAppliedSourceSettings(answer.source_settings);
  } catch (error) {
    if (pendingOfferController && pendingOfferController.signal.aborted) {
      pendingOfferController = null;
    }
    if (error.name === "AbortError") {
      if (connection) closeConnection(connection);
      return;
    }
    if (connection && isActiveAttempt(connection, attemptId)) {
      teardownActiveConnection();
      log(`启动失败: ${error.message}`);
    } else if (connection) {
      closeConnection(connection);
    } else {
      teardownActiveConnection();
      log(`启动失败: ${error.message}`);
    }
  } finally {
    if (activeAttemptId === attemptId) {
      startInProgress = false;
      setControlsBusy(false);
    }
  }
}

function bindEvents() {
  elements.model.addEventListener("change", updateModelInput);
  elements.start.addEventListener("click", () => {
    startConnection().catch((error) => log(`启动失败: ${error.message}`));
  });
  elements.stop.addEventListener("click", () => stopConnection());
  elements.clearLog.addEventListener("click", () => {
    elements.logOutput.textContent = "";
  });
  bindVideoEvents();
  window.addEventListener("beforeunload", () => {
    stopConnection({ logMessage: false });
  });
}

async function init() {
  bindEvents();
  resetRemoteVideoSize();
  resetFpsDisplay();
  setText(elements.peerStatus, "未建立");
  setText(elements.bitrateStatus, "-");
  setText(elements.npuStatus, "-");
  setText(elements.inferStatus, "-");
  setText(elements.detectionStatus, "-");
  setText(elements.trackFpsStatus, "-");
  await Promise.all([loadModels(), checkHealth()]);
  log("页面就绪。");
}

init().catch((error) => {
  setText(elements.serverStatus, "不可用");
  log(`初始化失败: ${error.message}`);
});
