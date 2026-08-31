---
marp: true
size: 16:9
theme: default
paginate: true
header: "昇腾310B 8周教学"
footer: "第4周：Vibe Coding基础"
---

# 第4周：Vibe Coding基础

昇腾 310B 8周教学计划

每周 3 课时，每课时 45 分钟

本周目标：用 DSH 生成、运行并审查 Python 代码

---

## 本周课程安排

| 课时 | 时长 | 主题 | 重点 |
|---|---|---|---|
| 第1课时 | 45分钟 | Vibe Coding 与 Agent 概念 | 人机分工、Agent 四部分、DSH 工作循环 |
| 第2课时 | 45分钟 | DSH 配置与安全 | 环境确认、模型与工作区、安全规则 |
| 第3课时 | 45分钟 | 提示词设计与代码审查 | 提示词五要素、生成脚本、验收与审查 |

---

## 第1课时：Vibe Coding 是什么

- 用自然语言描述目标
- Agent 负责生成代码、运行和修改
- 人负责目标、边界和结果
- 不要求提前掌握完整 Python 语法
- 适合探索代码、生成初稿、解释报错和快速迭代

---

## 第1课时：Vibe Coding 完整循环

```text
定义问题 → 要求最小版本 → 运行 → 反馈真实结果 → 追问 → 验证
```

---

## 第1课时：循环每一步的含义

- 定义问题：说明要读什么、做什么、输出到哪里
- 要求最小版本：先做能运行的最小脚本
- 运行：让 Agent 展示命令后再执行
- 反馈真实结果：把命令输出或报错原文交给 Agent
- 追问：看不懂就逐行问
- 验证：自己检查文件内容和输出

---

## 第1课时：每一步由谁负责

| 工作 | 谁负责 | 检查重点 |
|---|---|---|
| 描述目标 | 你 | 要读什么、做什么、输出到哪里 |
| 制定计划 | Agent | 计划是否只动指定文件 |
| 生成代码 | Agent | 代码是否使用允许的工具和依赖 |
| 运行代码 | Agent | 是否先展示命令，再执行 |
| 解释代码 | Agent | 每一行是否都能讲清楚 |
| 审查结果 | 你 | 输出是否来自真实文件，结论能否复现 |
| 验证安全 | 你 | 是否修改了不该改的文件，是否泄露 Key |

---

## 第1课时：Agent 由四部分组成

| 组成部分 | 作用 | DSH 中的例子 |
|---|---|---|
| 大模型 | 理解任务，生成计划和代码 | DeepSeek 大模型 |
| 工具 | 执行文件、命令和网络操作 | 读写文件、运行命令、搜索网页 |
| 记忆 | 记录目标和上下文 | 当前会话、长期目标 |
| 工作区 | Agent 的活动范围 | 当前仓库根目录 |

---

## 第1课时：常见 Agent 类型

| Agent | 类型 | 是否直接操作本地文件 | 适合用途 |
|---|---|---|---|
| ChatGPT | 网页/App 助手 | 通常不会 | 问答、写作、代码解释 |
| AI IDE | 代码编辑器 | 会，但通常在打开的项目内 | 代码补全、修改当前项目 |
| DSH | 本地 Agent | 会，可以运行命令 | 读取文件、生成脚本、执行验证 |

---

## 第1课时：DSH 的工作循环

```text
你提出目标 → Agent 制定计划 → Agent 调用工具 → 工具返回真实结果
→ 判断结果是否符合目标 → 不符合则调整计划 → 符合则汇报 → 你审查并验证
```

---

## 第1课时：为什么先看工具结果

- Agent 的计划不等于结果，只有工具返回的文件内容或命令输出才算数
- 结果不符合目标时，回到计划阶段，不硬改输出
- 汇报后必须由你审查，不能因为“看起来完成”就验收
- 模型 API 成本会随厂商调整，实际以官网为准

---

