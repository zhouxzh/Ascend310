---
title: "附录 3：Python 编程基础"
author: [周贤中]
date: 2026-08-31
subject: "昇腾310B教程补充材料"
keywords: [Python, 标准库, 昇腾310B, 模型部署]
lang: zh-cn
---

# 附录 3：Python 编程基础

> **本章导读**
> 目标：用 Python 检查昇腾 310B 模型部署中常见的配置、清单和结果数据。
> 数据：正文内联示例，不依赖外部数据集。
> 标准库：`json`、`statistics`、`math`。
> 输出：可直接在 Python REPL 中运行，也可保存为 `.py` 文件复现。

本附录按实际使用顺序讲解 Python。模型 ID、输入形状、预处理均值、精度模式和准入状态会反复出现在样例代码中，因此本章直接用这些对象编写示例。

`for`、`if`、`dict`、`set`、异常处理等基础语法会在前几节展开，第 4 节补充 `samples/` 中高频出现的文件、JSON、命令行和异步写法。所有示例都只使用标准库，不引入 `pandas`，也不需要板端运行环境。

## 1. 数据、类型与容器

### 1.1 变量与基本类型

变量是给值起的名字。Python 中每个值都有类型，类型决定它可以参与哪些运算。检查类型用 `type()`，查看结果用 `print()`。

```python
model_id = "mobileclip_s0"
feature_dim = 512
input_scale = 0.00392156862745098
is_admitted = True

print(type(model_id))
print(type(feature_dim))
print(type(input_scale))
print(type(is_admitted))
```

输出：

```text
<class 'str'>
<class 'int'>
<class 'float'>
<class 'bool'>
```

`str` 是文本，`int` 是整数，`float` 是小数，`bool` 是布尔值。模型 ID 适合存成 `str`，嵌入维度适合存成 `int`。

### 1.2 数字运算与单位换算

模型文件大小经常需要在字节和 MiB 之间换算。`/` 是普通除法，`//` 是向下取整，`%` 是取余数，`round()` 控制显示精度。

```python
bytes_size = 131904474
mib_size = bytes_size / 1024 / 1024

print(mib_size)
print(round(mib_size, 2))
print(bytes_size // 1024)
print(bytes_size % 1024)
```

输出：

```text
125.79369640350342
125.79
128812
986
```

`round()` 只改变显示结果，不改变原始变量。

### 1.3 列表与元组

模型输入形状、预处理均值和标准差通常是有序数值。`list` 保存可增删的有序数据，`tuple` 保存创建后不修改的固定组合。

```python
input_shape = [1, 3, 224, 224]
image_mean = (0.485, 0.456, 0.406)

print(input_shape[0])
print(input_shape[-1])
print(len(input_shape))
print(image_mean[0])
```

输出：

```text
1
224
4
0.485
```

列表索引从 0 开始，`-1` 表示最后一个元素。`len()` 返回元素个数。

### 1.4 字典

模型清单由多个字段组成，每个字段用一个键和对应的值表示。`dict` 保存键值对，适合描述一个模型条目。

```python
model = {
    "model_id": "mobileclip_s0",
    "input_shape": [1, 3, 256, 256],
    "precision_mode": "mixed_fp16",
    "status": "admitted",
}

print(model["model_id"])
print(model["input_shape"])
```

输出：

```text
mobileclip_s0
[1, 3, 256, 256]
```

键通常使用字符串，值可以是字符串、数字、列表或另一个字典。

### 1.5 集合

需要知道模型清单中出现过哪些精度模式时，用 `set` 去重。

```python
precision_modes = {"mixed_fp16", "allow_fp32_to_fp16", "mixed_fp16"}
print(precision_modes)
```

输出：

```text
{'allow_fp32_to_fp16', 'mixed_fp16'}
```

集合不保留插入顺序，也不能通过索引取值。顺序不重要的去重任务才适合使用集合。

## 2. 控制流

### 2.1 条件分支

`if` 处理一个条件，`elif` 处理“否则如果”，`else` 是兜底。判断从上往下执行，先命中的分支生效。

```python
status = "admitted"

if status == "admitted":
    print("可进入 NPU 服务")
elif status == "candidate":
    print("仅用于离线评估")
else:
    print("状态未知")
```

