# MindSpore Lite 环境安装与 CANN 版本矩阵

本文件回答一个具体问题：在当前 Ascend 310B4 / 8T 开发板上，MindSpore
Lite 应该使用什么环境，哪些安装已经实测通过，哪些问题仍然没有解决。
它不把 CPU 推理结果或 Python 导入结果当作 NPU 端到端成功。

## 当前板卡与软件指纹

| 项目 | 实测值 | 证据标签 |
| --- | --- | --- |
| 板卡 | Ascend310B4 / 8T，aarch64 | `observed-pass` |
| 地址 | `192.168.1.90` | `observed` |
| 驱动 | `25.2.0`，firmware 组件包含 `7.3.3.0.b309` | `observed` |
| Toolkit 目录 | `/usr/local/Ascend/ascend-toolkit/8.0.0` | `observed` |
| Toolkit 内部版本 | `7.6.0.1.220`，`version_dir=8.0.0` | `observed` |
| Python | `3.9.25`（Lite 隔离环境） | `observed-pass` |
| Lite wheel | `2.4.10`，Linux-aarch64、Python 3.9 cloud/Ascend wheel | `observed-pass` |
| Lite wheel SHA-256 | `96a1da9be455e03caf32cdfee5206c4599d4c5aa9dee25dd4b48e1361d5d1fba` | `verified` |

驱动和 firmware 的版本来自板端报告；不能只根据 Toolkit 目录名推导出完整
CANN 发行组合。`npu-smi` 的 `Health: Alarm` 作为诊断字段保存，不作为 Lite
兼容性的单独失败条件。

## Lite 运行环境的必要条件

当前 Lite Python 绑定的环境检查器会读取 `ASCEND_HOME_PATH`、Toolkit 的
`latest/lib64` 和 `latest/opp`，并要求 `te`、`hccl` 等 Python 依赖的主次版本
属于 `7.5` 或 `7.6`。因此当前 CANN 8.0.0/内部 7.6 组合在“版本检查”层是
可接受的；这不等于图编译一定成功。

