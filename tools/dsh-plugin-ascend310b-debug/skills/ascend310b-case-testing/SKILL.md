---
name: ascend310b-case-testing
description: Ascend 310B1/310B4 案例的可发布复现测试。用于从 GitHub 拉取指定 commit 的案例程序、从 Hugging Face（默认 hf-mirror.com）按固定 revision 下载模型、在板端完成环境预检/测试/ATC/ACL 验收，并禁止把控制器本地模型或源码通过 scp/rsync 当作发布链路。
---

# Ascend 310B 案例测试与发布复现

这个 skill 解决的是“作者电脑上能跑，但读者无法复现”的问题。案例测试必须从**公开源码来源**和**模型来源**重建运行现场，不能把控制器上的本地模型、OM、数据集或未提交源码复制到开发板后宣称通过。

只适用于 Ascend 310B1 / 310B4。探测到其他 SoC 时停止，不要套用 310B 的结论。

## 1. 两类资产合同

| 资产 | 发布测试允许的来源 | 发布测试禁止的做法 |
| --- | --- | --- |
| 案例程序 | 公开 GitHub 仓库的固定 commit/tag；板端 `git clone`、`git fetch` 或 `git archive` | 从作者电脑 `scp`/`rsync` 整个源码目录后当作可复现来源 |
| 模型权重 | Hugging Face 仓库的固定 revision；通过 `hf-mirror.com` 或 `ascend_model_download source=hf` 下载 | 从作者电脑 `scp`/`rsync` 模型、ONNX、OM、tokenizer 到板端 |
| 配置和密钥 | profile/environment，不含明文提交 | 把 token、cookie、SSH 密码写入仓库或命令日志 |
| 生成物 | 板端生成 OM、报告和日志，记录大小与 SHA-256 | 把本地生成的 OM/报告当作公开模型发布内容 |

源码和模型可以分开发布，但都必须有稳定的来源标识：

```text
source_repo   = https://github.com/<owner>/<repo>.git
source_commit = <40 位 SHA，不是仅分支名>
model_repo    = <owner>/<model>
model_revision= <commit SHA，不是可变的 main>
```

## 2. 板端前置门

先运行 `ascend_probe`，记录 SoC、档位、CANN、驱动、Python、统一 `ascend` 环境、`acl`、`atc` 和存储空间。然后确认：

- 板端能访问 GitHub；不能访问时，明确把测试标为 `blocked`，不能改用本地副本冒充公开下载。
- 板端能访问 `https://hf-mirror.com`；不能访问时，记录网络失败，不要悄悄切到作者私有缓存。
- 目标目录在用户工作区或明确的发布目录；不得把模型写进仓库的 `src/`、`samples/` 跟踪文件或 Git 索引。
- 代码和模型的目标路径分开，例如：

```text
$HOME/Ascend310-case-run/<case>/src/
$HOME/Ascend310-case-run/<case>/assets/models/
$HOME/Ascend310-case-run/<case>/run/
$HOME/Ascend310-case-run/<case>/reports/
```

## 3. 从 GitHub 获取程序

在板端执行，不要求本地控制器先有源码：

```bash
export CASE_REPO='https://github.com/<owner>/Ascend310.git'
export CASE_COMMIT='<完整 commit SHA>'
export CASE_ROOT="$HOME/Ascend310-case-run/caseN"
mkdir -p "$CASE_ROOT"
if [ ! -d "$CASE_ROOT/src/.git" ]; then
  git clone "$CASE_REPO" "$CASE_ROOT/src"
fi
git -C "$CASE_ROOT/src" fetch --all --prune
git -C "$CASE_ROOT/src" checkout --detach "$CASE_COMMIT"
test "$(git -C "$CASE_ROOT/src" rev-parse HEAD)" = "$CASE_COMMIT"
git -C "$CASE_ROOT/src" status --short
```

要求：

- 记录 clone URL、完整 commit、分支/标签来源和 `git status --short`。
- 发布验证使用 `--detach` 的固定 commit；不要用“作者电脑上当前目录”作为源码证据。
- 需要 GitHub token 时使用 SSH key 或凭据助手，不要把 token 拼进 URL 或日志；私有仓库不能作为公开复现来源。
- 源码测试命令在该固定 checkout 内执行。任何本地补丁必须先提交到公开仓库，不能只在控制器工作区存在。