输出：

```text
可进入 NPU 服务
```

比较运算符 `==` 判断是否相等，结果是一个布尔值。

### 2.2 for 循环

`for` 循环依次取出列表中的元素，重复执行缩进块。累加前先设置 `total = 0`，这个初始值必须写在循环外。

```python
feature_dims = [512, 1024, 2048]

total = 0
for dim in feature_dims:
    total = total + dim

print(total)
print(min(feature_dims))
print(max(feature_dims))
```

输出：

```text
3584
512
2048
```

### 2.3 range、enumerate 与 zip

`range(n)` 生成从 0 到 `n - 1` 的整数序列。`enumerate()` 同时返回序号和元素，`zip()` 把多个序列按位置配对。

```python
for index, dim in enumerate([512, 1024, 2048], start=1):
    print(index, dim)
```

输出：

```text
1 512
2 1024
3 2048
```

```python
model_ids = ["mobileclip_s0", "resnet50_feature"]
dims = [512, 2048]

for model_id, dim in zip(model_ids, dims):
    print(model_id, dim)
```

输出：

```text
mobileclip_s0 512
resnet50_feature 2048
```

### 2.4 while、break 与 continue

`while` 在条件仍为真时重复执行。循环内必须修改条件变量，否则会无限循环。`continue` 跳过本次循环，`break` 立即结束整个循环。

```python
attempt = 0

while attempt < 3:
    print(f"attempt {attempt + 1}")
    attempt += 1
```

输出：

```text
attempt 1
attempt 2
attempt 3
```

```python
dims = [512, 1024, 2048]

for dim in dims:
    if dim == 1024:
        continue
    print(dim)

for dim in dims:
    if dim > 1024:
        break
    print(dim)
```

输出：

```text
512
2048
512
1024
```

### 2.5 列表与字典推导式

推导式用一行代码生成新列表或字典，适合结构简单的过滤和转换。

```python
dims = [512, 1024, 2048]
large_dims = [dim for dim in dims if dim >= 1024]

print(large_dims)
```

输出：

```text
[1024, 2048]
```

```python
sizes = {"mobileclip_s0": 131904474, "resnet50_feature": 102400000}
sizes_mib = {model: round(size / 1024 / 1024, 2) for model, size in sizes.items()}

print(sizes_mib)
```

输出：

```text
{'mobileclip_s0': 125.79, 'resnet50_feature': 97.66}
```

## 3. 函数与模型清单

### 3.1 函数封装

函数把重复逻辑打包成可复用单元。下面函数接收模型列表，返回按精度模式统计的字典。

```python
def count_by_precision(models):
    result = {}
    for model in models:
        mode = model["precision_mode"]
        result[mode] = result.get(mode, 0) + 1
    return result

models = [
    {"precision_mode": "mixed_fp16"},
    {"precision_mode": "allow_fp32_to_fp16"},
    {"precision_mode": "allow_fp32_to_fp16"},
]

summary = count_by_precision(models)
for mode, count in sorted(summary.items()):
    print(f"{mode}: {count}")
```

输出：

```text
allow_fp32_to_fp16: 2
mixed_fp16: 1
```

`dict.get(key, 0)` 在键不存在时返回默认值 0。`sorted()` 让输出顺序稳定。

### 3.2 默认参数

默认参数让调用更简洁，同时允许按名字覆盖默认值。

```python
def format_model(model_id, precision="mixed_fp16"):
    return f"{model_id}: {precision}"

print(format_model("mobileclip_s0"))
print(format_model("resnet50_feature", precision="allow_fp32_to_fp16"))
```

输出：

```text
mobileclip_s0: mixed_fp16
resnet50_feature: allow_fp32_to_fp16
```

### 3.3 *args 与 **kwargs

`*values` 收集位置参数，`**options` 收集关键字参数。

```python
def summarize(title, *values, **options):
    print(title, values, options)

summarize("models", 512, 1024, status="admitted")
```

输出：

```text
models (512, 1024) {'status': 'admitted'}
```

### 3.4 lambda

`lambda` 适合写一个短小的临时函数。

