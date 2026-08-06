# WebUI 触摸屏终审与实机压测

本文记录 2026-08-04 在 `ascend8t` 上完成的 Case3 WebUI 最终验收方法、
判定阈值和实测结果。它既是发布候选版的测试记录，也可作为以后修改前端、
音频路由或 DDSP-VST Effect 后的复测步骤。

> **结果范围。** 本文中的性能数据、资源哈希和原始证据属于 2026-08-04 的发布候选版。
> 后续实时演奏已收敛为一个 Piano-DDSP 会话及两个输入视图，因此“DDSP-VST Synth”、
> “MIDI 文件抽屉”和五项顶层导航的旧描述不再适用。当前界面与 12 张实机截图见
> [WebUI 操作、部署与 API](02-webui.md)；下次发布前应完整复跑本文第 4-10 节，而不是沿用
> 本页的历史分位数。

测试只使用一个 FastAPI 服务和一个 Firefox kiosk。前端在开发电脑构建，
开发板不安装 Node/npm；OM 推理、PulseAudio 双工和物理屏幕截图只在真实
Ascend 310B 开发板上执行。

## 1. 验收结论

| 项目 | 结果 | 关键数据 |
| :--- | :--- | :--- |
| Python 单元测试 | 通过 | 256 passed，1 skipped |
| 前端单元测试 | 通过 | 59 passed |
| 前端生产构建 | 通过 | Vite 1597 modules transformed |
| 四视口 Playwright | 通过 | 31 passed，3 个实机专用用例按设计跳过 |
| 实机控件逐项审计 | 通过 | 17/17 步通过，浏览器、HTTP 和资源泄漏错误为 0 |
| 100 轮 UI soak | 通过 | 1520 次计时操作，p95 75.314 ms，p99 91.615 ms |
| 5 分钟 API 负载 | 通过 | 3000/3000 成功，p95 276.066 ms，p99 692.940 ms |
| DDSP-VST 600 秒双工 | 链路通过 | 600.035 s，29983 帧，模型合计 p95 12.331 ms |
| 声学输入资格 | 未确认 | 没有确认独立单音声源，不能把现场输入记为合格声学刺激 |
| 资源释放 | 通过 | Effect 和实时演奏均停止，`active_owner` 为空 |
| 资产保护 | 通过 | 压测前后 MIDI 11 个、WAV 6 个 |

DDSP-VST 的计算、调度和音频安全指标全部通过，但整项报告的
`qualification.passed` 为 `false`。这是有意保留的严格结论：现场没有确认
摄像头前的声源独立于 EDIFIER 输出，程序指标不能替代声学输入资格或听感验收。

## 2. 测试环境

### 2.1 开发电脑

- 工作目录：`D:\Github\Ascend310\samples\case3`
- 测试发起端：Windows PowerShell
- 前端工具：Node/npm、Vitest、Vite、Playwright、Microsoft Edge Chromium
- Python 测试入口：当前 case3 开发环境中的 `python -m pytest`
- 负载发起地址：`http://192.168.1.90:8765`

### 2.2 开发板

| 项目 | 实测值 |
| :--- | :--- |
| SSH 名称 | `ascend8t` |
| 主机名 | `orangepiaipro` |
| IPv4 | `192.168.1.90` |
| 平台 | Ascend 310B4，aarch64，Linux 5.10.0+ |
| Python | `/usr/local/miniconda3/bin/python` 3.9.2 |
| pytest | 8.4.2 |
| Web 服务 | `python scripts/run_webui.py`，唯一端口 8765 |
| 图形会话 | 唯一 Firefox kiosk，`http://127.0.0.1:8765` |
| 生产前端 | `webui/dist-releases/dist-20260804044625-feacf81-touch-audit` |
| 物理屏幕 | 1920x1080；浏览器内容视口约 1920x969，启用触摸 |
| DDSP-VST 输入 | UGREEN Camera 1080P Analog Stereo |
| DDSP-VST 输出 | EDIFIER M16 Pro |
| DDSP-VST 后端 | Feature `acl/om`，Control `acl/om` |
| Control OM | 11 个可选音色 |

