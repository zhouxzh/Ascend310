---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "案例 4：掌纹识别"
---

<!-- _class: cover -->

# 案例 4：掌纹识别

<div class="byline">从整帧手掌检测到可解释的身份候选</div>
<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html">案例4正文</a>；样例：<a href="https://github.com/zhouxzh/Ascend310/tree/main/samples/case4">samples/case4</a></div>

<!--
本讲围绕当前8T两阶段工作台：手掌检测、ROI、CCNet特征、模板检索。重点不是页面能打开，而是每一层的合同和证据。
-->

---

<!-- _class: center -->

## 身份识别不能从“整帧分类”开始

1. 整帧中有背景、光照和多只手候选。
2. 检测器回答“手掌在哪里”。
3. ROI回答“哪块区域适合提取掌纹”。
4. CCNet回答“这块ROI的2048维特征是什么”。
5. 模板检索回答“候选是谁”。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-problem">这项任务解决什么问题</a></div>

<!--
先让学生区分检测与识别。整帧分类把定位和身份混在一起，难以调试，也无法自然支持多手。
-->

---

<!-- _class: center -->

## 本讲路线

- 理解两阶段链条与NPU-only边界
- 读取NanoDet和PicoDet的不同输入输出合同
- 完成匿名注册与掌侧核对
- 解释实时摄像头为什么曾卡顿
- 用分层证据判断系统是否真的可用

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-learning-outcomes">学习成果</a></div>

<!--
把课程目标映射到可观察产出：数据流图、合同核对、一次注册、一次摄像头请求和失败分类。
-->

---

<!-- _class: center -->

## 两阶段流水线

```text
整帧相机画面
    ↓
手掌检测：框、分数、left/right
    ↓
ROI裁剪与质量检查：128×128灰度
    ↓
CCNet：2048维embedding
    ↓
同模型、同ROI/光谱合同的模板检索
    ↓
Top-K候选或拒绝原因
```

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-architecture">系统总体架构</a></div>

<!--
强调每层输出不同：框、ROI、embedding、模板分数。任何一层失败，都不能用下一页面的成功替代。
-->

---

<!-- _class: center -->

## 手掌检测模型可以在界面独立选择

| 模型 | 输入 | 输出合同 | 状态 |
|---|---|---|---|
| NanoDet-Plus M-320 | `[1,3,320,320]` | `[1,2125,34]` | 默认人工测试 |
| PicoDet XS-320 | `[1,3,320,320]` | 8输出 | 实验 |
| PicoDet XS-416 | `[1,3,416,416]` | 8输出 | 实验 |

`detector_id`控制检测模型，`model_id`控制掌纹特征模型，两者不能混用。切换检测模型必须同步切换OM和对应解码器。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-detector-catalog">可选模型</a>；清单：<a href="https://github.com/zhouxzh/Ascend310/blob/main/samples/case4/detector_manifest.json">detector_manifest.json</a></div>

<!--
强调选择器必须切换OM与解码器，不能只改显示名称。PicoDet仍是实验模型。
-->

---

<!-- _class: center -->

## 长方形画面被直接拉伸为方形模型输入

| 原图 | 横纵缩放到320×320 |
|---|---|
| 320×240 | x=1，y=4/3 |
| 640×480 | x=1/2，y=2/3 |
| 1280×720 | x=1/4，y=4/9 |

检测框会按原图宽高分别反算，因此框位置仍对应原始视频；页面显示保持原始宽高比。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-direct-stretch">直接拉伸合同</a></div>

<!--
解释几何拉伸不是文件压缩。模型内部比例变化，页面框通过逆变换恢复，显示画面不拉伸。
-->

---

<!-- _class: center -->

## NanoDet和PicoDet不能共用解码器

| 项目 | NanoDet | PicoDet |
|---|---|---|
| 输出 | 单张量`[1,2125,34]` | 4层分类+4层DFL |
| 分数 | 前2通道已sigmoid | 图内已sigmoid |
| DFL | 后32通道 | 每层32通道 |
| 中心偏移 | 0.0 | 0.5 |

`family`字段决定预处理、分类分数处理和DFL解码分支。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-nanodet-output">检测输出合同</a></div>

<!--
这是模型选择最容易出错的地方：形状相同不代表语义相同。必须依据family选择预处理和解码。
-->

---

<!-- _class: center -->

## CCNet把ROI转换为可比较特征

- 输入：单通道`[1,1,128,128]`
- 输出：2048维embedding
- 比较：L2归一化后的余弦相似度
- 模板namespace：`<model_id>__npu__mixed_fp16`
- ROI、模态、光谱和摄像头合同也必须兼容

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-embedding-contract">掌纹特征合同</a></div>

<!--
模板不是一张图或一个名字，而是同合同embedding集合。换特征模型后不能直接比较旧模板。
-->

---

<!-- _class: center -->

## 注册必须同时通过“单只手”和“掌侧一致”

1. 画面中只能检测到一只手。
2. 检测标签必须是`left_palm`或`right_palm`。
3. 检测掌侧必须与页面人工选择一致。
4. 不一致时不裁剪ROI、不保存样本、不创建模板。
5. 左右掌稳定ID分别以`:left`和`:right`结尾。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-side-mismatch-experiment">注册掌侧不一致实验</a></div>

<!--
这是身份数据完整性门，不是UI提示。错误掌侧不能通过自动交换标签进入模板。
-->

---

<!-- _class: center -->

## 最小实验先验证环境和资产

