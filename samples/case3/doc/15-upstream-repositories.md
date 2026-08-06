# Upstream 参考仓库清单

> 本文记录 case3 调研和移植时使用的第三方源码、当前本地提交和保留规则。
> [返回文档索引](README.md)。

## 为什么之前看起来只剩两个

审计开始时，`_upstream/` 中不是只有两个 Git 仓库，而是四个：

- `ddsp-vst`
- `ddsp-realtime`
- `ascend-cann-samples`
- `ascend-cann-samples-official`

如果只看 DDSP 相关目录，确实只剩 `ddsp-vst` 和 `ddsp-realtime` 两个。文档中还
引用了 MIDI-DDSP、两个 PyTorch DDSP、realtimeDDSP 和两个钢琴 DDSP 仓库，但
它们当时没有位于 `_upstream/`。

主仓库的 `.gitignore` 明确忽略 `samples/case3/_upstream/`，而且主仓库历史中没有
该目录的提交。因此现有 Git 记录无法证明这些目录是何时被删除的，还是最初只作为
网页参考、从未克隆到这里。以前同步 case3 到开发板时也有意排除了 `_upstream`，
因为它不是板端运行依赖；该排除规则不会删除本地目录。

2026-07-21 已根据现有文档中的可验证地址恢复 6 个缺失仓库。现在 `_upstream/`
共有 10 个 Git 仓库，另有一个无 Git 元数据的 Python 依赖快照。

## 当前 Git 仓库

