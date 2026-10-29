# 昇腾310B调试（dsh-plugin-ascend310b-debug）

目录和包名固定为 `dsh-plugin-ascend310b-debug`。这里的 `310b` 是芯片家族限定，不是泛化的 `Ascend 310`；旧名 `dsh-plugin-ascend310-debug` 已弃用，避免把插件误解为覆盖整个 Ascend 310 系列。

DSH 插件：面向**昇腾 310B**（Ascend 310B1 / 310B4）程序调试。它把两件事合到一起：

- **板端事实采集**：从真实开发板读出 SoC、npu-smi、CANN、驱动、Python/PyACL、设备节点、内核模块、声卡、播放/录音设备、USB 设备和 V4L2 摄像头，并给出**版本配套判定**（含证据级别）。当前 310B 板卡不为非特权 SSH 用户暴露可信固件版本，因此默认探测不采集固件版本；也不会用驱动号代替固件号。
- **分层调试路由**：把症状（`import acl` 失败、ATC 报错、OM 加载失败、VENC/DVPP、摄像头、MindSpore/MindIR、服务健康但推理失败、性能不达标……）映射到具体的检查顺序、停止条件、只读命令和教材样例入口。

面板名字固定为 **昇腾310B调试**，侧栏、面板标题和工具卡片一致使用这个全名，避免只写 `310B` 让人分不清是哪家芯片。

## 适用范围

插件里的调试程序、样例索引和版本配套表只针对 **昇腾 310B1 / 310B4**。这不是厂商兼容性结论，而是插件的设计边界：

| 探测结果 | `scope.applicable` | 行为 |
| --- | --- | --- |
| `Ascend310B1` / `Ascend310B4` | `true` | 正常给出调试计划与版本判定 |
| 其他 Ascend 芯片（如 `Ascend310P3`） | `false` | 面板红色提示、计划工具拒绝返回 310B 程序 |
| 非昇腾加速卡 | `false` | 同上，并明确写出检测到的芯片名 |
| 未读到 SoC | `unknown` | 提示先探测，不做任何适用性假设 |

其他品牌的开发板请使用对应芯片自己的工具链和文档，不要套用本插件。

它的核心约束只有一句：**插件不会断言任何没有从板上读到的板卡事实。**

配套的 [`昇腾310B程序开发、工程目录与文档规范`](docs/ascend310b-development-standard.md) 规定了 310B 程序的设计、ACL/ATC 合同、资源生命周期、仓库目录、样例工程、README、编号文档、证据记录和分层验收门。调试计划负责定位失败层，这份规范负责规定程序从第一行代码到交付证据应如何组织。

## 安装

源码位于教材仓库的 `tools/dsh-plugin-ascend310b-debug/`。安装前先在源码目录跑一次离线测试：

```powershell
cd <教材仓库根目录>\tools\dsh-plugin-ascend310b-debug
npm test
```

然后在仓库根目录执行安装器。它会 `npm pack` 生成带内容哈希的 tarball，通过 `dsh plugin add` 安装，并校验版本和文件内容；不要手工把源码目录复制或软链到 profile：

```powershell
cd <教材仓库根目录>
node tools/install-dsh-plugins.mjs --profile desktop
```

安装完成后，在 `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml` 中保留或更新本机 `dsh-plugin-ascend310b-debug` 配置块（**板卡地址只写在这里，不写进插件**）。如果已经有同 `id` 的插件行，只更新它的 `config`，不要重复添加：

