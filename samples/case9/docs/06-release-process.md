# 发布流程

## 版本策略

Ascend Edge Chat使用语义化版本。补丁版本修复实现缺陷，不改变公共契约或锁定的模型身份。次版本可以增加已通过验证的能力。主版本可以改变公共API行为、安全边界或产物兼容性。

## 发布检查清单

### 源码与产物审查

- 确认包版本和`CHANGELOG.md`条目。
- 确认当前代码和文档中只出现`ASCEND_EDGE_CHAT_`环境变量。
- 确认共享的`configs/release-model.json`包含一个固定的MindSpore配置，同时面向Ascend310B1/20T和Ascend310B4/8T。
- 确认两个板端目录默认选择该共享MindSpore配置；可选的PyACL候选仍保持显式的目标锁定。
- 确认可选的`configs/release-8t-static-kv.lock.json`包含其九个固定的公开产物、基座模型revision、目标SoC和Apache-2.0许可证。
- 校验目标专属发布锁、产物大小、SHA-256值和运行时指纹。
- 确认模型权重、缓存、报告和日志仍由Git忽略。
- 确认历史实验仍位于`docs/archive/`下，并且UI或README没有宣传它们。

### 本地验证

```bash
python -m compileall -q ascend_edge_chat
python -m pytest -q tests
python -m ascend_edge_chat --version
python -m ascend_edge_chat verify --data-dir <verified-20t-model-directory>
python -m ascend_edge_chat acl verify --data-dir <verified-8t-data-directory>
git diff --check
```

运行Markdown链接检查并检查渲染后的Mermaid图。更新教材源文件后，使用`pnpm run docs:build`构建仓库文档。

### 板端验证

从同一个干净的公开源码commit独立运行每块板。验收客户端要求显式的40位十六进制commit、干净的源码树、固定模型revision、经验证的产物字节数/SHA-256以及显式获取标签。它会记录观测到的B1/B4 SoC和算力档位，如果模型锁中不含该板，则执行阻断。中文语义质量仍与机器门禁分开。

- 20T：运行`scripts/verify/run-release-acceptance.sh`；记录MindSpore/MindNLP、目标HBM、模型哈希、gateway/UI JSON与SSE、长度检查、稳定性和性能。
- 8T：使用同一验收脚本和模型锁，并配合`configs/release-8t.env.example`；记录独立的MindSpore加载、gateway/UI JSON与SSE、长度检查、稳定性、HBM和性能。
- 可选PyACL：单独运行独立或候选专用的ACL验收；不得用其OM证据替代共享MindSpore路径。

教程UI没有身份认证，因此验证局域网必须可信：可达客户端可以提交聊天，并且在启用语音模式时，可以读取经净化的语音状态，或请求白名单内的板载音频控制。

原始日志和测量数据不要提交到Git。只提交一份简明、带证据链接的验证记录，其中包含确切的协议参数以及保留在板端的证据路径。

### 人工审查

启动本地文档预览：

```bash
pnpm run docs:dev
```

审查渲染后的部署说明、UI安全警告、限制声明和案例教程。静态构建成功不能替代这项审查。在审查者确认本地预览之前，不要创建tag、推送发布分支或发布GitHub release。

## 回滚原则

发布回滚只停止已识别的发布PID，并从其自身目录恢复此前经验证的部署。不得为了让发布通过而覆盖CANN、driver、firmware、OPP、共享Conda环境、另一个模型目录或未知的NPU进程。
