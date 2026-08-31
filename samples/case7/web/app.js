const state = { config: null, current: null, displayEnabled: true, panel: null, idleTimer: null, weatherTimer: null, displayTimer: null, feedbackTimer: null, weather: null, viewportKey: null, viewportTimer: null, imageUrl: null, uploadFiles: [], uploadIgnored: 0, uploading: false, currentLoading: false, galleryLoading: false, galleryRefreshQueued: false, lastPairing: null, devicePullWatch: null };
const supportedPhotoFrameProfiles = new Set(['waveshare_photopainter_73', 'seeedstudio_reterminal_e1002']);
const json = async (url, options = {}) => { const response = await fetch(url, {...options, cache: 'no-store'}); if (!response.ok) { const payload = await response.json().catch(() => ({})); const detail = payload.detail; const message = typeof detail === 'string' ? detail : (detail && typeof detail === 'object' ? (detail.message || JSON.stringify(detail)) : `${response.status}`); const error = new Error(message); error.status = response.status; error.payload = payload; throw error; } const type = response.headers.get('content-type') || ''; return type.includes('application/json') ? response.json() : response; };
const formPatch = form => { const value = {}; for (const [key, field] of new FormData(form)) { const parts = key.split('.'); let target = value; while (parts.length > 1) target = target[parts.shift()] ||= {}; target[parts[0]] = field === '' ? '' : (field.match(/^-?\d+(\.\d+)?$/) ? Number(field) : field); } return value; };
const merge = (target, patch) => { Object.entries(patch).forEach(([key, value]) => { target[key] = value && typeof value === 'object' && !Array.isArray(value) ? merge(target[key] || {}, value) : value; }); return target; };
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

// Do not let a browser restore a stale manual model choice from a previous
// visit.  Users can still choose a model explicitly after opening the panel.
const searchModel = document.querySelector('#search-model');
if (searchModel) searchModel.value = 'auto';

function armIdle() { clearTimeout(state.idleTimer); document.body.classList.remove('hero-idle'); state.idleTimer = setTimeout(() => { if (!state.panel) document.body.classList.add('hero-idle'); }, 8000); }
function showFeedback(message, success = false) {
  const node = document.querySelector('#hero-error');
  clearTimeout(state.feedbackTimer);
  node.textContent = message;
  node.classList.toggle('is-success', Boolean(success));
  node.hidden = !message;
  if (message) state.feedbackTimer = setTimeout(() => { node.hidden = true; }, 5000);
}
function showError(message) { showFeedback(message, false); }
function showNotice(message) { showFeedback(message, true); }
function setPanel(name) {
  state.panel = name;
  document.querySelector('#panel-layer').hidden = false;
  document.querySelectorAll('.panel-view').forEach(node => node.classList.toggle('active', node.id === `${name}-panel`));
  document.querySelectorAll('.nav-button').forEach(node => node.classList.toggle('active', node.dataset.panel === name));
  document.querySelector('#panel-title').textContent = {gallery:'图库', search:'智能搜索', upload:'上传照片', devices:'设备管理', settings:'服务器设置'}[name] || '相册控制台';
  if (name === 'gallery') loadGallery();
  if (name === 'devices') loadDevices();
  if (name === 'settings') { loadConfig(); loadSystem(); }
  armIdle();
}
function closePanel() { state.panel = null; document.querySelector('#panel-layer').hidden = true; document.querySelectorAll('.nav-button').forEach(node => node.classList.toggle('active', node.dataset.panel === 'gallery')); armIdle(); }
function renderFilenameWatermark() {
  const watermark = document.querySelector('#filename-watermark');
  const filename = state.current?.filename;
  watermark.hidden = !filename || state.config?.display?.show_filename === false;
  document.querySelector('#filename-watermark-text').textContent = filename || '';
}
function renderSelectionSource(current) {
  const label = document.querySelector('#selection-label');
  if (!label) return;
  const source = String(current?.selection_source || '');
  if (source.startsWith('npu_semantic')) {
    label.textContent = source === 'npu_semantic_sync' ? 'NPU 刚完成排序' : 'NPU 语义预选';
    label.title = '当前照片由 Chinese-CLIP 与 FAISS 语义排序产生';
  } else if (source.includes('metadata')) {
    label.textContent = '元数据回退';
    label.title = '当前没有可用的语义候选，使用了受控回退';
  } else {
    label.textContent = 'NPU 排序准备中';
    label.title = '正在等待语义候选计划';
  }
}

function displayViewport() {
  const bounds = document.querySelector('#hero').getBoundingClientRect();
  const width = Math.max(1, Math.round(bounds.width || window.innerWidth));
  const height = Math.max(1, Math.round(bounds.height || window.innerHeight));
  // A square viewport has no public landscape/portrait contract. The server
  // infers it from dimensions, so omit the orientation query in that case.
  const orientation = width === height ? null : (width > height ? 'landscape' : 'portrait');
  return {width, height, orientation, key: `${width}x${height}`};
}

function displayCurrentUrl(viewport) {
  const query = new URLSearchParams({
    profile: 'jpeg',
    width: String(viewport.width),
    height: String(viewport.height),
  });
  if (viewport.orientation) query.set('orientation', viewport.orientation);
  return `/api/display/current?${query}`;
}