```yaml
- id: dsh-plugin-ascend310b-debug
  name: dsh-plugin-ascend310b-debug
  config:
    repoRoot: <教材仓库根目录>
    sudoPassword: <默认 HwHiAiUser 密码>  # 仅 ascend_apt_install 使用；只放 profile
    defaultTarget: board-8t
    targets:
      - id: board-8t
        label: 开发板 1（8T / Ascend310B4）
        transport: ssh
        host: orangepiaipro.local       # 优先唯一主机名，不写固定 IP
        mac: <board-mac>                 # 可选但推荐：绑定物理板，避免同 hostname 误绑
        user: <SSH 用户>
        note: 8T 证据集；结果不与 20T 合并
      - id: board-20t
        label: 开发板 2（20T / Ascend310B1）
        transport: ssh
        host: orangepiaipro-20t.local
        mac: <board-mac>
        user: <SSH 用户>
        note: 20T 证据集；与 8T 分开记录
    discovery:
      cidrs: []                       # 默认每次扫描动态推导；需要时再填固定覆盖网段
      user: <SSH 用户>
      port: 22
      scanConcurrency: 64
      probeConcurrency: 4
      scanTimeoutMs: 350
      probeTimeoutMs: 10000
      # inventoryPath: <本地资产表；默认 DSH_HOME/ascend310b-inventory.json>
    serial:
      port: auto                    # 自动枚举 CH340/CH343/ttyUSB；也可显式填 COM14
      baudRate: 115200
      user: HwHiAiUser
      password: <串口密码>           # 只放本机 profile，不提交
      allowAutoLogin: true           # 明确允许 auto 模式向候选串口尝试登录
      timeoutMs: 60000
```

串口凭据只放在 profile 的本机配置中，不写进插件源码、skill、README 示例或 Git。`port: auto` 会枚举当前存在的 USB 串口；只有 profile 显式设置 `allowAutoLogin: true` 时才允许向候选串口逐个尝试登录。高安全环境应显式填写 `port: COM<N>` 或 `/dev/ttyUSB<N>`，避免密码被发送到无关适配器。`ascend_serial_probe` 会自动登录并执行只读网络探测；`ascend_serial_exec` 用于网络不可用时的单条只读诊断。串口工具只使用 `HwHiAiUser`，不提供 root 串口探测。

只需要本机解析探测记录时，可以省略 `targets`；需要 SSH 探测时再填板卡配置。动态 IP 环境应给每块板唯一的 mDNS/DNS 主机名，或使用 DHCP 保留地址；`ascend_discover` 会保存一块逻辑板卡及其全部物理网口 MAC 别名，并记录系统变化。主机名和 IP 只用于当前网络定位。

### 统一案例运行环境

所有案例统一使用 conda 环境：

```text
ascend
Python 3.9.25
CPython 3.9 aarch64 ABI
```

约束如下：

- 案例服务、测试、模型脚本和前端后端 Python 进程都使用 `ascend`。
- 禁止案例回退到 `base`；`base` 只用于开发板系统诊断和显式排障。
- 禁止 `pip install --user`；统一设置 `PYTHONNOUSERSITE=1`，不能依赖 `~/.local`。
- CANN、NPU driver、固件和系统工具仍由开发板系统提供，不复制进 conda。
- `ascend_probe` 检查环境是否存在、Python/ABI、user-site 隔离、PyACL 导入和关键包覆盖；`ascend_check` 单独输出统一环境结论。
- 8T 和 20T 使用相同环境名，但板端验收证据仍必须分开记录。
- 所有案例测试通过后才生成统一锁文件并清理旧环境；在此之前保留旧环境作为回滚。

当前开发板为 CANN 8.0。CANN 8.0.RC3 官方安装文档写明支持 Python 3.7.x 至 3.10.x；本项目为保持已经过板端验证的 MindSpore/MindNLP 组合，统一锁定 Python 3.9.25，不把“本机 3.11 可运行”扩大成官方兼容结论。

