# 昇腾310B调试插件安装与源码审核

审核日期：2026-10-17
审核对象：`tools/dsh-plugin-ascend310b-debug/`
结论：**可以交付给其他 DSH 用户按 README 的文件拷贝方式安装。** 本机离线门禁全部通过；真实板端结论仍只覆盖已经记录过的板卡组合，不由源码审核代替。

## 交付边界

| 项目 | 结论 |
| --- | --- |
| 包名 | `dsh-plugin-ascend310b-debug` |
| 版本 | `1.2.0` |
| 可见名称 | `昇腾310B调试` |
| 适用芯片 | `Ascend310B1` / `Ascend310B4`（20T / 8T） |
| 其他芯片 | 明确标记 `scope.applicable=false`，不返回 310B 调试程序 |
| 安装方式 | 从本仓库 `tools/` 复制到 DSH profile 的 `node_modules/` |
| 运行时依赖 | Node.js ≥ 18；主机侧只用 `node:` 内置模块与 DSH 提供的 `@deepseek-ai/dsh-tools` |
| 板端依赖 | SSH 公钥登录、`bash`、`npu-smi`、CANN/驱动；探测只读，任务需显式 `allowStateChange` |
| 发布方式 | `private: true`，面向源码分发；不作为公共 npm 包发布 |

## 本次审核修复的问题

