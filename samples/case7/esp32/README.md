# E1002 Case7 主动推送固件补丁

本目录只保存针对上游 `aitjcize/esp32-photoframe` 的最小 ESP-IDF 补丁，不维护一份复制的 ESP32 固件源码。补丁目标是 Seeed Studio reTerminal E1002（ESP32-S3，800x480），属于可选的历史对照路径；它不是当前 Waveshare PhotoPainter 的刷写或配网入口。当前微雪设备请先阅读 [PhotoPainter 接入手册](../docs/04-photopainter-7in3-integration.md) 和 [串口/IP 手册](../docs/13-photopainter-serial-ip-and-wifi.md)。

当前 Case7 服务器示例地址为 `192.168.1.135:7860`。它仅适用于受信任局域网；设备 IP 必须由
串口、设备网页或路由器租约确认，不能从旧板记录推断。

## 协议

补丁新增一个与上游接口分开的接收端点：

```http
POST /api/case7/push
Content-Type: image/jpeg
X-Case7-Push: 1
```

固件成功响应包含：

```http
X-Case7-Push: 1
```

Case7 服务器只有在看到这个响应头时才记录推送成功。服务器不会探测局域网、尝试其他端点或把普通 `200` 响应当成修改固件。

首次通过请求校验后，补丁调用上游已有的配置管理接口持久化关闭深度睡眠。主动推送要求设备保持 Wi-Fi 和 HTTP 服务唤醒；这不是远程唤醒机制。补丁不改变官方 `/api/display-image`、`/api/rotate` 或 URL Rotation 行为。

## 固定上游版本

当前补丁以以下提交为基线：

```text
repository: https://github.com/aitjcize/esp32-photoframe
commit: 6a4eeac8591325e0000eb6d4ec3422a4425b33c1
ESP-IDF CI baseline: release-v6.0
target: esp32s3
board: seeedstudio_reterminal_e1002
```

上游 E1002 release 镜像的 SHA-256 只用于备份和对照，不能代表当前设备实际运行的固件。刷写前应从设备或用户保存的原始文件记录实际 SHA-256。

## 构建

修改固件需要完整 ESP-IDF 构建环境；仅刷写现成二进制不需要安装编译环境。构建前确认 `idf.py --version`、Node.js 和 Python 均可用，并使用上游要求的 ESP-IDF `release-v6.0` 环境。

```bash
git clone https://github.com/aitjcize/esp32-photoframe.git
cd esp32-photoframe
git checkout 6a4eeac8591325e0000eb6d4ec3422a4425b33c1
git apply --check /path/to/Ascend310/samples/case7/esp32/patches/0001-case7-push-endpoint.patch
git apply /path/to/Ascend310/samples/case7/esp32/patches/0001-case7-push-endpoint.patch
idf.py --version
python3 build.py --board seeedstudio_reterminal_e1002
```

`build.py` 会生成上游的网页资源、启动图和 ESP32-S3 固件。不要在 Ascend 310B 上编译 ESP-IDF 固件；310B 只运行 Case7 服务端。不要把构建目录、固件二进制或设备日志提交到 Git。

## 备份、刷写和回滚

刷写前必须确认实际串口、芯片型号、Flash 容量和下载模式。下面的命令只展示流程，不替代对 COM 端口和分区表的确认：

```bash
# 在擦写前保存原始镜像和串口日志；具体 flash-size 以设备工具输出为准
esptool.py --chip esp32s3 --port COM13 chip_id
esptool.py --chip esp32s3 --port COM13 flash_id
esptool.py --chip esp32s3 --port COM13 read_flash 0x0 <confirmed-size> e1002-before-case7.bin

# 在上游项目目录执行生成的标准刷写流程
idf.py -p COM13 flash monitor
```

保留 `e1002-before-case7.bin`、构建提交、分区表、刷写时间和串口启动日志。回滚时使用已验证的原始镜像和与其匹配的分区/bootloader，不要只替换应用区。刷写过程、供电和串口参数必须以 E1002 实际硬件为准。

## 310B 端配置

只有在 E1002 已经刷入并启动该补丁、用户确认其 IP、且确认设备与 310B 在同一 LAN 后，才启用 `case7_push`：

```bash
curl -X PATCH http://192.168.1.135:7860/api/admin/devices/<device_id> \
  -H 'Content-Type: application/json' \
  -d '{"push":{"enabled":true,"base_url":"http://<E1002-IP>","protocol":"case7_push","timeout_seconds":60,"attempts":1}}'

curl -X POST http://192.168.1.135:7860/api/admin/devices/<device_id>/push \
  -H 'Content-Type: application/json' \
  -d '{"force":false,"force_send":true}'

curl http://192.168.1.135:7860/api/admin/devices/<device_id>/state
```

首次联调先发送一张合成、无人物图片。保存 HTTP 状态、`X-Case7-Push` 响应头、photo ID、ETag、服务器状态和 E1002 串口刷新日志。确认成功后再启用 `*/5 * *` 播放列表；设备深度睡眠必须保持关闭。

## 验收边界

已验证：补丁文本可应用到固定上游提交，服务器端 `case7_push` 客户端会发送 raw JPEG、携带请求标记并校验响应标记。

尚未由本仓库自动完成：E1002 实机刷写、当前固件 SHA 读取、设备 IP 确认、HTTP 实机响应、电子纸刷新观察和长时间供电测试。没有这些证据时，不能声称主动推送或彩色屏刷新已经通过。不要同时连接 USB 和锂电池进行长期测试，直到根据 E1002 硬件版本说明确认供电行为。

相关服务器代码：[photoframe_push.py](../photoframe_push.py)、[device_registry.py](../device_registry.py)、[app.py](../app.py)；协议和证据记录见 [11-photoframe-active-push.md](../docs/11-photoframe-active-push.md)。
