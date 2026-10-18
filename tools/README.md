# 仓库开发工具

这个目录存放与教材配套、但独立于书稿构建的 DSH 插件。每个子目录本身就是一个可复制安装的
DSH bundle，不依赖仓库根目录的 `node_modules`。

| 插件 | 可见名称 | 用途 | 源码与安装说明 | 审核记录 |
| --- | --- | --- | --- | --- |
| `dsh-plugin-ascend310b-debug` | 昇腾310B调试 | 板端事实探测、版本配套判定、分层调试计划、样例索引、只读诊断、显式任务执行与模型下载 | [`dsh-plugin-ascend310b-debug/README.md`](dsh-plugin-ascend310b-debug/README.md) | [`installation-audit.md`](dsh-plugin-ascend310b-debug/docs/installation-audit.md) |

## 随插件发布的 Skills

插件源码内的 `skills/` 是源文件，根目录 `.dsh/skills/` 是项目级安装副本：

| Skill | 用途 | 源文件 |
| --- | --- | --- |
| `ascend310b-cann-development` | 310B CANN 程序开发、PyACL/ATC/DVPP/摄像头、接口合同和 M0-M7 验收门 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-cann-development/SKILL.md) |
| `ascend310b-debug` | 失败分层、探针、诊断命令和停止条件 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-debug/SKILL.md) |
| `ascend310b-npu-arch` | 310B1/310B4 架构、8T/20T 和平台差异核对 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-npu-arch/SKILL.md) |
| `ascend310b-env-check` | 板端 CANN/ACL/Python/ATC/设备环境门 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-env-check/SKILL.md) |
| `ascend310b-api-best-practices` | Ascend C/ACL API、对齐、dtype、Buffer 和平台假设审查 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-api-best-practices/SKILL.md) |
| `ascend310b-runtime-debug` | import、返回码、Tiling、Kernel 和 plog 分层排查 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-runtime-debug/SKILL.md) |
| `ascend310b-crash-debug` | 崩溃、卡死、退出码、设备异常和内存问题 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-crash-debug/SKILL.md) |
| `ascend310b-precision-debug` | golden、容差、dtype/layout 和数值门 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-precision-debug/SKILL.md) |
| `ascend310b-sync-audit` | SetFlag/WaitFlag、EnQue/DeQue、CrossCore 和 stream 同步审计 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-sync-audit/SKILL.md) |
| `ascend310b-code-review` | 310B 代码审查、平台边界、资源和证据检查 | [`SKILL.md`](dsh-plugin-ascend310b-debug/skills/ascend310b-code-review/SKILL.md) |
| `ascend310b-writing` | 写作总守门和跨对象规则 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing/SKILL.md) |
| `ascend310b-writing-theory` | 第1章至第10章理论教程 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-theory/SKILL.md) |
| `ascend310b-writing-case` | 案例1至案例9实践教程 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-case/SKILL.md) |
| `ascend310b-writing-presentation` | Marp/Presentation 课件 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-presentation/SKILL.md) |
| `ascend310b-writing-docs` | 代码工程 README、`docs/`、API/部署/验证说明 | [`SKILL.md`](dsh-plugin-ascend310b-writing/skills/ascend310b-writing-docs/SKILL.md) |

修改源 skill 后运行 `npm run skills:sync`，再用 `npm run skills:check` 检查项目级副本。

## 通用安装步骤

1. 进入对应插件目录，先跑该目录 README 里列出的离线测试。
2. 把整个插件目录复制到目标 DSH profile 的 `node_modules/<包名>/`。
3. 在该 profile 的 `package.json` 里把包名加入 `dsh.profile.bundles`。
4. 按插件 README 在 `cordis.patch.yml` 里写本机配置。板卡地址、仓库路径和 SSH 用户只放在 profile，不写进插件源码。
5. 如果插件带有 `skills/`，把其中的技能目录复制到 `$DSH_HOME/skills/`；如果从仓库根目录启动 DSH，则使用已提交的 `.dsh/skills/` 项目级副本。
6. 重启 DSH，确认侧栏入口和工具出现。

桌面版 DSH 的 profile 由应用托管，不能用 `dsh --profile desktop` 在 shell 里直接启动或安装；
文件复制和 profile 配置完成后必须重启桌面应用。

## 边界

- `dsh-plugin-ascend310b-debug` 只适用于**昇腾 310B1 / 310B4**。探测到其他芯片时，插件会标记为不适用，不返回 310B 调试程序。
- 本机不是 Ascend 设备：CANN、ATC、PyACL、OM、`npu-smi`、DVPP 和摄像头检查必须到真实开发板执行。
- 插件源码不包含实际部署的板卡 IP、个人 SSH 凭据、本机检出路径或用户主目录；测试中的 `192.0.2.x`、`198.51.100.x` 和标准户名 `HwHiAiUser` 都是保留值。相关测试在各自的 `test/portability.mjs` 中。