function displayContentUrl(url) {
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}_=${Date.now()}`;
}

function armDisplayRefresh() {
  clearInterval(state.displayTimer);
  const touchscreenMode = new URLSearchParams(location.search).get('mode') === 'touchscreen';
  const configured = Number(state.config?.display?.[
    touchscreenMode ? 'touchscreen_interval_seconds' : 'remote_refresh_seconds'
  ]);
  const seconds = Number.isFinite(configured) ? Math.max(5, configured) : 30;
  // The metadata endpoint is cheap and carries an ETag.  ``loadCurrent``
  // skips the JPEG request when the selection has not changed, so a fast
  // browser/touchscreen refresh does not cause repeated image encoding.
  state.displayTimer = setInterval(() => {
    if (!state.uploading) loadCurrent(false).catch(error => showError(error.message));
  }, seconds * 1000);
}

async function loadCurrent(retry = true) {
  if (state.currentLoading) return state.current;
  state.currentLoading = true;
  try {
    return await loadCurrentOnce(retry);
  } finally {
    state.currentLoading = false;
  }
}

async function loadCurrentOnce(retry = true) {
  const viewport = displayViewport();
  const value = await json(displayCurrentUrl(viewport));
  if (!value.current && retry) {
    await new Promise(resolve => setTimeout(resolve, 1200));
    await json('/api/display/refresh', {method:'POST'});
    return loadCurrentOnce(false);
  }
  const previousEtag = state.current?.etag;
  state.current = value.current;
  renderSelectionSource(state.current);
  state.displayEnabled = value.display?.enabled !== false;
  const image = document.querySelector('#current-image');
  const empty = document.querySelector('#hero-empty');
  if (!value.current) { if (state.imageUrl) URL.revokeObjectURL(state.imageUrl), state.imageUrl = null; image.removeAttribute('src'); empty.hidden = false; renderSelectionSource(null); renderFilenameWatermark(); return; }
  empty.hidden = true;
  if (previousEtag && previousEtag === value.current.etag && state.imageUrl && state.viewportKey === viewport.key) {
    // The local scheduler may run faster than this browser poll.  Keep the
    // already decoded image when its representation is unchanged.
    renderFilenameWatermark();
    document.querySelector('#pause-button').querySelector('span:last-child').textContent = state.displayEnabled ? '暂停轮播' : '继续轮播';
    document.querySelector('#pause-button').dataset.displayAction = state.displayEnabled ? 'pause' : 'resume';
    return value.current;
  }
  const response = await fetch(displayContentUrl(value.current.url), {cache: 'no-store'});
  if (!response.ok) throw new Error(`当前照片加载失败 ${response.status}`);
  const imageUrl = URL.createObjectURL(await response.blob());
  if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
  state.imageUrl = imageUrl;
  image.src = imageUrl;
  state.viewportKey = viewport.key;
  renderFilenameWatermark();
  document.querySelector('#pause-button').querySelector('span:last-child').textContent = state.displayEnabled ? '暂停轮播' : '继续轮播';
  document.querySelector('#pause-button').dataset.displayAction = state.displayEnabled ? 'pause' : 'resume';
  return value.current;
}
function weatherPresentation(weather) {
  const code = Number(weather?.weather_code);
  if (weather?.status === 'disabled') return {icon:'○', label:'天气服务已关闭'};
  if (!weather || weather.status !== 'ok' || !Number.isFinite(code)) return {icon:'?', label:'天气未知'};
  if (code === 0) return {icon:'☀', label:'晴天'};
  if ([1, 2, 3].includes(code)) return {icon:'☁', label:'多云'};
  if ([45, 48].includes(code)) return {icon:'≋', label:'雾天'};
  if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82].includes(code)) return {icon:'☂', label:'雨天'};
  if ([71, 73, 75, 77, 85, 86].includes(code)) return {icon:'❄', label:'雪天'};
  return {icon:'⚡', label:'雷雨'};
}
function renderWeather(weather) {
  const presentation = weatherPresentation(weather);
  const temperature = Number(weather?.temperature);
  const wind = Number(weather?.wind_speed);
  document.querySelector('#weather-icon').textContent = presentation.icon;
  document.querySelector('#weather-label').textContent = presentation.label;
  document.querySelector('#weather-detail').textContent = weather?.status === 'ok'
    ? `${Number.isFinite(temperature) ? `${temperature.toFixed(1)}°C` : '--°C'} · 风速 ${Number.isFinite(wind) ? `${wind.toFixed(1)} m/s` : '--'}`
    : '等待天气数据';
  const updated = Number(weather?.updated_at);
  document.querySelector('#weather-source').textContent = Number.isFinite(updated) ? `更新 ${new Intl.DateTimeFormat('zh-CN', {hour:'2-digit', minute:'2-digit'}).format(new Date(updated * 1000))}` : 'Open-Meteo';
}
async function loadWeather() { try { const value = await json('/api/display/status'); state.weather = value.weather; renderWeather(state.weather); } catch { state.weather = {status:'unknown'}; renderWeather(state.weather); } }
async function controlDisplay(action) {
  const buttons = [...document.querySelectorAll(`[data-display-action="${action}"]`)];
  buttons.forEach(button => {
    button.disabled = true;
    const label = button.querySelector('span:last-child');
    if (label) {
      label.dataset.original = label.textContent;
      label.textContent = '切换中…';
    }
  });
  try {
    // The fullscreen controls belong to the built-in HDMI panel. Keep its
    // pause switch independent from the legacy global switch that also
    // controls the physical e-paper output.
    if (action === 'pause' || action === 'resume') {
      await advanceTouchscreen(action);
    } else {
      await json('/api/display/control', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action}),
      });
    }
    await loadCurrent();
    showError('');
  } catch (error) {
    const label = action === 'previous' ? '上一张' : action === 'next' ? '下一张' : '轮播控制';
    showError(`${label}失败：${error.message}`);
  } finally {
    buttons.forEach(button => {
      button.disabled = false;
      const label = button.querySelector('span:last-child');
      if (label && label.dataset.original) label.textContent = label.dataset.original;
    });
  }
  armIdle();
}

function renderPhotos(target, photos) {
  target.replaceChildren(); const template = document.querySelector('#photo-template');
  photos.forEach(photo => {
    const card = template.content.cloneNode(true);
    const button = card.querySelector('.photo-tile');
    const image = card.querySelector('img');
    const scoreNode = card.querySelector('.photo-tile-score');
    const previewUrl = photo.preview_url || photo.url || photo.file_url;
    image.alt = photo.filename || '相册照片';
    image.loading = 'lazy';
    image.decoding = 'async';
    image.fetchPriority = 'low';
    image.onerror = () => {
      image.removeAttribute('src');
      image.alt = '预览暂时不可用';
      button.classList.add('preview-error');
      scoreNode.textContent = '预览失败，可重试';
    };
    if (previewUrl) image.src = previewUrl;
    card.querySelector('.photo-tile-name').textContent = photo.filename || `照片 ${photo.id || photo.photo_id}`;
    scoreNode.textContent = photo.score == null ? '点击设为主屏' : `相关度 ${(Number(photo.score) * 100).toFixed(1)}%`;
    button.onclick = async () => { try { await json('/api/display/select', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({photo_id: photo.id || photo.photo_id})}); closePanel(); await loadCurrent(); } catch (error) { showError(error.message); } };
    target.append(card);
  });
}
async function loadGallery(force = false) {
  if (state.galleryLoading) {
    if (force) state.galleryRefreshQueued = true;
    return;
  }
  state.galleryLoading = true;
  const status = document.querySelector('#gallery-status');
  try {
    const value = await json('/api/photos?limit=500');
    status.textContent = value.photos.length ? `${value.photos.length} 张照片 · 点击任意照片设为主屏` : '图库中还没有可显示的照片';
    renderPhotos(document.querySelector('#gallery-grid'), value.photos);
  } catch (error) {
    status.textContent = error.message;
  } finally {
    state.galleryLoading = false;
    if (state.galleryRefreshQueued) {
      state.galleryRefreshQueued = false;
      void loadGallery();
    }
  }
}
function touchscreenFallback() {
  const config = state.config || {};
  const display = config.display || {};
  return {
    device: {
      device_id: 'local-touchscreen',
      name: '本机触摸屏',
      enabled: display.touchscreen_enabled ?? (display.enabled !== false),
      display: {kind: 'touchscreen', width: 1920, height: 1080},
      last_status: '本机服务',
    },
    config,
    state: {current: state.current, paused: display.touchscreen_enabled === false},
  };
}

async function loadTouchscreen() {
  try {
    const value = await json('/api/admin/touchscreen');
    return value.device ? value : {...touchscreenFallback(), ...value};
  } catch (error) {
    if (error.status && error.status !== 404) throw error;
    // Older releases have no virtual-device endpoint. Keep the settings page
    // useful by projecting the existing global display configuration.
    return touchscreenFallback();
  }
}

async function saveTouchscreen(payload) {
  try {
    return await json('/api/admin/touchscreen', {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...payload, revision: state.config?.revision}),
    });
  } catch (error) {
    if (error.status && error.status !== 404) throw error;
    // Compatibility with a server upgraded before the virtual-device API.
    const display = {...(state.config?.display || {}), ...payload.display};
    Object.assign(display, {
      // Keep the legacy master display switch untouched. This fallback must
      // pause only the local HDMI panel, just like the dedicated endpoint.
      touchscreen_enabled: payload.enabled,
      touchscreen_interval_seconds: payload.touchscreen_interval_seconds,
      orientation_mode: payload.orientation_mode,
      rotation: payload.rotation,
      show_filename: payload.show_filename,
      repeat_window: payload.repeat_window,
    });
    return await json('/api/config', {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...state.config, display, revision: state.config?.revision}),
    });
  }
}

async function advanceTouchscreen(action = 'next') {
  try {
    return await json('/api/admin/touchscreen/advance', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action}),
    });
  } catch (error) {
    if (error.status && error.status !== 404) throw error;
    if (action === 'pause' || action === 'resume') {
      return await json('/api/config', {
        method: 'PATCH', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          display: {...(state.config?.display || {}), touchscreen_enabled: action === 'resume'},
          revision: state.config?.revision,
        }),
      });
    }
    return await json('/api/display/control', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action}),
    });
  }
}

function addField(parent, label, name, value, type = 'text') {
  const node = document.createElement('label');
  node.innerHTML = `<span>${esc(label)}</span><input name="${esc(name)}" type="${esc(type)}">`;
  node.querySelector('input').value = value == null ? '' : value;
  parent.append(node);
  return node.querySelector('input');
}

function addSelect(parent, label, name, options, selected) {
  const node = document.createElement('label');
  node.innerHTML = `<span>${esc(label)}</span><select name="${esc(name)}">${options.map(([value, text]) => `<option value="${esc(value)}">${esc(text)}</option>`).join('')}</select>`;
  node.querySelector('select').value = selected == null ? options[0][0] : String(selected);
  parent.append(node);
  return node.querySelector('select');
}

function addToggle(parent, label, name, checked) {
  const node = document.createElement('label');
  node.className = 'toggle-label';
  node.innerHTML = `<input name="${esc(name)}" type="checkbox"><span>${esc(label)}</span>`;
  node.querySelector('input').checked = checked !== false;
  parent.append(node);
  return node.querySelector('input');
}

function setDeviceStatus(node, text, error = false) {
  node.textContent = text;
  node.classList.toggle('is-error', error);
}

function formatDeviceTime(value) {
  const timestamp = Number(value);
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '尚未记录';
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return '尚未记录';
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function deviceProfileLabel(device) {
  if (device.profile_id === 'seeedstudio_reterminal_e1002') return 'Seeed Studio reTerminal E1002';
  if (device.profile_id === 'waveshare_photopainter_73') return 'Waveshare ESP32-S3-PhotoPainter 7.3"';
  return '型号待确认';
}

function deviceGroupKey(device) {
  if (device.display?.kind !== 'photoframe') return device.display?.kind || 'other';
  return supportedPhotoFrameProfiles.has(device.profile_id) ? device.profile_id : 'unidentified';
}

function deviceGroupLabel(key) {
  return ({
    unidentified: '待确认型号',
    waveshare_photopainter_73: 'Waveshare PhotoPainter',
    seeedstudio_reterminal_e1002: 'Seeed reTerminal E1002',
    lcd: 'ESP32 LCD',
    epaper: 'E6 电子纸',
  })[key] || '其他设备';
}

function deviceStatusInfo(device, snapshot) {
  if (device.enabled === false) return {label: '已禁用', className: 'off'};
  if (device.profile_required || (device.display?.kind === 'photoframe' && !supportedPhotoFrameProfiles.has(device.profile_id))) {
    return {label: '待确认型号', className: 'pending'};
  }
  // A legacy ``last_request`` may have been produced by a browser or an old
  // server path.  For PhotoFrame devices the provisioning record is the
  // authoritative state until a complete firmware/display request proves a
  // real ESP32 pull.  Keep the headline actionable instead of showing an old
  // request as if the device were configured.
  if (device.display?.kind === 'photoframe') {
    const provisionState = pullProvisionState(device, snapshot);
    if (provisionState.key === 'unconfigured') {
      return {label: '未配置主动拉取', className: 'pending'};
    }
    if (provisionState.key === 'awaiting') {
      return {label: '已验证配置 · 等待设备主动拉图', className: 'pending'};
    }
    if (provisionState.key === 'unreachable') {
      return {label: '主动拉取配置失败', className: 'warn'};
    }
    if (provisionState.key === 'pulled') {
      return {label: '已启用 · 已验证拉图', className: 'on'};
    }
    if (provisionState.key === 'unverified') {
      return {label: '已配置 · 等待设备证据', className: 'pending'};
    }
  }
  const lastRequest = snapshot?.last_request ?? device.last_request;
  const requestFirmware = snapshot?.last_request_firmware ?? device.last_request_firmware;
  const requestDisplay = snapshot?.last_request_display ?? device.last_request_display;
  const verifiedPull = device.display?.kind === 'photoframe'
    && [requestFirmware, requestDisplay].every(item => item !== null && item !== undefined && String(item).trim() !== '');
  const rawStatus = String(snapshot?.last_status ?? device.last_status ?? '').toLowerCase();
  if (rawStatus === 'advanced') {
    return {label: '已启用 · 等待设备主动请求', className: 'pending'};
  }
  if (!lastRequest || ['disabled', 'never', 'unknown'].includes(rawStatus)) {
    return {label: '已启用 · 等待设备主动请求', className: 'pending'};
  }
  if (rawStatus.includes('error') || rawStatus.includes('fail') || rawStatus.includes('timeout') || rawStatus.includes('unreachable')) {
    return {label: '已启用 · 最近失败', className: 'warn'};
  }
  if (device.display?.kind === 'photoframe' && !verifiedPull) {
    return {label: '已启用 · 历史请求未验证', className: 'pending'};
  }
  return {label: verifiedPull ? '已启用 · 已验证拉图' : '已启用 · 最近有请求', className: 'on'};
}

function deviceRequestLabel(value) {
  const key = String(value || '').toLowerCase();
  return ({
    unknown: '尚未请求',
    never: '尚未请求',
    ok: '成功',
    success: '成功',
    advanced: '已手动推进',
    configuring: '正在验证设备配置',
    awaiting_pull: '已验证配置，等待设备拉图',
    configured: '已验证配置，等待设备拉图',
    pending_connection: '历史待验证记录 · 不可用',
    pulled: '设备已拉图',
    active: '设备已拉图',
    empty: '没有可用照片',
    disabled: '已禁用',
    error: '失败',
  })[key] || (value ? String(value) : '尚未记录');
}

function appendDeviceFacts(parent, facts) {
  const grid = document.createElement('div');
  grid.className = 'device-live-grid';
  facts.forEach(([label, value, className = '']) => {
    const cell = document.createElement('div');
    cell.className = `device-live-item${className ? ` ${className}` : ''}`;
    const name = document.createElement('span');
    name.className = 'device-live-label';
    name.textContent = label;
    const content = document.createElement('strong');
    content.className = 'device-live-value';
    content.textContent = value == null || value === '' ? '未设置' : String(value);
    cell.append(name, content);
    grid.append(cell);
  });
  parent.append(grid);
  return grid;
}

function appendDeviceEndpoint(parent, deviceId, registeredUrl = '') {
  if (!deviceId) return;
  const row = document.createElement('div');
  row.className = 'device-endpoint-row';
  const label = document.createElement('span');
  label.className = 'device-live-label';
  label.textContent = '设备取图 URL';
  const code = document.createElement('code');
  code.className = 'device-endpoint';
  const endpoint = registeredUrl || `${location.origin}/api/devices/${encodeURIComponent(deviceId)}/photoframe`;
  code.textContent = endpoint;
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.className = 'secondary-button endpoint-copy';
  copy.textContent = '复制 URL';
  copy.title = '复制设备取图地址';
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(endpoint);
      copy.textContent = '已复制';
      setTimeout(() => { copy.textContent = '复制 URL'; }, 1600);
    } catch (error) {
      copy.textContent = '复制失败';
      setTimeout(() => { copy.textContent = '复制 URL'; }, 1600);
    }
  };
  row.append(label, code, copy);
  parent.append(row);
  const help = document.createElement('p');
  help.className = 'device-pull-help';
  help.textContent = '设备主动拉取：此 URL 是 ESP32 的图片来源。URL Rotation 配置验证成功后，日常照片更新始终由 ESP32 主动拉取，310B 不会向设备推送照片。若固件没有 URL Rotation 选项，当前固件不能直接使用这条链路。';
  parent.append(help);
}

function pullProvisionState(device, snapshot = null) {
  // Provisioning state is deliberately separate from the legacy ``push``
  // object.  The server records the address/configuration attempt here, while
  // ``last_request`` proves that the ESP32 subsequently pulled an image.
  const value = {
    ...(device?.pull_provision || {}),
    ...(snapshot?.pull_provision || {}),
  };
  const status = String(value.status || '').trim().toLowerCase();
  const deviceUrl = String(
    value.device_url || value.url || value.base_url || snapshot?.device_url || device?.device_url || ''
  ).trim();
  const configuredAt = Number(value.last_success || value.configured_at || value.configured_at_epoch);
  const lastRequest = Number(snapshot?.last_request ?? device?.last_request);
  const requestFirmware = snapshot?.last_request_firmware
    ?? device?.last_request_firmware
    ?? value.last_request_firmware;
  const requestDisplay = snapshot?.last_request_display
    ?? device?.last_request_display
    ?? value.last_request_display;
  const hasRequestEvidence = [requestFirmware, requestDisplay].every(item => (
    item !== null && item !== undefined && String(item).trim() !== ''
  ));
  const requestAfterConfig = Boolean(
    deviceUrl && Number.isFinite(lastRequest) && lastRequest > 0
      && hasRequestEvidence && Number.isFinite(configuredAt) && lastRequest >= configuredAt
  );
  if (['unreachable', 'timeout', 'error', 'rejected', 'invalid'].includes(status)) {
    const httpStatus = value.last_http_status ? `（HTTP ${value.last_http_status}）` : '';
    return {
      key: 'unreachable',
      label: '设备不可达',
      className: 'is-unreachable',
      detail: `${value.last_error || '无法访问设备网页'}${httpStatus}`,
      deviceUrl,
      value,
    };
  }
  if (status === 'configuring') {
    return {
      key: 'working',
      label: '正在验证设备配置',
      className: 'is-working',
      detail: '服务器正在访问 ESP32 网页并校验 URL Rotation；尚未完成登记',
      deviceUrl,
      value,
    };
  }
  // An explicit unconfigured record wins over legacy request timestamps.  A
  // prior browser/curl request is diagnostic history, not proof that URL
  // Rotation has ever been enabled on the ESP32.
  if (status === 'unconfigured' && !deviceUrl) {
    return {
      key: 'unconfigured',
      label: '未配置',
      className: 'is-unconfigured',
      detail: Number.isFinite(lastRequest) && lastRequest > 0
        ? `尚未验证 ESP32 地址；旧请求记录：${formatDeviceTime(lastRequest)}`
        : '填写 ESP32 网页地址后验证并登记',
      deviceUrl,
      value,
    };
  }
  // Never infer a successful pull from a legacy status string alone.  The
  // server only marks a request as verified when the timestamp, firmware and
  // display capability headers were all recorded.
  const pulled = hasRequestEvidence && Number.isFinite(lastRequest) && lastRequest > 0
    && (requestAfterConfig || ['pulled', 'active', 'device_pulled'].includes(status));
  if (pulled) {
    return {
      key: 'pulled',
      label: '设备已拉图',
      className: 'is-pulled',
      detail: lastRequest > 0 ? `最近拉图：${formatDeviceTime(lastRequest)}` : '设备已经访问取图地址',
      deviceUrl,
      value,
    };
  }
  if (Number.isFinite(lastRequest) && lastRequest > 0 && !hasRequestEvidence) {
    return {
      key: 'unverified',
      label: '历史请求未验证',
      className: 'is-unverified',
      detail: `记录过请求：${formatDeviceTime(lastRequest)}；缺少固件/显示能力证据`,
      deviceUrl,
      value,
    };
  }
  if (deviceUrl || ['configured', 'awaiting_pull', 'success', 'ready'].includes(status)) {
    return {
      key: 'awaiting',
      label: '已验证配置 · 等待设备主动拉图',
      className: 'is-awaiting',
      detail: '设备身份和 URL Rotation 已验证；等待 ESP32 主动访问取图 URL',
      deviceUrl,
      value,
    };
  }
  return {
    key: 'unconfigured',
    label: '未配置',
    className: 'is-unconfigured',
    detail: '填写 ESP32 网页地址后验证并登记',
    deviceUrl,
    value,
  };
}

function appendPullProvision(parent, device, snapshot = null) {
  const section = document.createElement('section');
  section.className = 'pull-provision';
  const initial = pullProvisionState(device, snapshot);
  const heading = document.createElement('div');
  heading.className = 'pull-provision-heading';
  heading.innerHTML = initial.key === 'unconfigured'
    ? '<div><strong>验证设备并写入配置</strong><small>必须先验证设备网页地址，才会建立有效注册</small></div>'
    : '<div><strong>重新验证设备配置</strong><small>重新验证地址并写入 URL Rotation 配置</small></div>';
  const status = document.createElement('span');
  status.className = 'pull-provision-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  heading.append(status);
  section.append(heading);

  const form = document.createElement('form');
  form.className = 'pull-provision-form';
  const label = document.createElement('label');
  label.innerHTML = '<span>设备网页地址</span><input name="device_url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="http://192.168.1.137" required>';
  const input = label.querySelector('input');
  input.value = initial.deviceUrl;
  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.className = 'primary-button';
  submit.textContent = initial.key === 'unconfigured' ? '验证并写入配置' : '重新验证配置';
  form.append(label, submit);
  section.append(form);
  const help = document.createElement('p');
  help.className = 'pull-provision-help';
  help.textContent = '服务器会先访问一次该地址并验证 PhotoFrame；验证失败不会建立或更新有效注册。成功后只表示配置已验证，照片仍由 ESP32 按自己的轮播计划主动拉取。';
  section.append(help);

  const updateStatus = (value, error = false) => {
    const state = pullProvisionState(device, value ? {pull_provision: value, last_request: snapshot?.last_request ?? device.last_request} : snapshot);
    status.textContent = state.detail ? `${state.label}：${state.detail}` : state.label;
    status.dataset.state = state.key;
    status.className = `pull-provision-status ${state.className}${error ? ' is-error' : ''}`;
    if (state.deviceUrl) input.value = state.deviceUrl;
  };
  updateStatus(null);

  form.onsubmit = async event => {
    event.preventDefault();
    const deviceUrl = input.value.trim().replace(/\/$/, '');
    if (!deviceUrl) {
      updateStatus({status: 'unconfigured'});
      input.focus();
      return;
    }
    let parsed;
    try { parsed = new URL(deviceUrl); } catch {
      updateStatus({status: 'rejected', last_error: '请输入完整的 http://设备地址'} , true);
      input.focus();
      return;
    }
    if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) {
      updateStatus({status: 'rejected', last_error: '地址必须是无账号、无参数的 http(s) 设备网页地址'}, true);
      input.focus();
      return;
    }
    submit.disabled = true;
    input.disabled = true;
    status.textContent = '正在验证设备网页并写入 URL Rotation…';
    status.dataset.state = 'working';
    status.className = 'pull-provision-status is-working';
    try {
      const value = await json(`/api/admin/devices/${encodeURIComponent(device.device_id)}/provision-pull`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({device_url}),
      });
      const provision = value.pull_provision || value.device?.pull_provision || value;
      updateStatus(provision);
      showNotice('设备配置已验证，等待 ESP32 主动拉图。');
      try {
        await loadDevices();
      } catch (refreshError) {
        // The provisioning request already succeeded.  A transient list
        // refresh failure must not turn that success into "设备不可达".
        showError(`设备配置已验证，但状态刷新失败：${refreshError.message}`);
      }
    } catch (error) {
      const detail = error.message || `HTTP ${error.status || '错误'}`;
      updateStatus({status: 'unreachable', device_url: deviceUrl, last_error: detail, last_http_status: error.status}, true);
    } finally {
      submit.disabled = false;
      input.disabled = false;
    }
  };
  parent.append(section);
}

function renderTouchscreenCard(host, value) {
  const device = value.device || {};
  const config = value.config || state.config || {};
  const display = config.display || {};
  const stateValue = value.state || {};
  const card = document.createElement('article');
  // The dedicated endpoint exposes the local switch on ``device.enabled``;
  // the global ``display.enabled`` flag is retained for legacy e-paper
  // compatibility and must not mask a paused touchscreen.
  const enabled = device.enabled !== undefined
    ? device.enabled !== false
    : (display.touchscreen_enabled ?? (display.enabled !== false));
  card.className = 'device-console-card touchscreen-device-card';
  const width = Number(device.display?.width) || 1920;
  const height = Number(device.display?.height) || 1080;
  const current = stateValue.current || state.current;
  card.innerHTML = `<header class="device-console-header"><div class="device-type-mark touchscreen-mark">触</div><div><h3>本机触摸屏相册</h3><p class="device-subtitle">开发板 HDMI 触摸屏 · ${width}×${height} · ${esc(device.device_id || 'local-touchscreen')}</p></div><span class="device-state ${enabled ? 'on' : 'off'}">${enabled ? '运行中' : '已暂停'}</span></header><p class="device-help">本机屏幕是独立设备，可单独设置换图、方向和文件名显示；这些设置不会改变 ESP32 电子相册的轮播计划。</p>`;
  appendDeviceFacts(card, [
    ['设备型号', 'QDtech MPI1001 · HDMI 触摸屏'],
    ['设备地址', '本机 310B（无网络地址）'],
    ['当前照片', current?.filename || '尚未选择'],
    ['最近状态', stateValue.last_status || '本机服务'],
    ['最近更新', formatDeviceTime(stateValue.last_seen || device.last_seen)],
    ['轮播间隔', `${Number(display.touchscreen_interval_seconds ?? display.interval_seconds ?? 60)} 秒`],
  ]);
  const form = document.createElement('form');
  form.className = 'device-settings-form';
  const grid = document.createElement('div');
  grid.className = 'device-settings-grid';
  addField(grid, '设备名称', 'name', device.name || '本机触摸屏');
  addToggle(grid, '启用本机自动轮播', 'enabled', enabled);
  addField(grid, '自动换图（秒）', 'touchscreen_interval_seconds', display.touchscreen_interval_seconds ?? display.interval_seconds ?? 60, 'number').min = '5';
  addSelect(grid, '照片方向', 'orientation_mode', [['auto', '自动校正 EXIF'], ['match_display', '匹配屏幕方向']], display.orientation_mode || 'auto');
  addSelect(grid, '安装角度', 'rotation', [['0', '0 度'], ['90', '90 度'], ['180', '180 度'], ['270', '270 度']], display.rotation || 0);
  addField(grid, '重复抑制图片数', 'repeat_window', display.repeat_window ?? 12, 'number').min = '0';
  addToggle(grid, '显示文件名水印', 'show_filename', display.show_filename !== false);
  form.append(grid);
  const settings = document.createElement('details');
  settings.className = 'device-advanced touchscreen-settings';
  const settingsSummary = document.createElement('summary');
  settingsSummary.textContent = '本机显示设置（方向、轮播、文件名水印）';
  settings.append(settingsSummary, form);
  const actions = document.createElement('div');
  actions.className = 'device-form-actions';
  const save = document.createElement('button');
  save.className = 'primary-button'; save.type = 'button'; save.textContent = '保存本机设置';
  const next = document.createElement('button');
  next.className = 'secondary-button'; next.type = 'button'; next.textContent = '立即换下一张';
  const pause = document.createElement('button');
  pause.className = 'secondary-button'; pause.type = 'button'; pause.textContent = enabled ? '暂停轮播' : '恢复轮播';
  actions.append(save, next, pause);
  const status = document.createElement('p');
  status.className = 'device-console-status';
  status.textContent = current?.filename ? `当前照片：${current.filename}` : '当前照片由本机显示状态恢复';
  form.onsubmit = async event => {
    event.preventDefault(); save.disabled = true;
    try {
      const body = {
        name: form.elements.name.value.trim(),
        enabled: form.elements.enabled.checked,
        touchscreen_interval_seconds: Number(form.elements.touchscreen_interval_seconds.value),
        interval_seconds: Number(form.elements.touchscreen_interval_seconds.value),
        orientation_mode: form.elements.orientation_mode.value,
        rotation: Number(form.elements.rotation.value),
        show_filename: form.elements.show_filename.checked,
        repeat_window: Number(form.elements.repeat_window.value),
      };
      const updated = await saveTouchscreen(body);
      const returnedConfig = updated.config || updated;
      const returnedDisplay = returnedConfig.display || {};
      state.config = merge(state.config || {}, {...returnedConfig, display: {...returnedDisplay, touchscreen_interval_seconds: returnedDisplay.touchscreen_interval_seconds ?? returnedDisplay.interval_seconds}});
      renderFilenameWatermark();
      armDisplayRefresh();
      setDeviceStatus(status, '本机触摸屏设置已保存');
      await loadCurrent(false);
    } catch (error) { setDeviceStatus(status, `保存失败：${error.message}`, true); }
    finally { save.disabled = false; }
  };
  save.onclick = () => form.requestSubmit();
  next.onclick = async () => { next.disabled = true; try { await advanceTouchscreen(); await loadCurrent(); setDeviceStatus(status, '已切换到下一张照片'); } catch (error) { setDeviceStatus(status, `换图失败：${error.message}`, true); } finally { next.disabled = false; } };
  pause.onclick = async () => { pause.disabled = true; const action = form.elements.enabled.checked ? 'pause' : 'resume'; try { await advanceTouchscreen(action); await loadDevices(); } catch (error) { setDeviceStatus(status, `轮播控制失败：${error.message}`, true); } finally { pause.disabled = false; } };
  card.append(settings, actions, status);
  host.append(card);
}

function renderEspPairingCard(host) {
  const card = document.createElement('article');
  card.className = 'device-console-card esp-pair-card';
  card.innerHTML = '<header class="device-console-header"><div class="device-type-mark esp-mark">E</div><div><h3>验证并登记 ESP32 电子相册</h3><p class="device-subtitle">Waveshare PhotoPainter 或 Seeed reTerminal E1002</p></div><span class="device-badge">先验证，再登记</span></header><p class="device-help">登记不是离线填表：必须填写 ESP32 当前网页地址。服务器会先访问设备、确认 PhotoFrame 身份并写入 URL Rotation；任何一步失败都不会显示配置成功，也不会留下误导性的在线记录。</p><p class="device-steps"><b>操作顺序：</b>按 KEY 唤醒 → 从串口日志取得 ESP32 IPv4 → 填写下方地址并点击“验证并登记” → 看到“已验证配置”后设备才会按计划主动取图。KEY 只负责唤醒或重置睡眠计时，不能代替 URL Rotation。</p>';
  const form = document.createElement('form'); form.className = 'device-settings-form esp-pairing-form';
  const grid = document.createElement('div'); grid.className = 'device-settings-grid';
  addField(grid, '设备名称', 'name', 'living-room');
  addSelect(grid, '设备型号', 'profile_id', [['', '请选择已确认的设备型号'], ['waveshare_photopainter_73', 'Waveshare ESP32-S3-PhotoPainter 7.3\"'], ['seeedstudio_reterminal_e1002', 'Seeed Studio reTerminal E1002']], '');
  addSelect(grid, '屏幕摆放', 'orientation', [['landscape', '横屏 800×480'], ['portrait', '竖屏 480×800']], 'landscape');
  const deviceUrlInput = addField(grid, 'ESP32 网页地址（必填）', 'device_url', '', 'url');
  deviceUrlInput.placeholder = 'http://192.168.1.137';
  deviceUrlInput.required = true;
  deviceUrlInput.autocomplete = 'off';
  deviceUrlInput.inputMode = 'url';
  const modeLabel = document.createElement('p');
  modeLabel.className = 'pair-fixed-mode';
  modeLabel.textContent = '传输方式：设备主动拉取（服务器先验证设备连通性）';
  card.append(modeLabel);
  form.append(grid);
  const profileSelect = form.elements.profile_id;
  const orientationSelect = form.elements.orientation;
  profileSelect.required = true;
  const syncProfileDimensions = () => {
    orientationSelect.querySelector('option[value="portrait"]').disabled = profileSelect.value === 'seeedstudio_reterminal_e1002';
    if (orientationSelect.value === 'portrait' && orientationSelect.querySelector('option[value="portrait"]').disabled) orientationSelect.value = 'landscape';
  };
  profileSelect.onchange = syncProfileDimensions; orientationSelect.onchange = syncProfileDimensions; syncProfileDimensions();
  const actions = document.createElement('div'); actions.className = 'device-form-actions';
  const submit = document.createElement('button'); submit.className = 'primary-button'; submit.type = 'submit'; submit.textContent = '验证并注册设备'; actions.append(submit); form.append(actions);
  const status = document.createElement('p'); status.className = 'device-console-status'; status.textContent = '填写局域网 ESP32 地址后，服务器会先验证连通性；验证失败不会创建注册记录。';
  const result = document.createElement('div'); result.className = 'pair-result device-pair-result'; result.hidden = true;
  result.innerHTML = '<label>服务器取图 URL<input name="url" readonly></label><p class="pair-result-help">服务器已完成设备验证和 URL Rotation 配置。此状态只表示配置成功；看到“设备已拉图”还需要 ESP32 实际访问服务器取图地址。</p>';
  if (state.lastPairing) {
    result.hidden = false;
    deviceUrlInput.value = state.lastPairing.device_url || '';
    result.querySelector('[name="url"]').value = state.lastPairing.pull_url || `${location.origin}/api/devices/${state.lastPairing.device_id}/photoframe`;
    status.textContent = `上次已验证配置 ${state.lastPairing.name || state.lastPairing.device_id}；如需重试，请重新填写设备地址。`;
  }
  form.onsubmit = async event => {
    event.preventDefault(); submit.disabled = true;
    result.hidden = true;
    const deviceUrl = form.elements.device_url.value.trim().replace(/\/$/, '');
    if (!deviceUrl) {
      setDeviceStatus(status, '注册失败：必须填写 ESP32 当前网页地址；未创建注册记录。', true);
      form.elements.device_url.focus();
      submit.disabled = false;
      return;
    }
    let parsed;
    try { parsed = new URL(deviceUrl); } catch {
      setDeviceStatus(status, '注册失败：请输入完整的 http://设备地址；未创建注册记录。', true);
      form.elements.device_url.focus();
      submit.disabled = false;
      return;
    }
    const octets = parsed.hostname.split('.').map(value => Number(value));
    const isPrivateIpv4 = octets.length === 4 && octets.every((value, index) => /^\d+$/.test(parsed.hostname.split('.')[index]) && value >= 0 && value <= 255)
      && ((octets[0] === 10) || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) || (octets[0] === 192 && octets[1] === 168));
    if (parsed.protocol !== 'http:' || !isPrivateIpv4 || parsed.port && parsed.port !== '80' || parsed.username || parsed.password || !['', '/'].includes(parsed.pathname) || parsed.search || parsed.hash) {
      setDeviceStatus(status, '注册失败：地址必须是无参数的局域网 IPv4 根地址，例如 http://192.168.1.137；未创建注册记录。', true);
      form.elements.device_url.focus();
      submit.disabled = false;
      return;
    }
    setDeviceStatus(status, '正在访问 ESP32 并验证 PhotoFrame；成功后才会登记设备…');
    try {
      // This endpoint is transactional: the server verifies and configures the
      // ESP32 before persisting the registration. Do not fall back to the old
      // offline POST /api/admin/devices path here.
      const value = await json('/api/admin/devices/register', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        kind: 'photoframe',
        delivery_mode: 'device_pull',
        device_url: deviceUrl,
        trigger_now: true,
        profile_id: form.elements.profile_id.value,
        name: form.elements.name.value.trim(),
        display: {kind: 'photoframe', width: form.elements.orientation.value === 'portrait' ? 480 : 800, height: form.elements.orientation.value === 'portrait' ? 800 : 480, orientation: form.elements.orientation.value, codecs: ['jpeg'], max_bytes: 2097152, rotation: 0, orientation_mode: 'auto'},
        policy: {rotation_cron: ['*/30 * *'], crop_mode: 'cover', orientation_mode: 'auto', rotation: 0, overlay_date: true, overlay_weather: true},
      })});
      const registered = value.device || value;
      const registeredId = registered.device_id || value.device_id;
      state.lastPairing = {device_id: registeredId, name: registered.name || value.name, device_url: deviceUrl, pull_url: value.pull_url || registered.pull_url};
      result.hidden = false;
      result.querySelector('[name="url"]').value = value.pull_url || registered.pull_url || `${location.origin}/api/devices/${registeredId}/photoframe`;
      const registrationStatus = String(value.registration_status || '').toLowerCase();
      if (!['connected', 'configured', 'awaiting_pull', 'pulled'].includes(registrationStatus)) {
        throw new Error('服务器未返回有效的配置验证状态，未确认设备注册结果');
      }
      const pullStatus = String(value.pull_status || '').toLowerCase();
      const hasDevicePullEvidence = registrationStatus === 'pulled' || pullStatus === 'device_pull_verified';
      // A control-plane 202 only proves that the server reached the ESP32 and
      // wrote its URL Rotation settings.  Do not trust a stale/overly broad
      // backend message to turn that into a false "connected" state.
      const statusMessage = hasDevicePullEvidence
        ? (value.message || `设备已完成真实拉图：${registered.name || registeredId}`)
        : `服务器已验证并登记配置 ${registered.name || registeredId}；尚未证明设备在线，等待真实拉图。`;
      setDeviceStatus(status, statusMessage);
      try {
        await loadDevices();
      } catch (refreshError) {
        // The transactional registration already succeeded. A temporary UI
        // refresh failure must not rewrite the success as an unreachable
        // device or clear the local pairing result.
        showError(`设备配置已验证，但设备列表刷新失败：${refreshError.message}`);
      }
      if (!hasDevicePullEvidence && registeredId) watchDevicePull(registeredId);
    } catch (error) {
      state.lastPairing = null;
      result.hidden = true;
      setDeviceStatus(status, `注册失败，未创建有效设备记录：${error.message}`, true);
    }
    finally { submit.disabled = false; }
  };
  const details = document.createElement('details');
  details.className = 'device-advanced pairing-settings';
  const summary = document.createElement('summary');
  summary.textContent = '验证并注册新 ESP32 电子相册（点击展开）';
  details.append(summary, form, result, status);
  card.append(details);
  host.append(card);
}

function renderExternalDevice(target, device, snapshot = null, stateError = null) {
  const item = document.createElement('article');
  item.className = 'device external-device-card';
  const kind = device.display?.kind || 'unknown';
  const deviceId = String(device.device_id || '');
  const profileRequired = kind === 'photoframe' && !supportedPhotoFrameProfiles.has(device.profile_id);
  const label = kind === 'photoframe' ? 'ESP32 电子相册' : kind === 'lcd' ? 'ESP32 LCD' : kind;
  const policy = device.policy || {};
  const stateValue = snapshot || {};
  const current = stateValue.current || device.current || null;
  const effectiveState = stateValue;
  const provisionState = kind === 'photoframe' ? pullProvisionState(device, effectiveState) : null;
  const verifiedDevicePull = provisionState?.key === 'pulled';
  const statusInfo = deviceStatusInfo(device, effectiveState);
  const width = Number(device.display?.width) || 800;
  const height = Number(device.display?.height) || 480;
  const orientation = device.display?.orientation === 'portrait' ? '竖屏' : '横屏';
  const selectionMode = stateValue.selection_mode || policy.selection_mode || 'smart';
  const cron = (policy.rotation_cron || ['*/30 * *'])[0];
  const connection = '设备主动拉取';
  const address = 'ESP32 定期访问服务器取图';
  const lastStatus = String(effectiveState.last_status ?? device.last_status ?? '').toLowerCase();
  const lastDeviceRequest = lastStatus === 'advanced' ? null : (effectiveState.last_request ?? device.last_request);
  item.innerHTML = `<header class="device-console-header"><div class="device-type-mark esp-mark">${kind === 'photoframe' ? 'E' : '屏'}</div><div><h3>${esc(device.name || label)}</h3><p class="device-subtitle">${esc(deviceProfileLabel(device))} · ${width}×${height} · ${orientation}</p><code class="device-id">设备 ID：${esc(deviceId || '未分配')}</code></div><span class="device-state ${statusInfo.className}">${statusInfo.label}</span></header>`;
  const facts = [
    ['设备型号', deviceProfileLabel(device)],
    ['连接方式', connection],
    ['设备地址', address],
    ['最近设备请求', formatDeviceTime(lastDeviceRequest)],
    ['请求结果', deviceRequestLabel(effectiveState.last_status ?? device.last_status)],
    ['轮播策略', `${selectionMode === 'playlist' ? '播放列表' : '智能选图'} · ${cron}`],
    ['策略版本', `revision ${Number(device.policy_revision || policy.policy_revision || 1)}`],
  ];
  if (kind === 'photoframe') {
    // ``current`` is the server's candidate selected by the selector.  It is
    // not evidence that the ESP32 rendered the image; only a complete
    // PhotoFrame request makes that claim safe to show in the UI.
    facts.splice(3, 0,
      ['服务器候选照片', current?.filename || '尚未选择'],
      ['设备已显示照片', verifiedDevicePull ? (current?.filename || '已拉取，文件名未知') : '尚未确认设备显示'],
    );
  } else {
    facts.splice(3, 0, ['当前照片', current?.filename || '尚未拉取或选择']);
  }
  appendDeviceFacts(item, facts);
  // A legacy record without a confirmed hardware profile is not usable yet;
  // do not present its URL as if the endpoint were ready for the device.
  if (kind === 'photoframe' && !profileRequired) {
    appendDeviceEndpoint(item, deviceId, device.pull_url);
    appendPullProvision(item, device, effectiveState);
  }
  if (stateError) {
    const note = document.createElement('p');
    note.className = 'device-console-status is-error';
    note.textContent = `设备状态读取失败：${stateError.message || stateError}`;
    item.append(note);
  }
  if (kind === 'photoframe') {
    if (profileRequired) {
      const warning = document.createElement('p');
      warning.className = 'device-console-status is-error device-identity-warning';
      warning.textContent = '该历史设备没有可靠的型号记录。确认实际硬件后选择型号；确认前不能取图或换图。';
      const identifyForm = document.createElement('form');
      identifyForm.className = 'device-settings-form device-profile-form';
      const identifyGrid = document.createElement('div');
      identifyGrid.className = 'device-settings-grid';
      addSelect(identifyGrid, '确认设备型号', 'profile_id', [
        ['', '请选择实际硬件'],
        ['waveshare_photopainter_73', 'Waveshare ESP32-S3-PhotoPainter 7.3"'],
        ['seeedstudio_reterminal_e1002', 'Seeed Studio reTerminal E1002'],
      ], '');
      identifyForm.append(identifyGrid);
      const identifyActions = document.createElement('div');
      identifyActions.className = 'device-form-actions';
      const identify = document.createElement('button');
      identify.className = 'primary-button'; identify.type = 'submit'; identify.textContent = '确认型号并启用设备设置';
      identifyActions.append(identify); identifyForm.append(identifyActions);
      identifyForm.onsubmit = async event => {
        event.preventDefault();
        const profileId = identifyForm.elements.profile_id.value;
        if (!profileId) { setDeviceStatus(warning, '请选择实际硬件型号。', true); return; }
        identify.disabled = true;
        try {
          await json(`/api/admin/devices/${encodeURIComponent(deviceId)}`, {
            method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({profile_id: profileId}),
          });
          await loadDevices();
        } catch (error) { setDeviceStatus(warning, `型号确认失败：${error.message}`, true); }
        finally { identify.disabled = false; }
      };
      item.append(warning, identifyForm);
    }
    const advanced = document.createElement('details');
    advanced.className = 'device-advanced';
    const advancedSummary = document.createElement('summary');
    advancedSummary.textContent = profileRequired ? '设备主动拉取策略（确认型号后可用）' : '设备主动拉取策略';
    advanced.append(advancedSummary);
    const form = document.createElement('form');
    form.className = 'device-settings-form device-policy-form';
    const grid = document.createElement('div');
    grid.className = 'device-settings-grid';
    addField(grid, '设备名称', 'name', device.name || label);
    addField(grid, '轮播 cron', 'rotation_cron', cron);
    addSelect(grid, '裁剪', 'crop_mode', [['cover', '填满'], ['fit', '留白']], policy.crop_mode || 'cover');
    addSelect(grid, '方向', 'orientation_mode', [['auto', '保持照片方向'], ['match_display', '匹配屏幕']], policy.orientation_mode || 'auto');
    addSelect(grid, '屏幕摆放', 'orientation', device.profile_id === 'waveshare_photopainter_73' ? [['landscape', '横屏 800×480'], ['portrait', '竖屏 480×800']] : [['landscape', '横屏 800×480']], device.display?.orientation || 'landscape');
    addToggle(grid, '日期', 'overlay_date', policy.overlay_date !== false);
    addToggle(grid, '天气', 'overlay_weather', policy.overlay_weather !== false);
    form.append(grid);
    const actions = document.createElement('div');
    actions.className = 'device-form-actions';
    const save = document.createElement('button');
    save.className = 'secondary-button'; save.type = 'submit'; save.textContent = '保存 ESP32 策略';
    actions.append(save); form.append(actions);
    form.onsubmit = async event => {
      event.preventDefault(); save.disabled = true;
      try {
        await json(`/api/admin/devices/${encodeURIComponent(deviceId)}`, {
          method: 'PATCH', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            name: form.elements.name.value.trim(),
            display: {
              orientation: form.elements.orientation.value,
              width: form.elements.orientation.value === 'portrait' ? 480 : 800,
              height: form.elements.orientation.value === 'portrait' ? 800 : 480,
            },
            policy: {
              rotation_cron: [form.elements.rotation_cron.value.trim()],
              crop_mode: form.elements.crop_mode.value,
              orientation_mode: form.elements.orientation_mode.value,
              rotation: 0,
              overlay_date: form.elements.overlay_date.checked,
              overlay_weather: form.elements.overlay_weather.checked,
            },
          }),
        });
        await loadDevices();
      } catch (error) { showError(error.message); }
      finally { save.disabled = false; }
    };
    if (profileRequired) form.querySelectorAll('input, select, button').forEach(node => { node.disabled = true; });
    advanced.append(form);
    const pullStatus = document.createElement('p');
    pullStatus.className = 'device-console-status device-pull-mode';
    pullStatus.textContent = '传输方式：设备主动拉取。首次使用时在上方“验证并登记”表单填写 ESP32 网页地址；成功后由 ESP32 自己定时获取照片。';
    advanced.append(pullStatus);
    item.append(advanced);
  }
  const actions = document.createElement('div');
  actions.className = 'device-form-actions device-actions';
  const advance = document.createElement('button');
  advance.className = 'secondary-button'; advance.type = 'button';
  advance.textContent = '推进下一张';
  advance.title = kind === 'photoframe'
    ? '改变该设备的当前选择；设备下次主动访问取图 URL 时会获取这张照片'
    : '';
  advance.disabled = device.enabled === false || profileRequired;
  advance.onclick = async () => {
    advance.disabled = true;
    try {
      const endpoint = `/api/admin/devices/${encodeURIComponent(deviceId)}/advance`;
      await json(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({force: false})});
      await loadDevices();
    } catch (error) { showError(`推进失败：${error.message}`); }
    finally { advance.disabled = device.enabled === false || profileRequired; }
  };
  const toggle = document.createElement('button');
  toggle.className = 'secondary-button'; toggle.type = 'button';
  toggle.textContent = device.enabled === false ? '启用设备' : '禁用设备';
  toggle.onclick = async () => {
    toggle.disabled = true;
    try {
      await json(`/api/admin/devices/${encodeURIComponent(deviceId)}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: device.enabled === false})});
      await loadDevices();
    } catch (error) { showError(error.message); }
    finally { toggle.disabled = false; }
  };
  const actionNote = document.createElement('p');
  actionNote.className = 'device-action-note';
  actionNote.textContent = kind === 'photoframe'
    ? '当前是设备主动拉取模式；“推进下一张”只更新服务器选择，设备下次访问取图 URL 时获取新照片。'
    : '点击“下一张”只改变该设备的选图状态。';
  const remove = document.createElement('button');
  remove.className = 'secondary-button danger-button'; remove.type = 'button';
  remove.textContent = '删除注册';
  remove.title = '删除这条设备注册记录；不会删除照片或图库内容';
  const cancelRemove = document.createElement('button');
  cancelRemove.className = 'secondary-button'; cancelRemove.type = 'button';
  cancelRemove.textContent = '取消删除'; cancelRemove.hidden = true;
  let confirmTimer = null;
  const resetRemoveConfirmation = () => {
    clearTimeout(confirmTimer);
    confirmTimer = null;
    remove.dataset.confirming = 'false';
    remove.textContent = '删除注册';
    remove.title = '删除这条设备注册记录；不会删除照片或图库内容';
    cancelRemove.hidden = true;
    actionNote.textContent = kind === 'photoframe'
      ? '当前是设备主动拉取模式；“推进下一张”只更新服务器选择，设备下次访问取图 URL 时获取新照片。'
      : '点击“下一张”只改变该设备的选图状态。';
    actionNote.classList.remove('is-warning', 'is-error');
  };
  remove.onclick = async () => {
    // A visible two-step confirmation works on kiosk touchscreens where a
    // browser-native confirm dialog can be hidden or dismissed without a clue.
    if (remove.dataset.confirming !== 'true') {
      remove.dataset.confirming = 'true';
      remove.textContent = '再次点击确认删除';
      remove.title = '再次点击确认；只删除设备注册记录，不删除照片';
      actionNote.textContent = '删除会移除设备注册及其轮播状态，但不会删除任何原始照片。再次点击“再次点击确认删除”继续。';
      actionNote.classList.add('is-warning');
      cancelRemove.hidden = false;
      clearTimeout(confirmTimer);
      confirmTimer = setTimeout(resetRemoveConfirmation, 8000);
      return;
    }
    clearTimeout(confirmTimer);
    remove.disabled = true;
    try {
      await json(`/api/admin/devices/${encodeURIComponent(deviceId)}?confirm=true`, {method: 'DELETE'});
      actionNote.textContent = `设备“${device.name || deviceId}”已删除注册记录，照片未受影响。`;
      actionNote.classList.remove('is-warning', 'is-error');
      showNotice(`设备“${device.name || deviceId}”已删除注册记录，照片未受影响。`);
      await loadDevices();
    } catch (error) {
      actionNote.textContent = `删除失败：${error.message}`;
      actionNote.classList.add('is-error');
      showError(`删除设备失败：${error.message}`);
      resetRemoveConfirmation();
    } finally { remove.disabled = false; }
  };
  cancelRemove.onclick = resetRemoveConfirmation;
  actions.append(advance, toggle, remove, cancelRemove);
  item.append(actionNote, actions);
  target.append(item);
}