官方来源：[CANN 8.0.RC3 安装依赖（Debian 系列）](https://www.hiascend.com/doc_center/source/zh/CANNCommunityEdition/80RC3alpha003/softwareinst/instg/instg_0009.html)。

### 动态 IP、发现与多板

`ascend_discover` 只扫描私有 IPv4 网段，不会扫描公网。默认不配置固定 CIDR：每次扫描先根据当前默认路由确定实际联网的物理网卡，只扫描该网卡的真实 `/22`–`/30` 网段；WSL、Hyper-V、Docker 等虚拟网卡会被排除。这样换 WiFi、换实验室或换有线网络不需要改配置或重启，也不会因为同时插着 USB 网卡就扫描两个 `/24`。显式传入 `cidrs` 或配置 `discovery.cidrs` 时才固定扫描范围；单次调用参数优先级最高。发现器先探测 22 端口，再用和 `ascend_probe` 相同的只读脚本读取主机名、全部物理网口 MAC、板型、驱动、CANN 和 Python，然后按逻辑板卡 ID 写入本地资产表：

```text
DSH_HOME/ascend310b-inventory.json
```

同一块板换 IP、换有线/无线网口会归并为同一条记录；重装系统、驱动或 CANN 变化会写入 `changes`，但不会改变逻辑板卡 ID。发现到的板卡会以 target id（例如 `ascend-c0742bfddd7a`）出现在面板和 `target` 选择中，不需要手工把 IP 写进 `targets`。**多块板可以有相同 hostname**，例如两台出厂镜像都叫 `orangepiaipro`；hostname 只用于显示或回退，不能作为物理板身份。`machine-id` 也不能作为唯一身份，因为克隆镜像可能相同。资产表保留所有物理网口 MAC 别名，并用逻辑板卡 ID 区分不同板卡；资产表包含 MAC，禁止提交到 Git 或放进插件包。固件版本不进入资产指纹：当前板端没有可信、可读的版本来源。

板卡身份规则：

- **同一块板**：观察到至少一个已知物理网口 MAC 别名，或用户在资产表中明确建立别名绑定。换 IP、换 WiFi、换有线网口、重装同一镜像或升级 CANN 时，逻辑 ID 不变；OS/hostname/machine-id 变化只记录为系统变化。
- **不同开发板**：没有共享物理网口 MAC 别名，且没有显式绑定。即使 hostname、machine-id、板型和 OS 完全相同，也必须保留为不同板卡。
- **无法判断**：所有已知 MAC 都变化、网卡被更换，且没有可验证的板级唯一序列号时，不能自动猜测是同一块板。此时保留新记录，需要用户明确绑定；`hisi,boardid` 等值只能作为辅助证据，不能在未验证唯一性前单独当身份键。


如果某块已配置的板没有开机，或者当前网段找不到它的主机名，`ascend_probe` 对那个 target 返回 `offline/unreachable`，并明确“未采集到 SoC、CANN、驱动”。这只是网络电源事实，不能写成 310B 不支持，也不能拿另一块在线板的结果替代。网络迁移后直接重新扫描即可；如果开发板接在非默认路由的有线副网卡上，临时调用 `ascend_discover cidrs=[...]`，不要把临时网段写死到 profile。

调用示例：

```text
ascend_discover
ascend_discover cidrs=["10.42.0.0/24"]
```

发现到的板卡可直接作为 target：

```text
ascend_probe target=ascend-c0742bfddd7a
```

对 10 块以上开发板，建议每块板第一次通过串口设置唯一主机名，之后用当前活动网段做周期扫描；如果实验室有多个固定网段，再在 `discovery.cidrs` 中显式列出。网络找不到时，先用 `ascend_serial_probe` 从串口读取当前 IP，再回到 SSH。串口工具只执行只读网络/诊断命令，不做网络配置修改。

4) 技能文件不会被插件自动发现，需要单独放置。插件当前包含 14 个 310B skill，完整清单见 [`tools/README.md`](../README.md)。命名规范固定为：目录名、frontmatter `name` 和文档引用必须完全一致，全部使用 `ascend310b-` 前缀；专项调试 skill 使用 `ascend310b-<domain>-debug`，其中设备专项固定为 `ascend310b-camera-debug`、`ascend310b-microphone-debug`、`ascend310b-speaker-debug`。

