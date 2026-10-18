# 昇腾310B调试（dsh-plugin-ascend310b-debug）

目录和包名固定为 `dsh-plugin-ascend310b-debug`。这里的 `310b` 是芯片家族限定，不是泛化的 `Ascend 310`；旧名 `dsh-plugin-ascend310-debug` 已弃用，避免把插件误解为覆盖整个 Ascend 310 系列。

DSH 插件：面向**昇腾 310B**（Ascend 310B1 / 310B4）程序调试。它把两件事合到一起：

- **板端事实采集**：从真实开发板读出 SoC、npu-smi、CANN、驱动、Python/PyACL、设备节点与内核模块，并给出**版本配套判定**（含证据级别）。当前 310B 板卡不为非特权 SSH 用户暴露可信固件版本，因此默认探测不采集固件版本；也不会用驱动号代替固件号。
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

源码位于教材仓库的 `tools/dsh-plugin-ascend310b-debug/`。桌面版 DSH 的 profile 由应用托管，插件以**文件拷贝**方式安装（不是软链、不走 pnpm）。安装前先在源码目录跑一次离线测试：

```powershell
cd <教材仓库根目录>\tools\dsh-plugin-ascend310b-debug
npm test
```

1) 复制插件到 desktop profile：

```powershell
$repo = "<教材仓库根目录>"
$plugin = Join-Path $repo "tools\dsh-plugin-ascend310b-debug"
$dest = "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\dsh-plugin-ascend310b-debug"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Path (Join-Path $plugin '*') -Destination $dest -Recurse -Force
```

2) 在 `%USERPROFILE%\.dsh\profiles\desktop\package.json` 的 `dsh.profile.bundles` 中加入 `dsh-plugin-ascend310b-debug`，保留已有 bundle：

```json
"dsh": {
  "profile": {
    "bundles": [
      "@deepseek-ai/dsh-base",
      "@deepseek-ai/dsh-web-app",
      "dsh-plugin-ascend310b-debug"
    ]
  }
}
```

3) 在 `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml` 末尾加入本机配置（**板卡地址只写在这里，不写进插件**）。如果已经有同 `id` 的插件行，只更新它的 `config`，不要重复添加：

```yaml
- id: dsh-plugin-ascend310b-debug
  name: dsh-plugin-ascend310b-debug
  config:
    repoRoot: <教材仓库根目录>
    defaultTarget: board-8t
    targets:
      - id: board-8t
        label: 开发板 1（8T / Ascend310B4）
        transport: ssh
        host: orangepiaipro.local       # 优先唯一主机名，不写固定 IP
        user: <SSH 用户>
        note: 8T 证据集；结果不与 20T 合并
      - id: board-20t
        label: 开发板 2（20T / Ascend310B1）
        transport: ssh
        host: orangepiaipro-20t.local
        user: <SSH 用户>
        note: 20T 证据集；与 8T 分开记录
    discovery:
      cidrs: ["10.0.0.0/24"]          # 只允许私有 IPv4；按实际局域网修改
      user: <SSH 用户>
      port: 22
      scanConcurrency: 24
      probeConcurrency: 4
      scanTimeoutMs: 500
      probeTimeoutMs: 60000
      # inventoryPath: <本地资产表；默认 DSH_HOME/ascend310b-inventory.json>
```

只需要本机解析探测记录时，可以省略 `targets`；需要 SSH 探测时再填板卡配置。动态 IP 环境应给每块板唯一的 mDNS/DNS 主机名，或使用 DHCP 保留地址；`ascend_discover` 会把发现的板卡按 MAC 写入本地资产表，并记录系统变化。

### 动态 IP、发现与多板

`ascend_discover` 只在显式给出的私有 IPv4 CIDR 中工作，不会扫描公网。它先探测 22 端口，再用和 `ascend_probe` 相同的只读脚本读取主机名、eth0 MAC、板型、驱动、CANN 和 Python，然后按 MAC 写入本地资产表：

```text
DSH_HOME/ascend310b-inventory.json
```

同一块板换 IP 会归并为同一条记录；重装系统、驱动或 CANN 变化会写入 `changes`。发现到的板卡会以 target id（例如 `ascend-c0742bfddd7a`）出现在面板和 `target` 选择中，不需要手工把 IP 写进 `targets`。资产表包含 MAC，禁止提交到 Git 或放进插件包。固件版本不进入资产指纹：当前板端没有可信、可读的版本来源。

调用示例：

```text
ascend_discover cidrs=["10.0.0.0/24"]
```

发现到的板卡可直接作为 target：

```text
ascend_probe target=ascend-c0742bfddd7a
```

对 10 块以上开发板，建议每块板第一次通过串口设置唯一主机名，之后用 `discovery.cidrs` 做周期扫描；串口不是持续发现通道，主要用于首次配置和网络故障恢复。当前版本实现的是网络发现，串口自动登录/探测仍需手工或后续扩展。

