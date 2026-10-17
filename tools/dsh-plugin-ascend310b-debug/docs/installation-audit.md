# 昇腾310B调试插件安装与源码审核

审核日期：2026-10-17
审核对象：`tools/dsh-plugin-ascend310b-debug/`
结论：**可以交付给其他 DSH 用户按 README 的文件拷贝方式安装。** 本机离线门禁全部通过；真实板端结论仍只覆盖已经记录过的板卡组合，不由源码审核代替。

## 交付边界

| 项目 | 结论 |
| --- | --- |
| 包名 | `dsh-plugin-ascend310b-debug` |
| 可见名称 | `昇腾310B调试` |
| 适用芯片 | `Ascend310B1` / `Ascend310B4`（20T / 8T） |
| 其他芯片 | 明确标记 `scope.applicable=false`，不返回 310B 调试程序 |
| 安装方式 | 从本仓库 `tools/` 复制到 DSH profile 的 `node_modules/` |
| 运行时依赖 | Node.js ≥ 18；主机侧只用 `node:` 内置模块与 DSH 提供的 `@deepseek-ai/dsh-tools` |
| 板端依赖 | SSH 公钥登录、`bash`、`npu-smi`、CANN/驱动；只读探测 |
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
| `npm test` | 解析、版本判定、适用范围、计划、工具输出 schema、只读命令门、浏览器装配、面板渲染、可移植性、HTTP 冒烟 | 通过 |
| 样例索引 | 用当前 `samples/` 重新生成后与包内快照比较 | 15 个样例，SHA-256 未变化 |
| 一次性 profile | 路由、CANN/驱动解析、8T 夹具、非 310B 拒绝、下发 bundle 含新名称 | 通过 |
| patch 组合 | 桌面 profile 的插件行能被解析并带到目标列表 | 通过 |
| 打包清单 | `npm pack --dry-run` 包含 `lib/`、`data/`、`skills/`、`LICENSE`、README | 通过 |
| 隐私扫描 | 不包含固定板卡 IP、本机用户名、检出目录或用户主目录 | 通过 |

## 仍需使用者确认的事项

1. **板端实测不能由本机代替。** 本次审核只使用夹具和已有探测记录，没有在审核期间重新 SSH 探测 20T 板；8T 的 `observed-match` 是一条已经记录过的证据，不是对所有板卡/系统组合的保证。
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