`npu-smi` 在该板上报告 `Health: Alarm`。本项目把它显示为警告，而不是直接判定
推理失败；本次真实 OM 推理连续运行 600 秒，因此 NPU 可用性以实际推理结果为准。

### 2.3 发布资源校验

最终页面实际返回的资源与本地 `webui/dist` 哈希一致：

| 文件 | SHA256 |
| :--- | :--- |
| `assets/index-C4-rDt2Y.css` | `47030ccbce17861862ab8fde0419dbfa09eb9918f280c8001e596756fb516554` |
| `assets/index-Djf6Uytk.js` | `0af6389b73671b0e5300b63986dff55ade70e4c1abd3879f68b00c42a0896f9d` |

验收结束时开发板只有一个 case3 Python 进程和一个 kiosk 主进程。WebSocket
`/api/v1/events` 能立即返回 `snapshot`，`GET /api/v1/status`、Effect 目录和静态资源
均可正常访问。

## 3. 前置检查

### 3.1 本地依赖和生产包

```powershell
python -m pip install -r requirements.txt
cd webui
npm ci
npm run build
cd ..
```

`npm ci` 和 `npm run build` 只在开发电脑执行。开发板只接收 `webui/dist` 和 Python
运行包，不运行 Vite，也不开放第二个前端端口。

### 3.2 板端状态

部署前先确认实时演奏、扬声器测试、输入测试和 Effect 都已停止：

```powershell
Invoke-RestMethod http://192.168.1.90:8765/api/v1/status |
  Select-Object active_owner,realtime,ddsp_vst_effect
```

还应确认：

- `active_owner` 为空；
- `realtime.state` 和 `ddsp_vst_effect.state` 都是 `stopped`；
- Effect 目录返回 11 个 Control OM、1 个物理输入和至少 1 个输出；
- Feature 和 Control 后端都报告 `acl/om`；
- UGREEN 是实体 `capture`，不是 PulseAudio monitor；
- EDIFIER 是明确选择的输出，不允许静默改用其他设备。

### 3.3 资产和进程基线

本次验收前的基线为：

```text
case3 Python 服务进程：1
Firefox kiosk 主进程：1
MIDI 文件：11
WAV 文件：6
active_owner：空
```

计数命令只读取文件，不执行清理：

```bash
find midi -maxdepth 1 -type f \( -iname '*.mid' -o -iname '*.midi' \) | wc -l
find midi_wav -maxdepth 1 -type f -iname '*.wav' | wc -l
```

## 4. 本地测试与四视口回归

### 4.1 执行方法

从 case3 根目录执行：

```powershell
python -m pytest -q

cd webui
npm run test
npm run build
npm run test:e2e
```

标准 Playwright 使用模拟 API，覆盖以下视口：

| 视口 | 用途 |
| :--- | :--- |
| 1920x969，触摸 | 10 英寸开发板主要验收视口 |
| 1366x768 | 桌面兼容性 |
| 1024x768 | 平板兼容性 |
| 390x844 | 手机底部导航与窄屏布局 |

### 4.2 检查内容

- 四项顶层导航均可打开，实时演奏内的触摸屏与 MIDI 键盘两种模式可切换；
- 触控演奏与 MIDI 键盘模式的钢琴均保留贴底的琴体边框；
- 触摸键在同一浏览器帧内按下和松开时，两个 MIDI 边沿都不会丢失；
- 两个触点可以同时按下不同琴键；
- MIDI-DDSP 只有一个可见钢琴卷帘，不恢复 waveform；
- DDSP-VST 的音色、输入门和效果子页均可用；
- 设备页 MIDI 列表覆盖 0、1、2 个端口，0 或 1 个端口时占满可用宽度；
- 音频输入测试状态显示中文“待机、启动中、测试中、停止中、已完成、失败”；
- 1920x969 下 DDSP-VST 没有页面级纵向滚动；
- 视口没有横向溢出、遮挡或不可触达控件；
- Canvas 不只检查 DOM，还读取像素确认非空。