| 问题 | 影响 | 修复 |
| --- | --- | --- |
| 目录、包名和插件 ID 使用 `ascend310-debug` | 会把插件误解为覆盖整个 Ascend 310 系列，并与同目录的 `ascend310b-writing` 命名不一致 | 统一改为 `dsh-plugin-ascend310b-debug`，同步面板 ID、HTTP 路由、环境变量、测试和 profile 配置 |
| 仓库根 `.gitignore` 忽略所有 `lib/` 和 `data/` | 直接提交会漏掉主机源码和样例索引，别人拉到的是坏包 | 增加包内 `.gitignore`，重新包含 `lib/`、`data/` |
| `ascend_board_exec` 的“只读”白名单可被绕过 | `npu-smi set`、`sed -i`、`python -c`、`atc`、`curl -X POST`、`dmesg -C`、`v4l2-ctl --set-*`、`ss -K` 和单管道均可能改板卡状态 | 改为“程序白名单 + 参数级校验”；移除可执行任意代码或写产物的程序；拒绝路径命令、单管道和后台连接符 |
| 只读边界缺少回归测试 | 后续改动可能把漏洞加回来 | 新增 `test/shell.mjs`，固定 21 条允许命令与 39 条拒绝场景 |
| 探测脚本的 `capture` 分支会写板端文件 | 与“探测只读”的承诺冲突，且 `outPath` 只做单引号拼接 | 删除未使用的 capture/tee 分支，探测函数只返回固定只读脚本 |
| SSH 用户名未做字符校验 | 恶意或误配的 `user` 可能被 OpenSSH 当成选项解析 | 增加 `isSafeUser`，远端执行前校验 host 与 user |
| `package.json` 声明 MIT 但包内没有许可证，仓库实际 `LICENSE` 又是 CC0 1.0 | 其他人无法确认再分发条件 | 包内附 `LICENSE`，`package.json` 改为 `CC0-1.0`，与仓库实际许可证文件一致 |
| README 的安装和测试路径仍指向 `%USERPROFILE%\.dsh\plugins` | 从仓库安装时照做会复制旧目录或失败 | 全部改为 `tools/dsh-plugin-ascend310b-debug`，并把板卡 IP、SSH 用户改为占位符 |
| 升级时用 `Copy-Item 源目录 目标目录` 会把整个目录再嵌一层 | 第二次安装后 DSH 找不到包入口 | 先创建目标目录，再复制源目录内容（`Join-Path $plugin '*'`） |
| 包清单未声明 Node 版本，自检未覆盖清单完整性 | 旧 Node 或缺少入口文件时到挂载阶段才报错 | 增加 `engines.node >= 18`，可移植性测试检查入口、导出、许可证与 `files` 目标是否存在 |
| `ascend_debug_plan` 和 `ascend_sample_index` 的返回对象含 schema 不接受的值 | 真实 DSH 运行时在工具返回后拒绝结果；空 `entry`/`test`、布尔 `applicable` 和额外样例字段都会触发校验失败 | 在输出边界规范化样例对象，把 `applicable` 映射为字符串，并补 smoke 断言防止回归 |
| 第4章的 PyACL 样例同时提到 ATC/OM，关键词投票把它归到 ATC | `archetype=pyacl-inference` 查询返回 0，调试入口被隐藏 | 对教材第4章固定主类型为 `pyacl-inference`，重新生成索引并加查询回归断言 |
| 插件只有调试工作流，没有完整的程序开发、工程目录和文档规范 | 使用者只能排查失败，无法按统一规则组织新程序、样例仓库和证据文档 | 新增 `docs/ascend310b-development-standard.md`，覆盖目录、编码、ACL/ATC 合同、文档、证据和 M0-M8 验收门，并在 README、技能和可移植性测试中接入 |
| 样例检索只匹配 id/title/keywords/archetype/capabilities，中文“摄像头、VENC、服务、推理、性能”返回 0 | 用户无法从常见症状找到教程样例，`ascend_debug_plan` 也缺少可参考程序 | 为 15 个样例增加 `searchTerms` 中文/英文词汇，`indexQuery` 和面板检索都使用该字段；补中文查询回归 |
| 关键词投票把 case2/5/7/9 等主路径归到 ATC，case4 入口被识别为 compileall | 样例分类和运行入口会误导开发与调试 | 增加主类型覆盖和 case4 服务入口覆盖；入口仍按 README 原文校验 |
| 已配置仓库根目录时仍优先读取包内快照，生成脚本声称的一致性测试也不存在 | 样例变更后工具可能继续显示旧入口或旧分类 | 有 checkout 时扫描 `samples/`；无 checkout 时回退包内快照；补无仓库根目录和中文查询测试 |
| `ascend_check` 无仓库根目录时直接抛错，且“五道验收门”只有文字没有结构化输出 | 纯版本检查被无关配置阻断，工具描述与实际返回不一致 | `ascend_check` 回退包内样例并省略未校验文档；新增 `acceptanceGates` 字段和回归断言 |
| `knowledge.js` 引用不存在的 `samples/chapter4/check_ascend_device.py`；README 对 `fresh=1` 的描述与实现不符 | 样例入口和面板路由说明不可信 | 引用改为 `samples/chapter4/README.md`；README 明确 `fresh=1` 只绕过缓存、不执行探测 |
| 教程需要的 `wget`、`atc`、`python`、`msame` 和测试脚本无法由调试工具执行 | 插件只能看不能跑，chapter2 等章节无法完成下载、转换和推理 | 新增 `ascend_board_run`，要求显式 `allowStateChange:true`，默认加载 base Python/CANN/conda 环境并限制 cwd；保留只读 `ascend_board_exec` |
| HF、华为云、GitHub 下载没有统一来源策略 | 用户需要手写不同的下载命令，HF/GitHub 容易失败 | 新增 `ascend_model_download`：华为云直连、HF 强制 `hf-mirror.com`、GitHub 默认 `gh-proxy.com` 网页代理；目标限制在工作区或 `/tmp` |
| 调试结果只有日志，没有前端页面证据 | 教程无法保存 case 的 UI 实测截图 | 新增 `ascend_ui_capture`，用宿主机 Edge/Chrome headless 截图，强制 `.png`、artifactRoot 边界和 `allowStateChange`；补浏览器实际截图测试 |
| 只能使用固定 IP 的 `targets`，动态 DHCP 和多板实验室无法发现板卡 | IP 变化后工具找不到板，10 块以上只能手工维护地址 | 新增 `ascend_discover`：只扫描显式私有 CIDR，SSH 探测后按 MAC 建本地资产表；发现的 ID 直接成为动态 target，无需改静态列表 |
| 系统重装、驱动/CANN 升级后没有历史，容易把旧结论套到新系统 | 同一块板换系统后仍被当成旧环境 | `ascend_probe`/`ascend_check`/面板探测和 `ascend_discover` 都更新资产表的系统指纹；记录 hostname、OS、kernel、driver、CANN、Python、machine-id 变化，并保留变更历史 |
| 310B 的固件版本在非特权 SSH 下不可靠：`npu-smi` 无版本、固件目录不可读、安装信息只有安装类型 | 面板长期显示“未采集”，还会让用户误以为漏解析 | 默认探测不再请求固件版本文件；资产表和前端不把固件版本作为字段；需要时由有权限的用户手工采集并作为外部证据记录 |
| 板卡需要稳定身份，但 machine-id 在克隆镜像中重复、npu-smi 序列号为 NA | 用 machine-id 或主机名会把多块 8T 合并成一块 | 探测脚本新增 eth0 MAC 和 machine-id 字段；资产表以 MAC 为唯一键，MAC 缺失时明确标为 hostname fallback |
| 同一块板同时出现在静态 `targets` 和发现 target 中，用户看到两个开发板 | 两块板被显示成四个 target，无法判断真实硬件数量 | 静态 target 通过 MAC/主机名/IP 与资产表合并；发现 target 只保留未配置板，面板按 MAC 显示物理开发板 |
| 前端把状态卡、事实表、样例、版本历史和原始输出一次性堆叠 | 信息层级混乱，难以完成发现、选择和探测 | 面板改为“概览 / 开发板资产 / 调试 / 原始输出”四个页签；发现结果进入资产页，版本与原始证据放入原始页 |

