# case5 双板调试记录

## 运行

- 部署目录：`/home/HwHiAiUser/Ascend310/cases/case5`
- 测试入口：`python -m pytest -q`
- 前端：PySide6/PyQtGraph 触摸仪表盘，不是浏览器页面；本次没有保存 UI 截图。
- 硬件：Hantek 6022BE / RTL-SDR、libsigrok、FFTW、OM 均未接入本轮测试。

## 结果

| 项目 | 8T | 20T |
| --- | --- | --- |
| 初次 pytest 收集 | 失败，缺 `onnx` | 失败，缺 `onnx` |
| 安装 `onnx`/`onnxruntime` 后 pytest | 227 passed / 17 skipped，5.34s | 227 passed / 17 skipped，5.74s |
| 硬件测试 | 未运行 | 未运行 |
| UI 截图 | N/A | N/A |

第一次运行在测试收集阶段因缺少 `onnx` 退出；按案例开发依赖安装后两板均完成纯 Python 回归。17 个跳过项是硬件/可选依赖门，仍不能替代 Hantek/RTL-SDR、FFTW 和 OM 的实机验收。

## 证据

- [8T 初次 pytest 失败](case5-pytest-8t.log)
- [20T 初次 pytest 失败](case5-pytest-20t.log)
- [8T 安装依赖日志](case5-pip-8t.log)
- [20T 安装依赖日志](case5-pip-20t.log)
- [8T 重试 pytest](case5-pytest-retry-8t.log)
- [20T 重试 pytest](case5-pytest-retry-20t.log)
