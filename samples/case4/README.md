# Ascend 310B4 / 8T 掌纹识别工作台

这是一个运行在 Ascend 310B4 / 8T 开发板上的 React + FastAPI 掌纹识别工作台。本文只说明源码构建、手动部署、启动和日常操作；模型评测、转换、故障和验收证据从 [`docs/00-document-index.md`](docs/00-document-index.md) 开始阅读。

在线接口固定使用 NPU `mixed_fp16` 推理。CPU 和 EDCC 只用于离线研究，不会出现在正式 API 或界面中。默认 `production` profile 使用 CCNet；人工测试阶段可显式设置 `PALMPRINT_PROFILE=manual_test`，在完成资产哈希校验后测试五个 CompNet。CompNet 会显示 `manual_test_pending`，不能把人工测试前的状态当作稳定验收通过。

> **网络边界**：服务默认监听 `0.0.0.0`，只适用于隔离的受信局域网。项目不提供账号、TLS、模板多租户隔离或公网安全保证，禁止直接暴露到互联网。

## 1. 环境要求

本地构建机需要 Python 3.9--3.11、Node.js 18+、npm 和 Git。开发板需要：

- Ascend 310B4，8T 算力档位；20T 设备必须单独建立结果集；
- Ubuntu 22.04/aarch64、Miniconda、与板卡匹配的 CANN 和 PyACL；
- 已单独同步并校验的模型、数据集、模板和运行报告资产；
- 开发板不需要 Node.js，也不在板端执行前端构建。

源码仓库不包含 checkpoint、ONNX、OM、数据集、模板、真实摄像头图像或原始运行报告。板端运行只要求已校验的 mixed-FP16 OM；参考 ONNX 可选，仅在导出和契约审计环境中使用。板端资产包可在许可证允许时单独携带数据集和模型，但模板、姓名、embedding 或密钥永远不进入发行包；默认按 `dataset_manifest.json` 从外部来源下载。请先阅读 [`docs/04-release-checklist.md`](docs/04-release-checklist.md)，确认源码包和板端资产包的边界。

## 2. 本地构建与检查

在源码包根目录执行。源码包使用正式 `palmprint_workbench` 包；根目录的旧平铺模块和旧兼容入口不属于发布包：

```bash
python -m compileall -q app.py palmprint_workbench tools
python -m palmprint_workbench.tools.verify_assets --strict
python -m pytest -ra tests
cd frontend
npm ci
npm test
npm run build
cd ..
```

`npm run build` 生成的 `frontend/dist` 由 FastAPI 直接托管。构建完成后可用下面的命令检查静态文件：

```bash
python -m tools.board.verify_frontend_assets --dist frontend/dist --strict
```

不要在本地执行 CANN、ATC、PyACL、`npu-smi`、DVPP 或板载摄像头测试；这些检查必须在开发板上完成。

## 3. 手动配置开发板环境

通过 SSH 登录实际设备。文档中的 `USER@HOST` 和远端目录必须替换为当前部署目标，不要依赖固定主机名或 IP：

Windows 端可使用系统 OpenSSH（在 PowerShell 中确认 `ssh -V`）和 WSL/Git Bash
中的 `rsync`；WSL 中需先确认 `openssh-client`、`rsync` 已安装，且板端提供
同名 `rsync` 命令。PowerShell 不直接解释下面的 Bash 续行语法时，请在同一
WSL/Git Bash 会话中执行整段同步命令。SSH 密钥、端口和远端目录由操作者显式
指定，文档不保存凭据。

```bash
ssh USER@HOST
cd ~/Documents/palmprint-recognition
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PALMPRINT_ROOT="$PWD"
# Some CANN installations do not add PyACL's Python package automatically.
export ACL_PYTHON_SITE=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages
export PYTHONPATH="$PALMPRINT_ROOT:$ACL_PYTHON_SITE${PYTHONPATH:+:$PYTHONPATH}"
python -c "import sys, acl; print(sys.executable); print(acl.__file__)"
python -c "import os; print('PALMPRINT_ROOT=', os.environ.get('PALMPRINT_ROOT')); print('LD_LIBRARY_PATH configured=', bool(os.environ.get('LD_LIBRARY_PATH')))"
python -m pip install --requirement requirements/board.lock
```

