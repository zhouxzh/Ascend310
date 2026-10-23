# 昇腾310B调试插件命令执行矩阵

本文件审查教材运行所需命令与调试插件执行边界。目标是让下载、转换、构建和样例运行可以执行，同时保留只读诊断与显式变更之间的边界。

## 1. 工具分工

| 工具 | 是否改变板端状态 | 用途 |
| --- | --- | --- |
| `ascend_probe` | 否 | 固定只读环境探测 |
| `ascend_board_exec` | 否 | 单条只读诊断，白名单和参数校验 |
| `ascend_board_run` | 是，必须 `allowStateChange:true` | 下载、ATC、Python、pytest、msame、教程脚本 |
| `ascend_apt_install` | 是，必须 `allowStateChange:true`；仅 SSH | 通过 `HwHiAiUser + sudo` 安装受控 apt 系统包 |
| `ascend_model_download` | 是，必须 `allowStateChange:true` | 华为云/HF mirror/GitHub proxy 资产下载 |
| `ascend_ui_capture` | 宿主机写 PNG，必须 `allowStateChange:true` | 开发板前端页面截图 |

## 2. 只读诊断命令

`ascend_board_exec` 继续覆盖：

- `npu-smi info` 及其只读 `info` 子命令；
- `uname`、`hostname`、`date`、`free`、`df`、`ls`、`stat`、`readlink`；
- `cat`、`head`、`tail`、`grep`、`wc`、`file`、`sha256sum`；
- `lsmod`、`dmesg`、`ps`、`pgrep`、`ss`、`v4l2-ctl` 查询。

它仍拒绝管道、链式命令、重定向、命令替换、路径可执行文件、`source`、`python`、`atc`、`msame` 和写操作。只读边界不因为新增任务工具而放宽。

## 3. 下载命令

| 来源 | 规范化 | 执行者 |
| --- | --- | --- |
| 华为云 OBS | 保留 `huaweicloud.com`/`myhuaweicloud.com` URL，直接 `wget`，失败回退 `curl` | `ascend_model_download` |
| Hugging Face | `https://hf-mirror.com/<repo>/resolve/<revision>/<file>` | `ascend_model_download` |
| GitHub | `<githubProxy><原始 GitHub URL>`，默认 `https://gh-proxy.com/` | `ascend_model_download` |
| 直链 | 允许显式 HTTP(S) URL | `ascend_model_download` |

Hugging Face 原始站不直接作为默认下载入口；GitHub 原始站不直接作为默认下载入口。代理前缀可由 profile 的 `config.githubProxy` 或工具参数覆盖。

## 4. 教程任务命令

`ascend_board_run` 显式允许：

- 环境：`source set_env.sh`、`conda activate`、`export`；
- 下载：`wget`、`curl`；
- 转换：`atc`；
- 推理/测试：`python`、`python3`、`pytest`、`msame`、`bash`、`npm`、`pnpm`；
- 文件准备：`mkdir`、`cp`、`mv`、`chmod`、`tar`、`unzip`；
- 性能：`msprof`、案例自带 benchmark 脚本。

这些命令可以在 `cwd` 指定的工作区运行，并返回退出码、耗时、stdout、stderr。运行前应先用 `dryRun:true` 检查命令。

`ascend_board_run` 默认激活统一案例环境 `ascend`，并设置
`PYTHONNOUSERSITE=1`；环境不存在时任务直接失败，不静默回退 `base`。
`base` 只能通过显式 `environment: "base"` 用于系统诊断，`"none"` 用于裸
SSH shell。`ascend` 固定 Python 3.9.25；CANN、NPU driver 和固件仍由板端
系统提供。

### 4.1 apt 系统程序安装

`ascend_apt_install` 是独立的系统安装工具。公版默认密码未修改时，它使用 profile 的 `sudoPassword`，通过 `HwHiAiUser` 和 `sudo -S` 执行固定操作：

```text
apt-get update
apt-get install -y --no-install-recommends <validated-packages>
```