### 4.3 结果

```text
Python: 256 passed, 1 skipped in 5.65 s
Vitest: 10 files passed, 59 tests passed
Vite: 1597 modules transformed, build succeeded
Playwright: 31 passed, 3 skipped in 18.7 s
```

三个跳过项是需要 `CASE3_LIVE_BOARD_E2E=1` 的真实板端目录测试、控件审计和 UI soak，
在后续实机步骤单独执行，不属于漏测。

## 5. 12 页面视觉审核

### 5.1 页面范围

历史候选版页面使用 1920x969 触摸上下文采集。当前手册将 2026-08-04 从 1920x1080
物理 kiosk 重新采集并复核的 12 张截图固化在 `doc/images/webui/`，依次覆盖实时演奏的
触摸屏与 MIDI 键盘、MIDI-DDSP 的音频库与新建渲染、DDSP-VST 的音色/输入门/效果，以及
设备概览、音频输出、音频输入、MIDI 与运行环境。原始候选版与物理 XFCE 截图继续保存在
`reports/webui/screenshots/` 作为历史证据。

### 5.2 尺寸判定

- 正文和主要控件文字目标为 16 px；次要文字不得小于 14 px；
- 主要动作按钮高度不得小于 56 px；
- 普通可点击按钮、select 和文本输入高度不得小于 52 px；
- 文档宽度不得超过视口宽度 1 px；
- 触控钢琴和 MIDI 可视键盘底边与视口底边误差不得超过 1 px；
- DDSP-VST `contentScrollHeight` 不得大于 `clientHeight + 1`；
- 有 Canvas 的页面至少有一个可见 Canvas 含有效像素；
- 页签必须具有 `tablist`、`tab` 和 `tabpanel` 语义；
- 状态不能只依赖颜色，必须有“待机、运行、故障”等文字。

MIDI 文件卷帘使用三层 Canvas。空闲时允许只有静态层含有效像素，不能错误地要求
所有三层在没有播放光标时都非空。

### 5.3 视觉结论

12 页均无横向溢出、重叠或文字截断；所有尺寸审计项通过。触控演奏和 MIDI 键盘
均保持钢琴贴底，DDSP-VST 三个子页都在一屏内。设备页的输出、输入、MIDI 和运行环境
职责分离清晰，NPU 仅显示结构化摘要和警告，没有直接堆叠 `npu-smi` 终端输出。

## 6. 100 轮 UI soak

### 6.1 执行方法

```powershell
cd webui
$env:PLAYWRIGHT_BASE_URL='http://192.168.1.90:8765'
$env:CASE3_LIVE_BOARD_E2E='1'
$env:CASE3_UI_SOAK_CYCLES='100'
$env:CASE3_UI_SOAK_REPORT='../reports/webui/stress/ui-soak.json'
npm run test:e2e:soak
```

当前复测每轮应切换 12 个视图：实时演奏的触摸屏与 MIDI 键盘、MIDI-DDSP 两个视图、
DDSP-VST 三个参数页，以及设备概览、音频输出、音频输入、MIDI 和运行环境。本文历史
候选版以 16 个计时步骤记录其页面结构，故保留 `95 x 16 = 1520` 个原始样本与分位数，
不将它直接用于当前 12 视图版本的性能比较。

每个操作的计时边界是“点击开始”到目标语义元素出现并完成两个
`requestAnimationFrame`。测试同时监听：

- `console.error`；
- 页面未捕获异常；
- 请求失败；
- HTTP 4xx/5xx；
- DOM 节点数；
- Chromium `performance.memory`。

