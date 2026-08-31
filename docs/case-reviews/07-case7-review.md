# Case 7 审查报告：昇腾 310B 智能相册

## 审查范围与方法

审查基准为当前工作树。审查只读检查了 `src/experiment/case7.md`、
`samples/case7/`、生成的 `latex/cases/case7.tex` 和 `latex/book.log`；没有在
开发机运行 CANN、ATC、PyACL 或 NPU，也没有把未提交的板端资产当作已验证证据。

## 总体结论

Case 7 的问题建模、模型空间隔离、相册索引和设备输出边界基本完整，适合作为案例草稿。但当前版本不能直接作为可复现的正式教材：生产注册表为空而正文给出已准入模型和板端指标，设备访问令牌没有实际校验，PDF 仍包含原始 Mermaid 和缺字警告。必须先补齐可追溯资产或降低正文证据等级，并明确局域网公开模式的安全边界。

## 严重性定义

| 等级 | 含义 |
| --- | --- |
| P1 | 阻断发布、部署或安全验收的问题 |
| P2 | 不阻断代码运行，但会造成教材误解、结果不可复核或出版排版不合格 |
| P3 | 可在后续版本改进的表达或维护问题 |

## 已核实问题

### P1-1 模型准入状态与性能证据无法由当前仓库复现

**证据**

- 当前工作树的 samples/case7/models/registry.json 第 5 行为 models: []。
- samples/case7/candidate_manifest.json 第 49、95、123 行将 MobileCLIP、Chinese-CLIP 和 ResNet50 都标为 candidate，而不是 admitted；其第 31--41、78--87、114--115 行只给出预期的 ONNX/OM 路径。
- 生成的 OM、ONNX、tokenizer、照片和 shared/reports/benchmarks/coco_cn_case7_performance.json 不在版本化资产中；性能报告路径也被报告目录忽略规则覆盖。
- 正文 src/experiment/case7.md 第 211--229 行把三个模型写成可准入链路，第 303--314 行给出 Recall 和 P50/P95；samples/case7/docs/02-ascend310b4-deployment-and-acceptance.md 第 45--69 行还声称有 500 张照片、三组 embedding 和完整报告。

**影响**

在 clean clone 中，服务的 require_artifacts 检查不能得到 ready 状态，读者也无法重新计算正文中的检索或延迟数值。把板端历史结果写成当前仓库可复现实验，会混淆模型准入、检索质量和性能证据三个门槛。

**修复建议**

1. 若这些结果仍有效，将 registry、manifest、模型输入输出合同、报告元数据和报告固定摘要放入明确的发布白名单；大型 OM/数据仍可放在外部存储，但正文应给出版本、字节数、摘要和可获取位置。
2. 若资产不能公开，正文将“当前板端已测”改为“历史板端记录”或“待复现实验”，删除无法追溯的精确数字。
3. 让 prepare_models 和服务启动检查对空注册表明确返回 blocked，而不是用候选清单暗示已准入。

**验证要求**

在目标 Ascend 310B4/8T 上重新执行模型合同、ATC、OM hash、ACL smoke、CPU/NPU 数值一致性和 COCO-CN 检索协议；将原始 JSON 报告与运行环境、预热次数、循环次数、查询集和模型版本绑定后，再回填正文。

### P1-2 设备令牌和管理接口的安全合同与实现不一致

**证据**

- samples/case7/app.py 第 497--504 行的 device_auth 接收 token 参数，但只读取设备是否 enabled，从未调用 DeviceRegistry.authorize，也不比较令牌。
- manifest、content、photoframe 和 heartbeat 路由在第 583--638 行传入令牌，却因此仍可在没有令牌或令牌错误时继续执行。
- 第 524--568 行的 /api/admin/devices 创建、修改、删除和 advance 路由没有管理员认证。
- 正文第 263--267 行一方面描述一次性 token、ETag 和设备协议，另一方面又说 LAN profile 不要求管理员令牌；没有明确哪些操作是公开读取、哪些操作必须认证。

**影响**

在普通局域网中，知道 device_id 的客户端可能读取或刷新设备内容，并调用管理路由改变设备状态。若令牌被读者理解为访问控制，当前实现会产生错误的安全承诺；若设计确实是可信 LAN 公开模式，则破坏性管理操作仍应有明确的网络边界和运维防护。

**修复建议**

1. 选择一种明确合同：对设备路由调用 authorize 并区分 401/403；或者删除 token 语义并在文档中明确这是隔离实验网协议。
2. 为 /api/admin/* 增加独立管理员认证、来源限制和审计日志，至少禁止匿名创建设备、删除设备和修改策略。
3. 为错误令牌、禁用设备、重放旧 token 和匿名管理请求补充接口测试。

**验证要求**

使用有效、无效、过期、缺失和重放令牌分别请求 manifest/content/heartbeat；确认管理路由在未认证时拒绝，并检查响应状态码、日志和不变量（设备策略、ETag、selection revision）。

### P2-1 PDF 仍输出 Mermaid 源码，并产生不可接受的缺字警告

**证据**

- src/experiment/case7.md 第 26、79、99、159、178、197 行保留六段 Mermaid。
- 当前生成的 latex/cases/case7.tex 第 49、118、138、240、265、287 行仍出现 flowchart 或 sequenceDiagram 文本；只有第 95 和 106 行的两张 PNG 使用 includegraphics。
- latex/book.log 在 Case 7 对标题和代码图标报告大量 Missing character（例如 U+1F3AF、U+1F3D7、U+1F9E0、U+1F4E6 等），说明电子书字体无法承载这些 emoji。

**影响**

PDF 中部分图会退化为代码块而不是图示，且标题和流程节点出现空白或缺字。在线 Mermaid 能显示不能替代印刷/电子书中的静态图，读者无法获得与正文描述一致的结构关系。

**修复建议**

1. 为数据流、启动时序和三种模型流程各生成验收通过的静态 PNG（或确定性矢量图），正文只引用最终图，不让转换器处理 Mermaid 源码。
2. 删除章节标题和图节点中的 emoji，使用普通中文标签；重新生成 LaTeX/PDF 后检查 Missing character 为零。
3. 逐页检查图中文字、箭头方向、缩放后字号和图题编号，避免只依据 LaTeX 编译成功判断排版正确。

**验证要求**

在 latex/book.log 中确认没有 Mermaid 原文、Missing character、Fatal error 或 Undefined control；渲染 Case 7 所有页面，检查图题、分页和表格是否被截断。

## 验证要求与未验证边界

- samples/case7/docs/02-ascend310b4-deployment-and-acceptance.md 第 71--75 行明确说明 E6 驱动板型号和 GPIO/SPI 接线尚未确认。这是诚实的未验收项，不应在正文中写成真实电子纸刷新已完成。
- 正文第 314 行的 COCO-CN Recall 和延迟只应在对应板端报告可取得、摘要可核对时保留；本次审查没有重新执行这些测试。
- 当前 PDF 日志还存在全书级 overfull hbox 和重复标签警告。应在完成本章图示替换后重新定位 Case 7 页面的具体溢出，不要手改生成的 tex。

## 发布前最小验收顺序

1. 固定模型和数据证据，或把正文数值降级为待复现记录。
2. 先修复设备认证合同，再运行 API 和设备协议测试。
3. 替换全部 PDF Mermaid、去除 emoji 并重新构建。
4. 在目标板端完成模型、索引、API 和触摸屏检查后，才发布性能结论。
