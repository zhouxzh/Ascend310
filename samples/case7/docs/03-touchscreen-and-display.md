# 触摸屏与本机显示

_说明 310B HDMI 触摸屏、本机显示控制和 E6 dry-run 的操作边界。_

---

本文说明 310B 连接的 `QDtech MPI1001` 触摸屏，以及 E6 dry-run 输出的操作边界。

## 触摸屏入口

启动服务后，触摸屏通过 Firefox kiosk 打开：

```bash
DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority \
bash scripts/launch_touchscreen_kiosk.sh
```

手机或电脑浏览器打开同一个服务地址。触摸屏模式通过 `?mode=touchscreen` 使用照片优先布局，手机页面保留管理入口。

## 首页和工具栏

首页优先显示当前照片。触摸照片或移动指针后显示底部控制栏，连续 8 秒无操作时控制栏自动隐藏。控制栏中的文件名水印与工具栏一起隐藏，不另占一行遮挡照片。

工具栏提供：

- 上一张：从本机显示历史中选择上一张可用照片；
- 下一张：触发下一次智能选图；
- 暂停/继续：控制本机自动轮播；
- 详情：查看文件名、尺寸、拍摄时间和索引状态；
- 设置：打开功能面板。

文件名水印是否显示由 `display.show_filename` 控制。天气、日期和 NPU 状态使用高对比度状态徽标，不覆盖照片主体。

## 功能面板

底部导航进入全屏功能面板：

| 面板 | 用途 |
| --- | --- |
| 图库 | 浏览固定比例网格并把照片设为当前照片 |
| 智能搜索 | 输入中文或英文查询，选择模型并设为当前照片 |
| 上传 | 选择图片或文件夹，查看文件和 NPU 索引进度 |
| 设备 | 管理本机触摸屏和两个远端 ESP32 profile |
| 设置 | 设置轮播、天气、模型、文件名水印和 E6 dry-run |
| 系统 | 查看 NPU、模型、图库、天气和服务状态 |

所有主要控件最小触控尺寸为 56 px。固定网格比例，避免图片异步加载造成布局跳动。
布局在 1920×1080、1280×800、1024×600 和窄屏手机视口下保持无页面横向滚动；手机使用响应式管理面板，
触摸屏使用照片优先首页和常驻底部导航。

## 本机显示 API

本机 HDMI 面板在设备页中使用固定 ID `local-touchscreen`，不需要 IP、设备令牌或网络握手。常用 API：

```bash
curl http://<BOARD_IP>:7860/api/display/current
curl -X POST http://<BOARD_IP>:7860/api/display/select \
  -H 'Content-Type: application/json' -d '{"photo_id":123}'
curl -X POST http://<BOARD_IP>:7860/api/display/control \
  -H 'Content-Type: application/json' -d '{"action":"next"}'
```

`action` 支持 `next`、`pause` 和 `resume`。当前照片、暂停状态和 selection revision 写入 SQLite，服务重启后恢复，不重新随机选择。

触摸屏默认使用较快的本机轮询和切图节奏；电子纸设备单独使用慢速轮播间隔。点击下一张不应触发天气网络请求，天气由后台刷新周期控制。

## 方向策略

照片先按 EXIF 方向归一化，再按显示设备的横屏或竖屏能力进行 cover 裁剪。触摸屏可以根据实际窗口尺寸协商宽高；远端设备只能使用其 profile 允许的方向。不要用 360 度自由旋转替代设备方向配置。

## E6 dry-run

E6 dry-run 只验证软件帧处理，不代表真实电子纸已经刷新：

- 输出尺寸固定为 800×480；
- 使用六色调色板；
- 4-bit 高半字节优先打包；
- 帧长度固定为 192000 bytes；
- SPI、GPIO、BUSY 超时和初始化序列在 dry-run 中可验证。

真实 E6 刷新还需要确认驱动板型号、SPI 和 GPIO 接线，并在目标硬件上独立验收。