## 第2课时：确认环境

昇腾 310B 自带 Anaconda，本课不安装 Python 环境。先确认基础命令可用：

```bash
python --version
conda --version
dsh web
```

---

## 第2课时：命令含义

- `python --version`：确认 Python 可执行
- `conda --version`：确认 Anaconda 可执行
- `dsh web`：启动 DSH Web 界面
- 能看到 python 和 conda 版本即可继续
- 看不到版本时先解决环境，再进入下一环节

---

## 第2课时：打开 DSH 页面

`dsh web` 启动后，浏览器打开：

```text
http://127.0.0.1:3080
```

---

## 第2课时：确认启动状态

- 等终端显示 `URL: http://127.0.0.1:3080` 后再刷新页面
- 页面打不开时，先检查 `dsh web` 启动命令是否还在运行
- 端口被占用是常见原因，先报告现象再排查
- DSH 官方仓库：https://github.com/deepseek-ai/deepseek-harness

---

## 第2课时：配置模型与工作区

DSH 需要两样配置才能开始工作：模型 API Key 和工作区。

1. 到 DeepSeek 开放平台申请 API Key：https://platform.deepseek.com/
2. 打开 DSH 的 `Settings → Models`，粘贴 API Key 并保存
3. 在主界面点击 `Choose workspace`，选择本仓库根目录

API Key 只保存在本地设置里，不要写进教程、代码、聊天内容或 GitHub。

---

## 第2课时：工作区路径示例

昇腾 310B 上常见路径：

```text
~/Documents/Ascend310
```

Windows 开发机上类似：

```text
D:\Github\Ascend310
```

---

## 第2课时：为什么路径很重要

- Agent 只在工作区里执行任务，选错路径就会找不到文件或改错目录
- 两个路径只是示例，实际以本仓库位置为准
- API Key 不进入对话内容，也不进入任何文件
- 配置完成后不随意修改 Settings

---

## 第2课时：DSH 界面区域

| 区域 | 作用 | 常见操作 |
|---|---|---|
| 左侧会话栏 | 新建会话、切换历史会话 | 点“新会话”开始一个干净任务 |
| 中部消息区 | 你与 Agent 的对话记录 | 滚动查看每一步 |
| 底部输入框 | 输入自然语言任务 | 回车发送 |
| 工具行 | 显示 Agent 正在读取、编辑、运行什么 | 点击查看工具执行详情 |
| 右上角状态 | 当前工作区、模型、安全模式 | 确认工作区是否正确 |
| 设置 | API Key、模型、工作区配置 | 只配置一次，之后不要随意修改 |

---

## 第2课时：每次任务前确认工作区

开始任务前先输入：

```text
请告诉我当前工作区路径。
```

---

## 第2课时：确认工作区的做法

- 让 DSH 先回答当前工作区路径，再开始任务
- 如果回答的路径不是本仓库，先改回本仓库再继续
- 这个动作能避免“找不到文件”和“修改了别的目录”两类问题

---

## 第2课时：五条安全规则

Agent 能做的事情和真人操作电脑一样有后果。

1. 先看计划，再批准执行。
2. 只修改工作区里的指定文件。
3. 不执行删除、格式化、上传、系统级安装等危险命令。
4. 不把 API Key 写进代码、文档或 GitHub。
5. 结论必须能从文件内容或命令输出中验证。

---

## 第2课时：常见风险与应对

| 风险 | 表现 | 应对 |
|---|---|---|
| 选错工作区 | DSH 找不到文件，或修改了别的目录 | 先问当前工作区路径，再重新选择 |
| 危险命令 | Agent 要删除文件、安装依赖或改系统配置 | 停下来，要求解释，确认后再继续 |
| 生成看不懂的代码 | 代码能运行，但自己讲不清每一行 | 让 DSH 逐行解释，能复述后再验收 |
| Key 泄露 | API Key 出现在代码或文档中 | 删除 Key，重新生成，绝不提交 |
| 长任务失控 | Agent 连续修改多个文件 | 拆成小任务，每步检查一次结果 |

