# 昇腾310B实战——从入门到精通边缘计算

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![VuePress](https://img.shields.io/badge/VuePress-2.0-3aab95)](https://vuejs.press/)
[![pnpm](https://img.shields.io/badge/pnpm-10-ff6b35)](https://pnpm.io/)

> 基于昇腾310B的边缘计算与AI推理部署开源电子书，理论与实践结合，项目驱动教学。

**作者：** 周贤中 | **邮箱：** zhouxzh@gdut.edu.cn

---

## 关于本书

本书跳出单纯的理论讲解，通过真实的AI推理部署案例，系统化讲解昇腾310B的硬件架构、Atlas工具链使用逻辑与端侧AI项目开发流程。从模型适配、量化优化到推理服务部署，每个知识点都配套可落地的代码示例，帮助读者实现从"了解芯片"到"能用芯片落地项目"的跨越。

### 目标读者

- **高校学生 / 科研新人** — 系统化路径快速理解边缘AI硬件与部署流程
- **嵌入式 / IoT 工程师** — 将AI模型真正跑在边缘端并做性能调优
- **AI应用开发者 / 创客** — 将训练好的模型迁移到昇腾310B进行高效推理与产品化落地

### 全书结构

| 部分 | 内容 | 说明 |
| :-- | :-- | :-- |
| **Part I：理论教程** | Chapter 1~9 | 从边缘计算基础到项目交付方法论 |
| **Part II：实验教程** | Case 0~9 | 十个动手实验，从开发板点亮到聊天机器人 |

---

## 目录结构

```
Ascend310/
├── src/                    # VuePress 文档源码
│   ├── book/               # 理论教程 (chapter1~9 + 附录)
│   ├── experiment/         # 实验教程 (case0~9)
│   └── .vuepress/          # VuePress 主题与插件配置
├── samples/                # 案例配套源代码
│   ├── case1/              # 智能人脸识别打卡机
│   ├── case2/              # 边缘端实时目标跟踪
│   ├── case3/              # 智能电子琴
│   ├── chapter2/           # ResNet 快速入门示例
│   ├── chapter3/           # AlexNet 模型迁移示例
│   ├── chapter4/           # SSD / SSDLite / ResNet18 PyACL 示例
│   └── chapter5/           # 算子开发示例
├── latex/                  # VuePress → LaTeX/PDF 转换工具
├── notebook/               # Jupyter Notebook 实验
├── convert-vuepress.sh     # Linux/macOS 文档转换脚本
├── convert-vuepress.ps1    # Windows 文档转换脚本
├── deploy.sh               # 部署脚本
└── package.json            # Node.js 项目配置
```

---

## 本地开发

### 环境要求

- [Node.js](https://nodejs.org/) >= 18
- [pnpm](https://pnpm.io/) >= 10

### 安装与运行

```bash
# 安装依赖
pnpm install

# 启动开发服务器
pnpm docs:dev

# 构建静态站点
pnpm docs:build

# 导出 PDF
pnpm export-pdf
```

开发服务器默认运行在 `http://localhost:8080`。

### 生成 LaTeX / PDF

如需生成纸质版或离线 PDF，可使用提供的转换脚本：

```bash
# Linux / macOS
bash convert-vuepress.sh

# Windows (PowerShell)
.\convert-vuepress.ps1
```

详细说明见 [latex/README.md](latex/README.md)。

---

## 阅读路线推荐

| 读者类型 | 推荐路径 | 目标 |
| :-- | :-- | :-- |
| 零基础学生 | Ch1 → Ch2 → Case 0 → Case 1 | 跑通首个模型 |
| 嵌入式工程师 | Ch4 → Ch5 → Ch6 | 掌握底层开发与优化 |
| AI应用开发者 | Ch2 → Ch3 → 选读案例 | 快速场景落地 |
| 技术负责人 | Ch1 → Ch7 → Ch8 | 构建团队方法论 |

---

## 当前版本

**v0.1** — 早期版本，持续更新中。

- [x] 结构规划 + Chapter 1~3 初稿 + Case 0~1 示例
- [x] Chapter 4~5 初稿
- [ ] Chapter 6~7 完善
- [ ] 全案例上线 (Case 0~9)
- [ ] 附录完善 + 全面审校 (v1.0)

---

## 开源协作

欢迎通过 Issue / PR 参与共建：

- 增补新模型 / 新任务的部署范式
- 分享自定义算子优化经验
- 提交性能测试报告（含硬件信息 + 指标）
- 文档校对与翻译

---

## 许可证

本书内容采用 [Apache 2.0](LICENSE) 许可证。

引用本书请注明：

> 《昇腾310B实战：从入门到精通边缘计算与人工智能》（GitHub: zhouxzh/Ascend310）