`source` 命令只负责加载已安装的 conda/CANN 环境；项目不会自动安装依赖、下载数据、构建 EDCC 或修改系统环境。当前内测版本不启用模板或图片加密，也不读取模板密钥变量。若 CANN 安装在其他位置，请把路径替换为实际 `set_env.sh`。`acl` 导入失败时不要添加 CPU 回退，应先修正当前 shell 的 Python 和 CANN 环境。

`PALMPRINT_ROOT` 必须指向包含 `models/`、`data/`、`frontend/` 和
`release_manifest.json` 的发行目录；如果从其他目录启动，改成该目录的绝对路径。
`PYTHONPATH` 必须包含发行目录和上面声明的 PyACL site-packages。`set_env.sh`
应负责配置与 CANN 匹配的 `LD_LIBRARY_PATH`；若上面的检查显示为空，先修复 CANN
shell，不要手工混用其他版本的 ACL 动态库。

本版本固定使用 `manual_test` 内测模式：模板以本地明文 `PWST1` 文件保存，图片和 ROI 归档在当前发行目录，便于人工查看和复盘。该模式只适用于本人或受信设备，不得暴露到公网。`PALMPRINT_TEMPLATE_KEY_FILE`、`PALMPRINT_TEMPLATE_DIR` 和 `PALMPRINT_REQUIRE_TEMPLATE_ENCRYPTION` 会被忽略。

可选环境变量通过人工设置：

```bash
export PALMPRINT_HOST=0.0.0.0
export PALMPRINT_PORT=7860
export PALMPRINT_PROFILE=manual_test
export PALMPRINT_NPU_DEVICE=0
export PALMPRINT_CAMERA_DEVICE=/dev/video0
export PALMPRINT_CAMERA_WIDTH=1280
export PALMPRINT_CAMERA_HEIGHT=720
export PALMPRINT_CAMERA_FPS=30
export PALMPRINT_CAMERA_RESOLUTIONS=1280x720,1920x1080,640x480
unset PALMPRINT_TEMPLATE_KEY_FILE
unset PALMPRINT_TEMPLATE_DIR
unset PALMPRINT_REQUIRE_TEMPLATE_ENCRYPTION
export PALMPRINT_JOB_TIMEOUT_SECONDS=900
export PALMPRINT_MAX_JOB_TIMEOUT_SECONDS=3600
export PALMPRINT_MAX_API_REPORT_FILES=120
export PALMPRINT_MAX_API_REPORT_BYTES=536870912
```

完整变量说明见 [`.env.example`](.env.example)。其中的示例值不包含任何凭据；HF 下载 token 不得写入仓库、日志或截图。

## 4. 手动同步与启动

在本地构建 `frontend/dist` 后，先创建板端版本目录，再用不删除远端文件的 `rsync` 同步源码和前端。以下命令只同步源码包，不触碰板端模型、数据集、模板和报告：

```bash
ssh USER@HOST 'mkdir -p ~/Documents/palmprint-recognition/releases/20260817'
rsync -a --protect-args \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache/' \
  --exclude 'build/' \
  --exclude '*.egg-info/' \
  --exclude 'frontend/node_modules/' \
  --exclude 'frontend/.vite/' \
  --exclude 'data/' \
  --exclude 'models/om/' \
  --exclude 'models/onnx/' \
  --exclude 'models/checkpoints/' \
  --exclude 'reports/' \
  --exclude 'artifacts/' \
  --exclude 'third_party/' \
  --exclude 'tools/offline/legacy/' \
  --exclude 'tools/offline/legacy_shell/' \
  ./ USER@HOST:~/Documents/palmprint-recognition/releases/20260817/
```

