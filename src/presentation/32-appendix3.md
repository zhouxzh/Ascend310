---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "附录 3：Python 基础"
---

<!-- _class: cover -->

# 附录 3：Python 基础

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 从语法基础到可运行的数据处理脚本


对应教材：`src/appendix/appendix3.md`

配套样例：`samples/case1/fusion_result.json`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

<!-- _class: center -->

## 本讲目标与路线

- 掌握数据类型与容器
- 用控制流和函数组织脚本
- 读写 JSON 与命令行参数
- 用调试和验证保证可复现

> 本讲主线：数据 → 控制流 → 函数 → 文件 → 验证

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a></div>

<!--
先给学生路线图；后续每页都对应这里的一项，最后回到小结。
-->
---

<!-- _class: section -->

# 01

## 数据与代码组织
---

<!-- _class: center -->

## 数据、类型与容器

- 变量是给值起的名字。Python 中每个值都有类型，类型决定它可以参与哪些运算。检查类型用 type()，查看结果用 print()。
- str 是文本，int 是整数，float 是小数，bool 是布尔值。模型 ID 适合存成 str，嵌入维度适合存成 int。
- 模型文件大小经常需要在字节和 MiB 之间换算。/ 是普通除法，// 是向下取整，% 是取余数，round() 控制显示精度。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a> · 1. 数据、类型与容器</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 列表与元组

- 模型输入形状、预处理均值和标准差通常是有序数值。list 保存可增删的有序数据，tuple 保存创建后不修改的固定组合。
- 列表索引从 0 开始，-1 表示最后一个元素。len() 返回元素个数。
- 模型清单由多个字段组成，每个字段用一个键和对应的值表示。dict 保存键值对，适合描述一个模型条目。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a> · 1.3 列表与元组</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 字典

- 模型清单由多个字段组成，每个字段用一个键和对应的值表示。dict 保存键值对，适合描述一个模型条目。
- 键通常使用字符串，值可以是字符串、数字、列表或另一个字典。
- 需要知道模型清单中出现过哪些精度模式时，用 set 去重。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a> · 1.4 字典</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 控制流

- if 处理一个条件，elif 处理“否则如果”，else 是兜底。判断从上往下执行，先命中的分支生效。
- 比较运算符 == 判断是否相等，结果是一个布尔值。
- for 循环依次取出列表中的元素，重复执行缩进块。累加前先设置 total = 0，这个初始值必须写在循环外。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a> · 2. 控制流</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 函数与模型清单

- 函数把重复逻辑打包成可复用单元。下面函数接收模型列表，返回按精度模式统计的字典。
- dict.get(key, 0) 在键不存在时返回默认值 0。sorted() 让输出顺序稳定。
- 默认参数让调用更简洁，同时允许按名字覆盖默认值。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a> · 3. 函数与模型清单</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: section -->

# 02

## 文件、JSON 与验证
---

<!-- _class: center -->

## dataclass

- dataclass 自动生成初始化、比较和打印方法，适合保存结构清晰的记录。
- __init__ 是实例创建时执行的方法，self 指向当前实例。
- 子主题：3.8 class 与 self

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a> · 3.7 dataclass</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 文件、JSON、命令行与异步

- 模型清单可能来自不同版本的脚本，字段并不总是一致。直接读取缺失字段会触发 KeyError，用 dict.get() 可以提供默认值。
- 把字符串转成数字时，输入内容决定转换是否成功。int("fp16") 会触发 ValueError，可以用 try/except 捕获。
- None 表示没有有效数值。统计前应先检查它，不要把 None 当成 0。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a> · 4. 文件、JSON、命令行与异步</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## json.loads 与 json.dumps

- 模型配置常以 JSON 保存。json.loads() 把 JSON 文本转回 Python 对象，json.dumps() 把 Python 对象转成 JSON 文本。
- else 在没有异常时执行，finally 无论是否异常都会执行。
- argparse 用来解析命令行参数，样例脚本中常用来接收模型 ID、精度和路径。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a> · 4.3 json.loads 与 json.dumps</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 调试与验证

- 出错时先确认对象的真实内容，不要只看报错文字。repr() 可以显示字符串中的空格和引号。
- 如果逻辑复杂，可以在可疑位置使用 breakpoint() 进入调试器，或把中间变量打印出来。脚本保存后，先在仓库根目录执行：
- py_compile 只检查语法，不运行板端逻辑。昇腾 310B 的 CANN、ATC 和 ACL 检查仍应在真实开发板上完成。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a> · 5. 调试与验证</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 本章小结

- 先让数据合同清楚，再写逻辑
- 函数和 dataclass 让脚本可维护
- 调试输出要能定位输入、分支和异常
- 未验证项必须标为 untested 或 blocked。

> 下一讲：进入 Vibe Coding 与 DSH。

<div class="source">教材：<a href="../appendix/appendix3.md"><code>src/appendix/appendix3.md</code></a></div>

<!--
用三句话收束；提醒学生把未验证项写成 untested 或 blocked。
-->