4) 技能文件不会被插件自动发现，需要单独放置。插件包含 `ascend310b-debug` 和 `ascend310b-cann-development` 两个 skill：

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
| `ascend_discover` | 扫描私有网段、按 MAC 建资产表并记录驱动/CANN/系统变化 | `cidrs`、`user`、`port`、`concurrency`、`timeoutMs`、`inventoryPath` |
| `ascend_check` | 用 `rawText` 或现场探测得到环境报告 | `target`、`rawText`、`repoRoot` |
| `ascend_debug_plan` | 按症状/领域生成分层调试计划 | `symptom`、`area`、`target`、`rawText`、`maxEntries` |
| `ascend_sample_index` | 检索 `samples/` 里的样例入口与测试命令 | `query`、`archetype`、`limit`、`repoRoot` |
| `ascend_board_exec` | 执行**单条只读**诊断命令 | `command`、`target`、`run`、`timeoutMs` |
| `ascend_board_run` | 显式执行下载、ATC、Python、pytest、msame 或教程脚本 | `command`、`cwd`、`target`、`allowStateChange`、`environment`、`dryRun`、`timeoutMs` |
| `ascend_model_download` | 下载华为云、HF mirror 或 GitHub 网页代理资产 | `source`、`url`/`repoId`、`filename`、`destination`、`githubProxy`、`allowStateChange` |
| `ascend_ui_capture` | 用宿主机 Edge/Chrome 截取开发板前端页面 | `url`、`output`、`width`、`height`、`waitMs`、`allowStateChange` |

浏览器侧另有 **昇腾310B调试** 面板：顶部是探测动作，左侧是按 MAC 去重的开发板列表，右侧按“概览 / 开发板资产 / 调试 / 原始输出”分区。概览只保留关键状态卡和版本配套；资产页显示完整资产和系统变化；调试页放症状和样例；原始页放完整事实、原始探测和仓库版本组合。面板在探测之前所有版本字段显示**未采集**；探测到非 310B 芯片时，面板顶部会明确写出“不适用”。

面板默认显示**上一次探测快照**：点“运行探测”执行一次只读探测，点“刷新”只重新读取报告缓存。左侧点击任一开发板会立即执行**一次**该板的只读探测并更新概览；即使没有开启实时监控，切换板子也会刷新当前板信息。需要持续观察板卡状态时，点“实时监控”开启 60 秒轮询；它每 60 秒重新执行一次只读探测并更新温度、内存、健康状态和版本字段，再点“停止实时”结束。点“发现开发板”会按 profile 中的 `discovery.cidrs` 扫描局域网，按 MAC 更新资产表和 target 列表。轮询是快照更新，不是采样率更高的板端指标流。

### HTTP 路由

面板使用三条路由：`GET /ascend310b-debug/report.json` 返回最近一次探测形成的报告；带 `?fresh=1` 时绕过 120 秒缓存，但仍不执行板端探测。`POST /ascend310b-debug/probe` 执行实时探测，`POST /ascend310b-debug/discover` 执行局域网发现；面板右上角的“运行探测”和“发现开发板”按钮分别调用它们。

### 面板预览

`data/panel-preview.html`、`.svg`、`.png` 是用一次真实 8T 探测记录渲染的面板预览，方便在没有打开 DSH 时检查字段与措辞。重新生成：

```powershell
node scripts/render-panel-preview.mjs <探测记录文件> data/panel-preview.html data/panel-preview.svg
```

## 只读诊断与任务执行

`ascend_board_exec` 仍然是严格只读工具。它只接受白名单内的诊断程序，并拒绝管道、链式命令、重定向、命令替换、包安装、服务重启和设备写操作。`run: false` 只校验并返回等价调用，不执行。

教程下载、模型转换和样例运行使用独立的 `ascend_board_run`：

- 默认 `environment: "base"` 会加载 `/usr/local/miniconda3`、CANN `set_env.sh` 和 conda 初始化；需要裸 SSH shell 时显式设为 `"none"`；
- 必须显式传入 `allowStateChange: true`；
- 默认拒绝 `sudo` 和递归/强制删除，分别在需要时传入 `allowPrivileged`、`allowDestructive`；
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
- 返回截图路径、尺寸、字节数、浏览器和耗时。

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

- 主机：Node.js ≥ 18（仅用 `node:` 内置模块与 DSH 提供的 `@deepseek-ai/dsh-tools`）。
- 板端：只要能 SSH，且具备 `npu-smi`/CANN/驱动；探测脚本用 `bash` 执行，全部只读。
- 本机没有 NPU：本机只能做语法、解析与规划测试。**不要在本机运行 CANN/ATC/PyACL/OM/npu-smi/DVPP。**

## 探测环境差异：`shell-environment-only`

非交互 SSH 默认不会 source CANN，`import acl` 与 `atc` 在那里失败并不代表板卡有问题。探测脚本因此把这两项**跑两遍**：

| 事实键 | 含义 |
| --- | --- |
| `aclImport` / `aclImportSourced` | source `set_env.sh`（有 conda 时先 `conda activate base`）后的真实结果，取值以此为准 |
| `aclImportBare` | 裸 SSH shell 的结果；与 sourced 相反时保留下来，并生成 `aclImportNote` |
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