| 本地目录 | 上游与用途 | 当前提交 | 状态 |
| :--- | :--- | :--- | :--- |
| `_upstream/ddsp-vst` | [`magenta/ddsp-vst`](https://github.com/magenta/ddsp-vst)，11 个实时 TFLite 音色和控制模型接口 | `f2996e97f9469f3956a6b8e9d2d9b50b6555e1e9` | 原有，干净 |
| `_upstream/ddsp-realtime` | [`woosukji/ddsp-realtime`](https://github.com/woosukji/ddsp-realtime)，实时线程与音频缓冲参考 | `6cdfb583e5e99acf02cd47dd0a327679d968242a` | 原有，干净 |
| `_upstream/midi-ddsp` | [`magenta/midi-ddsp`](https://github.com/magenta/midi-ddsp)，MIDI 条件控制与音频合成研究参考 | `d7af42704a63b47267ae6a1bc0fee1ed7dc5c855` | 2026-07-21 恢复，干净 |
| `_upstream/ddsp_pytorch` | [`acids-ircam/ddsp_pytorch`](https://github.com/acids-ircam/ddsp_pytorch)，训练、实时 TorchScript 和状态设计参考 | `9db246f48dba66e9b2133691d7abf4af6ede0279` | 2026-07-21 恢复，干净 |
| `_upstream/ddsp-pytorch` | [`sweetcocoa/ddsp-pytorch`](https://github.com/sweetcocoa/ddsp-pytorch)，数据集、多尺度频谱损失和离线训练参考 | `ea5f25318dd4cd22c601dd405ebc2bac8e3f4cb6` | 2026-07-21 恢复，干净 |
| `_upstream/realtimeDDSP` | [`hyakuchiki/realtimeDDSP`](https://github.com/hyakuchiki/realtimeDDSP)，显式流式状态与缓存设计参考 | `3f2f79039413fb01c1a00164b4429539c7db358e` | 2026-07-21 恢复，干净 |
| `_upstream/ddsp-piano` | [`lrenault/ddsp-piano`](https://github.com/lrenault/ddsp-piano)，复音钢琴 DDSP 与预训练模型参考 | `e868b7ccd3fe31b39132048a72561d7fcf1b465f` | 2026-07-21 恢复，干净 |
| `_upstream/ddsp-piano-pytorch` | [`ytsrt66589/ddsp-piano-pytorch`](https://github.com/ytsrt66589/ddsp-piano-pytorch)，钢琴模型 PyTorch 层结构参考 | `2c9e17aa0c179e2c5dd6e9bdf2d78ab7cb0b9ee5` | 2026-07-21 恢复，干净 |
| `_upstream/ascend-cann-samples-official` | [`huqi/ascend-cann-samples`](https://gitcode.com/huqi/ascend-cann-samples)，CANN 音频样例基线 | `6511a5f4a45a1f68bd5e617989e68560f2f35cd6` | 原有，detached HEAD，干净 |
| `_upstream/ascend-cann-samples` | 同一 CANN 样例的本地适配副本 | `6511a5f4a45a1f68bd5e617989e68560f2f35cd6` | 原有，2 个音频文件有本地修改 |

`_upstream/ascend-cann-samples` 的已知本地修改是：

```text
cplusplus/level1_single_api/6_media/1_audio/audio_gitee/include/sample_comm_audio.h
cplusplus/level1_single_api/6_media/1_audio/audio_gitee/sample_audio.c
```

`ascend-cann-samples-official` 用作干净基线，本地适配保留在另一个目录。不要为了
让状态变干净而覆盖或还原这两个已修改文件。

## 非 Git 参考内容

`_upstream/_python_tools` 是为 ONNX 导出准备的 Python 依赖快照，没有 `.git`
元数据，不能用 `git remote` 或提交号追踪。它不是一个缺失的上游仓库，也不应被
误计入 Git 仓库数量。

`_upstream/` 根目录还可能有 WAV、MIDI、临时 ONNX/JSON 等导出或检查产物。这些
是测试生成物，不是独立仓库。新增文件时应尽量放到正式的 `midi/`、`models/`
或 `reports/`，避免和上游源码混在一起。

## 前端设计参考

实时演奏界面的钢琴卷帘参考了
[`ptnghia-j/ChordMiniApp`](https://github.com/ptnghia-j/ChordMiniApp) 的 Piano
Visualizer 信息层次，固定参考提交为
`33623b8885259f59c4005dad79b489aca8ae4ef9`，许可证为 MIT。本项目只参考紧凑上下文
栏、深色卷帘、命中线、琴键对齐、图例和相邻 transport 的组织方式，没有复制其
Next.js 组件、Canvas 实现、图片或其他资源。case3 的实时历史卷帘使用现有 React/Vite
音符流独立实现，因此不需要把 ChordMiniApp 克隆到 `_upstream/`，也不会增加板端运行依赖。

完整设计参考声明见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

## 独立训练仓库

本机还有一个不属于 case3 `_upstream` 的用户训练仓库：

```text
D:\Github\piano-ddsp-pytorch
origin: https://github.com/zhouxzh/piano-ddsp-pytorch.git
HEAD: 4354d0727bac80d5d834dce824e83af329cd36be
```

审计时该仓库有未提交改动，因此本次没有移动、覆盖、清理或更新它。case3 只在
文档中记录其位置；以后把训练产物接入 case3 时，应通过明确的导出和哈希校验步骤
复制模型，不能把这个工作仓库当成可重建缓存。

## 恢复方式

6 个补回的仓库使用浅克隆并跳过 Git LFS 大文件下载：

```bash
git -c filter.lfs.smudge= -c filter.lfs.required=false \
  clone --depth 1 --filter=blob:none <url> _upstream/<name>
```

这种方式保留当前源码、origin 和 HEAD，足够进行接口调研，也避免自动下载大型
checkpoint。需要历史提交时，在对应仓库中单独执行 `git fetch --unshallow`；
需要 LFS 资产时，先确认容量和许可证，再执行 `git lfs pull`。不要对全部仓库
批量下载模型权重。

## 审计命令

从 case3 根目录执行：

```powershell
Get-ChildItem _upstream -Directory
git -C _upstream/ddsp-vst remote -v
git -C _upstream/ddsp-vst rev-parse HEAD
git -C _upstream/ddsp-vst status --short
git check-ignore -v _upstream
```

检查全部子仓库时，应为每个目录记录：目录名、origin、分支或 detached HEAD、完整
提交号和 `git status --short`。仅统计含 `.git` 的目录，不把依赖快照和生成物算作
仓库。

## 保留规则

1. `_upstream` 是本地可重建参考区，不同步到 Ascend 板，也不作为运行时依赖。
2. 不自动删除 `_upstream`；清理前必须先查看本清单并取得明确确认。
3. 主仓库忽略该目录，因此每次重要调研后把 origin、HEAD 和用途更新到本文。
4. 保持 `ascend-cann-samples-official` 为干净基线，本地实验只改适配副本。
5. 第三方代码、模型和数据继续遵守各自仓库的许可证，不直接纳入 case3 提交。
6. 不覆盖 `D:\Github\piano-ddsp-pytorch` 等用户独立工作仓库。
7. 板端同步脚本继续排除 `_upstream`，但本地清理脚本不得把它作为缓存自动删除。

模型接口、训练路线和各参考仓库的技术比较见
[MIDI-DDSP 历史导出](12-midi-ddsp-export.md) 与 [模型与 OM 部署](03-om-deployment.md)。