## 已验证的门禁

在 `tools/dsh-plugin-ascend310b-debug/` 执行：

```powershell
npm test
node scripts/generate-sample-index.mjs <教材仓库根目录>
powershell -File scripts/verify-profile.ps1 -RepoRoot <教材仓库根目录>
powershell -File scripts/check-patch-compose.ps1
npm pack --dry-run
```

| 门禁 | 验证内容 | 本次结果 |
| --- | --- | --- |
| `npm test` | 解析、版本判定、适用范围、计划、工具输出 schema、只读命令门、任务执行策略、HF/GitHub/Huawei 下载 URL、发现 CIDR/MAC 资产表/系统变更、浏览器装配、面板渲染、可移植性、开发规范完整性、HTTP 冒烟 | 通过 |
| 样例索引 | 当前 `samples/` 扫描、无 checkout 的包内快照、中文症状查询、主类型和 case4 入口 | 通过；`摄像头`、`VENC`、`服务`、`推理`、`性能` 均可命中，`pyacl-inference` 命中第4章 |
| 发现与资产表 | 私网 CIDR 校验、SSH 探测、MAC 去重、IP 变化、驱动/CANN 变更 | 通过；本地 /24 实测发现 8T/20T，二次探测为 unchanged，MAC 和系统指纹已写入本地资产表 |
| 一次性 profile | 路由、CANN/驱动解析、8T 夹具、非 310B 拒绝、下发 bundle 含新名称 | 通过 |
| patch 组合 | 桌面 profile 的插件行能被解析并带到目标列表 | 通过 |
| 新执行工具实板验证 | `ascend_board_run` 的 base Python、`ascend_model_download` 的 HF mirror/GitHub proxy/Huawei OBS、ATC、`msame` | 通过；8T/20T 的 Python 0 退出，HF/GitHub/Huawei 下载成功，ATC 与 `msame` 均成功 |
| 截图工具 | 宿主机 Edge/Chrome headless、PNG 尺寸/签名、输出路径边界 | 通过；本地 HTTP 测试页生成 800x600 PNG，大小 >10KB |
| 打包清单 | `npm pack --dry-run` 包含 `lib/`、`data/`、`docs/`（含开发规范）、`skills/`、`LICENSE`、README | 通过 |
| 隐私扫描 | 不包含固定板卡 IP、本机用户名、检出目录或用户主目录 | 通过 |

## 仍需使用者确认的事项

1. **板端实测有边界。** 本次已在真实 8T/20T 板执行章节样例、任务、下载、ATC 和 `msame`，但这些仍是记录过的具体组合，不是对所有板卡、系统或模型组合的保证；换版本后必须重跑。
2. **SSH 首次指纹。** 默认使用 `StrictHostKeyChecking=accept-new`。高安全环境应先手工核对板卡指纹，或预置 `known_hosts`。
3. **DSH 版本兼容。** 插件依赖当前的 bundle patch、`defineTool` 和 `slots` 接口。升级 DSH 后应重跑一次性 profile 验证。
4. **许可证政策。** 插件按其实际下发的仓库 `LICENSE` 使用 CC0 1.0；根 README 的许可证徽章/文字与 `LICENSE` 文件本身存在历史不一致，若要以 Apache 2.0 发布，需要先统一仓库级声明。
5. **Windows 专用验证脚本。** 插件运行时代码是跨平台的，但 `verify-profile.ps1` 和 `check-patch-compose.ps1` 是 PowerShell，Linux/macOS 使用者只能运行 `npm test` 与手工安装。

## 其他安装者最短路径

```powershell
# 1. 在仓库里验证
cd <教材仓库根目录>\tools\dsh-plugin-ascend310b-debug
npm test

# 2. 复制到桌面 profile
$plugin = Join-Path "<教材仓库根目录>" "tools\dsh-plugin-ascend310b-debug"
$dest = "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\dsh-plugin-ascend310b-debug"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Path (Join-Path $plugin '*') -Destination $dest -Recurse -Force

# 3. 在 desktop/package.json 的 dsh.profile.bundles 加入
#    dsh-plugin-ascend310b-debug

# 4. 在 desktop/cordis.patch.yml 增加插件行和本机 repoRoot/targets

# 5. 复制技能文件
New-Item -ItemType Directory -Force "$env:USERPROFILE\.dsh\skills\ascend310b-debug" | Out-Null
Copy-Item -Force "$plugin\skills\ascend310b-debug\SKILL.md" `
  "$env:USERPROFILE\.dsh\skills\ascend310b-debug\SKILL.md"

# 6. 重启 DSH，确认侧栏出现“昇腾310B调试”
```

完整配置示例见 [`../README.md`](../README.md) 的“安装”一节。