案例测试使用 `ascend310b-case-testing`：程序从 GitHub 固定 commit 在板端检出，模型从 Hugging Face 固定 revision（默认 `hf-mirror.com`）下载；不能把作者电脑上的模型、ONNX/OM、数据集或未提交源码通过 `scp`/`rsync` 复制后当作发布证据。离线包只能标为 `offline-bundle`，并附带来源、revision、字节数和 SHA-256。

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.dsh\skills" | Out-Null
Copy-Item -Path "$plugin\skills\*" `
  -Destination "$env:USERPROFILE\.dsh\skills" `
  -Recurse -Force
```

5) 重启桌面应用（host 半边的工具需要重启后才会注册；浏览器面板刷新即可）。

SSH 采用**公钥认证**（`BatchMode=yes`），不会读取或保存密码。首次连接使用 `StrictHostKeyChecking=accept-new`；更严格的环境应先手工核对并写入目标板指纹。板端首次配置见教材附录 1 的 SSH 免密章节。

升级时重复第 1 步和第 5 步即可；源码目录中的 `README.md`、`docs/`、`test/`、`scripts/` 和 `data/` 会一起复制，方便使用者在本地复核。许可证与审核记录见 [`LICENSE`](LICENSE) 和 [`docs/installation-audit.md`](docs/installation-audit.md)。

Linux/macOS 的 profile 路径是 `$DSH_HOME/profiles/desktop/node_modules/dsh-plugin-ascend310b-debug`，等价复制命令为：

```bash
plugin="<教材仓库根目录>/tools/dsh-plugin-ascend310b-debug"
dest="$DSH_HOME/profiles/desktop/node_modules/dsh-plugin-ascend310b-debug"
mkdir -p "$dest"
cp -R "$plugin"/. "$dest"/
```

两个 PowerShell 验证脚本仍只在 Windows 上运行；其他平台先跑 `npm test`。

## 工具

| 工具 | 作用 | 关键参数 |
| --- | --- | --- |
| `ascend_probe` | 在目标板执行**只读**探测脚本并解析 | `target`、`rawText`（解析已有记录）、`timeoutMs` |
| `ascend_discover` | 扫描私有网段、按逻辑板卡 ID 和多网口 MAC 别名建资产表并记录驱动/CANN/系统变化 | `cidrs`、`user`、`port`、`concurrency`、`timeoutMs`、`inventoryPath` |
| `ascend_serial_probe` | 以 HwHiAiUser 串口登录后读取主机名、IPv4、接口、MAC 和默认路由，并自动尝试 SSH 写入设备列表 | `port`、`baudRate`、`timeoutMs` |
| `ascend_serial_exec` | 以 HwHiAiUser 执行一条只读诊断命令 | `command`、`port`、`baudRate`、`timeoutMs` |
| `ascend_check` | 用 `rawText` 或现场探测得到环境报告 | `target`、`rawText`、`repoRoot` |
| `ascend_debug_plan` | 按症状/领域生成分层调试计划 | `symptom`、`area`、`target`、`rawText`、`maxEntries` |
| `ascend_sample_index` | 检索 `samples/` 里的样例入口与测试命令 | `query`、`archetype`、`limit`、`repoRoot` |
| `ascend_board_exec` | 执行**单条只读**诊断命令 | `command`、`target`、`run`、`timeoutMs` |
| `ascend_camera_capture` | 从 V4L2 摄像头读取非空帧，优先 OpenCV、缺少 `cv2` 时回退 ffmpeg，输出实际尺寸/FourCC/FPS、JPEG 与哈希 | `target`、`device`、`width`、`height`、`pixelFormat`、`frameCount`、`allowStateChange`、`dryRun` |
| `ascend_microphone_record` | 从 ALSA 或 PulseAudio/PipeWire source 按请求秒数录制 PCM16 WAV，返回实际 WAV 时长、采集耗时、总耗时、RMS、峰值、全零和削波比例 | `target`、`backend`、`device`、`sampleRate`、`channels`、`durationSec`、`allowStateChange`、`dryRun` |
| `ascend_speaker_test` | 生成低幅度正弦 WAV，通过 ALSA 或 PulseAudio/PipeWire sink 播放；人耳结论保持未确认 | `target`、`backend`、`device`、`frequency`、`durationSec`、`allowStateChange`、`dryRun` |
| `ascend_play_recording` | 通过已确认的 ALSA/Pulse sink 回放插件刚录制的麦克风 WAV；只接受固定录音目录中的路径 | `target`、`backend`、`device`、`recording`、`allowStateChange`、`dryRun` |
| `ascend_board_run` | 显式执行下载、ATC、Python、pytest、msame 或教程脚本 | `command`、`cwd`、`target`、`allowStateChange`、`environment`、`dryRun`、`timeoutMs` |
| `ascend_apt_install` | 通过 `HwHiAiUser + sudo` 安装受控 apt 软件包，密码仅走 SSH stdin | `target`、`packages`、`update`、`allowStateChange`、`dryRun`、`timeoutMs` |
| `ascend_model_download` | 下载华为云、HF mirror 或 GitHub 网页代理资产，支持临时文件原子替换和可选 SHA-256 校验 | `source`、`url`/`repoId`、`filename`、`destination`、`githubProxy`、`sha256`、`allowStateChange` |
| `ascend_ui_capture` | 用宿主机 Edge/Chrome 截取开发板前端页面 | `url`、`output`、`width`、`height`、`waitMs`、`allowStateChange` |

浏览器侧另有 **昇腾310B调试** 面板：顶部是当前板卡和探测动作，左侧是按逻辑板卡 ID 去重的开发板列表（同一块板的多个网口 MAC 显示为别名），右侧按“概览 / 摄像头 / 音频接口 / 开发板记录 / 调试 / 原始输出”分区。板卡名称统一规范为 `Orange Pi AI Pro 8T / 20T · <总内存> GB` 的形式，例如 `Orange Pi AI Pro 8T · 7.4 GB`；内存总量是固定硬件信息，CANN、驱动、健康状态和实时内存占用只放在详情字段，不进入设备名。概览同时显示 SoC、健康/温度、NPU 已用/总内存、CANN、驱动和外设计数；摄像头页提供带有设备名称、`/dev/videoN`、Video Capture/Metadata/多平面能力标识的 V4L2 枚举与单帧采集，采集成功后直接在固定预览框中显示 JPEG 并支持打开原图；像素格式、分辨率和 FPS 下拉均来自该节点 `v4l2-ctl --list-formats-ext` 的实际输出，并完整列出所有报告的格式、尺寸和帧率，metadata-only 节点会明确禁用；音频接口页在同一屏左侧显示麦克风输入、右侧显示扬声器输出，下方录音回放直接使用右侧当前 Sink，不需要切换模式。麦克风和扬声器可以独立测试，不存在强制先后顺序。麦克风设备优先排列 USB/摄像头等外接输入；板载 capture 节点仍会列出，但不会因为排在最前就被默认使用。输入设备打不开时会返回具体 ALSA/Pulse 错误和设备名。麦克风录音完成后，可直接通过当前扬声器设备回放这段 WAV，再进行人工确认；如果回放无声，再单独检查扬声器和输出路由。枚举区同时列出 ALSA card/device 或 Pulse source/sink 名称、Pulse card/profile 和蓝牙配对/连接详情。蓝牙设备先在系统侧完成配对/连接，插件从 PulseAudio/PipeWire 已连接的 source/sink 进行录音或播放，不自动配对。开发板记录页显示当前选中板的身份、IP/MAC、内存、软件变化和串口状态，左侧列表负责选择与展开历史，避免同一份全量列表重复出现；调试页放症状、样例和按 case 汇总的系统程序依赖；原始页放完整事实、原始探测和仓库版本组合。调试页的系统程序区只显示文档记录的 apt/Python/设备/用户组依赖和 `ascend_apt_install` dry-run 提示，不会从浏览器自动提权安装。面板在探测之前所有版本字段显示**未采集**；探测到非 310B 芯片时，面板顶部会明确写出“不适用”。

面板启动时直接读取**最近一次保存的设备信息**，不会自动反复执行整板探测。更新动作按项目需要分开：

- “检查在线”：只检查当前开发板 SSH 端口是否可达，不读取 CANN、驱动或外设。
- “更新系统信息”：只更新硬件、操作系统、NPU、CANN、驱动和 ATC 事实，不执行 Python/conda 环境导入。
- “更新摄像头信息”：只更新 V4L2/USB 设备、格式、分辨率和 FPS。
- “更新音频接口信息”：只更新 ALSA、PulseAudio/PipeWire 和蓝牙事实。

摄像头、音频和 USB 卡片分别显示所属类别的更新时间，系统刷新不会把外设旧数据伪装成刚更新。刷新采用固定高度状态槽，在线检查中的旧结果会明确标为“上次检测”，不会加删板卡行或推挤主工作区。

左侧点击任一开发板只切换该板的历史报告，不会因为历史板离线而启动阻塞式 SSH 探测。绿色只表示**本次 SSH 可达**，红色表示本次不可达，灰色表示尚未检测。历史变更显示为独立的“已变更”徽标，不再覆盖在线颜色。点“串口发现”会通过 profile 的串口登录读取当前板卡主机名、IPv4、接口、MAC 和默认路由，并自动尝试 SSH 探测后写入设备列表；点“局域网扫描”默认只扫描当前默认路由所在物理网段，profile 的 `discovery.cidrs` 非空时才作为固定覆盖，并在结果中显示实际 CIDR、SSH 开放数和记录文件路径。

### HTTP 路由

面板使用十四条路由：`GET /ascend310b-debug/report.json` 返回最近一次探测形成的报告；带 `?fresh=1` 时绕过 120 秒缓存，但仍不执行板端探测。`POST /ascend310b-debug/liveness` 并行检查 target 的 SSH 端口，只返回本次传输可达性；`POST /ascend310b-debug/probe` 执行整板环境探测；`system-refresh`、`camera-refresh`、`audio-refresh` 分别只刷新系统、V4L2/USB 和 ALSA/Pulse/Bluetooth facts，`POST /ascend310b-debug/discover` 执行局域网扫描，`GET /ascend310b-debug/camera-preview` 只返回固定目录中的 `camera-*.jpg`，`POST /ascend310b-debug/camera-capture`、`microphone-record`、`speaker-test`、`play-recording` 分别执行固定外设动作，录音和回放证据位于 `/tmp/ascend310b-debug/peripherals`，`POST /ascend310b-debug/bind` 将由用户明确确认的发现板绑定到已有配置，`POST /ascend310b-debug/serial-probe` 执行串口发现并尝试 SSH 写入开发板记录。面板右上角的“检查在线”“串口发现”“局域网扫描”，以及系统、摄像头、音频页内的分类更新按钮调用这些路由。

### 面板预览

`data/panel-preview.html`、`.svg`、`.png` 是用一次真实 8T 探测记录渲染的面板预览，方便在没有打开 DSH 时检查字段与措辞。重新生成：

```powershell
node scripts/render-panel-preview.mjs <探测记录文件> data/panel-preview.html data/panel-preview.svg
```

## 只读诊断与任务执行

`ascend_board_exec` 仍然是严格只读工具。它只接受白名单内的诊断程序，并拒绝管道、链式命令、重定向、命令替换、包安装、服务重启和设备写操作；`target: local` 会被拒绝，避免把控制器主机当开发板读取凭据。`run: false` 只校验并返回等价调用，不执行。

教程下载、模型转换和样例运行使用独立的 `ascend_board_run`：

- 默认 `environment: "ascend"` 会激活统一环境、设置 `PYTHONNOUSERSITE=1`、加载 conda 和 CANN；环境不存在时任务直接失败，不会静默回退 `base`。显式设为 `"base"` 只用于系统诊断，`"none"` 用于裸 SSH shell；
- 必须显式传入 `allowStateChange: true`；
- 默认拒绝 `sudo` 和递归/强制删除，分别在需要时传入 `allowPrivileged`、`allowDestructive`；这些开关是命令策略提示，不是 OS 沙箱，不能把任意脚本变成安全脚本；
- `ascend_board_run` 只接受显式 `allowStateChange` 且拒绝 local target、编码后管道、`su`/`doas`/`pkexec`、重启和直接写块设备；仍应在隔离的开发板账户和已知教程脚本上使用；
- 仍拒绝 `npu-smi set/reset/upgrade`、重启、关机和直接写块设备；
- 支持 `source`、`conda activate`、`python`、`pytest`、`bash`、`atc`、`msame`、`wget` 和 `curl`；
- `dryRun: true` 只显示解析后的命令，不执行。

`ascend_model_download` 的下载路由：

- 华为云 OBS：保留原始 `huaweicloud.com` / `myhuaweicloud.com` URL，直接下载；
- Hugging Face：把请求解析到 `https://hf-mirror.com/<repo>/resolve/<revision>/<file>`；
- GitHub：使用网页代理，默认 `https://gh-proxy.com/https://github.com/...`，可通过 `githubProxy` 或 profile 的 `config.githubProxy` 替换；
- 不把模型二进制、ONNX、OM 或数据集写进插件源码或 Git。