不要加 `--delete`。同步后在板端核对 `release_manifest.json`、`models/registry.json` 和前端 bundle 的 SHA-256，再人工更新一个指向已核验版本的符号链接或目录变量。失败时只恢复上一个源码版本，不删除模型、数据集、模板或报告。

在已经加载 conda/CANN 的同一 shell 中执行。资产校验只检查当前发行目录和已明确同步的模型，不会下载或转换模型：

```bash
cd ~/Documents/palmprint-recognition/releases/20260817
python -m palmprint_workbench.tools.verify_assets --strict
export PALMPRINT_PROFILE=manual_test
unset PALMPRINT_TEMPLATE_KEY_FILE
unset PALMPRINT_TEMPLATE_DIR
unset PALMPRINT_REQUIRE_TEMPLATE_ENCRYPTION
python -m palmprint_workbench.api --host 0.0.0.0 --port 7860
```

注册和用户主动触发的识别会自动保存到 `data/captures/`；连续摄像头预览不会保存图片。模板位于 `data/templates/`，报告位于 `reports/`。删除模板只删除 embedding，关联原图和 ROI 会保留。

查看或清理内测数据必须由操作者手动执行，程序不会自动删除历史图片：

```bash
find "$PALMPRINT_ROOT/data/captures" -type f
rm -rf "$PALMPRINT_ROOT/data/captures"
rm -rf "$PALMPRINT_ROOT/data/templates"
```

部署同步时始终排除 `data/`、`reports/` 和 `.env`；切换版本时若要继续使用内测模板和图片，需人工复制完整 `data/` 目录到新发行目录。

停止服务时先根据进程命令确认 PID，再执行 `kill <PID>`；不要结束不属于本项目的 Python 进程。启动后从另一终端检查：

```bash
curl --fail http://127.0.0.1:7860/api/health
curl --fail http://127.0.0.1:7860/api/bootstrap
curl --fail http://127.0.0.1:7860/
```

健康检查通过表示服务、模型资产和本地模板存储策略均已就绪；`inference_smoke` 仍不会主动加载 NPU，必须再人工执行一次上传或摄像头请求。若 health 返回 warning，先查看 `template_store` 字段和当前发行目录的 `data/` 权限。

## 5. 手动备份与回滚

部署新源码前，在板端为源码版本、生产 registry、候选清单、release manifest、前端 bundle 和服务 PID 建立一个带时间戳的备份。模型、数据集、模板、图片和报告不复制到源码包，也不在回滚时删除：

```bash
export RELEASE_ROOT="$HOME/Documents/palmprint-recognition/releases/20260817"
export BACKUP_ROOT="$HOME/Documents/palmprint-recognition/releases/backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_ROOT/frontend-dist"
cp -a "$RELEASE_ROOT/models/registry.json" "$BACKUP_ROOT/"
cp -a "$RELEASE_ROOT/candidate_manifest.json" "$BACKUP_ROOT/"
cp -a "$RELEASE_ROOT/release_manifest.json" "$BACKUP_ROOT/"
cp -a "$RELEASE_ROOT/frontend/dist/." "$BACKUP_ROOT/frontend-dist/"
pgrep -af 'python -m palmprint_workbench.api' | tee "$BACKUP_ROOT/service-process.txt"
```

失败时只停止确认过的新 PID，并切换到上一个已经核验的版本目录；不要用 `killall`, `pkill -f python` 或 `rsync --delete`：

```bash
export NEW_PID=<the-new-service-pid>
export PREVIOUS_ROOT="$HOME/Documents/palmprint-recognition/releases/<previous-release-id>"
kill "$NEW_PID"
cd "$PREVIOUS_ROOT"
export PALMPRINT_ROOT="$PREVIOUS_ROOT"
export PALMPRINT_PROFILE=manual_test
unset PALMPRINT_TEMPLATE_KEY_FILE
unset PALMPRINT_TEMPLATE_DIR
unset PALMPRINT_REQUIRE_TEMPLATE_ENCRYPTION
python -m palmprint_workbench.api --host 0.0.0.0 --port 7860
```

