---
marp: true
theme: ascend310
paginate: true
size: 16:9
header: "《昇腾310B实战》教材配套演示"
footer: "案例 9：智能聊天机器人"
---

<!-- presentation-scaffold: case-09 -->

<!-- _class: cover -->

# 案例 9：智能聊天机器人

案例课件 · 教师授课版

对应教材：`src/experiment/case9.md`

配套样例：`samples/case9`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

## 本讲教学主线

1. 边缘聊天服务的信任与算力边界
2. 模型工件锁和环境准备
3. worker、网关与文字界面
4. OpenAI兼容JSON和SSE验收

课堂结束时，学生应能沿着这 4 个节点复述设计、运行入口和证据边界。

<div class="source">教材：`src/experiment/case9.md`；样例：`samples/case9`</div>

<!--
授课提示：逐项带读，不逐字念稿；每讲完一个节点就指出对应教材小节。
-->


---

<!-- _class: screenshot -->

## 前端界面证据

> 本页预留真实前端截图；采集后必须写明开发板档位、页面路径、视口、隐私场景和截图清单编号。

<div class="source">待接入：`src/presentation/screenshots/capture-manifest.json`；来源：`samples/case9`</div>

<!--
授课提示：截图只展示合成数据或匿名数据。先让学生识别页面上的输入、运行状态和结果区域。
-->

---

## 配套源码与课堂产出

| 项目 | 本讲入口 |
|---|---|
| 教材正文 | `src/experiment/case9.md` |
| 可运行样例 | `samples/case9` |
| 截图清单 | `src/presentation/screenshots/capture-manifest.json` |
| 课件清单 | `src/presentation/catalog.mjs` |

课堂产出必须包含实际运行的命令、界面或日志，以及失败时停在哪一层。

<div class="source">课件制作中：case-09；完成正文、截图和讲师备注后移除 scaffold 标记。</div>

<!--
授课提示：最后留下一个可检查的课堂任务；学生提交运行入口、结果和未完成边界。
-->
