---
title: "第2讲：CANN 软件栈核心与模型转换全流程"
author: [周贤中]
date: 2025-09-04
subject: "Markdown"
keywords: [Ascend, CANN, 模型转换, ATC, ACL, Profiling, Dump]
lang: zh-cn
---

本章系统阐述 Ascend CANN 软件栈的分层结构、模型从框架格式到 OM 的转换原理、转换工具 ATC 的关键参数、OM 文件组织结构、AscendCL (ACL) 推理编程模型、精度与性能验证方法以及工程级质量保障流水线建设。

## CANN异构计算架构

昇腾AI异构计算架构CANN（Compute Architecture for Neural Networks）是面向基于达芬奇架构的昇腾处理器的全栈软件体系。
作为承接主流 AI 框架与通用模型格式、向下对接异构硬件与运行时的中枢层，CANN 构建了覆盖模型表示、转换编译、图优化、调度执行与可观测分析的端到端技术链路，实现语义一致性、性能最优化与诊断可控性的协同统一，从而系统地释放昇腾处理器的算力与能效潜力。

### CANN异构计算与人工智能
异构计算与人工智能是相互促进的关系。深度学习训练与推理负载由高密度线性代数（向量/矩阵乘）、张量算子融合链、非规整控制流（条件/循环）、以及高带宽低延迟的数据搬移共同构成，表现出算子类型多样性、数据复用模式差异与访存/算力不均衡等特征。单一通用处理器在算力利用率、内存层次效率与能效上难以同时达到最优。
针对边缘计算场景的典型 SoC（如 昇腾310B、RK3588、Jetson Nano 等）普遍采用低功耗约束下设计的 ARM 通用核，其单核与整体算力在受限功耗窗口内更偏向控制与轻量任务，难以在纯 CPU 路径上满足大规模张量运算所需的高吞吐与低时延深度学习推理需求；因此需要通过集成专用 NPU/加速器进行算子下沉与数据流协同，以弥补通用核在向量化宽度、片上带宽与能效曲线上的结构性不足。 
异构体系通过在同一系统中组合通用 CPU 与专用 NPU，辅以分层内存与高吞吐互连，依据算子粒度、数据局部性与精度需求执行跨设备调度：密集算子下沉至矩阵/张量专用单元，控制与调度逻辑保留在通用核，数据流经由优化的流水与对齐策略减少跨域搬运成本。结果是在单位功耗与芯片面积约束下提升有效吞吐、降低端到端推理时延并改进能效曲线的可扩展性。该协同范式构成 CANN 设计的理论基础：通过抽象统一语义表示与针对性后端优化，将模型图中不同类别算子映射到最合适执行单元，实现性能、能效与可诊断性三者的统筹平衡。 

### CANN与CUDA
CANN是面向华为昇腾达芬奇架构昇腾处理器，聚焦深度学习推理与训练的算子图优化、编译调度及可观测性闭环，而CUDA（Compute Unified Device Architecture）则是针对 NVIDIA GPU 的通用并行计算平台与编程模型，覆盖 GPGPU 与 AI 复合场景。二者均体现“软硬件协同加速”范式，但在抽象层次、硬件耦合与生态策略上形成差异化路径。
CANN与CUDA二者的共同点主要体现在，它们都是通过软硬件协同，把模型或并行程序转化为底层设备能理解的指令，从而提升运行速度和效率。同时这两种计算平台都提供了内存管理、算子或内核调用、性能分析、调试和日志等工具，帮助开发者更方便地开发和诊断问题。它们都支持自定义算子或内核插件，方便针对特定需求进行优化或替换实现。此外，CANN与CUDA都能通过性能分析、数据导出和分级日志等方式，降低了定位性能瓶颈和对齐精度的难度。 
在设计理念上，CANN 与 CUDA 展现出显著的差异。CUDA 作为面向广义并行计算与 HPC/图形及 AI 统一生态的平台，强调线程块与网格（Block/Grid）以及 SIMT 并行范式，开发者需显式组织核函数、流（Stream）、事件（Event）和内存管理，实现灵活的并行与资源重叠。而 CANN 则专注于模型图语义，突出图级算子融合、AIPP 预处理下沉和全局内存复用，通过任务列表与算子调度策略有效治理动态形状与内存复用，降低推理工程的不确定性。在精度策略方面，CANN 内置 FP16 与 INT8 的混合精度及校准链路，适应端到端推理与训练的工程质量要求；而 CUDA 虽然基础类型丰富，但精度管理更多依赖于上层库如 cuDNN 和 TensorRT 的组合。硬件结构上，CANN紧密耦合于达芬奇架构的矩阵单元、向量单元及片上分层内存，优化模型推理的能效与性能；CUDA 则绑定于 NVIDIA 的 SM、Tensor Core 及多级缓存体系，着重提升访存效率与线程调度。生态成熟度上，CUDA 自 2007 年以来积累了庞大的社区与库支持，形成了全球主流的开发者生态；CANN 虽处于快速迭代阶段，但聚焦国产化替代与特定行业应用，推动本地生态建设。编程灵活度方面，CUDA 需要开发者具备底层并行划分与核函数开发能力，提供极高的灵活性；CANN 则通过贴近主流框架的算子开发工具（TBE）与高层接口，降低了入门门槛，但在底层调度上牺牲了一定的自由度。整体而言，二者在抽象层次、硬件耦合与生态策略上形成了各自鲜明的技术路径，反映了其服务对象与应用场景的根本差异。

