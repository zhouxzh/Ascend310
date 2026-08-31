# samples 配套代码说明

本目录存放书稿章节和实践案例的可运行代码。CANN、PyACL、ATC、OM、DVPP 和 `npu-smi` 相关命令必须在真实 Ascend 310B 设备执行；普通开发机只适合文档、语法、前端和不依赖硬件的单元测试。

案例的稳定 ID、语义关键词和迁移状态见 [实践案例目录规范](CASE_LAYOUT.md) 与 [案例索引](case-index.json)。

## 📚 章节代码

`chapter2` 至 `chapter8` 分别对应 CANN/ATC、NPU 训练、PyACL、DVPP、自定义算子、性能优化和量化教程。每个目录的 README 说明其运行入口。

## 🧩 实践案例

| 目录 | 案例 |
| --- | --- |
| [`case1/`](case1/) | Case 1 · 人脸考勤（face-attendance） |
| [`case2/`](case2/) | 目标检测与多目标跟踪 |
| [`case3/`](case3/) | 智能电子琴 |
| [`case4/`](case4/) | 智能掌纹识别机 |
| [`case5/`](case5/) | 智能数据采集仪 |
| [`case6/`](case6/) | 小车视觉感知 |
| [`case7/`](case7/) | 昇腾 310B 智能相册服务器 |
| [`case8/`](case8/) | 手势识别 |
| [`case9/`](case9/) | OpenAI 兼容 RAG 网关 |

## 🖼️ Case7 入口

Case7 将 Orange Pi AIpro / Ascend 310B4 作为 NPU 相册服务器，同时服务手机、10 寸 QDtech MPI1001 触摸屏、ESP32/PhotoPainter 和 E6 dry-run。运行入口是 [`case7/README.md`](case7/README.md)，完整理论教程是 [`../src/experiment/case7.md`](../src/experiment/case7.md)，工程文档在 [`case7/docs/`](case7/docs/)。

## 🧾 统一目录角色

- `README.md`：运行入口、语义名称和案例索引
- `app.py`：保持兼容的服务入口
- `face_attendance/`、`time_frequency_dashboard/` 等语义包：业务运行代码
- `scripts/`、`tests/`、`docs/`：操作脚本、测试和工程说明
- `models/`、`data/`、`reports/`：本地模型、运行数据和验证证据，默认不进入 Git