调试计划中出现的 `source`、`python -c`、`atc`、`conda activate`、`wget` 和管道命令是教程实际步骤；只读工具不会执行它们，必须改用 `ascend_board_run`，并在 `dryRun` 通过后显式运行。

## 前端截图

`ascend_ui_capture` 在宿主机用 Edge/Chrome 无头模式访问开发板 HTTP 页面并保存 PNG：

- 先用 `ascend_board_run` 启动并确认页面可访问；
- 传 `url`、`output`、`allowStateChange: true`；
- 默认视口 `1440x900`，可指定 `width`、`height`、`waitMs`；
- 输出必须在插件 `artifactRoot`（默认当前工作区）内且以 `.png` 结尾；
- 返回截图路径、尺寸、字节数、SHA-256、浏览器和耗时；空白或旧截图不会被当作本次证据。

这解决 chapter/case 的“运行日志有了，但前端交互结果没有证据”的问题；截图不替代 API/健康检查，二者都要保存。

## 版本配套判定

`pairing.verdict` 共五种，全部带证据级别：

| verdict | 含义 | 证据 |
| --- | --- | --- |
| `observed-match` | 探测到的组合与教材中已记录的某一组一致 | `observed-pass` |
| `documented-pairing` | CANN 发布线与所查 HDK 线配套 | `documented` |
| `documented-unpaired` | 发布线已收录，但驱动 major.minor 不在记录的 HDK 线内 | `inferred` |
| `undocumented-cann` | CANN 不在收录的发布线内，需查版本配套表 | `untested` |
| `incomplete` | 缺 CANN 或驱动，不做判定 | `untested` |