```bash
conda activate ascend
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PALMPRINT_ROOT=<release-root>
export PALMPRINT_MODEL_ASSET_ROOT=<verified-assets>
python -c "import cv2, acl; print(cv2.__version__)"
python -m palmprint_workbench.tools.verify_assets --strict
```

只有`ok=true`且`failures=[]`才能继续。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-verify-assets">资产校验</a>；运行手册：<a href="https://github.com/zhouxzh/Ascend310/tree/main/samples/case4">samples/case4</a></div>

<!--
资产门同时检查检测OM、CCNet OM和registry。文件存在但哈希不符必需停止。
-->

---

<!-- _class: center -->

## 服务启动后再验证四层入口

```bash
python -m palmprint_workbench.api --host 0.0.0.0 --port 7861
curl --fail http://127.0.0.1:7861/api/health
curl --fail http://127.0.0.1:7861/api/bootstrap
curl --fail http://127.0.0.1:7861/api/cameras
curl --fail http://127.0.0.1:7861/
```

health正常仍不会主动加载NPU；必须再执行检测或识别。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-start-service">启动服务</a></div>

<!--
健康检查证明服务、运行时和资产状态，不证明真实摄像头、NPU推理或身份质量。
-->

---

<!-- _class: center -->

## 页面把检测模型与特征模型分开

- “手掌检测模型”选择NanoDet或PicoDet。
- “掌纹特征模型”选择CCNet。
- 未知`detector_id`返回422。
- PicoDet显示实验提示和原始数值门状态。
- 切换检测模型后清除旧检测框。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-select-detector">选择模型并观察检测</a></div>

<!--
检查bootstrap中的detectors与models。界面显示名不是证据，响应里的detector_id才是实际选择结果。
-->

---

<!-- _class: center -->

## 卡顿来自重复取帧，不是NPU算不动

| 方案 | 10秒检测数 | 平均请求 |
|---|---:|---:|
| 640×480单路 | 16 | 520ms |
| 320×240单路 | 34–39 | 219–242ms |

NPU内部约20–23ms。旧页面同时请求预览和检测，两路争用同一台C922，因此应先修采集链路。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-camera-performance">实时页面性能</a></div>

<!--
先定位瓶颈再优化。当前一次采集同时返回框和预览，独立/frame请求为0，检测后再等待60ms。
-->

---

<!-- _class: center -->

## 证据必须按层保留

| 层 | 可以证明 | 不能证明 |
|---|---|---|
| 资产 | 文件身份 | 数值正确 |
| ACL | OM可加载执行 | 任务质量 |
| 数值 | 张量误差 | 身份准确率 |
| 检测任务 | 框与掌侧指标 | embedding质量 |
| 特征任务 | EER/Rank-1 | 摄像头体验 |
| UI/摄像头 | 服务闭环 | 跨场景可靠性 |

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-evidence-layers">分层验收</a></div>

<!--
每层通过只能支持本层结论。禁止用health、截图或单次识别替代数值和任务指标。
-->

---

<!-- _class: center -->

## 失败先分层，再修复

| 现象 | 先检查 |
|---|---|
| `import acl`失败 | Python、CANN、`acl.__file__` |
| 模型不显示 | manifest、OM大小和SHA |
| 摄像头为空 | V4L2、权限、设备占用 |
| 注册不完成 | 手数量、掌侧、ROI质量 |
| 检索为空 | namespace、ROI、光谱、模型ID |
| 页面卡顿 | 是否双路取流、输入分辨率、MJPG |

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-troubleshooting">常见失败定位</a></div>

<!--
失败发生在哪一层，就在哪一层收集证据。不要用降低阈值、交换标签或CPU fallback掩盖问题。
-->

---

<!-- _class: center -->

## 生物特征默认不落盘

- 连续预览帧默认不保存。
- 调试模式必须显式开启并限制帧数。
- 调试内容只留在受控发行目录。
- 真实掌纹、姓名、embedding和原图不进入Git。
- 课堂截图只使用合成或匿名素材。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-privacy">调试帧和隐私</a></div>

<!--
调试捕获是有限、显式、可清理的例外，不是默认日志策略。发布仓库不能包含真实生物特征。
-->

---

<!-- _class: center -->

## 课堂任务

1. 分别调用三份检测OM并核对`detector_id`。
2. 比较320×240、640×480、1280×720的刷新率。
3. 注册匿名左右掌并验证模板ID隔离。
4. 制造掌侧不一致，确认没有样本落盘。
5. 用错误SHA制造资产失败并恢复到`ok=true`。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-guided-labs">分步实验</a></div>

<!--
课堂评价看学生能否解释每项证据的边界，而不是只看最终页面是否出现匹配结果。
-->

---

<!-- _class: center -->

## 总结：一次成功请求不是系统验收

- 两阶段链条把定位与身份拆开。
- 检测模型与特征模型必须分别选择、分别验证。
- 直接拉伸合同、类别顺序和DFL解码是检测精度的前提。
- 严格掌侧核对保护注册模板。
- 单路取流解决摄像头争用，但不能改变模型质量。
- 资产、ACL、数值、任务、性能、UI证据不可互相替代。

<div class="source">教材：<a href="https://310b.cn/experiment/case4.html#src-experiment-case4-summary">案例小结</a>；样例：<a href="https://github.com/zhouxzh/Ascend310/tree/main/samples/case4">samples/case4</a></div>

<!--
最后回到主线：问题分解、输入合同、身份数据完整性、摄像头性能和证据边界。
-->