async function loadDevices() {
  const consoleHost = document.querySelector('#device-console');
  if (!consoleHost) throw new Error('设备控制台容器不可用');
  consoleHost.replaceChildren();
  const [touchscreenResult, devicesResult] = await Promise.allSettled([loadTouchscreen(), json('/api/admin/devices')]);
  const touchscreen = touchscreenResult.status === 'fulfilled' ? touchscreenResult.value : touchscreenFallback();
  const devices = devicesResult.status === 'fulfilled' ? (devicesResult.value.devices || []) : [];
  const external = devices.filter(device => device.display?.kind !== 'touchscreen' && device.device_id !== 'local-touchscreen');
  const enabledCount = external.filter(device => device.enabled !== false).length;
  const identifiedCount = external.filter(device => device.display?.kind !== 'photoframe' || supportedPhotoFrameProfiles.has(device.profile_id)).length;
  const pendingCount = external.length - identifiedCount;
  const verifiedConfigCount = external.filter(device => {
    const status = String(device.pull_provision?.status || '').toLowerCase();
    return device.display?.kind !== 'photoframe' || ['awaiting_pull', 'pulled'].includes(status);
  }).length;
  const pulledCount = external.filter(device => String(device.pull_provision?.status || '').toLowerCase() === 'pulled').length;
  const overview = document.createElement('section');
  overview.className = 'device-overview';
  overview.innerHTML = `<div class="device-overview-heading"><div><span class="eyebrow">设备注册表</span><h2>设备总览</h2><p>这里显示服务器登记的设备。只有完成地址和配置验证才算有效登记；真实 ESP32 拉图后才算已连通。</p></div><span class="device-overview-refresh">数据来自 /api/admin/devices</span></div><div class="device-overview-metrics"><div><strong>${external.length}</strong><span>登记记录</span></div><div><strong>${verifiedConfigCount}</strong><span>配置已验证</span></div><div><strong>${pulledCount}</strong><span>已验证拉图</span></div><div><strong>${enabledCount}</strong><span>已启用</span></div><div><strong>${identifiedCount}</strong><span>型号已确认</span></div><div><strong>${pendingCount}</strong><span>待确认型号</span></div></div><p class="device-overview-note"><b>状态含义：</b>“配置已验证”表示服务器实际访问 ESP32 并读回 URL Rotation，但不表示设备已取图；“已验证拉图”需要设备真实请求并匹配登记地址。失败的原子注册不会留下记录；旧兼容记录会明确显示为待验证。已禁用记录仍保留但不能访问照片。</p>`;
  consoleHost.append(overview);
  renderTouchscreenCard(consoleHost, touchscreen);
  renderEspPairingCard(consoleHost);
  const stateById = new Map();
  const stateErrors = new Map();
  await Promise.all(external.map(async device => {
    const deviceId = String(device.device_id || '');
    if (!deviceId) return;
    try {
      stateById.set(deviceId, await json(`/api/admin/devices/${encodeURIComponent(deviceId)}/state`));
    } catch (error) {
      stateErrors.set(deviceId, error);
    }
  }));
  const heading = document.createElement('div');
  heading.className = 'device-section-heading device-section-toolbar';
  const headingCopy = document.createElement('div');
  headingCopy.innerHTML = `<h3>已注册的 ESP32 设备 <span class="device-count">${external.length}</span></h3><p>按实际型号分组；状态、地址和当前照片显示在卡片顶部。</p>`;
  heading.append(headingCopy);
  const refresh = document.createElement('button');
  refresh.className = 'secondary-button'; refresh.type = 'button'; refresh.textContent = '刷新设备状态';
  refresh.onclick = () => loadDevices();
  heading.append(refresh);
  consoleHost.append(heading);
  const list = document.createElement('div');
  list.className = 'device-list';
  consoleHost.append(list);
  if (!external.length) {
    list.innerHTML = '<p class="panel-status">还没有 ESP32 电子相册。使用上方配对表单注册第一台设备。</p>';
  } else {
    const groups = new Map();
    external.forEach(device => {
      const key = deviceGroupKey(device);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(device);
    });
    const order = ['unidentified', 'waveshare_photopainter_73', 'seeedstudio_reterminal_e1002', 'lcd', 'epaper', 'other'];
    [...groups.entries()].sort((a, b) => {
      const ai = order.indexOf(a[0]); const bi = order.indexOf(b[0]);
      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
    }).forEach(([key, group]) => {
      const section = document.createElement('section');
      section.className = 'device-group';
      const groupHeading = document.createElement('header');
      groupHeading.className = 'device-group-heading';
      const groupEnabled = group.filter(device => device.enabled !== false).length;
      const groupPending = group.filter(device => device.display?.kind === 'photoframe' && !supportedPhotoFrameProfiles.has(device.profile_id)).length;
      groupHeading.innerHTML = `<div><h4>${esc(deviceGroupLabel(key))} <span class="device-count">${group.length}</span></h4><p>${groupEnabled} 台启用${groupPending ? ` · ${groupPending} 台待确认型号` : ''}</p></div>`;
      section.append(groupHeading);
      const groupList = document.createElement('div');
      groupList.className = 'device-list device-group-list';
      group.forEach(device => {
        const id = String(device.device_id || '');
        renderExternalDevice(groupList, device, stateById.get(id) || null, stateErrors.get(id) || null);
      });
      section.append(groupList);
      list.append(section);
    });
  }
  if (devicesResult.status === 'rejected') { const note = document.createElement('p'); note.className = 'device-console-status is-error'; note.textContent = `ESP32 设备列表读取失败：${devicesResult.reason.message}`; consoleHost.append(note); }
}

