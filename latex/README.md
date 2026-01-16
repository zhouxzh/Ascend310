# VuePress 到 LaTeX/PDF 转换工具

## 功能
- 自动下载 Eisvogel 模板
- 支持中文字体配置
- 生成 LaTeX 和 PDF 文件
- 自动处理文件顺序

## 安装前提
1. 安装 Pandoc: https://pandoc.org/installing.html
2. 安装 LaTeX: 推荐 MiKTeX 或 TeX Live

## 使用方法

### 基本用法
```powershell
# 运行脚本（需要在 PowerShell 中执行）
.\convert-vuepress.ps1
```

### 高级选项
```powershell
# 指定源目录和输出目录
.\convert-vuepress.ps1 -SourceDir "./my-docs" -OutputDir "./my-output"

# 只生成 LaTeX，不生成 PDF
.\convert-vuepress.ps1 -SkipPdf

# 只生成 PDF，不生成 LaTeX  
.\convert-vuepress.ps1 -SkipLatex

# 自定义字体
.\convert-vuepress.ps1 -MainFont "SimHei" -SansFont "KaiTi" -MonoFont "FangSong"
```

### 支持的字体
- SimSun (宋体)
- SimHei (黑体)
- KaiTi (楷体)
- FangSong (仿宋)
- Microsoft YaHei (微软雅黑)
- Consolas (等宽字体)

## 文件结构
```
project/
├── docs/                 # VuePress 文档目录
├── output/              # 输出目录（自动创建）
├── convert-vuepress.ps1 # 本脚本
└── eisvogel.latex       # 模板（自动下载）
```