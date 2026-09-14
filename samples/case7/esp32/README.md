# ESP32 设备目录

本目录只保存 ESP32 端的可选开发材料。Case7 当前的生产路径是：Waveshare PhotoPainter 或
Seeed Studio reTerminal E1002 通过设备主动 URL 请求从 310B 获取 JPEG；310B 不向设备发起网络
唤醒，也不在服务端保存派生图片。设备型号、唤醒、IP 验证、注册和 URL 拉取流程见
[ESP32 设备管理](../docs/04-esp32-device-management.md)，接口字段见
[API 参考](../docs/06-api-reference.md)。

## 当前支持的设备

| profile ID | 设备 | 输出 | 方向 |
| --- | --- | --- | --- |
| `waveshare_photopainter_73` | Waveshare ESP32-S3-PhotoPainter 7.3 英寸 | JPEG，800x480 或 480x800 | 横屏/竖屏 |
| `seeed_reterminal_e1002` | Seeed Studio reTerminal E1002 | JPEG，800x480 | 仅横屏 |

设备必须先由实体按键、上电或固件定时器唤醒，确认串口或 DHCP 得到的 IPv4 地址后，再在
310B 页面完成验证和注册。深度休眠期间 Wi-Fi、mDNS 和 HTTP 关闭，服务器无法通过网络唤醒。

## 固件选择

上游 PhotoFrame 固件可以直接作为设备端 URL 拉取客户端，是否包含所需端点要以实际版本的
固件说明和串口/HTTP 验证为准。刷写前记录芯片型号、Flash 信息、原始镜像 SHA-256 和完整
启动日志；Case7 不把固件二进制、构建目录或设备日志提交到 Git。

```bash
git clone https://github.com/aitjcize/esp32-photoframe.git
cd esp32-photoframe
git checkout <verified-release-or-commit>
idf.py --version
idf.py -p <SERIAL_PORT> flash monitor
```

修改 ESP-IDF 固件时需要完整 ESP-IDF 工具链；只刷写已验证的发布镜像不需要在 310B 安装
ESP-IDF。310B 只运行 Case7 FastAPI 服务，不编译 ESP32 固件。

## 目录中的补丁

`patches/0001-case7-push-endpoint.patch` 是早期实验材料，不属于当前 URL 拉取工作流，不能
据此宣称设备支持服务端发送或网络唤醒。若必须维护这条实验路径，应在独立固件分支中完成
编译、刷写、HTTP 实机响应和电子纸刷新验收，并单独记录证据；生产设备仍按
[ESP32 设备管理](../docs/04-esp32-device-management.md) 配置。

## 最小联调顺序

1. 唤醒设备并从串口日志读取 `sta ip`，或从路由器租约取得当前 IPv4。
2. 在同一局域网用 `http://<ESP32_IP>/` 和设备身份接口确认型号。
3. 在 310B 的“发现与配对”页面选择正确 `profile_id` 后注册。
4. 使用 PhotoFrame URL 配置指向 `http://<BOARD_IP>:7860/api/devices/<device_id>/photoframe`。
5. 在 310B 设备状态中确认首次 `200`、重复请求 `304` 和最近显示时间。

只有设备实际请求并显示 JPEG，才算 URL 拉取链路通过；管理页面显示“已注册”本身不代表屏幕
已经刷新。
