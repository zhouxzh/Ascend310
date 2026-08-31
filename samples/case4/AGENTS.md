# 掌纹识别工作台维护约束

本目录采用手动部署版发布边界，优先级高于父仓库中针对旧版样例的说明。

## 正式入口

- `app.py` 只负责兼容启动参数；正式实现位于 `palmprint_workbench/`。
- 服务启动使用 `python -m palmprint_workbench.api --host ... --port ...`。
- 资产检查使用 `python -m palmprint_workbench.tools.verify_assets --strict`。
- `tools/board/`、`tools/offline/` 和 `tools/export/` 分别承载板端诊断、离线评测和模型导出。
- 根目录平铺模块和历史 shell 文件不属于源码发行包；不要重新添加 `setup.sh`、启动/同步 shell 包装器或 wildcard 兼容导入。

## 环境边界

环境激活、依赖安装、文件同步和服务停止都由操作者按 `README.md` 手动执行。源码不得在 import 或启动时联网安装、下载数据、构建 EDCC 或修改系统环境。

生产 API 只接受已准入的 NPU `mixed_fp16` embedding。CPU、EDCC、origin 和未准入候选只能由 `tools/offline/` 使用，不能进入 registry、模板、前端模型选择或 HTTP 推理路由。

## 资产与证据

OM、ONNX、checkpoint、数据集、模板和原始板端报告默认不进 Git。复制 OM 前按 `om_manifest.json` 核对字节数和 SHA-256；没有许可证批准和 HF 认证时，不执行公开上传。转换、数值一致性、识别精度、性能、温度和生命周期证据必须分开记录。

## 修改与验证

使用 `apply_patch` 编辑；保留用户已有改动。修改 Python 后运行 `python -m compileall -q app.py palmprint_workbench tools` 和相关 pytest；修改前端后在 `frontend/` 运行 `npm test`、`npm run build`，再运行静态 bundle 校验。不得在本地运行 CANN、ATC、PyACL、`npu-smi` 或摄像头硬件测试；这些只能在配置正确的 Ascend 310B4 / 8T 开发板上执行并记录原始证据。

独立教程 `src/experiment/case4.md` 是父仓库固定路径，必须自包含，不得依赖本目录的编号文档。