**"没有记录"永远不等于"不支持"。** 这是插件的核心区分。

## 依赖与端口

- 主机：Node.js ≥ 18（仅用 `node:` 内置模块与 DSH 提供的 `@deepseek-ai/dsh-tools`）。串口自动发现/登录在 Windows 使用系统 `powershell.exe`；Linux/macOS 需要安装 PowerShell 7（`pwsh`），否则网络探测和只读工具仍可用，但串口发现会明确失败。
- 板端：只要能 SSH，且具备 `npu-smi`/CANN/驱动；探测脚本用 `bash` 执行，全部只读。
- 本机没有 NPU：本机只能做语法、解析与规划测试。**不要在本机运行 CANN/ATC/PyACL/OM/npu-smi/DVPP。**

## 探测环境差异：`shell-environment-only`

非交互 SSH 默认不会 source CANN，`import acl` 与 `atc` 在那里失败并不代表板卡有问题。探测脚本因此把这两项**跑两遍**：

| 事实键 | 含义 |
| --- | --- |
| `aclImport` / `aclImportSourced` | 基础探测中 source `set_env.sh`（有 conda 时先 `conda activate base`）后的系统结果 |
| `aclImportBare` | 裸 SSH shell 的结果；与 sourced 相反时保留下来，并生成 `aclImportNote` |
| `ascendEnvPresent` / `ascendEnvPrefix` | 统一 `ascend` 环境是否存在及其绝对路径 |
| `ascendEnvVersion` / `ascendEnvAbi` | 必须为 Python `3.9.25` / `cpython-39-...` |
| `ascendEnvIsolated` | 直接运行解释器时的 user-site 状态，仅作诊断 |
| `ascendEnvPolicyIsolated` | 以案例启动策略设置 `PYTHONNOUSERSITE=1` 后必须禁用 user-site |
| `ascendEnvAclImport` | 在 `ascend` 内 source CANN 后导入 PyACL 的结果 |
| `ascendEnvAclruntime` | 在 `ascend` 内 source CANN 后导入 `aclruntime` 的结果 |
| `ascendEnvMindsporeContext` | 在 `ascend` 内实际设置 MindSpore `device_target=Ascend` 的结果；仅找到包文件不算通过 |
| `ascendEnvPackages` | 统一案例依赖的关键模块覆盖情况，包括 FAISS、Torch、aiortc、PySide6、USB 和 MIDI |
| `atcOnPath` / `atcPath` | 从 `PATH` 或 `latest/bin/atc` 解析到的可执行文件；某些 CANN 构建不认 `--version`，此时以路径为证据 |

