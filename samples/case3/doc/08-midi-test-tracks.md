# MIDI 测试素材

仓库同时保留两类 MIDI 素材：用于教程复现和连续集成的确定性夹具
`midi/ddsp-test.mid`，以及用于人工试听和长曲目解析的曲库。曲库中的 `.mid` 文件与
同名 `.mscz` 工程文件保存在 `midi/`；两个试听 WAV 保存在 `midi_wav/`。这些素材属于
输入数据，不是模型或代码依赖，重新部署 WebUI 时也应一并同步。

## 生成

```bash
python tools/create_test_midi.py --output midi/ddsp-test.mid
```

脚本固定节拍、力度、音色程序和音符序列；重新生成后文件内容应保持一致。可用
`python -c "import mido; print(mido.MidiFile('midi/ddsp-test.mid').length)"` 检查时长。

## 曲库

| 曲目 | MIDI 文件 |
| --- | --- |
| Canon in D - Johann Pachelbel | `midi/canon-in-d-johann-pachelbel.mid` |
| Prelude I in C major, BWV 846 - J. S. Bach | `midi/prelude-i-in-c-major-bwv-846-well-tempered-clavier-first-book.mid` |
| 12 Variations on Ah vous dirai-je, Maman - W. A. Mozart | `midi/variations-on-ah-vous-dirai-je-maman-k265300e-1781-2-french-folk-song-wolfgang-amadeus-mozart.mid` |
| Nocturne Op. 9 No. 2 - Frederic Chopin | `midi/chopin-nocturne-op-9-no-2-e-flat-major.mid` |
| Gymnopedie No. 1 - Erik Satie | `midi/gymnopedie-no-1-satie.mid` |
| Flight of the Bumblebee - Nikolai Rimsky-Korsakov | `midi/flight-of-the-bumblebee.mid` |
| Prelude in C-sharp minor, Op. 3 No. 2 - Sergei Rachmaninoff | `midi/prelude-in-c-sharp-minor-opus-3-no-2-sergei-rachmaninoff.mid` |
| Passacaglia - Handel/Halvorsen | `midi/passacaglia-handelhalvorsen-easy-version.mid` |
| Ode to Joy, easy variation | `midi/ode-to-joy-easy-variation.mid` |
| Ode to Joy, violin | `midi/ode-to-joy-violin.mid` |

每个曲目都有同名 `.mscz` 工程文件，可使用 MuseScore 查看或编辑。曲库的版权、授权和
分发范围应按实际来源逐项核对；未能确认授权的文件只用于本地实验，不应作为教程发布包的
默认下载内容。

## 使用边界

该文件是单声部测试输入，用于验证 MIDI 解析、音符边界、stateful DDSP 分块和 WAV 渲染。
它不是音乐作品音质评价集，也不代表真实演奏数据。需要复音、长曲目或版权清晰的研究数据
时，应在实验记录中单独声明数据集、版本和授权，不要把外部下载文件直接提交到示例代码包。

## 输出记录

本地试听结果放在被忽略的 `reports/midi_ddsp/` 或 `exports/midi_tests/` 下，并在 JSON
中记录模型、脚本版本、采样率、种子和输入 SHA256。`midi_wav/` 中的两个 WAV 是随曲库
保留的固定试听样本；模型生成的长音频仍应放在 `reports/`，不要混入源代码目录。
