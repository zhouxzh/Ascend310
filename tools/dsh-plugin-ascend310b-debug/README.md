# 昇腾310B调试（dsh-plugin-ascend310b-debug）

目录和包名固定为 `dsh-plugin-ascend310b-debug`。这里的 `310b` 是芯片家族限定，不是泛化的 `Ascend 310`；旧名 `dsh-plugin-ascend310-debug` 已弃用，避免把插件误解为覆盖整个 Ascend 310 系列。

DSH 插件：面向**昇腾 310B**（Ascend 310B1 / 310B4）程序调试。它把两件事合到一起：

- **板端事实采集**：从真实开发板读出 SoC、npu-smi、CANN、驱动、固件、Python/PyACL、设备节点与内核模块，并给出**版本配套判定**（含证据级别）。
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
        host: <8T 开发板 IP>
        user: <SSH 用户>
        note: 8T 证据集；结果不与 20T 合并
      - id: board-20t
        label: 开发板 2（20T / Ascend310B1）
        transport: ssh
        host: <20T 开发板 IP>
        user: <SSH 用户>
        note: 20T 证据集；与 8T 分开记录
```

只需要本机解析探测记录时，可以省略 `targets`；需要 SSH 探测时再填板卡配置。

4) 技能文件不会被插件自动发现，需要单独放置：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.dsh\skills\ascend310b-debug" | Out-Null
Copy-Item -Force `
  "$plugin\skills\ascend310b-debug\SKILL.md" `
  "$env:USERPROFILE\.dsh\skills\ascend310b-debug\SKILL.md"
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
| `ascend_check` | 用 `rawText` 或现场探测得到环境报告 | `target`、`rawText`、`repoRoot` |
| `ascend_debug_plan` | 按症状/领域生成分层调试计划 | `symptom`、`area`、`target`、`rawText`、`maxEntries` |
| `ascend_sample_index` | 检索 `samples/` 里的样例入口与测试命令 | `query`、`archetype`、`limit`、`repoRoot` |
| `ascend_board_exec` | 执行**单条只读**诊断命令 | `command`、`target`、`run`、`timeoutMs` |

浏览器侧另有 **昇腾310B调试** 面板：板卡/驱动/固件/CANN 卡片、版本配套判定、已记录版本组合、样例检索和探测原文。面板在探测之前所有版本字段显示**未采集**；探测到非 310B 芯片时，面板顶部会明确写出“不适用”。

### HTTP 路由

面板使用两条只读路由：`GET /ascend310b-debug/report.json` 返回最近一次探测形成的报告；带 `?fresh=1` 时绕过 120 秒缓存，但仍不执行板端探测。实时探测使用 `POST /ascend310b-debug/probe`，面板右上角的“运行探测”按钮调用这条路由。

### 面板预览

`data/panel-preview.html`、`.svg`、`.png` 是用一次真实 8T 探测记录渲染的面板预览，方便在没有打开 DSH 时检查字段与措辞。重新生成：

```powershell
node scripts/render-panel-preview.mjs <探测记录文件> data/panel-preview.html data/panel-preview.svg
```

## `ascend_board_exec` 的只读边界

命令必须先通过白名单校验，再考虑执行：

- 拒绝任何管道、`;`、`&&`、`||`、单个 `&`、反引号、`$( )`、重定向与进程替换。`cat file | bash` 这类单管道绕行也会被拒绝。
- 拒绝变更类子串：`rm`、`mv`、`dd`、`mkfs`、`sudo`、`apt`、`pip install`、`systemctl restart`、`reboot`、`upgrade-tool`、git 写操作等。
- 程序名必须是 `PATH` 中的裸命令名，`./npu-smi` 或 `/tmp/npu-smi` 不能借用白名单。
- 命令首词必须在只读白名单内（`npu-smi`、`uname`、`cat`、`grep`、`ls`、`stat`、`readlink`、`sha256sum`、`df`、`free`、`lsmod`、`dmesg`、`v4l2-ctl`、`ps`、`ss`……）。
- 有变更子命令或写参数的程序按参数逐一校验：`npu-smi` 只允许 `info`；`v4l2-ctl` 只允许枚举/查询选项；`dmesg` 不允许清空或改控制台级别；`hostname` 和 `date` 不允许设置操作。
- 会执行任意代码或写产物的程序不进入白名单：`bash`、`source`、`python -c`、`atc`、`msame`、`curl`、`pip`、`conda`、`sed -i`。
- `run: false` 只校验并返回等价调用，不执行。

需要改动板卡状态时，插件会把命令和回退条件交给用户，由用户执行。调试计划里出现的 `source`、`python -c`、`atc`、`conda activate` 和管道命令是供人阅读、在已激活环境中手工执行的步骤，不表示 `ascend_board_exec` 会接受它们。

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

`npm test` 覆盖：解析、版本判定、适用范围（310B 内/外/未确认）、调试计划、样例检索（含中文症状）、只读命令白名单与绕过回归、浏览器半装配、面板渲染（含非 310B 拒绝提示）、可移植性与冒烟路由。

**主机半改动需要重启 DSH 才会生效。** 桌面 Electron profile 由应用托管，`dsh --profile desktop` 会拒绝执行，所以改动后用 `verify-profile.ps1` 先在一次性 profile 里验证；浏览器半会热更新，主机半（工具与路由）要重启应用。重启前可以用 `live-probe.mjs` 直接确认板端探测结果。

命名约定：

- 板卡档位：`Ascend310B1` → 20T，`Ascend310B4` → 8T（`documented`）。
- 证据词汇：`documented`、`inferred`、`observed-pass`、`observed-fail`、`untested`。
- 档位与 IP 无关：结果按 **SoC + 档位** 归档，不按板卡名或地址合并。