function watchDevicePull(deviceId, tries = 15) {
  // Registration configures the ESP32 first; its URL Rotation request may
  // arrive a few seconds later. Poll the persisted evidence briefly so the
  // operator sees the real transition without treating a browser request as
  // a device connection. The bounded watch avoids a background polling loop.
  if (state.devicePullWatch) state.devicePullWatch.cancelled = true;
  const watch = {deviceId: String(deviceId), cancelled: false};
  state.devicePullWatch = watch;
  const check = async remaining => {
    if (watch.cancelled) return;
    try {
      const snapshot = await json(`/api/admin/devices/${encodeURIComponent(watch.deviceId)}/state`);
      const status = String(snapshot?.pull_provision?.status || '').toLowerCase();
      if (status === 'pulled') {
        showNotice('ESP32 已完成真实拉图，设备现在已连通。');
        if (state.panel === 'devices') await loadDevices();
        return;
      }
    } catch (error) {
      // A transient refresh failure should not turn a successful registration
      // into an error; the card remains in its explicit awaiting state.
    }
    if (remaining > 0 && !watch.cancelled) {
      setTimeout(() => check(remaining - 1), 2000);
    } else if (!watch.cancelled && state.panel === 'devices') {
      showNotice('尚未收到 ESP32 拉图请求；请确认设备已唤醒并保存了服务器取图 URL。');
    }
  };
  check(tries);
}
async function loadConfig() { try { state.config = await json('/api/config'); const form = document.querySelector('#config-form'); Object.entries({timezone:state.config.timezone,'display.touchscreen_interval_seconds':state.config.display.touchscreen_interval_seconds ?? state.config.display.interval_seconds,'display.remote_refresh_seconds':state.config.display.remote_refresh_seconds ?? 30,'display.repeat_window':state.config.display.repeat_window,'display.orientation_mode':state.config.display.orientation_mode || 'auto','display.rotation':state.config.display.rotation ?? 0,'epaper.rotation_interval_seconds':state.config.epaper?.rotation_interval_seconds ?? 1800,'epaper.orientation_mode':state.config.epaper?.orientation_mode || 'auto','epaper.rotation':state.config.epaper?.rotation ?? 0,'weather.latitude':state.config.weather.latitude,'weather.longitude':state.config.weather.longitude,'weather.refresh_seconds':state.config.weather.refresh_seconds,'device.jpeg_quality':state.config.device.jpeg_quality,'device.poll_seconds':state.config.device.poll_seconds}).forEach(([name, value]) => { if (form.elements[name]) form.elements[name].value = value; }); form.elements['display.show_filename'].checked = state.config.display.show_filename !== false; form.elements['epaper.e6_dither'].checked = state.config.epaper?.e6_dither !== false; document.querySelector('#config-json').value = JSON.stringify(state.config, null, 2); renderFilenameWatermark(); armDisplayRefresh(); } catch (error) { document.querySelector('#config-status').textContent = error.message; } }
async function loadSystem() { try { document.querySelector('#system-status').textContent = JSON.stringify(await json('/api/health'), null, 2); } catch (error) { document.querySelector('#system-status').textContent = error.message; } }
function updateClock() { document.querySelector('#clock-label').textContent = new Intl.DateTimeFormat('zh-CN', {hour:'2-digit', minute:'2-digit'}).format(new Date()); }