`/dev/davinci*`、`lsmod`、`npu-smi` 与版本文件不依赖 shell 初始化，读不到就是板端事实缺失。

## 测试

```powershell
cd <教材仓库根目录>\tools\dsh-plugin-ascend310b-debug
npm test                                                       # 全部离线测试
node test/smoke.mjs <教材仓库根目录>                            # 含样例索引与 HTTP 路由
node scripts/generate-sample-index.mjs <教材仓库根目录>         # 重新生成样例索引
node scripts/live-probe.mjs <user@host>                         # 直接对真实板卡跑一次只读探测
powershell -File scripts/verify-profile.ps1 -RepoRoot <仓库>    # 用一次性 profile 启动并验证插件
powershell -File scripts/check-patch-compose.ps1                # 验证 profile patch 能被解析并生效
```

`npm test` 覆盖：解析、版本判定、适用范围（310B 内/外/未确认）、调试计划、样例检索（含中文症状）、只读命令白名单与绕过回归、显式任务执行策略、HF/GitHub/Huawei 下载 URL 归一化、浏览器半装配、面板渲染（含非 310B 拒绝提示）、可移植性与冒烟路由。

**主机半改动需要重启 DSH 才会生效。** 桌面 Electron profile 由应用托管，`dsh --profile desktop` 会拒绝执行，所以改动后用 `verify-profile.ps1` 先在一次性 profile 里验证；浏览器半会热更新，主机半（工具与路由）要重启应用。重启前可以用 `live-probe.mjs` 直接确认板端探测结果。

命名约定：

- 板卡档位：`Ascend310B1` → 20T，`Ascend310B4` → 8T（`documented`）。
- 证据词汇：`documented`、`inferred`、`observed-pass`、`observed-fail`、`untested`。
- 档位与 IP 无关：结果按 **SoC + 档位** 归档，不按板卡名或地址合并。