浏览器以 `--js-flags=--expose-gc` 启动，基线和终点读取堆内存前各执行两次 GC，
避免把尚未回收的临时对象误判为泄漏。

### 6.2 阈值

| 指标 | 阈值 |
| :--- | :--- |
| 控制台、页面、请求、HTTP 错误 | 0 |
| 切换 p95 | `< 250 ms` |
| 切换 p99 | `< 500 ms` |
| 预热后 DOM 增长 | `<= 10%` |
| GC 后 JS heap 增长 | `<= 20%` |
| 横向溢出、尺寸、Canvas、页签语义 | 全部通过 |

### 6.3 最终结果

| 指标 | 实测值 |
| :--- | ---: |
| 计时样本 | 1520 |
| p50 | 50.212 ms |
| p95 | 75.314 ms |
| p99 | 91.615 ms |
| 最大值 | 216.248 ms |
| DOM | 485 -> 485，增长 0% |
| JS heap | 6092941 -> 7082163 bytes，增长 16.236% |
| 控制台错误 | 0 |
| 页面异常 | 0 |
| 请求失败 | 0 |
| HTTP 失败响应 | 0 |

最慢的几类操作仍明显低于阈值：

| 操作 | p95 | 最大值 |
| :--- | ---: | ---: |
| MIDI-DDSP 新建渲染 | 92.191 ms | 125.219 ms |
| 进入 MIDI 键盘 | 83.765 ms | 216.248 ms |
| 进入 MIDI-DDSP | 83.631 ms | 117.247 ms |
| 进入 DDSP-VST | 83.460 ms | 99.972 ms |

预检时 DDSP-VST 每次返回都会重新读取目录、状态并创建 WebSocket，使总体 p95 达到
约 333 ms。修复后该工作区首次打开后保留挂载状态，隐藏时 Canvas 不绘制，最终 p95
降到 75.314 ms。失败预检保留在 `ui-soak-preflight-final.json` 和
`ui-soak-diagnostic.json`，最终报告是 `ui-soak.json`。

## 7. 实机控件逐项审计

### 7.1 执行方法

该审计只在显式开启实机开关时运行，常规本地 E2E 会按设计跳过：

```powershell
cd webui
$env:PLAYWRIGHT_BASE_URL='http://192.168.1.90:8765'
$env:CASE3_LIVE_BOARD_E2E='1'
$env:CASE3_CONTROLS_AUDIT_REPORT='../reports/webui/stress/live-controls-audit-20260804.json'
$env:CASE3_CONTROLS_AUDIT_SCREENSHOT_DIR='../reports/webui/screenshots/live-controls-audit-20260804-final'
npm run test:e2e:live-controls
```

审计使用 1920x969 触摸上下文，逐步记录名称、耗时、失败截图、可用/禁用按钮、
滑块和下拉框清单。每个真实音频工作流都用 `try/finally` 停止；任一步失败时还会
调用状态和停止接口进行兜底清理，防止前一项失败造成后续资源冲突。

### 7.2 覆盖范围

| 工作区 | 实际操作 |
| :--- | :--- |
| 应用外壳 | 启动、全局刷新、四项顶层导航与实时演奏的两个输入模式 |
| 实时演奏 / 触摸屏 | Piano-DDSP 钢琴预设、共享滑块、2/4/8 秒卷帘、13/25 键、三档键盘大小、八度、启动、琴键、延音、录音、监听、Panic、停止 |
| 实时演奏 / MIDI 键盘 | 与触摸屏相同的 Piano-DDSP 会话、32/49/61/88 键、八度、实体 MIDI 端口、录音、监听与停止；没有 DDSP-VST Synth 或 MIDI 文件抽屉 |
| MIDI-DDSP | 曲目与版本切换、卷帘缩放/跟随/复位/折叠、浏览器循环与播放、开发板播放、上传选择器、渲染配置与自动建议 |
| DDSP-VST | 目录刷新、输入/输出/11 音色选择、三个参数页、全部滑块、OM 启动、输入校准、停止 |
| 设备 | 三个主页签、蓝牙刷新与扫描、输出/输入/MIDI 子页、扬声器声道/频率/音量/时长及短测试、麦克风阈值/时长及短测试 |

