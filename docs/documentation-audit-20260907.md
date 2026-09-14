# 文档结构审计记录

本记录只说明仓库文档入口和维护边界，不记录任何板端历史性能或模型验收数字。

## 审计结论

仓库现在按四层组织：

| 层级 | 入口 | 职责 |
| --- | --- | --- |
| 项目首页 | [`README.md`](../README.md) | 项目定位、本地起步和入口链接 |
| 教材源稿 | [`src/README.md`](../src/README.md) | VuePress/Pandoc 书稿导航和理论正文 |
| 可运行代码 | [`samples/README.md`](../samples/README.md) | 案例代码目录和运行入口 |
| 工程文档 | 本目录和各案例 `docs/` | 部署、接口、验收和结构规范 |

根 README 不再复制全书目录、案例长篇说明或历史报告；`docs/README.md` 是全仓库工程文档索引；`samples/case7/README.md` 是 Case7 完整操作手册；`samples/case7/docs/README.md` 是 Case7 工程专题索引。

## Case7 文档入口

1. 从 [`samples/case7/README.md`](../samples/case7/README.md) 完成部署、上传、触摸屏操作和设备注册。
2. 从 [`samples/case7/docs/README.md`](../samples/case7/docs/README.md) 按任务进入 7 份工程文档。
3. 从 [`src/experiment/case7.md`](../src/experiment/case7.md) 阅读架构、模型原理和 NPU 迁移教程。

## 维护边界

- `samples/case7/docs/` 只保留当前工作流，不维护旧板卡、旧 IP 和早期设备发送实验。
- 旧文档编号已合并为 01-07 主题文档，仓库内不保留旧路径兼容页。
- `latex/`、`src/.vuepress/dist/` 和报告、模型、照片等运行时资产不由文档整理操作修改。
- 文档数字必须有当前代码或当前报告支持；没有证据就写“待验证”。

## 检查命令

```bash
rg -n "README|docs/|samples/|src/|latex/" README.md docs samples src --glob "*.md"
git diff --check
pnpm docs:build
```