应使用 Linux-aarch64 的 **cloud/Ascend** wheel，而不是只含 CPU 的 device
包。MindSpore Lite 2.4.10 下载页列出了该 Python 3.9 wheel 及 SHA-256；Lite
官方 Python 云侧流程要求在创建 Ascend context 后调用 `build_from_file` 和
`predict`。[Lite 2.4.10 下载页](https://www.mindspore.cn/lite/docs/zh-CN/r2.4.10/use/downloads.html)
[Lite Python 云侧推理流程](https://www.mindspore.cn/lite/docs/zh-CN/r2.4.10/mindir/runtime_python.html)

建议的隔离边界如下：

- Python 3.9；
- `mindspore_lite` 一个版本；
- NumPy 1.x（当前 2.x 会触发 `np.float_` 兼容错误）；
- 与 Toolkit 同版本的 `te`、`hccl`（以及它们声明的 `sympy`、`decorator` 等依赖）；
- 不安装完整 `mindspore`、`torch`、`torch_npu`、`torchaudio`、`transformers`、
  `onnxruntime`、MindSpore NLP 或其他推理框架；
- 不把另一个 conda 环境的完整 `site-packages` 作为长期 `PYTHONPATH`。此前为
  诊断临时借用了 base 的 CANN Python 模块，虽使依赖检查通过，却不是可交付的
  隔离生产环境。

## 当前 CANN 8.0 的安装方式

以下命令只创建用户级 conda 环境，不覆盖系统 CANN、驱动、firmware 或 OPP。
执行前应确认 wheel 文件已通过大小和 SHA-256 校验：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda create --yes --no-default-packages -n case9-mslite2410 python=3.9 pip
conda activate case9-mslite2410
export PYTHONNOUSERSITE=1

# 这是已校验的官方 Lite wheel；路径按板端实际文件调整。
python -m pip install --no-deps --no-index \
  /home/HwHiAiUser/case9-mindspore-lite-2.5.0/artifacts/mindspore_lite-2.4.10-cp39-cp39-linux_aarch64.whl

# Lite 2.4/2.5 的 Python 绑定需要 NumPy 1.x。
python -m pip install --no-deps --no-index \
  /path/to/numpy-1.26.4-*.whl \
  /path/to/sympy-1.12-*.whl \
  /path/to/decorator-5.1.1-*.whl

export ASCEND_HOME_PATH=/usr/local/Ascend/ascend-toolkit/latest
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
```

`te`、`hccl` 和 `topi` 不能从网上随意安装“最新版”。先在同一 Toolkit 中
查找随 CANN 提供的 wheel 或 Python 目录，确认版本后再安装或以只读路径加入
`PYTHONPATH`：

```bash
find "$ASCEND_HOME_PATH" -type f \( -name 'te-*.whl' -o -name 'hccl-*.whl' -o -name 'topi-*.whl' \) -print
find "$ASCEND_HOME_PATH" -type d -path '*/python/site-packages' -print
python -c 'import te, hccl; print(te.__file__); print(hccl.__file__)'
```

安装完成后必须在**新进程**中运行 Lite 自带检查器，并保存输出：

```bash
python - <<'PY'
from mindspore_lite._check_ascend import AscendEnvChecker
checker = AscendEnvChecker()
print("check_env", checker.check_env())
print("check_python_deps", checker.check_python_deps())
PY
python -c 'import mindspore_lite as mslite; print(mslite.__version__)'
```

本板结果为 `check_env=True`、`check_python_deps=True`（使用 NumPy 1.x、
`sympy`/`decorator` 和 CANN Python 模块后）。这是环境门通过，不是 NPU 图
编译门通过。

## 已实测的 Lite 结果

在同一块板上，Lite 2.4.10 和 2.5.0 都完成了导入、Ascend Context 配置和 CPU
MindIR `build + predict`。官方 MobileNetV2 MindIR 和只有一个 Add 算子的最小
MindIR，在 Ascend `build_from_file` 阶段均以 `SIGSEGV`、退出码 `139` 结束；
`predict` 没有执行。补齐依赖后，日志进入
`GenAclOptions: ge.socVersion=Ascend310B4`，仍在 GE/Lite 插件初始化处崩溃。

因此当前分层判定是：

| 层 | 状态 |
| --- | --- |
| Python 3.9 + Lite wheel 导入 | `observed-pass` |
| ACL 初始化、设备 context | `observed-pass` |
| Lite 环境检查 | `observed-pass` |
| MindIR CPU 构建/预测 | `observed-pass` |
| Lite Ascend 图构建 | `observed-fail`，可复现 `SIGSEGV(139)` |
| Lite Ascend 算子预测 | `not-run` |
| Lite LLM 加载和生成 | `blocked` |

完整崩溃栈位于板端 `/var/log/npu/coredump/`，经过
`libaicpu_extend_kernels.so`、`libfe.so`、`libgraph_base.so` 和
`libge_compiler.so`。同一环境下完整 MindSpore 2.4.10 的 Add 算子可在 Ascend
输出 `[3,3,3,3]`，所以当前证据把失败范围限定为 Lite Ascend graph-build/plugin
组合，而不是“310B、ACL 或 CANN 完全不能运行”。

## 是否现在升级到 CANN 8.5

**当前不建议立即升级。** 现有 Lite 2.4.10/2.5.0 的环境检查已经可以在 CANN
内部 7.6 通过；升级到 8.5 还没有证据能修复这个 GE 插件崩溃，且会扩大变量
范围。CANN 8.5 改用了 Toolkit + Ops 的新包布局，并要求匹配的 HDK、driver
和 firmware；Orange Pi 页面中的 `Ascend-cann-310b-ops_8.5.0` 只是升级系统
CANN 的示例，不能把它覆盖安装到当前系统上。[CANN 8.5 发布说明](https://www.hiascend.com/document/detail/en/canncommercial/850/releasenote/releasenote_0001.html)

需要特别区分：引用的 [Orange Pi 2.8 环境页面](https://www.mindspore.cn/tutorials/en/r2.8.0/orange_pi/environment_setup.html)
是 **完整 MindSpore 2.8.0** 的安装教程。页面明确在后半部分执行
`pip show mindspore`、安装 `MindSpore 2.8.0`，并用 `mindspore.run_check()`
验证；它不是 MindSpore Lite 的安装或兼容性矩阵，不能作为 Lite wheel 与 CANN
8.5 的直接配对证据。本文件只引用它来确认 Orange Pi 示例中的系统 CANN 升级
包名称和顺序。

只有在用户明确批准系统级升级后，才进入以下单独实验：

1. 先备份当前 Toolkit、驱动/firmware 版本、Lite 日志和 coredump；
2. 获取与 310B4、当前 Linux 内核和 HDK 匹配的 CANN 8.5 Toolkit + 310B Ops，
   以及配套 driver/firmware；
3. 选择官方文档明确匹配该 CANN 组合的 Lite/转换器版本；
4. 在新环境中重复 ACL、最小 AddNet、MobileNetV2 三层探针；
5. 只有 `build_from_file + predict` 通过后，才评估 LLM MindIR。

MindSpore 2.7.2 的完整框架文档确实以 CANN 8.5 为配套版本，但这不能直接
推导出独立 `mindspore_lite` wheel 或当前板 firmware 的兼容性；完整 MindSpore
与 Lite 必须分别验证。[MindSpore 2.7.2 发布说明](https://www.mindspore.cn/docs/zh-CN/r2.7.2/RELEASE.html)

## 结论

目前“正确安装”的答案是：使用 Python 3.9、独立 Lite cloud/Ascend aarch64
wheel、NumPy 1.x、同一 CANN 提供的 7.6 Python 依赖，并在同一 shell 中设置
Toolkit 环境变量。这个组合的环境检查已经通过，但 Lite 在本板 Ascend 图构建
仍然可复现崩溃，所以**现在还不能说通过安装即可让 mslite 跑起 NPU 推理**。

下一步应优先获得与当前 CANN 8.0/310B4 明确匹配的 Lite runtime/plugin 或
官方已验证 MindIR；在此之前不安装 Torch、不混用完整 MindSpore，也不盲目升级
CANN 8.5。