---

## 第3课时：提示词结构

| 要素 | 要求 | 示例 |
|---|---|---|
| 目标 | 说明要做什么 | 读取融合结果并输出摘要 |
| 数据路径 | 给出明确路径 | `samples/case1/fusion_result.json` |
| 输出位置 | 说明代码保存到哪里 | `tmp/week04/<姓名>/vibe_read_fusion.py` |
| 约束 | 说明允许和禁止的操作 | 只读、只用标准库、不装依赖 |
| 验收标准 | 说明怎样算完成 | 输出 fusion pass 名称与 match_times |

---

## 第3课时：好提示词与坏提示词

- 好的提示词：目标、数据路径、输出位置、约束、验收标准都写清楚
- 坏的提示词只有一句话，例如“帮我分析一下”
- 坏提示词会让 Agent 选错文件、改变工作区或生成无法验证的结论
- 写完提示词后先自己读一遍：换成新同学能听懂吗

---

## 第3课时：实战任务提示词

在 DSH 中输入：

```text
请先告诉我当前工作区路径，确认后开始。
请读取 samples/case1/fusion_result.json，不要修改原文件。
用标准库 json 输出：
1. graph_fusion 和 ub_fusion 中每个 fusion pass 的名称；
2. 每个 pass 的 effect_times 和 match_times。
代码保存到 tmp/week04/<姓名>/vibe_read_fusion.py。
只允许读取，不安装任何依赖。
```

---

## 第3课时：提示词逐项解释

- 第一句：先确认工作区，避免读错文件
- 第二句：给出数据路径并声明只读
- 第三、四句：说明要读取和输出的字段
- 第五句：给出代码保存位置
- 第六句：限定只读和不安装依赖

---

## 第3课时：参考脚本

DSH 生成的代码可能不完全相同，下面是一个可运行的参考写法：

```python
import json
from pathlib import Path

path = Path("samples/case1/fusion_result.json")
data = json.loads(path.read_text(encoding="utf-8"))

for session_id, session_data in data.items():
    print("session:", session_id)
    for category in ("graph_fusion", "ub_fusion"):
        passes = session_data[category]
        print(category, "passes:", len(passes))
        for name, stats in passes.items():
            print(name, stats["effect_times"], stats["match_times"])
```

---

## 第3课时：脚本逐行解释

- `import json`：使用标准库 json，不安装 pandas
- `from pathlib import Path`：用 Path 管理文件路径
- `path.read_text(encoding="utf-8")`：按 UTF-8 读取文件
- `json.loads(...)`：把 JSON 文本解析成 Python 字典
- 外层 `for`：遍历每个 session 的结果
- 内层 `for`：遍历 `graph_fusion` 和 `ub_fusion` 的 pass
- `len(passes)`：统计每种分类下的 pass 数量
- 输出名称、`effect_times`、`match_times`，便于和原文件核对

---

## 第3课时：预期结果与核对

- `samples/case1/fusion_result.json` 的顶层键是 `session_and_graph_id_0_0`
- 其中 `graph_fusion` 包含 37 个 pass，`ub_fusion` 包含 2 个 pass
- 脚本输出必须来自真实文件，不能凭记忆写结果
- 若输出与文件不一致，先检查工作区路径、文件内容和代码
- 不要直接接受 DSH 给出的数字，自己运行一次脚本

---

## 第3课时：验收步骤

DSH 完成任务后，按以下顺序验收：

1. 检查计划：是否只读取指定 JSON，不修改原文件。
2. 检查命令：是否只运行 Python，不执行安装或删除命令。
3. 检查代码：是否使用标准库 `json` 和 `pathlib`，没有 pandas。
4. 自己运行一次脚本，确认输出与文件内容一致。
5. 用一句话说明脚本读取了哪个字段、计算了什么、输出了什么。

