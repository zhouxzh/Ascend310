# convert-vuepress.sh 使用文档

## 概述

`convert-vuepress.sh` 是一个 Bash 脚本，用于将 VuePress 项目中的 Markdown 文件转换为 LaTeX 和 PDF 格式。该脚本利用 Pandoc 工具进行转换，支持自定义字体、模板和输出选项。

## 功能

- **自动查找 Markdown 文件**：在指定源目录中递归查找所有 `.md` 文件，并按名称排序。
- **生成文件列表**：创建包含所有 Markdown 文件路径的文本文件，用于指定转换顺序。
- **LaTeX 生成**：可选生成 LaTeX 文件，支持目录、章节编号等。
- **PDF 生成**：可选生成 PDF 文件，首先尝试使用 XeLaTeX，如果失败则回退到 PDFLaTeX。
- **自定义配置**：支持自定义源目录、输出目录、模板路径和字体设置。
- **跳过选项**：可以选择跳过 LaTeX 或 PDF 生成。

## 参数

脚本支持以下命令行参数：

| 参数 | 描述 | 默认值 | 示例 |
|------|------|--------|------|
| `--source-dir` | Markdown 文件的源目录路径 | `../src/book` | `--source-dir "../docs"` |
| `--output-dir` | 输出文件的目录路径 | `./output` | `--output-dir "./build"` |
| `--template-path` | LaTeX 模板文件路径 | `./eisvogel.latex` | `--template-path "./custom-template.tex"` |
| `--main-font` | 主字体名称 | `SimSun` | `--main-font "Arial"` |
| `--sans-font` | 无衬线字体名称 | `Microsoft YaHei` | `--sans-font "Helvetica"` |
| `--mono-font` | 等宽字体名称 | `Consolas` | `--mono-font "Courier"` |
| `--skip-pdf` | 跳过 PDF 生成 | `false` | `--skip-pdf` |
| `--skip-latex` | 跳过 LaTeX 生成 | `false` | `--skip-latex` |

## 使用示例

### 基本使用

```bash
./convert-vuepress.sh
```

使用默认参数运行脚本，源目录为 `../src/book`，输出到 `./output`。

### 自定义源和输出目录

```bash
./convert-vuepress.sh --source-dir "../my-docs" --output-dir "./my-output"
```

### 仅生成 PDF，跳过 LaTeX

```bash
./convert-vuepress.sh --skip-latex
```

### 仅生成 LaTeX，跳过 PDF

```bash
./convert-vuepress.sh --skip-pdf
```

### 自定义字体和模板

```bash
./convert-vuepress.sh --main-font "Times New Roman" --sans-font "Arial" --mono-font "Courier New" --template-path "./my-template.latex"
```

### 完整示例

```bash
./convert-vuepress.sh --source-dir "../src/docs" --output-dir "./dist" --template-path "./eisvogel.latex" --main-font "SimSun" --sans-font "Microsoft YaHei" --mono-font "Consolas"
```

## 输出文件

脚本会在输出目录中生成以下文件：

- `file-list.txt`：包含所有 Markdown 文件路径的列表文件。
- `book.tex`：生成的 LaTeX 文件（如果未跳过）。
- `book.pdf`：生成的 PDF 文件（如果未跳过）。

## 依赖

脚本需要以下工具和软件：

- **Bash**：脚本运行环境。
- **Pandoc**：用于 Markdown 到 LaTeX/PDF 的转换。
- **XeLaTeX 或 PDFLaTeX**：用于生成 PDF（推荐 XeLaTeX 以支持中文和自定义字体）。
- **find 和 sort**：用于查找和排序文件（通常在 Unix-like 系统上可用）。

### 安装依赖

在 Ubuntu/Debian 上：

```bash
sudo apt update
sudo apt install pandoc texlive-xetex texlive-latex-base
```

在 macOS 上（使用 Homebrew）：

```bash
brew install pandoc
brew install --cask mactex  # 包含 XeLaTeX
```

在 Windows 上，可以使用 Chocolatey 或手动安装 Pandoc 和 TeX Live。

## 注意事项

- **文件路径**：确保源目录和输出目录的路径正确。脚本会自动创建输出目录如果不存在。
- **字体支持**：自定义字体需要系统中安装相应字体，否则可能导致 PDF 生成失败。
- **模板文件**：确保 LaTeX 模板文件存在且格式正确。脚本使用 eisvogel 模板作为默认。
- **错误处理**：如果 Pandoc 或 LaTeX 引擎失败，脚本会输出错误信息并尝试备用方法（PDF 生成时）。
- **兼容性**：脚本设计为兼容 POSIX shell，但某些功能可能需要 Bash。
- **性能**：对于大量 Markdown 文件，转换可能需要一些时间。
- **中文支持**：脚本配置了 CJK 字体支持，确保 XeLaTeX 正确处理中文内容。

## 故障排除

- **未找到 Markdown 文件**：检查源目录路径和文件是否存在。
- **Pandoc 错误**：确保 Pandoc 已安装且版本支持所需功能。
- **LaTeX 错误**：检查模板文件和字体设置。尝试使用 PDFLaTeX 作为备用。
- **权限问题**：确保脚本有执行权限（`chmod +x convert-vuepress.sh`）和目录写入权限。

## 许可证

此脚本基于开源许可证分发。请根据需要修改和使用。