### CANN的架构

CANN 通过提供分层清晰的编程与运行时结构，以覆盖训练与推理的“全场景”、贴近主流框架的“低门槛”以及面向昇腾硬件深度优化的“高性能”为核心特性，支撑用户在昇腾平台上快速构建与部署各类 AI 应用与业务。从整体上看，CANN 可以抽象为一个自上而下递进的五层架构（计算语言接口、计算服务层、计算编译引擎、计算执行引擎与计算基础层），如图\ref{fig:architecture}所示。各层通过稳定的接口与数据流协议进行解耦协同，在保证语义一致性的前提下最大化发挥底层算力与能效潜力。

![CANN架构图](img2\CANN_Architecture.png){#fig:architecture width=90%}

在分层结构上，CANN可抽象为五个层次：上层的计算语言接口——AscendCL接口负责设备与上下文管理、流与内存控制、模型与算子的加载执行，以及媒体与图管理等通用API，为应用提供稳定的编程入口；其下的昇腾计算服务层汇聚神经网络与线性代数库，承载算子与子图的自动调优、梯度优化与模型压缩，并通过框架适配器降低迁移成本；昇腾计算编译层的编译引擎通过图编译器与TBE算子开发支持，把前端计算图转化为在NPU可执行的模型与内核，实现图级语义到后端实现的精准映射；下一层的昇腾执行引擎面向运行时，负责模型与算子的调度执行，并内置数字视觉与AI预处理、集合通信等能力以提升端到端效率；最下层的昇腾计算基础层提供共享虚拟内存、设备虚拟化与主机—设备通信等底座服务，保证跨设备数据流与资源管理的可靠性与可扩展性。

与此相辅的是三层逻辑架构：应用层承载具体业务与开发者工具，芯片使能层开放解决方案能力并驱动基于计算图的业务流运行，计算资源层则聚焦数据处理与运算执行，形成从业务到硬件的清晰闭环。这个CANN的三层逻辑结构如下图\ref{fig:cann_logic}所示:

![CANN架构图](img2\CANN_logic.png){#fig:cann_logic width=90%}

在技术特性上，CANN通过对计算图的编译与优化，将密集算子与数据流合理分配到异构单元上执行，显著提升吞吐与时延表现，并在能效上取得可工程化的优势；同时提供贴近主流框架的易用接口与完善的工具链，降低入门与迁移门槛，使开发者能够快速完成部署与调优。生态方面，CANN构建了面向个人、高校、科研与企业的赋能体系，并与开源社区协同，支持多种框架与推理引擎在异构硬件上的高效运行。依托这些能力，开发者可以深入调用运行时与图编译能力，释放底层硬件潜力，在性能与成本维度形成差异化竞争力。 

在工程实践中，CANN 的核心价值可以概括为在“模型—编译—执行—诊断”的完整链路上实现软硬件协同与语义稳定：通过构建对主流深度学习框架高度兼容的前端接口，最大限度降低模型迁移与语义对齐成本，在图级优化阶段则依托算子融合、内存复用以及混合精度策略的协同设计，系统性提升吞吐能力、推理时延以及功耗效率；与此同时，借助自定义算子机制与实现优先级调度策略，使新结构能够快速接入并在多实现版本之间灵活切换，从而在不同硬件代际与不同业务场景下充分挖掘底层计算资源的潜力，并在运行时通过分级日志、Profiling 时间线与精度 Dump 等手段构建起闭环的可观测体系。在大规模、复杂模型的工程化路径中，首先需要完成面向硬件架构的感知建模，在导出阶段显式固化张量形状与算子语义，为后续编译与部署奠定稳定的语义基础；随后在模型转换阶段，以“转换即优化”为原则，围绕 precision_mode、op_select_implmode 与 AIPP 等关键配置项进行显式调优，使图优化与算子选型在编译期前置完成，其中精度策略以 FP16 为主，并辅以具有代表性的校准数据集对 INT8 量化误差进行严格控制，以在性能与精度之间取得可工程化的平衡。对于动态形状问题，可以采用分桶与 Padding 结合的方式，并在必要时生成多份 OM 模型，以在一定范围内换取更可控的峰值资源占用与时延方差；在内存与带宽层面，则通过将预处理逻辑尽可能下沉到设备侧、合并数据搬移操作，并配合 Pinned 内存与多 Stream 并行传输技术，显著降低 H2D/D2H 的时间占比，从系统视角优化整体流水线的调度效率。针对在实际 Profiling 中暴露出的性能瓶颈算子，需要开展有针对性的算子重写与图重构，并充分利用实现优先级机制在不同 Kernel 之间进行策略化选择，以进一步榨取底层硬件的计算与访存潜力；最终，通过围绕“转换—精度对齐—Benchmark—回归监测”构建自动化流水线，将模型转换、性能评估与精度验证纳入统一的持续反馈闭环，在 Ascend 310B 等专用加速平台上沉淀出可复用的优化经验与差异化性能优势，不仅在算力利用率与综合成本上形成工程级竞争力，也为大模型与行业应用场景的规模化加速落地提供坚实的基础设施支撑。

### CANN的快速安装

本节给出 CANN 软件的极速安装示例。若需更完整的操作指导，请按实际环境选择对应安装场景并参考[官方说明](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/83RC1/index/index.html)，推荐优先使用离线安装。  

#### 安装前准备

- 驱动与固件检查
  - 在目标设备执行 `npu-smi info`；若能正常输出设备信息，说明 NPU 驱动与固件已安装。
  - 对于昇腾 310B 产品/开发板，系统通常预置相关驱动与固件。

- 环境准备
  - 推荐采用离线安装；需预先准备 Python 与 `pip3`。
  - 当前支持 Python 3.7.x–3.11.4。
  - 昇腾310B开发板一般已预装 Miniconda 与 `pip3`，可直接使用。

- 安装介质获取
  - 先下载所需 CANN 软件包并上传到可访问路径后再执行安装。
  - 例如 CANN 8.3.RC1 可从：https://www.hiascend.com/developer/download/community/result?module=cann&cann=8.3.RC1 获取；其他版本可在页面切换。
  - 确保 CANN Toolkit 与 CANN Kernels 版本严格匹配（同一发行号）。
  - 建议优先使用 .run 安装包；昇腾 310B 开发板为 AArch64 架构，请选择 aarch64 对应包。以 8.3.RC1 为例：
    - Ascend-cann-kernels-310b_8.3.RC1_linux-aarch64.run
    - Ascend-cann-toolkit_8.3.RC1_linux-aarch64.run
  - 若版本不同，请将文件名中的版本号替换为实际下载的版本。

#### 安装命令与依赖说明
安装 CANN 可使用 root 或默认账户 HwHiAiUser。推荐以 root 安装；若使用 HwHiAiUser，需在所有安装与文件操作命令前加 sudo。
下面以 root 用户为例，详细介绍 CANN 的安装过程：

- 切换到 root：
  ```bash
  su -
  # 输入 root 密码后继续执行安装
  ```

- 命令示例
  - 以 root 执行：
    ```bash
    ./Ascend-cann-toolkit_8.3.RC1_linux-aarch64.run --install
    ```
  - 以 HwHiAiUser 执行：
    ```bash
    sudo ./Ascend-cann-toolkit_8.3.RC1_linux-aarch64.run --install
    ```
- 安装 Toolkit 开发套件  
  ```bash
  chmod +x Ascend-cann-toolkit_8.3.RC1_linux-aarch64.run 
  ./Ascend-cann-toolkit_8.3.RC1_linux-aarch64.run --install
  ```
  整个安装过程大概会持续十几分钟，甚至更长，请耐心等待。  

- 配置环境变量
  ```bash
  source /usr/local/miniconda3/Ascend/ascend-toolkit/set_env.sh
  ```

- 安装 Kernels 算子包（310B 平台）
  ```bash
  chmod +x Ascend-cann-kernels-310b_8.3.RC1_linux-aarch64.run
  ./Ascend-cann-kernels-310b_8.3.RC1_linux-aarch64.run --install
  ```

- 安装业务运行时所需的 Python 第三方库（使用非 root 用户时保留 --user 参数）
  ```bash
  pip3 install \
    attrs cython 'numpy>=1.19.2,<=1.24.0' decorator sympy cffi pyyaml pathlib2 \
    psutil protobuf==3.20.0 scipy requests absl-py
  ```

- 说明
  - 若使用非 root 用户，请在上述命令末尾追加 `--user`。
  - 依赖版本范围已覆盖常见环境，若发生冲突，请按实际 Python/OS 版本调整。
  - 安装完成后，可将 CANN 环境变量追加到 `~/.bashrc` 以便自动加载；昇腾 310B 开发板的系统镜像通常已预置该配置。 

## ATC模型转换详解

### ATC工具介绍

昇腾张量编译器（Ascend Tensor Compiler，ATC）是 CANN 异构计算体系中的模型转换组件，支持将主流开源框架导出的网络模型以及基于 Ascend IR 的单算子描述文件（JSON）编译为昇腾 AI 处理器可执行的离线模型（.om）。如图下图所示，ATC 在转换过程中会执行算子调度优化、权重重排与内存复用等关键步骤，对原始深度学习模型进行面向部署场景的系统化调优，从而在昇腾硬件上实现高吞吐、低时延的高效执行。

![ATC框架图](img2\atc.png){#fig:atc width=70%}

从上面的流程图我们可以看到，ATC 工具聚焦模型到设备可执行体的“转换即优化”闭环：一方面，它能够将开源框架导出的网络模型解析为中间态 IR Graph，经由图准备、拆分与融合、形状推理、内存复用与算子选型等编译步骤，生成适配昇腾处理器的离线模型（OM），并在板端通过 AscendCL 接口加载执行，从语义到性能实现端到端落地；另一方面，ATC 也支持基于 Ascend IR 的单算子 JSON 直接编译为离线 OM，用于在设备侧进行算子级功能验证与精度对齐，帮助开发者快速定位与迭代关键算子实现，在图级与算子级两条路径上形成统一的工程化编译能力。

#### onnx格式介绍
ONNX（Open Neural Network Exchange）是一种针对深度学习所设计的开放式文件格式，用于存储训练好的模型。它使得不同的人工智能框架（如PyTorch、TensorFlow、mindspore等）可以采用相同格式存储模型数据并交互。ONNX的规范包含计算图模型的定义，以及内置运算符的定义和标准数据类型。

ONNX的核心价值在于解决了AI生态系统中“碎片化”的问题。在没有ONNX之前，开发者如果想将一个在PyTorch中训练好的模型部署到移动端推理引擎上，通常需要进行繁琐的代码重写或复杂的格式转换。ONNX提供了一种中间表达（Intermediate Representation, IR），充当了不同框架之间的“通用语”。

一个标准的ONNX模型文件（通常以`.onnx`为后缀）主要包含以下几个部分：
1.  **Model Proto**：顶层结构，包含模型的元数据（如版本信息、生产者信息）和计算图（Graph）。
2.  **Graph Proto**：描述了模型的计算逻辑，由一系列节点（Node）、输入（Input）、输出（Output）和初始化器（Initializer，即权重数据）组成。
3.  **Node Proto**：代表计算图中的一个操作（Operator），如Conv、Relu、Add等。每个节点包含操作类型、输入输出张量的名称以及属性（Attribute，如卷积的步长、填充等）。
4.  **Tensor Proto**：用于存储权重数据或常量的具体数值和形状信息。

**ATC对ONNX的支持情况**
虽然ONNX旨在成为通用的AI模型交换标准，但CANN的ATC工具并非支持ONNX规范中的所有算子和特性。ATC对ONNX的支持主要集中在CV（计算机视觉）和NLP（自然语言处理）领域的常用算子，对于一些较新或较冷门的算子，可能会出现不支持的情况。

具体来说，ATC对ONNX的支持限制主要体现在以下几个方面：
1.  **算子覆盖率**：ATC支持绝大多数主流的CNN和Transformer类算子（如Conv, MatMul, Relu, Softmax等）。但对于部分非标准或特定框架自定义的算子（Custom Ops），ATC无法直接解析，需要开发者通过TBE（Tensor Boost Engine）开发自定义算子并注册到CANN中。
2.  **动态控制流**：ONNX中的动态控制流（如`If`, `Loop`, `Scan`）在静态图编译中处理较为复杂。虽然ATC支持部分控制流算子，但在某些复杂嵌套场景下可能会导致编译失败或性能下降，通常建议在导出模型时尽量将控制流展开（Unroll）或转为静态图结构。
3.  **数据类型限制**：虽然ONNX支持多种数据类型（如Double, Int64等），但昇腾AI处理器主要针对FP16和INT8进行了硬件加速优化。对于FP64（Double）类型，ATC通常不支持或需要强制转为FP32/FP16处理；对于Int64类型的索引或计数，部分算子可能要求转为Int32。
4.  **动态Shape支持**：虽然ATC支持动态Batch和动态分辨率，但对于模型内部中间层出现的完全动态且无法推导的Shape，可能会导致编译报错。

因此，在进行模型转换前，建议开发者查阅华为昇腾社区发布的《CANN 算子清单》，确认模型中使用的ONNX算子版本（Opset Version）是否在当前CANN版本的支持范围内。如果遇到不支持的算子，通常的解决路径包括：修改模型结构以替换为等价的支持算子、在ONNX导出阶段进行算子简化（使用`onnx-simplifier`工具）、或者开发自定义算子。

### ATC工具使用流程

使用ATC工具进行模型转换的使用流程如下图所示：

![ATC流程图](img2\atc_flow.png){#fig:atc_flow width=70%}

在开始模型转换之前，首先需要在开发环境中安装与 CANN 软件包版本相匹配的版本，并确保 ATC 可执行文件的路径可用。接下来，准备待转换的模型文件或基于 Ascend IR 的单算子 JSON，并将其上传到开发环境中可访问的目录。最后，使用 ATC 执行转换，并根据实际业务需求和输入规范配置相关参数。如果需要将预处理步骤（如色彩空间转换、归一化和尺度调整）下沉到设备侧，可以同时提供并启用 AIPP（Artificial Intelligence Pre-Processing）配置。AIPP是昇腾处理器内置的硬件级图像预处理模块，负责将上游（如 DVPP）输出的对齐后YUV420SP图像在设备侧完成色域转换（如YUV图像格式转换为RGB或者BGR图像格式）、归一化（减均值/乘系数/尺度放大）与抠图（在指定起始点裁剪到模型输入尺寸），从而把原始图像规范化为模型所需的输入格式与数值范围。由于昇腾310B在推理及训练中常以DVPP输出YUV420SP图片格式，这种图像格式是有损图像颜色编码格式，常用为YUV420SP_UV、YUV420SP_VU两种格式，不直接提供RGB图片。AIPP能将YUV420SP类型图像无缝转化为模型期望的RGB/BGR图像格式，并在同一数据流中完成裁剪与数值处理，避免将预处理放在CPU侧导致的多次拷贝与额外时延，提升端到端吞吐与能效。

####  模型转换-以ResNet50为例
ResNet-50 由多级残差单元堆叠形成 50 层卷积网络，通过跨层跳连缓解深层退化与梯度消失，使其在 ImageNet 等大规模数据集上具备稳定的特征表达与分类精度，因此常被选作各类视觉任务的通用骨干。要在昇腾 310B 上部署该模型，需要借助 ATC 将 ONNX 版本转换为 OM，可参考华为昇腾官方示例仓库（https://github.com/Ascend/samples/tree/master/inference/modelInference/sampleResnetQuickStart）。为方便快速复现，可按以下步骤从零搭建工程：

1. 在昇腾 310B 的 `Documents` 目录创建 `sample_resnet_quick_start`，并划分 `data`、`model`、`out`、`src` 四个子目录用于存放测试图片、模型、脚本与源码：
  ```bash
  cd ~/Documents
  mkdir -p sample_resnet_quick_start/{data,model,out,src}
  cd sample_resnet_quick_start
  ```
2. 下载示例图片至 `data` 目录：
  ```bash
  cd ~/Documents/sample_resnet_quick_start/data
  wget https://obs-9be7.obs.cn-east-2.myhuaweicloud.com/models/aclsample/dog1_1024_683.jpg
  ```
3. 进入 `model` 目录获取 ResNet-50 ONNX 模型：
  ```bash
  cd ~/Documents/sample_resnet_quick_start/model
  wget https://obs-9be7.obs.cn-east-2.myhuaweicloud.com/003_Atc_Models/resnet50/resnet50.onnx
  ```
4. 设置编译并行度后执行典型 ATC 命令，生成适配昇腾 310B4 的 OM 文件：
  ```bash
  export TE_PARALLEL_COMPILER=1
  export MAX_COMPILE_CORE_NUMBER=1
  atc \
    --model=resnet50.onnx \
    --framework=5 \
    --output=resnet50 \
    --input_shape="actual_input_1:1,3,224,224" \
    --soc_version=Ascend310B4
  ```
**关键参数说明**
| 参数 | 作用 | 注意事项 |
| ---- | ---- | -------- |
| `--framework` | 指定原始模型框架 | 0=Caffe，1=MindSpore，3=TensorFlow，5=ONNX，需与导出框架一致 |
| `--input_shape` | 定义静态输入 Shape | 按 `"name:n,c,h,w"` 写法，字符串需加引号；动态改用区间或配合动态参数 |
| `--dynamic_batch_size` | 设置可选 Batch 列表 | 以 `"1,4,8"` 形式填写；与同一输入的完全静态 shape 互斥 |
| `--dynamic_image_size` | 配置多分辨率输入 | `"h1,w1;h2,w2"`，常用于多尺度检测；需与模型实际支持一致 |
| `--precision_mode` | 控制混合精度策略 | 常用值：`force_fp16`、`allow_mix_precision`、`allow_fp32_to_fp16`，需兼顾精度 |
| `--soc_version` | 选择目标芯片 | 例：`Ascend310B4`，可通过 `npu-smi info` 查询；310B/P 区分清楚 |
| `--insert_op_conf` | 下沉 AIPP/自定义算子 | 指定 JSON/YAML，支持色域转换、归一化等预处理 |
| `--op_select_implmode` | 算子实现优先级 | 支持 `high_performance`(默认)、`high_precision` 等，可配合 `--optypelist_for_implmode` |
| `--input_format` | 声明输入数据排布 | 如 `NCHW`、`NHWC`；需与模型导出及 `--input_shape` 保持一致 |
| `--output_type` | 重指定输出 dtype | 可设全局 `FP16`/`FP32`，或按 `node:idx:FP32` 精细配置，便于后处理 |
| `--enable_small_channel` | 小通道优化开关 | 取值 0/1，轻量网络或低通道数场景启用可减时延 |
| `--model` | 指定待转换模型文件 | 支持 `.onnx`、`.pb`、`.prototxt` 等类型，路径需可访问 |
| `--output` | 设置 OM 输出前缀 | 不需写后缀；默认位置在当前目录，可结合 `--output_type` 使用 |
| `--dynamic_dims` | 定义多维动态组合 | `"d1_1,d1_2;d2_1,d2_2"` 格式；用于多输入或非 Batch 维变动 |
| `--enable_single_stream` | 强制单流执行 | `true/false`；在推理序列化或调试稳定性时启用 |
| `--dump_mode` | 导出模型结构 JSON | 配合 `--mode=1` 使用，`1` 开启；便于排查 Shape 与算子信息 |

如果对于参数有任何的疑惑，可以通过命令查询atc的参数作用：
```bash
atc --help
```

成果转换模型后，我们可以看到命令窗口有如下输出：

```bash
ATC start working now, please wait for a moment.
...
ATC run success, welcome to the next use.
  ```


#### 注意事项
1. 在昇腾310B上将 ONNX 模型转换为 OM 时若出现 `BrokenPipeError: [Errno 32] Broken pipe`，通常是编译阶段内存不足导致进程被系统终止。可参照以下两类方案缓解：

- **扩展可用内存**
  ```bash
  dd if=/dev/zero of=/swapfile bs=1M count=8192
  chmod 600 /swapfile
  swapon /swapfile
  ```
  并在 `/etc/fstab` 中新增：
  ```
  /swapfile swap swap defaults 0 0
  ```

- **降低编译资源占用**
  ```bash
  export TE_PARALLEL_COMPILER=1      # 限制算子最大并行编译进程数
  export MAX_COMPILE_CORE_NUMBER=1   # 限制图编译占用的 CPU 核数
  ```
  方便起见，我们可以采用第二种方案，可以有效解决内存不足的问题。

2. 若无法确认当前设备的 `soc_version`，可在安装驱动的昇腾310B上执行 `npu-smi info` 查询，示例如下： 

```bash
+--------------------------------------------------------------------------------------------------------+
| npu-smi 23.0.0                                   Version: 23.0.0                                       |
+-------------------------------+-----------------+------------------------------------------------------+
| NPU     Name                  | Health          | Power(W)     Temp(C)           Hugepages-Usage(page) |
| Chip    Device                | Bus-Id          | AICore(%)    Memory-Usage(MB)                        |
+===============================+=================+======================================================+
| 0       310B4                 | Alarm           | 0.0          58                15    / 15            |
| 0       0                     | NA              | 0            2500 / 7545                             |
+===============================+=================+======================================================+
```

请在查询结果的 `Name` 值前追加 `Ascend` 前缀：若 `Name` 为 `310B4`，则需配置 `soc_version=Ascend310B4`。

3. 模型转换完成后会生成一份 JSON 文件，用作 ATC 编译日志的结构化摘要，集中记录当前会话（session_and_graph_id=0_0）内图级与 UB 级融合规则的触发统计。具体而言，graph_fusion 字段逐条给出各图融合 Pass 的匹配次数与实际生效次数（match_times 与 effect_times），可据此识别潜在的优化空档；ub_fusion 字段在上述统计基础上新增 repository_hit_times，用以衡量算子库模板的命中情况。实践中，可重点关注 effect_times 低于 match_times 的 Pass，分析是否因算子属性、精度模式或实现优先级等因素造成融合未落地，并据此调整 ATC 参数或模型图结构，以复现并提升性能优化效果。 

### 模型推理工具msame工具概览
在完成 ATC 转换得到 `.om` 模型后，最快验证部署链路是否畅通的方式就是调用华为昇腾提供的模型推理工具 msame。该工具封装了 OM 加载、设备内存初始化、输入二进制数据映射以及推理调度等通用流程，无需额外编写 AscendCL 程序即可直接对接 ATC 输出的模型，只需准备好与模型输入规格匹配的 `.bin` 文件和基础运行时环境，即可在命令行下完成端到端推理验证。通过 msame，开发者能够快速检查模型是否成功落地、输出是否合理，并以最小成本排除输入格式或环境配置类问题，为后续集成到自研应用或高层框架之前提供低门槛的功能性回归手段。

msame 的设计目标是将 OM 模型的快速验证能力标准化，覆蓋单输入、多输入和循环执行等常见推理形态，支持在相同模型与不同输入数据组合之间做对比试验，同时允许选择 TXT 或 BIN 格式导出结果，以便于人工审查或自动化脚本解析。在已正确安装 CANN Runtime 的环境中，msame 会自动完成与设备的上下文绑定、内存申请和任务提交，将推理过程抽象成简单的命令行参数；这不仅适用于初步确认模型可运行，也可用于小批量性能评估、数据一致性校验以及在定位性能或精度问题时的基准对照，从而在模型转换与实际业务部署之间搭建起轻量而高效的桥梁。

#### msame工具的获取与构建
1. 通过 `git clone https://gitee.com/ascend/tools.git` 拉取仓库，或下载 ZIP 包后解压到开发环境。
2. 进入 `msame` 子目录：
  ```bash
  cd ./tools/msame
  ```
3. 配置 CANN 环境变量（以下路径请按实际安装位置调整）：
  ```bash
  export DDK_PATH=/usr/local/Ascend/ascend-toolkit/latest
  export NPU_HOST_LIB=/usr/local/Ascend/ascend-toolkit/latest/runtime/lib64/stub
  ```
4. 运行构建脚本生成可执行文件：
  ```bash
  ./build.sh g++
  ```
5. 构建成功后会在 `out` 目录生成二进制文件 `msame`，将其复制到工程目录 `sample_resnet_quick_start` 中备用。

  + 常用参数速览
    | 参数 | 说明 |
    | --- | --- |
    | `--model` | OM 文件路径 |
    | `--input` | 输入 `bin` 文件或目录，支持多输入 |
    | `--output` | 推理结果存放目录 |
    | `--outfmt` | 输出格式：`TXT` / `BIN` |
    | `--loop` | 重复推理次数（1–100） |
    | `--profiler` / `--dump` | 性能分析或 Dump，互斥 |
    | `--device` | 设备 ID，默认 0 |
    | `--dymBatch` / `--dymHW` / `--dymDims` / `--dymShape` | 对应 ATC 动态配置的实际取值 |
    | `--outputSize` | 动态 Shape 场景下手动申请输出内存 |
    | `--debug` | 打印模型描述信息 |
    | `--help` | 查看全部参数 |

  + 使用注意
    - 运行账号需具备当前目录的执行与写入权限。
    - `TXT` 输出不适用于部分动态 Shape，可改用 `BIN`。
    - 配置 `NPU_HOST_LIB` 时应指向 `runtime/lib64/stub` 目录，运行阶段通过 `LD_LIBRARY_PATH` 链接实际依赖。
    - profiler 与 dump 不可同时开启；动态 shape 时需同步设置合适的输出内存大小。

#### 图片预处理
由于 msame 工具不具备图像预处理能力，输入必须是已转换好的 `.bin` 文件。因为之前在模型转换时未显式指定精度，ResNet50 默认接受 FP32 格式的输入；同样地，未设置输入内存布局时 ATC 会采用默认的 NCHW。NCHW 表示批次 N、通道 C、空间高度 H 与宽度 W，意味着同一张图片的三个颜色通道会按通道优先的顺序依次排布，再展开到空间维度。这与 TensorFlow 常见的 NHWC（通道在最后）相对。`sampleResnetQuickStart.py` 中的 `image_rgb.transpose([2, 0, 1])` 正是将 OpenCV 读取的 HWC 布局转换为 CHW，并配合外层批次维得到符合要求的 NCHW。是否必须使用 NCHW 取决于模型导出时的约定。PyTorch 与 CANN 默认使用 NCHW，因此常见的 `resnet50.onnx`/`resnet50.om` 都在此布局下训练与推理。只要导出模型为 NCHW，推理阶段同样必须以 NCHW 喂入数据，否则通道错位会导致结果异常。当然，也可以导出为 NHWC，只需在模型与预处理两端同时调整即可。
由于刚才我们从华为云哪里获取的测试图片“dog1_1024_683.jpg”如下：

![resnet测试图片](img2/dog1_1024_683.jpg){#fig:dog width=90%}

由于这个图片是jpg格式的，因此为了可以利用这个图片推理，我们第一步需要对图片进行预处理，具体过程如何：
1. 在src文件目录创建一个预处理的python文件“make_bin_resnet224_float32.py”如下：

  ```python make_bin_resnet224_float32.py
  from PIL import Image
  import numpy as np

  # 加载图像并转换为 RGB
  img = Image.open('data/dog1_1024_683.jpg').convert('RGB')
  # 使用双线性插值缩放到 256x256
  img = img.resize((256, 256), Image.BILINEAR)
  # 计算 224x224 中心裁剪的坐标
  left = (256 - 224) // 2
  top = (256 - 224) // 2
  # 执行中心裁剪
  img = img.crop((left, top, left + 224, top + 224))

  # 转换为 [0, 1] 范围的 float32 数组
  arr = np.array(img).astype(np.float32) / 255.0
  # 定义归一化常量
  mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
  std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
  # 按通道进行归一化
  arr = (arr - mean) / std

  # 调整为 NCHW 格式并添加批次维度
  arr = arr.transpose(2, 0, 1)[None, :, :, :]
  # 保存为二进制文件
  arr.tofile('data/dog1_224_float32.bin')
  # 打印缓冲区大小（字节数）
  print('bytes:', arr.nbytes)
  ```
  
2. 在shell中运行这个python文件：

  ```bash
  python src/make_bin_resnet224_float32.py
  ```
  
  该脚本在导出 `.bin` 输入前会完成：将示例图像缩放到 256×256 后再做 224×224 中心裁剪；把像素转换为 NCHW 排布的 `float32` 缓冲区并写入文件；整个过程中先除以 255 将数值归一化到 [0,1]，再按通道减去 `[0.485, 0.456, 0.406]`、除以 `[0.229, 0.224, 0.225]` 标准差，以对齐 ResNet 在 ImageNet 上的训练预处理，从而避免因均值或方差失配导致的输出偏移，最终在`data`文件夹内得到 `dog1_224_float32.bin`。

3. 快速运行——单输入文件
  运行下面这个命令：

  ```bash
  ./msame --model "./model/resnet50.om" --input "./data/dog1_224_float32.bin" --output "./out" --outfmt BIN
  ```

  运行成功后，我们会得到以下的信息：

  ```bash
  [INFO] acl init success
  [INFO] open device 0 success
  [INFO] create context success
  [INFO] create stream success
  [INFO] get run mode success
  [INFO] load model ./model/resnet50.om success
  [INFO] create model description success
  [INFO] get input dynamic gear count success
  [INFO] create model output success
  ./out/20251213_11_6_52_564388
  [INFO] start to process file:./data/dog1_224_float32.bin
  [INFO] model execute success
  Inference time: 6.811ms
  [INFO] get max dynamic batch size success
  [INFO] output data success
  Inference average time: 6.811000 ms
  [INFO] destroy model input success
  [INFO] unload model success, model Id is 1
  [INFO] pid: 98299 Execute sample success
  [INFO] end to destroy stream
  [INFO] end to destroy context
  [INFO] end to reset device is 0
  [INFO] end to finalize acl
  ```

  + 该日志显示推理流程已完整走通：ACL 初始化、设备上下文、模型加载、输入输出申请均返回 `success`，`Inference time`/`Inference average time` 给出单次与平均耗时，路径 `./out/20251213_11_6_52_564388` 指向本次推理结果目录。该输出目录遵循“YYYYMMDD_H_M_S_microsec”命名：`20251213` 表示日期（2025-12-13），`11_6_52` 为时分秒，`564388` 为微秒级序列号，用于区分同日多次推理结果。
  + 若选择 `--outfmt BIN`，可用 `numpy.fromfile('./out/.../resnet50_output_0.bin', dtype=np.float32).reshape(1, 1000)` 读取并执行 `np.argsort` 获取 TopK；使用 `softmax` 还原概率后结合 ImageNet label 映射即可解读分类结果。
  + 若选择 `--outfmt TXT`，直接 `cat ./out/.../resnet50_output_0.txt | head` 快速查看前若干输出值，同样可以配合脚本解析为 TopK 概率。


4. 后处理  
  无论 msame 的 `--outfmt` 选择 `BIN` 还是 `TXT`，得到的都是形如 `1×1000` 的分类 logits，需要结合 ImageNet 标签映射再做一次后处理才能输出可读结果。以下示例脚本演示如何解析 `BIN` 文件、执行 softmax 并打印 Top-K 概率。  

  + 先下载 `imagenet_class_index.json`（可来自 Kaggle 或 GitHub）放到 `src` 目录，然后创建 `src/postprocess_resnet50.py`：  
  ```python
  # src/postprocess_resnet50.py
  import os
  import argparse
  import json
  import numpy as np

  parser = argparse.ArgumentParser(description="将ResNet50的 BIN 输出解码为ImageNet标签。")
  parser.add_argument("--bin", required=True, help="msame 输出BIN文件的路径。")
  parser.add_argument("--topk", type=int, default=5, help="需要打印的最高置信度数量。")
  args = parser.parse_args()

  logits = np.fromfile(args.bin, dtype=np.float32).reshape(1, -1)
  probs = np.exp(logits - logits.max(axis=-1, keepdims=True))
  probs = probs / probs.sum(axis=-1, keepdims=True)
  probs = probs[0]

  labels_path = os.path.join("src", "imagenet_class_index.json")
  if not os.path.exists(labels_path):
      raise FileNotFoundError("请先将imagenet_class_index.json下载到src目录。")

  with open(labels_path, "r", encoding="utf-8") as f:
      data = json.load(f)
  labels = [data[str(i)][1] for i in range(len(data))]

  topk = np.argsort(probs)[::-1][:args.topk]
  print(f"Decoded {args.bin} (Top-{args.topk})")
  for rank, idx in enumerate(topk, start=1):
      label = labels[idx] if idx < len(labels) else f"cls_{idx}"
      print(f"{rank:>2d}: class={idx:<4d} prob={probs[idx]:.6f} label={label}")
  ```

  + 执行脚本：  
  ```bash
  python ./src/postprocess_resnet50.py --bin out/20251213_11_6_52_564388/resnet50_output_0.bin
  ```
  + 示例输出：  
  ```bash
  Decoded out/20251213_11_6_52_564388/resnet50_output_0.bin (Top-5)
   1: class=162  prob=0.964885 label=beagle
   2: class=161  prob=0.023230 label=basset
   3: class=166  prob=0.006107 label=Walker_hound
   4: class=167  prob=0.004985 label=English_foxhound
   5: class=164  prob=0.000334 label=bluetick
  ```

 


## 章节小结
本章从宏观分层、转换编译、OM 结构、ACL 编程、性能与精度保障、调试工具、自动化流水线到动态 Shape 与安全实践建立了闭环。掌握这些内容后即可进入后续“边缘系统架构与部署实践”章节，扩展到多模型、多进程及系统级优化。

## 实践任务
1. 任选一个公开 ONNX 分类模型（如 ResNet50）完成 ATC 转换，提交：命令 + atc.log。
2. 以 C 或 Python 实现最小推理程序，输出前 5 TopK 结果与 softmax 概率。
3. 编写对齐脚本比较 50 张图片 ONNX vs OM 输出差异（报告 L1/Top1 差异）。
4. 收集 Profiling Timeline，列出前 3 耗时算子类型及优化建议。
5. 输出 `signature.json`、`metrics.json`、`conversion_meta.yaml` 并归档。