## 4. 从 Hugging Face 获取模型

优先使用板端 Hugging Face 客户端和镜像：

```bash
export HF_ENDPOINT='https://hf-mirror.com'
export MODEL_REPO='<owner>/<model>'
export MODEL_REVISION='<完整 revision SHA>'
export MODEL_DIR="$CASE_ROOT/assets/models/<model-id>"
mkdir -p "$MODEL_DIR"
hf download "$MODEL_REPO" \
  --revision "$MODEL_REVISION" \
  --local-dir "$MODEL_DIR"
```

如果案例使用单个文件，也可以用插件的板端下载工具：先 `ascend_model_download` 的 `dryRun: true`，再以 `source: "hf"`、固定 `repoId`、`filename`、`revision` 和可选 `sha256` 执行。工具输出中的 `integrity` 必须为 `verified`，否则不能进入 ATC/ACL。

下载后必须：

- 检查文件字节数、SHA-256、tokenizer/config 和许可证；
- 保存 Hugging Face 仓库、revision、文件名、大小和哈希到本次报告；
- 确认模型不属于 Git 跟踪文件，且没有从本地模型目录复制；
- 如果模型需要登录，token 只从板端环境或 profile 读取，不写入命令输出。

禁止的发布证据：

```text
scp ./models/model.safetensors HwHiAiUser@board:...
rsync -a ./models/ HwHiAiUser@board:...
# 从作者本地缓存复制模型后直接运行
```

## 5. 测试和证据顺序

按以下顺序执行，前一层失败不要用后一层结果覆盖：

1. **source gate**：板端 checkout 的 commit 与记录一致，工作树干净。
2. **model gate**：HF revision、大小、SHA-256、许可证和文件集合一致。
3. **environment gate**：统一 `ascend` 环境、`PYTHONNOUSERSITE=1`、CANN source、`import acl`、ATC 路径。
4. **load/smoke gate**：加载真实模型或 OM，记录路径、哈希、返回码和输入合同。
5. **task gate**：运行案例测试/服务/摄像头/API，记录输入、输出和原始报告。
6. **release gate**：从固定 commit 和固定 HF revision 重跑，确认没有本地模型或未提交源码依赖。

推荐报告字段：

```text
case:
source_repo:
source_commit:
model_repo:
model_revision:
model_files: [{name, bytes, sha256, license}]
board: {soc, tier, hostname, mac}
software: {cann, driver, python, conda_env}
commands: [probe, download, test, atc, inference]
result: observed-pass | observed-fail | untested | blocked
raw_report:
```

可复制模板见 [references/case-release-manifest.json](references/case-release-manifest.json)；发布前把模板中的 commit、revision、文件大小、哈希和许可证替换为本次实测值。

## 6. 离线例外

只有在用户明确要求离线包、且网络不可用时，才允许使用离线资产；必须满足：

- 包内带来源仓库、HF 仓库、revision、文件清单、字节数、SHA-256 和许可证；
- 板端重新校验所有哈希后才可使用；
- 报告标记 `offline-bundle`，不能声称“从公开 HF 下载成功”；
- 不能把离线包当成发布后的默认安装路径，也不能把本地模型清单写成公开可复现证据。

## 7. 停止条件

出现以下任一情况，停止测试并把结果标为 `blocked` 或 `untested`：

- 只有分支名、没有完整 commit；
- 模型 revision 是 `main` 或未记录；
- 命令依赖作者电脑上的模型路径、私有缓存或未提交补丁；
- 板端下载失败后用本地复制绕过，却没有改变证据标签；
- 模型文件进入 Git、源码工作树不干净，或测试命令引用了 `../../models` 之外的本地绝对路径；
- 没有重新校验模型哈希就进入 ATC/ACL；
- 只验证 HTTP 200，没有真实模型加载或任务证据。

## 8. 汇报模板

```text
已从 <GitHub URL> 的 commit <SHA> 在板端重建源码。
已从 <HF repo> revision <SHA> 通过 <hf-mirror/ascend_model_download> 下载模型。
模型文件 <name> = <bytes> bytes，SHA-256 <hash>。
本地控制器没有向板端复制模型或未提交源码。
环境：<SoC/档位>，CANN <version>，驱动 <version>，conda ascend。
测试：<命令/输入/输出/退出码>。
结论：observed-pass / observed-fail / untested / blocked。
原始报告：<path>。
```