在另一终端确认 PID、`/api/health`、`/api/bootstrap` 和首页恢复后，再处理旧版本目录。模板、图片、数据集、OM/ONNX 和报告始终由人工按各自清单管理；切换版本时需要人工复制完整 `data/` 目录。

## 6. 工作台操作

- **实时识别**：production profile 选择 CCNet；manual_test profile 可逐一选择五个 CompNet，上传掌纹或选择摄像头，确认 ROI 质量后执行识别。页面中的 `manual_test_pending` 只表示等待人工验收。
- **掌纹注册**：填写姓名和掌侧，采集 3--5 个合格样本后提交。模板按 `<model_id>__npu__<precision>` 隔离保存。
- **候选审计**：在线只读查看候选的任务类型、模态、权重、转换和 NPU 状态；候选评测仅在离线研究环境使用 `python -m tools.offline.benchmark` 执行。
- **系统状态**：查看 API、CANN、PyACL、NPU 和摄像头状态。`Health: Alarm` 是诊断字段；具体的 ACL 崩溃、设备重置和资源释放错误仍需停止相关测试。

摄像头先通过 `GET /api/cameras` 查看设备和实际支持的分辨率。若驱动支持 1920x1080，可在界面中选择该模式；预览请求保持串行，单次完成后约等待 80 ms，以避免积压旧帧。切换摄像头、切换分辨率或刷新设备列表时，前端会先登记新的预览会话；旧请求不会再关闭新设备。若运行中物理拔插摄像头，先在界面关闭预览，再刷新状态并重新打开；第一次读帧失败时服务会自动重建一次 V4L2 句柄。预览结束后关闭页面或调用关闭操作，释放 V4L2 设备。

## 7. 模型资产与离线研究

源码包不包含模型二进制。运行时从受信的板端资产目录读取 `models/om/`；`models/registry.json` 只保存已准入模型的元数据。当前工作区已经从板端取回 CCNet 和五个 canonical CompNet mixed-FP16 OM，并按 [`om_manifest.json`](om_manifest.json) 核对字节数与 SHA-256；旧静态测试图不属于发布资产。这些文件保持 Git ignored，只用于生成独立资产包。

当前不自动从 GitHub、Hugging Face 或其他远程服务下载模型。板端或受控资产包提供 OM 后，先按 `om_manifest.json` 核对字节数和 SHA-256，再运行：

```bash
python -m palmprint_workbench.tools.verify_assets --strict
```

`zhouxzh/ascend310-palmprint` 只作为用户自行管理的外部资产位置；本源码版本不包含上传凭据，也不把远程可下载性当作上游再分发授权。

五个 CompNet OM 的下载路径、字节数、SHA-256、许可证和 `manual_test_pending=true` 状态以 `om_manifest.json` 和 `candidate_manifest.json` 为准。下载成功不等于稳定性验收通过；manual_test profile 只用于人工功能测试，production profile 仍遵守完整准入门槛。

离线数据集和 benchmark 不是工作台启动依赖。研究人员必须按 `dataset_manifest.json` 手动下载、解压并校验归档；详细协议和指标见 [`docs/01-model-evaluation-and-deployment-plan.md`](docs/01-model-evaluation-and-deployment-plan.md)。正式源码包中的离线入口为：

```bash
python -m tools.offline.benchmark audit --dataset tongji
python -m tools.offline.benchmark audit --dataset polyu --spectrum B
```

真实掌纹图像、数据集、模板和运行报告不得进入 Git 或截图；`ui_smoke_5_20_1` 只能验证界面和报告链路，不能作为正式精度排名。

## 8. 常见故障