下列操作会改变持久资产或外部设备关系，因此不在自动逐项点击范围内：蓝牙连接、
断开、配对和信任；开始新的 MIDI-DDSP 渲染；实时录音；下载文件。测试仍验证
渲染开始按钮和录音按钮的可见/可用状态，并实际打开后取消文件选择器。

### 7.3 浏览器结果

| 指标 | 实测值 |
| :--- | ---: |
| 审计步骤 | 17 |
| 通过 / 失败 | 17 / 0 |
| 控制台错误 | 0 |
| 页面未捕获异常 | 0 |
| 请求失败 | 0 |
| HTTP 4xx/5xx | 0 |
| 最终 `active_owner` | 空 |

停止浏览器 WAV 播放时 Chromium 会主动取消尚未读完的 artifact 流；报告把这 1 次
`net::ERR_ABORTED` 单独记为 `expected_media_aborts`，不把用户主动停止媒体误报成
网络故障。普通 REST 请求被取消或返回失败仍会使测试失败。

### 7.4 xdotool 物理 kiosk 复核

先确认只有一个可见 Firefox 窗口，并激活、刷新它：

```powershell
ssh ascend8t "DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority xdotool search --onlyvisible --class firefox"
ssh ascend8t "DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority xdotool windowactivate --sync WINDOW_ID key --clearmodifiers ctrl+r"
```

在 1920x1080 kiosk 中再用 `xdotool mousemove --sync X 42 click 1` 依次点按实时演奏、
MIDI-DDSP、DDSP-VST 和设备，并在实时演奏内部切换触摸屏与 MIDI 键盘。四个工作区与
两个输入模式均正确切换，Canvas 非空，没有错位、遮挡或空白工作区；最终重新打开触摸
屏模式。证据文件为：

```text
physical-xdotool-touch-20260804.png
physical-xdotool-midi-20260804.png
physical-xdotool-midi-ddsp-20260804.png
physical-xdotool-ddsp-vst-20260804.png
physical-xdotool-devices-20260804.png
```

## 8. 5 分钟 API 负载

### 8.1 执行方法

```powershell
python tools/stress_webui_api.py `
  --base-url http://192.168.1.90:8765 `
  --duration-seconds 300 `
  --requests-per-second 10 `
  --workers 20 `
  --timeout-seconds 10 `
  --output reports/webui/stress/api-load.json
