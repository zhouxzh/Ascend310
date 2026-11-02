# 教学模型目录

## 为何将技术运行与质量分开

本项目是一份测试教程。一个模型即使回答缓慢、仅使用英文、被截断或事实性较弱，只要能够加载、在昇腾设备上执行并通过API作答，就仍具有教学价值。因此，目录保留两项独立结论：

- `technical_status`：已测试的运行时路径是否可以复现；
- `text_integrity_status`：在记录的生成预算下，解码后的UTF-8文本是否完整且不含替换字符；
- `quality_status`：基于完整回答、记录的提示词、token预算和模型revision作出的人工语义质量独立结论。

文本完整性问题与达到最大token的截断都不会使一个已通过技术测试的配置消失，二者本身也都不构成中文语义质量结论。它们提示学生下一步应检查：tokenizer文件、chat template、解码行为、模型训练语言、上下文预算或输出限制。

## 当前教学条目

浏览器读取`configs/tutorial-model-catalog.json`。它会分别呈现历史8T/20T证据，但仅当当前SoC已由其注册表声明、每个锁定的产物文件均存在，并且启动路径校验完整的字节数/SHA-256锁时，才会启用某个条目。标记为`blocked`或`not-run`的条目仍保持可见，使学生能够区分未测试的前置条件与实测失败。

| 配置 | 技术结果 | 质量结果 | 结果意义 |
| --- | --- | --- | --- |
| `qwen1.5-0.5b-chat` | 20T观测通过；当前8T尝试在HBM/运行时初始化阶段被阻断 | Pending（待定） | 默认20T基线；8T不继承该配置 |
| `qwen1.5-1.8b-mindspore` | 20T机器门禁观测通过 | Pending（待定） | 更大的Qwen1.5对照；8T必须独立测量 |
| `qwen2-0.5b-instruct-mindspore` | 20T API、64-token、稳定性和性能通过 | Pending（待定） | 历史上的`U+FFFD`是tokenizer/解码观察结果，不是NPU或语义质量判定 |
| `qwen2-1.5b-instruct-mindspore` | 20T机器门禁观测通过；修正后的ChatML模板达到64/128/192 token长度门禁 | Pending（待定） | 缺少tokenizer模板曾导致Qwen1.5风格角色标记；该配置模板修复了这条路径，但256 token仍以fail-closed方式失败 |
| `qwen2.5-0.5b-mindspore` | 20T修正超时后的机器门禁通过 | Pending（待定） | 此前的仅上下文/30秒超时报告是一条调试教训，不是最终技术结果 |
| `qwen2.5-1.5b-mindspore` | 20T低占用路径观测为降级 | Pending（待定） | 低占用API路径运行过；另一次高占用运行产生`Alloc failed` |
| `tinyllama-1.1b-mindspore` | 20T短时冒烟测试；当前教学注册表没有可独立启动的8T契约 | Pending（待定） | 面向英文的模型；长输出暴露出需要单独质量运行的文本完整性/TBE边界 |
| `deepseek-r1-qwen-1.5b-mindspore` | 20T降级证据；当前教学注册表没有可独立启动的8T契约 | Pending（待定） | `<think>`可能在最终回答前耗尽短预算；需要完成回答审查 |
| `qwen3-0.6b-mindspore` | 加载前被阻断 | Not run（未运行） | MindNLP 0.4.1没有加载器；不对硬件本身作任何判断 |
| `minicpm3-4b-mindspore` | Not run（未运行） | Not run（未运行） | 专用加载器、产物和内存门禁仍属未来工作 |

本表刻意不作为排行榜。8T与20T结果属于彼此独立的证据集，质量标签不能替代原始回答文件。

## 启动教学配置

worker仍采用单模型、单请求模式。这样可以避免将多个大型检查点加载到同一个310B设备。页面选择器可以切换模型，但切换方式为受控重启：它校验所选目录/注册表/缓存条目，停止已报告的worker PID，通过能感知CANN的启动器启动固定配置，等待`/health`，并清除浏览器会话。如果新worker启动失败，它会回滚到先前的配置。选择器绝不会接收任意模型路径、shell命令或Python表达式。

```bash
export ASCEND_EDGE_CHAT_PROFILE=qwen2-1.5b-instruct-mindspore
ascend-edge-chat worker \
  --profile "$ASCEND_EDGE_CHAT_PROFILE" \
  --registry configs/tutorial-models.json
```

启动前，将`ASCEND_EDGE_CHAT_DATA_DIR`设为包含所选配置经过验证的本地缓存的目录。启动器会校验完整的教学产物锁：每个声明的文件名、字节数、SHA-256、仓库和revision都必须匹配。它会拒绝缺失、不完整或哈希不匹配的缓存，并且绝不在启动期间回退到下载未固定模型。

worker就绪后，照常启动gateway和UI。在教学页面中，选择一个可用配置，并等待状态返回“就绪”后再发送提示词。选择器会清除当前会话，因为使用一种chat template构建的对话不得带入另一个模型。如果模型缓存不完整或worker未能进入健康状态，UI会显示该失败，并在可能时恢复先前的worker。选择器在生成期间禁用；在终止SSE事件和短暂的服务端清理窗口结束后，它才恢复可用。

## 解释失败的质量探针

当可选模型给出较差回答时，按以下顺序处理：

1. 保存完整的JSON/SSE响应和确切的配置revision。
2. 记录请求的`max_tokens`、实际completion-token数、`finish_reason`，以及输出是模型停止还是上下文/最大token截断。以`length`结束属于长度结果，不是语义失败。
3. 将tokenizer和chat-template文件与配置revision进行对照。
4. 检查`npu-smi`、worker健康状况和提供方放置字段。没有设备/运行时错误时，不得将质量失败重新标记为NPU失败。
5. 将结果记录在该配置的`quality_detail`下；除非运行时本身失败，否则保持技术状态不变。

### 人工中文质量协议

对每个配置使用相同的10条中文提示词、全新对话、贪婪解码和`max_tokens=256`（或该配置记录的封顶值）运行。保留原始JSON/SSE响应、配置revision、prompt-token数、completion-token数和`finish_reason`。以`length`结束的回答会标记为**不完整**，不作为失败回答评分。首先单独记录UTF-8/文本完整性。然后由至少两名审查者按0/1/2评分量表评估相关性、事实性、流畅性和完整性；保留每位审查者的分数和分歧。只有这项审查可以将`quality_status`改为`passed`或`failed`。

对于TinyLlama，中文质量仍属假设，因为其训练目标以英文为主。对于DeepSeek-R1 Distill，可见的思考前缀可能在最终回答前耗尽短预算。对于Qwen2/Qwen2.5，替换字符仍是值得调试的文本完整性问题，即使NPU/API门禁已经通过。

## 尚不可选择的模型

Qwen3可见但不可选择，因为安装的MindNLP 0.4.1未提供其加载器。MiniCPM3可见但不可选择，因为其专用加载器、4B产物和内存门禁尚未运行。这些是`blocked`或`not-run`前置条件，不代表Ascend 310B永远无法运行这些模型。未来实验在收集相同的环境、产物、NPU、API和性能证据后，可以将某个条目改为`short-smoke`或`observed-pass`。