document.querySelectorAll('[data-panel]').forEach(node => node.onclick = () => setPanel(node.dataset.panel));
document.querySelectorAll('[data-display-action]').forEach(node => node.onclick = () => controlDisplay(node.dataset.displayAction));
document.querySelector('#hero').addEventListener('pointerdown', event => { if (event.target.closest('button')) return; document.body.classList.remove('hero-idle'); armIdle(); });
document.querySelector('#close-panel').onclick = closePanel;
document.querySelector('#panel-scrim').onclick = closePanel;
document.querySelector('#reload-gallery').onclick = () => loadGallery(true);
window.addEventListener('resize', () => { clearTimeout(state.viewportTimer); state.viewportTimer = setTimeout(() => { const viewport = displayViewport(); if (state.current && viewport.key !== state.viewportKey) loadCurrent(false).catch(error => showError(error.message)); }, 250); });
document.querySelector('#search-form').onsubmit = async event => { event.preventDefault(); const status = document.querySelector('#search-status'); const grid = document.querySelector('#search-grid'); status.textContent = 'NPU 搜索中…'; grid.replaceChildren(); try { const form = event.currentTarget; const query = form.query.value.trim(); if (!query) { status.textContent = '请输入搜索内容'; return; } const value = await json('/api/search/text', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query, model:form.model.value, top_k:24})}); const results = Array.isArray(value.results) ? value.results : []; const topScore = results.reduce((best, item) => Math.max(best, Number(item.score) || 0), 0); status.textContent = results.length ? `${value.model_id} 返回 ${results.length} 项，最高相关度 ${(topScore * 100).toFixed(1)}%` : `${value.model_id} 暂无匹配照片`; renderPhotos(grid, results.map(item => ({...item, id:item.photo_id}))); } catch (error) { status.textContent = `搜索失败：${error.message}`; } };
function uploadSelection() {
  const allFiles = [
    ...document.querySelector('#upload-files').files,
    ...document.querySelector('#upload-folder').files,
  ];
  const extensions = new Set(['.jpg', '.jpeg', '.png', '.bmp', '.webp']);
  const files = allFiles.filter(file => extensions.has(file.name.slice(file.name.lastIndexOf('.')).toLowerCase()));
  state.uploadIgnored = allFiles.length - files.length;
  // Selecting the same file through both controls should not submit it twice.
  const seen = new Set();
  return files.filter(file => {
    const key = `${file.webkitRelativePath || file.name}\u0000${file.size}\u0000${file.lastModified}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderUploadSelection() {
  const files = uploadSelection();
  state.uploadFiles = files;
  const status = document.querySelector('#upload-selection-status');
  if (!files.length) {
    status.textContent = state.uploadIgnored ? `未找到支持的图片，已忽略 ${state.uploadIgnored} 个非图片文件。` : '可以选择照片，也可以选择整个文件夹。';
    return;
  }
  const folders = [...new Set(files.map(file => file.webkitRelativePath?.split('/')[0]).filter(Boolean))];
  const source = folders.length ? `文件夹：${folders.join('、')}` : '已选照片';
  const ignored = state.uploadIgnored ? ` · 忽略 ${state.uploadIgnored} 个非图片文件` : '';
  status.textContent = `${source} · 共 ${files.length} 张${ignored}；服务器会自动分批串行建立 NPU 索引。`;
}

document.querySelector('#upload-files').addEventListener('change', renderUploadSelection);
document.querySelector('#upload-folder').addEventListener('change', renderUploadSelection);

const clampProgress = value => Math.max(0, Math.min(1, Number.isFinite(Number(value)) ? Number(value) : 0));
const formatBytes = value => {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 1024 * 1024 * 1024 ? 1 : 2)} MB`;
};

function setUploadProgress({transfer, transferLabel, index, indexLabel, detail, failed = false}) {
  const group = document.querySelector('#upload-progress');
  group.hidden = false;
  group.dataset.state = failed ? 'failed' : 'active';
  if (transfer != null) document.querySelector('#upload-transfer-progress').value = Math.round(clampProgress(transfer) * 100);
  if (transferLabel != null) document.querySelector('#upload-transfer-label').textContent = transferLabel;
  if (index != null) document.querySelector('#upload-index-progress').value = Math.round(clampProgress(index) * 100);
  if (indexLabel != null) document.querySelector('#upload-index-label').textContent = indexLabel;
  if (detail != null) document.querySelector('#upload-progress-detail').textContent = detail;
}

function setUploadControls(disabled) {
  document.querySelectorAll('#upload-form input, #upload-form button').forEach(control => { control.disabled = disabled; });
}

function uploadBatch(data, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', '/api/photos/upload');
    request.responseType = 'text';
    request.upload.onprogress = event => {
      if (event.lengthComputable) onProgress(clampProgress(event.loaded / event.total));
    };
    request.onerror = () => reject(new Error('上传连接中断，请检查局域网连接后重试。'));
    request.onabort = () => reject(new Error('上传已取消。'));
    request.onload = () => {
      let value = null;
      if (request.responseText) {
        try { value = JSON.parse(request.responseText); } catch { value = null; }
      }
      if (request.status >= 200 && request.status < 300) {
        resolve(value || {});
        return;
      }
      reject(new Error(value?.detail || `上传请求失败：${request.status}`));
    };
    request.send(data);
  });
}