```python
models = [
    {"model_id": "mobileclip_s0", "embedding_dim": 512},
    {"model_id": "resnet50_feature", "embedding_dim": 2048},
]

models.sort(key=lambda model: model["embedding_dim"], reverse=True)
print([model["model_id"] for model in models])
```

输出：

```text
['resnet50_feature', 'mobileclip_s0']
```

### 3.5 f-string

f-string 用 `f"..."` 开头，大括号内插入变量。

```python
model_id = "mobileclip_s0"
embedding_dim = 512

print(f"{model_id}: {embedding_dim} dim")
```

输出：

```text
mobileclip_s0: 512 dim
```

### 3.6 类型注解

类型注解帮助阅读代码，也方便工具检查错误。它不会改变运行结果。

```python
def admitted_models(models: list[dict]) -> list[dict]:
    return [model for model in models if model.get("status") == "admitted"]
```

### 3.7 dataclass

`dataclass` 自动生成初始化、比较和打印方法，适合保存结构清晰的记录。

```python
from dataclasses import dataclass

@dataclass
class ModelRecord:
    model_id: str
    embedding_dim: int
    status: str = "admitted"

record = ModelRecord("mobileclip_s0", 512)
print(record)
```

输出：

```text
ModelRecord(model_id='mobileclip_s0', embedding_dim=512, status='admitted')
```

### 3.8 class 与 self

`__init__` 是实例创建时执行的方法，`self` 指向当前实例。

```python
class ModelChecker:
    def __init__(self, status="candidate"):
        self.status = status

    def is_admitted(self):
        return self.status == "admitted"

checker = ModelChecker("admitted")
print(checker.is_admitted())
```

输出：

```text
True
```

## 4. 文件、JSON、命令行与异步

### 4.1 缺失键与类型转换

模型清单可能来自不同版本的脚本，字段并不总是一致。直接读取缺失字段会触发 `KeyError`，用 `dict.get()` 可以提供默认值。

```python
model = {
    "model_id": "mobileclip_s0",
    "precision_mode": "mixed_fp16",
}

print(model.get("status", "unknown"))
```

输出：

```text
unknown
```

把字符串转成数字时，输入内容决定转换是否成功。`int("fp16")` 会触发 `ValueError`，可以用 `try/except` 捕获。

```python
value = "fp16"

try:
    numeric_value = int(value)
except ValueError:
    numeric_value = None

print(numeric_value)
```

输出：

```text
None
```

`None` 表示没有有效数值。统计前应先检查它，不要把 `None` 当成 0。

### 4.2 pathlib 与 with open

`Path` 提供跨平台的路径操作，`with open` 在读取或写入结束后自动关闭文件。

```python
from pathlib import Path

path = Path("tmp/appendix3/notes.txt")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("mixed_fp16\n", encoding="utf-8")

with path.open(encoding="utf-8") as f:
    print(f.read().strip())
```

输出：

```text
mixed_fp16
```

### 4.3 json.loads 与 json.dumps

模型配置常以 JSON 保存。`json.loads()` 把 JSON 文本转回 Python 对象，`json.dumps()` 把 Python 对象转成 JSON 文本。

```python
import json

text = '{"model_id": "mobileclip_s0", "status": "admitted"}'
data = json.loads(text)

print(data["model_id"])
print(data.get("status", "unknown"))
```

输出：

```text
mobileclip_s0
admitted
```

```python
config = {"model_id": "mobileclip_s0", "precision_mode": "mixed_fp16"}
print(json.dumps(config, ensure_ascii=False, indent=2))
```

输出：

```text
{
  "model_id": "mobileclip_s0",
  "precision_mode": "mixed_fp16"
}
```

解析失败时，`json.loads()` 会抛出 `json.JSONDecodeError`。处理外部配置或日志时，先确认它确实是合法 JSON，再读取字段，不要把解析失败和字段缺失混为一谈。

### 4.4 try / except / else / finally

`else` 在没有异常时执行，`finally` 无论是否异常都会执行。

```python
try:
    dim = int("512")
except ValueError:
    dim = None
else:
    print("converted")
finally:
    print("done")
```

输出：

```text
converted
done
```

### 4.5 argparse

