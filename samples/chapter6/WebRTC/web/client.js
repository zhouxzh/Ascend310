const elements = {
  resolution: document.querySelector("#resolution"),
  fps: document.querySelector("#fps"),
  bitrateKbps: document.querySelector("#bitrateKbps"),
  start: document.querySelector("#start"),
  stop: document.querySelector("#stop"),
  clearLog: document.querySelector("#clearLog"),
  remoteVideo: document.querySelector("#remoteVideo"),
  videoDimensions: document.querySelector("#videoDimensions"),
  serverStatus: document.querySelector("#serverStatus"),
  runtimeStatus: document.querySelector("#runtimeStatus"),
  peerStatus: document.querySelector("#peerStatus"),
  bitrateStatus: document.querySelector("#bitrateStatus"),
  fpsOverlay: document.querySelector("#fpsOverlay"),
  fpsStatus: document.querySelector("#fpsStatus"),
  logOutput: document.querySelector("#logOutput"),
};

let activeConnection = null;
let activeAttemptId = 0;
let pendingOfferController = null;
let statsTimer = null;
let lastStats = null;
let startInProgress = false;
let fpsTrackingId = null;
let fpsTimestamps = [];
let lastFpsStats = null;
let lastDecodeStats = null;
let rvfcSupported = false;
let serverVideoCodec = "h264";

function log(message) {
  const timestamp = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  elements.logOutput.textContent += `[${timestamp}] ${message}\n`;
  elements.logOutput.scrollTop = elements.logOutput.scrollHeight;
}

function setServerStatus(text) {
  elements.serverStatus.textContent = text;
}

function setPeerStatus(text) {
  elements.peerStatus.textContent = text;
}

function setRuntimeStatus(text) {
  elements.runtimeStatus.textContent = text;
}

function setBitrateStatus(text) {
  elements.bitrateStatus.textContent = text;
}

function resetRemoteVideoSize() {
  elements.remoteVideo.style.width = "";
  elements.remoteVideo.style.height = "";
  elements.videoDimensions.textContent = "Source: - | Display: -";
}

function updateRemoteVideoSize() {
  const sourceWidth = elements.remoteVideo.videoWidth;
  const sourceHeight = elements.remoteVideo.videoHeight;

  if (!sourceWidth || !sourceHeight) {
    return;
  }

  const displayWidth = Math.round(elements.remoteVideo.getBoundingClientRect().width);
  const displayHeight = Math.round(elements.remoteVideo.getBoundingClientRect().height);
  elements.videoDimensions.textContent =
    `Source: ${sourceWidth}x${sourceHeight} | Display: ${displayWidth}x${displayHeight}`;
}

function setControlsBusy(isBusy) {
  elements.start.disabled = isBusy;
  elements.resolution.disabled = isBusy;
  elements.fps.disabled = isBusy;
  elements.bitrateKbps.disabled = isBusy;
}

function parseResolution(value) {
  const [width, height] = value.split("x").map(Number);
  return { width, height };
}

function parseBitrateKbps(value) {
  if (!value || !String(value).trim()) {
    return null;
  }
  const bitrateKbps = Number(value);
  if (!Number.isFinite(bitrateKbps) || bitrateKbps <= 0) {
    throw new Error("目标码率必须是正整数。");
  }
  return bitrateKbps;
}

function makeAbortError(message) {
  const error = new Error(message);
  error.name = "AbortError";
  return error;
}

function isActiveAttempt(connection, attemptId) {
  return activeConnection === connection && activeAttemptId === attemptId;
}