- **PyACL 不可用**：在启动服务的同一个 shell 中重新加载 conda 和 CANN，确认 `python -c "import acl"` 成功；不要添加 CPU fallback。
- **模型不在下拉框**：确认服务使用 `PALMPRINT_PROFILE=manual_test`，再检查 `/api/bootstrap`、registry、OM 文件字节数和 SHA-256。production profile 默认只显示 CCNet。
- **摄像头画面卡顿、热插拔后打不开或分辨率缺失**：先关闭预览，确认新设备出现在 `GET /api/cameras`，再刷新页面并重新打开；检查 V4L2 节点、MJPG 格式、实际帧尺寸和占用进程。预览采用串行约 80 ms 间隔，且旧会话的延迟关闭请求会被忽略；若设备句柄在拔插时失效，服务会自动重建一次。仍失败时手动停止占用设备的进程，记录 HTTP 响应和服务日志后再重试。
- **服务健康但识别失败**：检查 CANN 环境、OM 文件 SHA-256、输入契约和服务日志；健康接口不代表完成推理 smoke。
- **模板不存在**：确认注册和识别使用同一 canonical model ID、`npu` 和 `mixed_fp16`，并检查 `data/templates/` 是否属于当前 `PALMPRINT_ROOT`。当前版本不读取外部模板目录或密钥变量。
- **模板文件损坏或服务异常退出**：不要手工编辑 `.pstore`、`.pstore.bak`、`.pstore.previous` 或 `.pstore.version.json`。服务会先验证当前快照，再尝试最新 `.bak`，最后尝试上一代 `.previous`；恢复后会重写当前文件和 generation/SHA 记录。恢复点只代表最近一次已提交快照，需把整个 `data/templates/` 目录纳入人工备份。
- **图片归档失败**：识别结果仍会返回，但应查看服务日志；检查 `data/captures/` 权限和剩余磁盘空间。原图/ROI 只为内测复盘，不要加入 Git、HF 或同步包。
- **评测任务超时或报告目录过大**：HTTP 评测默认 `PALMPRINT_JOB_TIMEOUT_SECONDS=900`，允许范围为 10--`PALMPRINT_MAX_JOB_TIMEOUT_SECONDS` 秒；队列最多 2 个任务、每个最多 100 个身份。服务只自动清理过期的 `api_*` 报告，并受 `PALMPRINT_MAX_API_REPORT_FILES` 与 `PALMPRINT_MAX_API_REPORT_BYTES` 限制；离线 benchmark 报告不会被服务删除。
- **ACL 异常退出、设备 reset 或进程意外退出**：立即停止当前人工测试，保存时间、模型 ID、PID、HTTP 响应和报告摘要；重启当前版本，必要时回退到 CCNet。不要升级依赖、驱动、CANN 或应用版本，先建立问题记录。
- **`Health: Alarm`**：只作为设备诊断字段记录，不能单独判断模型失败或成功；应继续检查实际请求、进程和模板链路。

## 9. 常用接口

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 传输、PyACL、模型和推理就绪状态 |
| `GET /api/bootstrap` | 正式 NPU 模型和前端启动信息 |
| `GET /api/candidates` | 全部候选的审计状态，不可直接推理 |
| `GET /api/cameras` | 摄像头节点、格式和分辨率 |
| `POST /api/recognitions` | 上传图像并执行 NPU 识别 |
| `/api/enrollment-sessions/*` | 注册样本和模板 |
| `GET /api/captures` | 查看内测图片归档索引 |
| `GET /api/captures/{capture_id}/original` | 查看归档原图 |
| `GET /api/captures/{capture_id}/roi` | 查看归档 ROI |
| `/api/comparisons/*` | 生产接口固定拒绝候选评测任务；请使用 `python -m tools.offline.benchmark` |

详细的模型、数据集、转换、性能和板端证据见编号文档；本 README 不复制历史日志或基准表格。