`argparse` 用来解析命令行参数，样例脚本中常用来接收模型 ID、精度和路径。

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--model-id", default="mobileclip_s0")
args = parser.parse_args(["--model-id", "resnet50_feature"])

print(args.model_id)
```

输出：

```text
resnet50_feature
```

### 4.6 if __name__ == "__main__"

被直接运行时会执行 `main()`，被其他模块导入时不会自动执行。

```python
def main():
    print("check complete")

if __name__ == "__main__":
    main()
```

输出：

```text
check complete
```

### 4.7 logging

`logging` 比 `print()` 更适合记录运行过程，样例服务中常用来区分普通日志和错误日志。

```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("appendix3")
logger.info("model check complete")
```

输出：

```text
INFO:appendix3:model check complete
```

### 4.8 subprocess

`subprocess.run()` 启动外部命令，`capture_output=True` 捕获标准输出。

```python
import subprocess

result = subprocess.run(["python", "--version"], capture_output=True, text=True)
print(result.stdout.strip())
```

输出内容取决于本机 Python 版本。

### 4.9 async / await

`async def` 定义异步函数，`await` 等待异步结果。样例中的 WebRTC 和聊天服务大量使用这种写法。

```python
import asyncio

async def check():
    return "admitted"

async def main():
    print(await check())

asyncio.run(main())
```

输出：

```text
admitted
```

## 5. 调试与验证

### 5.1 最小调试手段

出错时先确认对象的真实内容，不要只看报错文字。`repr()` 可以显示字符串中的空格和引号。

```python
status = " admitted"

print(status)
print(repr(status))
```

输出：

```text
 admitted
' admitted'
```

如果逻辑复杂，可以在可疑位置使用 `breakpoint()` 进入调试器，或把中间变量打印出来。脚本保存后，先在仓库根目录执行：

```bash
python -m py_compile 脚本路径.py
```

`py_compile` 只检查语法，不运行板端逻辑。昇腾 310B 的 CANN、ATC 和 ACL 检查仍应在真实开发板上完成。

### 5.2 验证清单

- [ ] 能说出 `int`、`float`、`str`、`bool` 的区别。
- [ ] 能用列表保存输入形状或特征维度，并用循环完成求和。
- [ ] 能用字典表示一个模型条目，按字段名读取值。
- [ ] 能写函数统计模型清单中的精度模式。
- [ ] 能区分 `KeyError`、`ValueError` 和 `json.JSONDecodeError`。
- [ ] 能阅读 `samples/` 中常见的列表推导、`enumerate`、`argparse` 和 `pathlib` 写法。
- [ ] 示例只使用标准库，没有 `pandas` 或板端运行时依赖。

### 5.3 常见错误与坑

| 现象 | 可能原因 | 处理方式 |
| --- | --- | --- |
| 读取缺失字段报 `KeyError` | 模型清单字段不一致 | 先审计字段，再用 `get()` |
| `int("fp16")` 报错 | 字符串不是数字 | 用 `try/except` 捕获并记录 |
| JSON 解析失败 | 文本不是合法 JSON | 检查完整输入，捕获 `JSONDecodeError` |
| 统计结果比预期少 | 把字符串当成数字 | 先看 `type()`，必要时转换 |
| 输出顺序每次不同 | 遍历集合或字典 | 需要稳定顺序时用 `sorted()` |

## 6. 作业

1. 新建一个脚本，统计 `feature_dims = [512, 1024, 2048, 512]` 的元素个数、最小值、最大值和去重后的维度集合。
2. 写一个函数 `count_admitted(models)`，接收模型字典列表，返回 `status == "admitted"` 的模型数量。
3. 写一个 `safe_get(data, key)` 函数：键存在时返回值，不存在或类型错误时返回 `None`。
4. 用列表推导式筛出 `[512, 1024, 2048, 512]` 中大于等于 1024 的元素。
5. 用 `argparse` 写一个脚本，接收 `--model-id`，并输出该参数值。
6. 把上面的脚本都交给 DSH 或人工审查一次，逐行解释每个分支和异常处理。

AI 辅助审查时仍遵循先理解计划、检查命令、小步执行、验证结果。DSH 生成代码后，必须自己运行并解释输出，不能只看“已完成”的结论。
