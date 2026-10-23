# samples 配套代码说明

本目录只负责“代码在哪里、如何进入和运行”。它不是全仓库文档索引；工程文档请从 [`../docs/README.md`](../docs/README.md) 开始，目录职责请看 [`CASE_LAYOUT.md`](CASE_LAYOUT.md)。CANN、PyACL、ATC、OM、DVPP 和 `npu-smi` 相关命令必须在真实 Ascend 310B 设备执行；普通开发机只适合文档、语法、前端和不依赖硬件的单元测试。章节样例的 8T/20T 实测数据见 [chapter2–9 双板对照记录](../docs/ascend310b-chapter-board-comparison-20261017.md)，实践案例的前端截图和运行证据见 [case1–9 双板调试记录](../docs/case-board-runs-20261017/README.md)。

案例的稳定 ID、语义关键词和迁移状态见 [实践案例目录规范](CASE_LAYOUT.md) 与 [案例索引](case-index.json)。仓库级工程文档入口见 [`../docs/README.md`](../docs/README.md)；根 README 只负责项目首页和本地书稿起步。

## 发布复现来源

案例测试必须能从公开来源重建，不以作者电脑上的运行目录为前置条件：

- **程序**：在板端从固定的 GitHub 仓库和完整 commit SHA 检出；记录 `git status --short`，不把本地未提交源码复制过去冒充发布来源。
- **模型**：从 Hugging Face 仓库按固定 revision 下载，默认使用 `HF_ENDPOINT=https://hf-mirror.com`；记录 repo、revision、文件清单、字节数、SHA-256 和许可证。
- **禁止默认使用** `scp`/`rsync` 把作者本地的模型、ONNX、OM、数据集或未提交源码复制到开发板。离线包只能是明确的 `offline-bundle` 例外，并带完整来源和哈希。
- 具体门禁、命令模板和报告字段见调试插件的 [`ascend310b-case-testing`](../tools/dsh-plugin-ascend310b-debug/skills/ascend310b-case-testing/SKILL.md)。

## 📚 章节代码

`chapter2` 至 `chapter9` 分别对应 CANN/ATC、NPU 训练、PyACL、DVPP、自定义算子、性能优化和量化教程。每个目录的 README 说明运行入口，详细工程合同和证据边界从对应 docs 索引进入：

| 章节样例 | 工程文档 |
| --- | --- |
| `chapter2` | [docs/README.md](chapter2/docs/README.md) |
| `chapter3` | [docs/README.md](chapter3/docs/README.md) |
| `chapter4` | [docs/README.md](chapter4/docs/README.md) |
| `chapter5` | [docs/README.md](chapter5/docs/README.md) |
| `chapter6` | [docs/README.md](chapter6/docs/README.md) |
| `chapter7` | [docs/README.md](chapter7/docs/README.md) |
| `chapter8` | [docs/README.md](chapter8/docs/README.md) |
| `chapter9` | [docs/README.md](chapter9/docs/README.md) |


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
