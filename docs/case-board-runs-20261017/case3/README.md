# case3 双板调试记录

## 运行

- 8T：`/home/HwHiAiUser/Ascend310/cases/case3`
- 20T：`/home/HwHiAiUser/Documents/case3`
- 启动：加载 CANN 后运行 `python scripts/run_webui.py`
- 服务端口：`8765`

## 结果

| 项目 | 8T | 20T |
| --- | --- | --- |
| HTTP `/` | 200 | 200 |
| NPU | `available=true`，310B4 Alarm | `available=true`，310B1 Alarm |
| FastAPI/Uvicorn | 正常 | 正常 |
| `acl` | 导入成功 | 导入成功 |
| `mido` / `rtmidi` / `sounddevice` | 缺失 | 缺失 |
| 当前乐器 | `FILM-FDN 钢琴` | 空 |
| 音频输出 | 无可用设备 | 无可用设备 |

两板页面都正常渲染；本次只验证 WebUI 和状态接口。MIDI、音频输入输出和真实 DDSP/VST 推理未执行，不能记录为音频链路通过。

## 截图

- [8T MIDI-DDSP Studio](board-8t.png)
- [20T MIDI-DDSP Studio](board-20t.png)

## 证据

- [8T 原始日志](board-8t.log)
- [20T 原始日志](board-20t.log)
- [状态接口与截图结果](capture.json)
