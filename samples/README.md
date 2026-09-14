# samples 配套代码说明

本目录只负责“代码在哪里、如何进入和运行”。它不是全仓库文档索引；工程文档请从 [`../docs/README.md`](../docs/README.md) 开始，目录职责请看 [`CASE_LAYOUT.md`](CASE_LAYOUT.md)。CANN、PyACL、ATC、OM、DVPP 和 `npu-smi` 相关命令必须在真实 Ascend 310B 设备执行；普通开发机只适合文档、语法、前端和不依赖硬件的单元测试。

案例的稳定 ID、语义关键词和迁移状态见 [实践案例目录规范](CASE_LAYOUT.md) 与 [案例索引](case-index.json)。仓库级工程文档入口见 [`../docs/README.md`](../docs/README.md)；根 README 只负责项目首页和本地书稿起步。

## 📚 章节代码

`chapter2` 至 `chapter8` 分别对应 CANN/ATC、NPU 训练、PyACL、DVPP、自定义算子、性能优化和量化教程。每个目录的 README 说明其运行入口。

## 🧩 实践案例

| 目录 | 案例 |
| --- | --- |
| [`case1/`](case1/) | 案例 1：智能考勤机 |
| [`case2/`](case2/) | 案例 2：目标跟踪检测 |
| [`case3/`](case3/) | 案例 3：智能电子琴 |
| [`case4/`](case4/) | 案例 4：掌纹识别 |
| [`case5/`](case5/) | 案例 5：智能数据采集分析仪 |
| [`case6/`](case6/) | 案例 6：智能小车 |
| [`case7/`](case7/) | 案例 7：智能相册 |
| [`case8/`](case8/) | 案例 8：实时手势识别 |
| [`case9/`](case9/) | 案例 9：智能聊天机器人 |

## 案例 7 入口

案例 7 将 Orange Pi AIpro / Ascend 310B4 作为 NPU 相册服务器，同时服务手机、10 寸 QDtech MPI1001 触摸屏、ESP32/PhotoPainter 和 E6 dry-run。运行入口是 [`case7/README.md`](case7/README.md)，完整理论教程是 [`../src/experiment/case7.md`](../src/experiment/case7.md)，按任务查找工程文档从 [`case7/docs/README.md`](case7/docs/README.md) 开始。

## 🧾 统一目录角色

- `README.md`：运行入口、语义名称和案例索引
- `app.py`：保持兼容的服务入口
- `face_attendance/`、`time_frequency_dashboard/` 等语义包：业务运行代码
- `scripts/`、`tests/`、`docs/`：操作脚本、测试和工程说明
- `models/`、`data/`、`reports/`：本地模型、运行数据和验证证据，默认不进入 Git
