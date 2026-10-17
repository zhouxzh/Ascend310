# 仓库开发工具

这个目录存放与教材配套、但独立于书稿构建的 DSH 插件。每个子目录本身就是一个可复制安装的
DSH bundle，不依赖仓库根目录的 `node_modules`。

| 插件 | 可见名称 | 用途 | 源码与安装说明 | 审核记录 |
| --- | --- | --- | --- | --- |
| `dsh-plugin-ascend310b-debug` | 昇腾310B调试 | 板端事实探测、版本配套判定、分层调试计划、样例索引、只读诊断命令 | [`dsh-plugin-ascend310b-debug/README.md`](dsh-plugin-ascend310b-debug/README.md) | [`installation-audit.md`](dsh-plugin-ascend310b-debug/docs/installation-audit.md) |

## 通用安装步骤

1. 进入对应插件目录，先跑该目录 README 里列出的离线测试。
2. 把整个插件目录复制到目标 DSH profile 的 `node_modules/<包名>/`。
3. 在该 profile 的 `package.json` 里把包名加入 `dsh.profile.bundles`。
4. 按插件 README 在 `cordis.patch.yml` 里写本机配置。板卡地址、仓库路径和 SSH 用户只放在 profile，不写进插件源码。
5. 如果插件带有 `skills/`，把其中的技能目录复制到 `$DSH_HOME/skills/`。
6. 重启 DSH，确认侧栏入口和工具出现。

桌面版 DSH 的 profile 由应用托管，不能用 `dsh --profile desktop` 在 shell 里直接启动或安装；
文件复制和 profile 配置完成后必须重启桌面应用。

## 边界

- `dsh-plugin-ascend310b-debug` 只适用于**昇腾 310B1 / 310B4**。探测到其他芯片时，插件会标记为不适用，不返回 310B 调试程序。
- 本机不是 Ascend 设备：CANN、ATC、PyACL、OM、`npu-smi`、DVPP 和摄像头检查必须到真实开发板执行。
- 插件源码不包含实际部署的板卡 IP、个人 SSH 凭据、本机检出路径或用户主目录；测试中的 `192.0.2.x`、`198.51.100.x` 和标准户名 `HwHiAiUser` 都是保留值。相关测试在各自的 `test/portability.mjs` 中。
