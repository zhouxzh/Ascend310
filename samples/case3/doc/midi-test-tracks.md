# MIDI 测试曲目与来源

仓库中的 MIDI 用于人工试听模型的音色、混响、动态表现和长序列稳定性。
这些文件由用户于 2026-07-23 从
[MuseScore](https://musescore.com/) 下载，保存在仓库的 `midi/` 目录中。

这里列出的曲目可以在 MuseScore 上搜索并下载。已有原始乐谱页记录时，应优先从
原始乐谱页下载；未记录或尚未核对原始页面时，可使用表中的检索链接查找对应编配。
MuseScore 的实际下载权限取决于用户账号、乐谱页面和授权状态。

## 当前曲目清单

下表与当前 `midi/` 目录一致。时长按 MIDI-DDSP 播放程序解析的时间记录，因此可能与
MuseScore 页面显示的乐谱时长略有差异。

| 曲目 | 本地 MIDI | 时长 | MuseScore 检索链接 | 原下载页 |
| --- | --- | ---: | --- | --- |
| Canon in D - Johann Pachelbel | `midi/canon-in-d-johann-pachelbel.mid` | 04:04.68 | [检索](https://musescore.com/sheetmusic?text=Canon%20in%20D%20Johann%20Pachelbel) | [原始乐谱页](https://musescore.com/user/1809056/scores/1019991) |
| Prelude I in C major, BWV 846 - J. S. Bach | `midi/prelude-i-in-c-major-bwv-846-well-tempered-clavier-first-book.mid` | 02:10.62 | [检索](https://musescore.com/sheetmusic?text=Prelude%20I%20in%20C%20major%20BWV%20846) | [原始乐谱页](https://musescore.com/user/101554/scores/117279) |
| 12 Variations on "Ah vous dirai-je, Maman", K. 265 - W. A. Mozart | `midi/variations-on-ah-vous-dirai-je-maman-k265300e-1781-2-french-folk-song-wolfgang-amadeus-mozart.mid` | 09:09.26 | [检索](https://musescore.com/sheetmusic?text=Mozart%2012%20Variations%20Ah%20vous%20dirai-je%20Maman%20K265) | [参考页，待核对](https://musescore.com/user/11152751/scores/9366064) |
| Nocturne Op. 9 No. 2 in E-flat major - Frederic Chopin | `midi/chopin-nocturne-op-9-no-2-e-flat-major.mid` | 03:39.37 | [检索](https://musescore.com/sheetmusic?text=Chopin%20Nocturne%20Op%209%20No%202%20E%20Flat%20Major) | [原始乐谱页](https://musescore.com/user/6662591/scores/4383881) |
| Gymnopedie No. 1 - Erik Satie | `midi/gymnopedie-no-1-satie.mid` | 04:22.27 | [检索](https://musescore.com/sheetmusic?text=Gymnopedie%20No%201%20Satie) | [原始乐谱页](https://musescore.com/user/19710/scores/4766391) |
| Flight of the Bumblebee - Nikolai Rimsky-Korsakov | `midi/flight-of-the-bumblebee.mid` | 01:23.44 | [检索](https://musescore.com/sheetmusic?text=Flight%20of%20the%20Bumblebee) | [原始乐谱页](https://musescore.com/nicolas/scores/437) |
| Prelude in C-sharp minor, Op. 3 No. 2 - Sergei Rachmaninoff | `midi/prelude-in-c-sharp-minor-opus-3-no-2-sergei-rachmaninoff.mid` | 03:30.99 | [检索](https://musescore.com/sheetmusic?text=Prelude%20in%20C%20sharp%20minor%20Opus%203%20No%202%20Rachmaninoff) | [原始乐谱页](https://musescore.com/user/2660886/scores/2101171) |
| Passacaglia - Handel/Halvorsen, easy version | `midi/passacaglia-handelhalvorsen-easy-version.mid` | 02:16.52 | [检索](https://musescore.com/sheetmusic?text=Passacaglia%20Handel%20Halvorsen%20Piano) | [参考页，待核对](https://musescore.com/user/37309912/scores/6790392) |
| Ode to Joy, easy variation | `midi/ode-to-joy-easy-variation.mid` | 00:29.01 | [检索](https://musescore.com/sheetmusic?text=Ode%20to%20Joy%20easy%20variation) | 未记录 |
| Ode to Joy, violin | `midi/ode-to-joy-violin.mid` | 00:32.00 | [检索](https://musescore.com/sheetmusic?text=Ode%20to%20Joy%20violin) | 未记录 |

每个 MIDI 当前都有同名 `.mscz` 文件，可使用 MuseScore 查看或编辑乐谱。

## 来源说明

- 上表的来源平台由下载者确认为 MuseScore。
- MIDI 文件本身不保存 MuseScore 作者账号、乐谱 ID、下载页 URL 或版权文本。
  表中的页面信息来自下载者记录。
- MuseScore 上同一曲目通常有多个编配版本。已记录原始乐谱页的曲目应使用对应页面，
  不要用同名搜索结果直接替换。
- 当前 Mozart 和 Passacaglia 文件已替换为与原记录文件名、时长不同的版本，原草稿中的
  页面只能作为参考。在确认页面编配与当前 `.mscz` 文件一致前，不能将其视为准确来源。
- 两个 Ode to Joy 文件尚未记录原始乐谱页。后续再次下载或分发时，应补充准确页面。
- 当前 MIDI 文件没有被仓库的 `*.mid` 规则忽略。是否纳入 Git 由仓库版本管理策略决定；
  复现实验时应单独确认 MIDI、MSCZ 和校验值完整一致。

## 使用说明

MIDI-DDSP 模型为单旋律模型。`ode-to-joy-violin.mid` 是单旋律输入；其余当前曲目包含
复音内容，只用于验证前端禁用状态和 `polyphonic_track` 错误，不再自动提取最高声部。
多轨文件只有在每轨均为单声部且能映射到 URMP 乐器时才逐轨合成。长曲目可用于后续
复音扩展测试，不能作为本轮原版模型音质验收输入。

## ONNX 试听输出

本节用于本地 ONNX 模型的人工 A/B 试听，不表示 Ascend 板端 Web UI 依赖 ONNX。
后续 current-fixed 与 v2 的音质测试约定使用本文列出的 MIDI，并分别输出到：

- current-fixed：`exports/midi_tests/current_fixed/`
- v2：`exports/midi_tests/v2/`

两个目录中的同名 WAV 应使用相同的 MIDI conditioning、钢琴音色索引、warm-up、释放
尾音和噪声种子，以便直接进行人工 A/B 试听。每个目录的 `manifest.json` 应记录模型路径、
输出时长、峰值、RMS 和复音溢出帧数。

上述输出目录当前尚未生成；完成试听导出后再提交对应清单和结果。