function assertActiveAttempt(connection, attemptId) {
  if (!isActiveAttempt(connection, attemptId)) {
    throw makeAbortError("Connection attempt was superseded by a newer action.");
  }
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function checkHealth() {
  try {
    const data = await fetchJson("/health");
    serverVideoCodec = (data.video_codec ?? "h264").toLowerCase();
    setServerStatus(data.status === "ok" ? "在线" : "异常");
    setRuntimeStatus(
      `${data.runtime_target ?? "unknown"} / ${data.default_source ?? "unknown"} / ${serverVideoCodec}`
    );
  } catch (error) {
    setServerStatus("不可用");
    setRuntimeStatus("未知");
    log(`Health check failed: ${error.message}`);
  }
}

function stopStats() {
  if (statsTimer) {
    clearInterval(statsTimer);
    statsTimer = null;
  }
  lastStats = null;
  lastFpsStats = null;
  lastDecodeStats = null;
  setBitrateStatus("-");
}

function setFpsDisplay(fps) {
  const text = `${fps} fps`;
  if (elements.fpsOverlay) elements.fpsOverlay.textContent = text;
  if (elements.fpsStatus) elements.fpsStatus.textContent = text;
}

function resetFpsDisplay() {
  if (elements.fpsOverlay) elements.fpsOverlay.textContent = "- fps";
  if (elements.fpsStatus) elements.fpsStatus.textContent = "-";
}

function rvfcCallback() {
  if (fpsTrackingId === null) return;
  fpsTimestamps.push(performance.now());
  while (fpsTimestamps.length > 0 && fpsTimestamps[0] <= performance.now() - 1000) {
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
    rvfcSupported = true;
    fpsTrackingId = elements.remoteVideo.requestVideoFrameCallback(rvfcCallback);
  } else {
    rvfcSupported = false;
    log("requestVideoFrameCallback not available, using stats-based FPS");
  }
}

function stopFpsTracking() {
  if (fpsTrackingId !== null) {
    try { elements.remoteVideo.cancelVideoFrameCallback(fpsTrackingId); } catch (_) {}
    fpsTrackingId = null;
  }
  fpsTimestamps = [];
  lastFpsStats = null;
  rvfcSupported = false;
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
    log(`Close connection warning: ${error.message}`);
  }
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
  setPeerStatus("未建立");
}

function stopConnection({ logMessage = true } = {}) {
  const hadActiveWork = Boolean(activeConnection || pendingOfferController || startInProgress);
  activeAttemptId += 1;
  startInProgress = false;
  setControlsBusy(false);
  teardownActiveConnection();

  if (logMessage && hadActiveWork) {
    log("浏览器侧连接已关闭。");
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
      setBitrateStatus(`${bitrateKbps} kbps`);
    }

    const framesDecoded = report.framesDecoded ?? report.framesReceived ?? 0;
    const framesDelta = framesDecoded - lastFpsStats.framesDecoded;
    const fpsTimeDeltaMs = report.timestamp - lastFpsStats.timestamp;
    if (fpsTimeDeltaMs > 0 && framesDelta > 0) {
      const fps = Math.round((framesDelta * 1000) / fpsTimeDeltaMs);
      setFpsDisplay(fps);
    }

    const decodeStats = {
      framesReceived: report.framesReceived ?? 0,
      framesDecoded: report.framesDecoded ?? 0,
      keyFramesDecoded: report.keyFramesDecoded ?? 0,
      framesDropped: report.framesDropped ?? 0,
      totalDecodeTime: report.totalDecodeTime ?? 0,
      decoderImplementation: report.decoderImplementation ?? "unknown",
    };
    if (
      !lastDecodeStats ||
      decodeStats.framesReceived !== lastDecodeStats.framesReceived ||
      decodeStats.framesDecoded !== lastDecodeStats.framesDecoded ||
      decodeStats.keyFramesDecoded !== lastDecodeStats.keyFramesDecoded
    ) {
      log(
        `Inbound stats received=${decodeStats.framesReceived} decoded=${decodeStats.framesDecoded} key=${decodeStats.keyFramesDecoded} dropped=${decodeStats.framesDropped} decoder=${decodeStats.decoderImplementation}`
      );
      lastDecodeStats = decodeStats;
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
    readInboundStats(connection).catch((error) => log(`Read stats failed: ${error.message}`));
  }, 1000);
}

function bindConnectionEvents(connection, attemptId) {
  connection.ontrack = (event) => {
    if (!isActiveAttempt(connection, attemptId)) {
      return;
    }
    elements.remoteVideo.srcObject = event.streams[0];
    startFpsTracking();
    log(`Received remote video track: ${event.track.id}`);
  };

  connection.onconnectionstatechange = () => {
    if (!isActiveAttempt(connection, attemptId)) {
      return;
    }
    setPeerStatus(connection.connectionState);
    log(`PeerConnection state: ${connection.connectionState}`);
  };

  connection.oniceconnectionstatechange = () => {
    if (!isActiveAttempt(connection, attemptId)) {
      return;
    }
    log(`ICE state: ${connection.iceConnectionState}`);
  };
}