```

负载由开发电脑发起，使用 20 个工作线程和全局 10 RPS 限流，只执行 GET，不启动、
停止或修改任何音频任务。14 个轮询端点为：

```text
/api/v1/status
/api/v1/catalog
/api/v1/audio-inputs
/api/v1/speaker-outputs
/api/v1/midi-ports
/api/v1/bluetooth-audio
/api/v1/ddsp-vst-effect/catalog
/api/v1/ddsp-vst-effect/status
/api/v1/realtime/catalog
/api/v1/realtime/status
/api/v1/speaker-test/status
/api/v1/audio-input-test/status
/api/v1/midi-ddsp/library
/api/v1/jobs
```

每个响应必须是 2xx 且正文可解析为 JSON。正式判定要求错误率为 0、p95 小于
500 ms、p99 小于 1 s，并且实际运行满 300 秒。

### 8.2 最终结果

| 指标 | 实测值 |
| :--- | ---: |
| 时长 | 300.157 s |
| 请求数 | 3000 |
| 成功 | 3000 |
| 错误 | 0 |
| 平均 | 147.070 ms |
| p50 | 101.974 ms |
| p95 | 276.066 ms |
| p99 | 692.940 ms |
| 最大值 | 6713.116 ms |

错误率、p95 和 p99 均通过。最大值不是判定分位数，但说明设备诊断目录仍可能出现
极少量长尾，后续若扩大并发应继续观察。

第一次使用 5 秒客户端超时的同等负载出现 3 个超时，分别落在 Effect 目录、
MIDI-DDSP 音频库和实时目录；服务日志显示这些请求最终都返回 200。该失败报告保存在
`api-load-5s-timeout.json`。正式测试把客户端截止时间调整为 10 秒，但没有放宽
p95 500 ms、p99 1 s 或错误率 0 的验收阈值。

## 9. DDSP-VST 600 秒实机双工

### 9.1 声学安全条件

推荐在 UGREEN 摄像头麦克风前提供独立单音声源。声源不能来自当前 EDIFIER 输出，
也不能让音箱声音直接反馈进摄像头麦克风。测试前保持：

- 实时演奏停止；
- 扬声器和输入测试停止；
- `active_owner` 为空；
- 输入固定为 UGREEN Capture；
- 输出固定为 EDIFIER M16 Pro；
- 音色固定为 Violin mixed-float16 OM；
- 输出增益使用默认 `-18 dB`；
- Feature 和 Control 后端均为 `acl/om`。

只有现场确实提供并确认独立单音声源时，才允许增加
`--confirm-independent-monophonic-stimulus`。本次没有该确认，因此没有使用该参数。

### 9.2 执行命令

```powershell
python tools/benchmark_ddsp_vst_effect.py `
  --base-url http://192.168.1.90:8765 `
  --duration-seconds 600 `
  --poll-interval-seconds 10 `
  --stimulus-description "现场未确认独立单音声源；仅验证真实 UGREEN 到 EDIFIER 的 OM 双工链路" `
  --output reports/webui/stress/ddsp-vst-effect-600s.json
```

工具先读取服务端目录并按名称选择 Violin、UGREEN 和 EDIFIER，再启动 Effect。
后端确认进入 `running` 后才开始 600 秒计时，每 10 秒采集一次完整状态。无论成功、
异常还是人工中断，`finally` 都会调用停止接口。

### 9.3 阈值

| 指标 | 阈值 |
| :--- | :--- |
| 实际 Effect 运行时长 | `>= 600 s` |
| 帧数 | 单调增加 |
| Feature p95 + Control p95 | `< 20 ms` |
| 总延迟 | `< 150 ms` |
| 采集溢出 | 0 |
| 播放欠载 | 0 |
| 削波样本 | 0 |
| 安全静音 | 不触发 |
| 物理输入、输出和有效 f0 | 测试期间至少一次非静音/有效 |
| 独立单音声源 | 必须由现场明确确认才通过 |

### 9.4 最终结果

| 项目 | 实测值 |
| :--- | :--- |
| UTC 时间 | 2026-08-04 03:36:24 至 03:46:24 |
| 实际 Effect 时长 | 600.035 s |
| 状态样本 | 61 |
| 最终帧数 | 29983 |
| 模型 | `Violin_mixed_float16.om` |
| Feature 模型 | `ddsp_vst_feature_mixed_float16.om` |
| Feature 最终 p95 | 10.985 ms |
| Control 最终 p95 | 1.347 ms |
| 模型合计最终 p95 | 12.331 ms |
| 最大观测 Feature p95 | 11.282 ms |
| 最大观测 Control p95 | 1.583 ms |
| 最终总延迟 | 123.7 ms |
| 最大观测总延迟 | 124.146 ms |
| 最大输入峰值 | -27.480 dBFS |
| 最大输出峰值 | -61.536 dBFS |
| 有效 f0 范围 | 32.815 至 664.461 Hz |
| 采集溢出 / 播放欠载 / 削波 | 0 / 0 / 0 |
| 安全静音 | 0 次 |
| 停止结果 | `state=stopped`，`running=false` |