function modelName(modelId) {
  return ({
    mobileclip_s0__npu__mixed_fp16: 'MobileCLIP-S0',
    chinese_clip_rn50__npu__mixed_fp16: 'Chinese-CLIP RN50',
    resnet50_feature__npu__mixed_fp16: 'ResNet50',
  })[modelId] || modelId || '';
}

function jobProgressDetail(job) {
  const phase = job.phase || job.status || 'queued';
  if (phase === 'hashing') return `计算内容哈希 ${job.files_completed || 0}/${job.files_total || 0}`;
  if (phase === 'importing') return `导入受管图库 ${job.files_completed || 0}/${job.files_total || 0} · 新照片 ${job.accepted || 0} · 重复 ${job.duplicates || 0}`;
  if (phase === 'validating') return `校验图片 ${job.index_files_completed || 0}/${job.index_files_total || 0}`;
  if (phase === 'embedding') return `NPU 编码 ${modelName(job.current_model)} ${job.embedding_completed || 0}/${job.embedding_total || 0}`;
  if (phase === 'finalizing') return '保存 SQLite 与 FAISS 索引';
  if (phase === 'completed') return '索引完成';
  if (phase === 'failed') return '索引失败';
  return '排队等待单线程索引';
}

async function waitForUploadJob(jobId, onUpdate) {
  while (true) {
    const current = await json(`/api/jobs/${jobId}`);
    onUpdate(current);
    if (current.status === 'completed' || current.status === 'failed') return current;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
}

document.querySelector('#upload-form').onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.querySelector('#upload-status');
  const files = state.uploadFiles.length ? state.uploadFiles : uploadSelection();
  if (!files.length) {
    status.textContent = '请先选择照片或文件夹。';
    return;
  }
  // Keep each multipart request bounded while exposing no user-facing count
  // limit. The server's single index worker processes batches in order.
  const batchSize = 100;
  const totalBatches = Math.ceil(files.length / batchSize);
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  let transferredBytes = 0;
  state.uploading = true;
  setUploadControls(true);
  setUploadProgress({
    transfer: 0,
    transferLabel: `0 / ${formatBytes(totalBytes)}`,
    index: 0,
    indexLabel: '等待开始',
    detail: `准备处理 ${files.length} 张照片。`,
  });
  try {
    const allPhotoIds = new Set();
    for (let offset = 0; offset < files.length; offset += batchSize) {
      const batch = files.slice(offset, offset + batchSize);
      const batchNumber = Math.floor(offset / batchSize) + 1;
      const batchBytes = batch.reduce((sum, file) => sum + file.size, 0);
      const data = new FormData();
      batch.forEach(file => data.append('files', file, file.name));
      data.append('capture_time', document.querySelector('#upload-time').value);
      status.textContent = `正在提交第 ${batchNumber}/${totalBatches} 批（${batch.length} 张）…`;
      const job = await uploadBatch(data, fraction => {
        const uploaded = transferredBytes + batchBytes * fraction;
        setUploadProgress({
          transfer: uploaded / Math.max(1, totalBytes),
          transferLabel: `${formatBytes(uploaded)} / ${formatBytes(totalBytes)}`,
          detail: `第 ${batchNumber}/${totalBatches} 批正在传输 ${batch.length} 张照片。`,
        });
      });
      transferredBytes += batchBytes;
      setUploadProgress({
        transfer: transferredBytes / Math.max(1, totalBytes),
        transferLabel: `${formatBytes(transferredBytes)} / ${formatBytes(totalBytes)}`,
        detail: `第 ${batchNumber}/${totalBatches} 批已送达服务器，等待 NPU 索引。`,
      });
      const current = await waitForUploadJob(job.job_id, value => {
        const batchProgress = clampProgress(value.progress);
        const overallProgress = (offset + batch.length * batchProgress) / files.length;
        const detail = `第 ${batchNumber}/${totalBatches} 批：${jobProgressDetail(value)}`;
        status.textContent = detail;
        setUploadProgress({
          index: overallProgress,
          indexLabel: `${Math.round(overallProgress * 100)}%`,
          detail,
          failed: value.status === 'failed',
        });
      });
      if (current.status !== 'completed') throw new Error(current.error || '上传索引失败');
      for (const photoId of (current.photo_ids || current.summary?.photo_ids || [])) {
        allPhotoIds.add(Number(photoId));
      }
      const summary = current.summary || {};
      status.textContent = `第 ${batchNumber}/${totalBatches} 批完成：新增 ${summary.indexed || 0}，重复 ${summary.duplicates || 0}，跳过 ${summary.skipped || 0}`;
    }
    form.reset();
    state.uploadFiles = [];
    state.uploadIgnored = 0;
    renderUploadSelection();
    status.textContent = `全部完成：${files.length} 张已处理，${allPhotoIds.size} 张进入图库。`;
    setUploadProgress({
      transfer: 1,
      transferLabel: `${formatBytes(totalBytes)} / ${formatBytes(totalBytes)}`,
      index: 1,
      indexLabel: '100%',
      detail: status.textContent,
    });
    await loadCurrent();
    await loadGallery(true);
  } catch (error) {
    status.textContent = `上传失败：${error.message}`;
    setUploadProgress({indexLabel: '失败', detail: status.textContent, failed: true});
  } finally {
    state.uploading = false;
    setUploadControls(false);
  }
};
document.querySelector('#config-form').onsubmit = async event => { event.preventDefault(); try { const patch = merge(JSON.parse(document.querySelector('#config-json').value), formPatch(event.currentTarget)); patch.display = {...patch.display, show_filename: event.currentTarget.elements['display.show_filename'].checked}; patch.epaper = {...patch.epaper, e6_dither: event.currentTarget.elements['epaper.e6_dither'].checked}; patch.revision = state.config.revision; const response = await json('/api/config', {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(patch)}); delete response.restart_required; state.config = response; document.querySelector('#config-status').textContent = `配置已保存，revision ${state.config.revision}`; renderFilenameWatermark(); await loadCurrent(false); } catch (error) { document.querySelector('#config-status').textContent = error.message; } };
Promise.all([loadCurrent(), loadSystem(), loadWeather(), loadConfig()]).then(() => { document.querySelector('#health-label').textContent = 'NPU 服务正常'; updateClock(); setInterval(updateClock, 30000); state.weatherTimer = setInterval(loadWeather, 60000); armIdle(); }).catch(error => { document.querySelector('#health-label').textContent = '服务异常'; document.querySelector('#health-dot').classList.add('bad'); showError(error.message); });