包名必须符合 apt 名称字符集，一次最多 24 个；密码只通过 SSH stdin 发送，不进入命令行、日志、skill 或 Git。执行前必须显式 `allowStateChange:true`，并建议先 `dryRun:true` 检查目标和命令。安装结果与真实摄像头、音频、VPU 或 NPU 功能验收分开记录。

## 5. 仍不允许或需要额外开关的命令

| 命令类别 | 默认行为 | 原因 |
| --- | --- | --- |
| `npu-smi set/reset/upgrade` | 始终拒绝 | 改变健康、复位或固件 |
| `reboot`、`shutdown`、`poweroff` | 始终拒绝 | 中断开发板 |
| `mkfs`、直接 `dd` 写块设备 | 始终拒绝 | 破坏文件系统/设备 |
| `rm -r`、`rm -f` | 需要 `allowDestructive:true` | 可能删除工作区或系统文件 |
| `sudo` | 需要 `allowPrivileged:true`；apt 安装使用 `ascend_apt_install` | 提权安装/系统修改 |
| `npu-smi` 写操作 | 只能由用户手工执行 | 插件不代替硬件运维 |

## 6. 章节命令审计

| 章节 | 需要执行的代表命令 | 支持路径 |
| --- | --- | --- |
| chapter2 | ResNet50 ONNX 下载、`atc`、`msame` | `ascend_model_download` + `ascend_board_run` |
| chapter3 | `python .../linear_regression_npu.py` | `ascend_board_run` |
| chapter4 | `python .../check_ascend_device.py`、PyACL 推理 | `ascend_board_run` |
| chapter5 | `python tools/run_board_examples.py`、pytest | `ascend_board_run` |
| chapter6 | `python check_cann.py`、VPC/VENC 脚本 | `ascend_board_run` |
| chapter7 | `python run.py`、TBE/Ascend C 编译命令 | `ascend_board_run` |
| chapter8 | `python 01_baseline_resnet_sync.py`、`msprof` | `ascend_board_run` |
| chapter9 | 数据下载、`atc`、`python 01_compare_outputs.py` | `ascend_model_download` + `ascend_board_run` |

案例目录的服务、前端、pytest 和部署脚本同样使用 `ascend_board_run`；服务启停是否允许由具体脚本决定，插件不自动执行硬件复位或固件升级。

## 7. 证据与安全

- 下载目标必须是工作区文件或 `/tmp` 文件，不能通过 `..` 跳出工作区。
- 执行 cwd 必须在目标板工作区、用户 home 或 `/tmp` 下。
- 每次任务记录目标、cwd、命令、退出码、耗时和输出。
- 截图记录 URL、viewport、字节数、耗时和浏览器。
- 任务失败不能用只读探测结果替代；只读检查通过也不能证明任务执行成功。
- 下载模型和数据集不进入插件源码或普通 Git；只在板端保存并记录摘要。

## 8. 前端截图

前端服务启动并通过健康检查后，使用 `ascend_ui_capture`：

- 宿主机 Edge/Chrome 以 headless 模式访问 `http://<board>:<port>/`；
- 输出 PNG 必须位于工作区或配置的 `artifactRoot`；
- 同时记录 URL、viewport、字节数、耗时和浏览器；
- 截图只证明页面渲染，必须另存 health/API/真实推理日志；
- 没有前端的 case 记录 `N/A`，不能用 API 输出冒充 UI 截图。

## 9. 本次实板验证

| 项目 | 8T | 20T |
| --- | --- | --- |
| base Python `-c` | exit 0，375 ms | exit 0，410 ms |
| HF mirror `config.json` | exit 0，1,360 ms | — |
| GitHub `gh-proxy.com` README | exit 0，1,591 ms | — |
| 华为云 ResNet50 ONNX | exit 0，22,251 ms | exit 0，9,156 ms |
| ATC ResNet50 | exit 0，154,826 ms | exit 0，208,820 ms |
| `msame` ResNet50 | exit 0，Inference 13.232 ms | exit 0，Inference 11.748 ms |

详细原始输出见 [`chapter-board-runs-20261017/tool-execution/`](chapter-board-runs-20261017/tool-execution/)。