除独立声源确认外，所有程序化检查通过。现场输入和输出在测试期间均出现非静音样本，
也观测到有效 f0，但这些数据不能证明声源独立性，也不能替代音色质量试听。

第一次 600 秒运行发现工具从启动请求前开始计时，OM 初始化占用了约 2.6 秒，导致
后端实际时长只有 597.436 秒。修复后计时起点移到服务确认 `running` 之后，并增加了
模拟 3 秒初始化的回归测试。旧报告保存在
`ddsp-vst-effect-600s-preclock-fix.json`，最终有效报告为
`ddsp-vst-effect-600s.json`。

## 10. 压测后检查

测试结束后必须再次读取总状态，并确认资源锁已释放：

```powershell
$status = Invoke-RestMethod http://192.168.1.90:8765/api/v1/status
$status.active_owner
$status.realtime.state
$status.ddsp_vst_effect.state
```

本次结果为：

```text
active_owner：空
realtime.state：stopped
ddsp_vst_effect.state：stopped
case3 Python 服务进程：1
Firefox kiosk 主进程：1
MIDI 文件：11
WAV 文件：6
```

静态资源 HTTP SHA256 与本地生产包一致，`/api/v1/status`、Effect 目录和
`/api/v1/events` WebSocket 均正常。最终 JSON 和 12 张截图已增量复制到开发板
`reports/webui/`，没有删除或覆盖 MIDI、WAV、模型、任务历史和转换日志。

## 11. 报告读取与复测规则

原始证据保存在本地且默认不提交 Git：

| 文件 | 用途 |
| :--- | :--- |
| `reports/webui/stress/ui-soak.json` | 最终 100 轮 UI soak |
| `reports/webui/stress/ui-soak-diagnostic.json` | DDSP-VST 重复加载问题的定位数据 |
| `reports/webui/stress/live-controls-audit-20260804.json` | 17 项真实控件操作、错误和最终资源状态 |
| `reports/webui/stress/api-load.json` | 最终 5 分钟 API 负载 |
| `reports/webui/stress/api-load-5s-timeout.json` | 5 秒客户端超时的失败证据 |
| `reports/webui/stress/ddsp-vst-effect-600s.json` | 最终 600 秒 Effect 报告 |
| `reports/webui/stress/ddsp-vst-effect-600s-preclock-fix.json` | 计时起点修复前的失败证据 |
| `reports/webui/screenshots/production-final/*.png` | 12 个真实生产页面 |
| `reports/webui/screenshots/touchscreen-final-1920x1080.png` | XFCE 物理屏幕证据 |
| `reports/webui/screenshots/physical-xdotool-*-20260804.png` | xdotool 实际点按四个工作区及实时演奏两种模式的物理截图 |

可用 PowerShell 读取摘要：

```powershell
$ui = Get-Content reports/webui/stress/ui-soak.json -Raw | ConvertFrom-Json
$api = Get-Content reports/webui/stress/api-load.json -Raw | ConvertFrom-Json
$effect = Get-Content reports/webui/stress/ddsp-vst-effect-600s.json -Raw | ConvertFrom-Json

$ui.navigation
$ui.memory
$api.summary
$effect.qualification.checks
```

以后修改以下区域时必须至少重跑对应测试：

- CSS、导航、工作区生命周期或 Canvas：四视口 E2E 和 100 轮 UI soak；
- 按钮、页签、滑块、播放或设备测试控制：实机控件逐项审计；
- API 目录、设备查询或状态聚合：5 分钟 API 负载；
- DDSP-VST 调度、OM、音频 FIFO、门限或输出安全：600 秒实机双工；
- MIDI 状态或触控琴键：快速触摸、双触点、短音可见性和悬挂音回归；
- 部署脚本或静态目录：SHA256、唯一进程、HTTP、WebSocket 和原子切换检查。

没有独立单音声源时，可以验证 OM 双工链路，但必须继续把声学输入资格标为未通过，
不得根据非静音输入或有效 f0 推断现场声源已经合格。