function bindVideoEvents() {
  elements.remoteVideo.addEventListener("loadedmetadata", () => {
    log(
      `Video metadata loaded: ${elements.remoteVideo.videoWidth}x${elements.remoteVideo.videoHeight}`
    );
    updateRemoteVideoSize();
  });

  elements.remoteVideo.addEventListener("playing", () => {
    log("Video element playing.");
  });

  elements.remoteVideo.addEventListener("waiting", () => {
    log("Video element waiting for decoded frames.");
  });

  elements.remoteVideo.addEventListener("error", () => {
    const error = elements.remoteVideo.error;
    log(`Video element error: code=${error?.code ?? "unknown"} message=${error?.message ?? ""}`);
  });

  elements.remoteVideo.addEventListener("resize", () => {
    log(
      `Video element resize: ${elements.remoteVideo.videoWidth}x${elements.remoteVideo.videoHeight}`
    );
    updateRemoteVideoSize();
  });
}

function getReceiverCodecs(mimeType) {
  const capabilities = RTCRtpReceiver.getCapabilities?.("video");
  return (capabilities?.codecs ?? []).filter(
    (codec) => codec.mimeType.toLowerCase() === mimeType.toLowerCase()
  );
}

function applyVideoCodecPreference(transceiver) {
  if (serverVideoCodec !== "h265") {
    return;
  }

  const h265Codecs = getReceiverCodecs("video/H265");
  if (!h265Codecs.length) {
    throw new Error(
      "当前浏览器没有暴露 video/H265 WebRTC 接收能力，无法启动 H.265 模式。"
    );
  }
  transceiver.setCodecPreferences(h265Codecs);
  log("Browser offer codec preference set to video/H265.");
}

function logAppliedSourceSettings(sourceSettings) {
  if (!sourceSettings?.applied) {
    return;
  }

  const requested = sourceSettings.requested ?? {};
  const applied = sourceSettings.applied;
  const bitrateMode = applied.bitrate_mode ?? "auto";
  const bitrate = applied.bitrate_kbps
    ? `${applied.bitrate_kbps}kbps/${bitrateMode}`
    : "unknown";
  log(
    `Server source=${sourceSettings.source ?? "unknown"} requested=${requested.width ?? "?"}x${requested.height ?? "?"}@${requested.fps ?? "?"} actual=${applied.width ?? "?"}x${applied.height ?? "?"}@${applied.fps ?? "?"} bitrate=${bitrate} mode=${applied.mode ?? "unknown"}`
  );
}

async function startConnection() {
  if (startInProgress) {
    log("A connection attempt is already in progress.");
    return;
  }

  startInProgress = true;
  setControlsBusy(true);

  const attemptId = activeAttemptId + 1;
  activeAttemptId = attemptId;
  teardownActiveConnection();

  const { width, height } = parseResolution(elements.resolution.value);
  const fps = Number(elements.fps.value);
  const bitrateKbps = parseBitrateKbps(elements.bitrateKbps.value);

  let connection = null;
  try {
    connection = new RTCPeerConnection();
    activeConnection = connection;
    const transceiver = connection.addTransceiver("video", { direction: "recvonly" });
    applyVideoCodecPreference(transceiver);
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
        width,
        height,
        fps,
        bitrate_kbps: bitrateKbps,
      }),
    });
    pendingOfferController = null;

    assertActiveAttempt(connection, attemptId);

    if (connection.signalingState !== "have-local-offer") {
      throw new Error(
        `Unexpected signaling state before answer: ${connection.signalingState}`
      );
    }

    await connection.setRemoteDescription({
      type: answer.type,
      sdp: answer.sdp,
    });
    assertActiveAttempt(connection, attemptId);

    startStats(connection);
    log(
      `Sent WebRTC offer to Python sender: requested=${width}x${height}@${fps}fps bitrate=${bitrateKbps ?? "auto"}`
    );
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
      log(`Start failed: ${error.message}`);
    } else if (connection) {
      closeConnection(connection);
    } else {
      teardownActiveConnection();
      log(`Start failed: ${error.message}`);
    }
  } finally {
    if (activeAttemptId === attemptId) {
      startInProgress = false;
      setControlsBusy(false);
    }
  }
}

function bindEvents() {
  elements.start.addEventListener("click", () => {
    startConnection().catch((error) => log(`Start failed: ${error.message}`));
  });

  elements.stop.addEventListener("click", () => {
    stopConnection();
  });

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
  setPeerStatus("未建立");
  setBitrateStatus("-");
  setRuntimeStatus("检查中");
  resetRemoteVideoSize();
  log("页面初始化完成。");
  await checkHealth();
}

init().catch((error) => {
  log(`初始化失败: ${error.message}`);
});