---

## 第3课时：审查清单

| 项目 | 检查内容 |
|---|---|
| 计划 | Agent 是否先说明将读取什么、运行什么、写入什么 |
| 命令 | 每条命令是否看得懂，是否修改原文件 |
| 代码 | 是否能用标准库完成，是否每一行都能解释 |
| 输出 | 是否来自真实文件，能否复现 |
| 安全 | 是否没有删除、上传、安装系统级依赖，是否没有泄露 Key |
| 记录 | 是否保留了 DSH 修改代码的过程 |

---

## 第3课时：常见错误与坑

| 现象 | 可能原因 | 处理方式 |
|---|---|---|
| DSH 找不到文件 | 工作区不在本仓库 | 先问当前工作区路径，再重新选择 |
| DSH 修改了原文件 | 文件边界不清晰 | 明确只读路径，检查计划后再批准 |
| DSH 使用了 pandas | 提示词没有限制依赖 | 明确只用标准库 `json` 和 `pathlib` |
| 代码能运行但讲不清 | 直接接受了生成结果 | 让 DSH 逐行解释，自己复述后再保存 |
| API Key 出现在代码中 | 配置时把 Key 当数据传给了 Agent | 删除 Key 并重新生成，绝不提交 |
| 输出与文件不一致 | 没有自己运行脚本 | 重新运行，检查路径、字段和输出 |
| DSH 页面打不开 | 启动命令还在运行，或端口被占用 | 等显示 `URL: http://127.0.0.1:3080` 后再刷新 |

---

## 参考资料

- [datawhalechina/easy-vibe](https://github.com/datawhalechina/easy-vibe)
- [datawhalechina/vibe-vibe](https://github.com/datawhalechina/vibe-vibe)
- [Hannibal046/Awesome-LLM](https://github.com/Hannibal046/Awesome-LLM)
- [luban-agi/Awesome-AIGC-Tutorials](https://github.com/luban-agi/Awesome-AIGC-Tutorials)
- [e2b-dev/awesome-ai-agents](https://github.com/e2b-dev/awesome-ai-agents)
- [DSH 官方仓库](https://github.com/deepseek-ai/deepseek-harness)
- [DeepSeek 开放平台](https://platform.deepseek.com/)

---

## 课堂任务

1. 启动 DSH，确认 `python --version`、`conda --version` 和 `http://127.0.0.1:3080` 可用。
2. 向 DSH 输入第3课时的实战任务提示词，先确认工作区，再要求只读读取 `samples/case1/fusion_result.json`。
3. 检查 Agent 的计划、命令和代码；让 DSH 逐行解释生成的脚本。
4. 自己运行脚本，逐项核对输出与 JSON 文件内容。
5. 让 DSH 增加一个统计维度，例如统计 `effect_times` 或 `match_times` 的合计，或找出 `match_times` 最大的 pass。

---

## 交付物

- `tmp/week04/<姓名>/vibe_read_fusion.py`
- `tmp/week04/<姓名>/safety.md`
- `tmp/week04/<姓名>/prompt-notes.md`
- `tmp/week04/<姓名>/review.md`

---

## 验收标准

- 能说出 Vibe Coding 的完整循环，并区分大模型、工具、记忆和工作区。
- `python --version`、`conda --version` 已确认可用，DSH 页面可以打开。
- 能背出 5 条安全规则，看不懂的命令会停下来要求解释。
- 提示词包含目标、数据路径、输出位置、约束和验收标准。
- 生成脚本只使用标准库 `json` 和 `pathlib`，没有 pandas，没有修改原文件。
- API Key 未出现在代码、文档或聊天记录中。
- 能逐行解释生成代码，并说明哪些行最可能出错。
- 自己运行脚本后，输出与 `samples/case1/fusion_result.json` 一致。
