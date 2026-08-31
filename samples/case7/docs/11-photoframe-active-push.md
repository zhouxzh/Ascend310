# PhotoFrame 主动推送与固件协议

*本文件说明 310B 与 PhotoFrame 终端之间的协议选择、固件识别、配置步骤和证据边界。当前实测目标是 Waveshare PhotoPainter；E1002 内容保留为历史对照。它把官方 PhotoFrame 固件与 Waveshare Demo 固件分开描述，避免把一个固件的端点套到另一套固件上。*

## 固定设备 profile

Waveshare [ESP32-S3-PhotoPainter 产品页](https://www.waveshare.com/product/displays/e-paper/epaper-1/esp32-s3-photopainter.htm) / [Wiki](https://www.waveshare.com/wiki/ESP32-S3-PhotoPainter) 记录为 7.3 英寸 E6 六色（黑、白、绿、蓝、红、黄）800x480；Wiki Mode 1 接受 800x480 或 480x800 图像，因此内容可标记为 `landscape`/`portrait`。Seeed Studio [reTerminal E1002 官方 Wiki](https://wiki.seeedstudio.com/getting_started_with_reterminal_e1002/) 记录为 7.3 英寸 ACeP / Spectra 6 全彩 800x480；Case7 为该设备固定 `landscape`。Case7 方向合同只使用 `landscape`、`portrait`，不支持 360°、180°或 90°/270°安装旋转；E1002 的 `portrait` 必须在请求校验时拒绝。Seeed 横屏限制是本项目策略；以上规格 profile 不等于固件协议或面板刷新通过结论。

当前操作板为 `192.168.1.135`，本文所有可执行 Case7 服务器 URL 均使用该地址。第 7.1 节是
2026-08-23/2026-08-27 的历史实机记录，保留其中的 `192.168.8.180` 仅作为证据，不是当前配置。

## 当前实测目标：Waveshare PhotoPainter

2026-08-30 刷写并配网的设备是 `waveshare_photopainter_73`，不是 E1002。串口为
`COM17`，MAC 为 `a4:cb:8f:da:a1:dc`，固件为 `esp32-photoframe v2.18.0`，Release
镜像 SHA-256 为
`41a680d59ae65f37ef581fd66568a988fd0e64469617651b1a1ec98e77fd30b3`。串口 STA 日志显示
当前 DHCP 地址 `192.168.1.137`，其 `/api/system-info` 和首页已从同一局域网电脑取得
HTTP 200，响应确认 `board_name=waveshare_photopainter_73`、`800x480`。

地址必须按 [串口/IP/Wi-Fi 手册](./13-photopainter-serial-ip-and-wifi.md) 从每次启动日志重新读取；
`photoframe.local` 只是可选 mDNS 别名，不是登录账号或固定地址。上述结果只证明固件刷写、
STA 联网和 HTTP 页面可达；当前尚未以真实 PhotoPainter 屏幕记录 direct push、URL Rotation
或彩色刷新通过。继续联调前先执行：

```bash
curl -i --connect-timeout 5 --max-time 10 http://<WAVESHARE-IP>/api/system-info
curl -I --connect-timeout 5 --max-time 10 http://<WAVESHARE-IP>/
```

`<WAVESHARE-IP>` 必须替换为串口日志中的当前 IPv4 地址，不能使用旧 E1002 地址
`192.168.1.117`。

## 1. 先确定传输方向（历史 E1002 与当前 PhotoPainter）

E1002 可能使用多种完全不同的通信方向和固件协议：

| 模式 | 发起方 | 传输内容 | 服务器是否主动连接设备 |
| --- | --- | --- | --- |
| 上游 PhotoFrame API 文档中的 direct push | 310B | `POST /api/display-image`，raw JPEG | 是（实机需验证） |
| Case7 修改固件 direct push | 310B | `POST /api/case7/push`，raw JPEG，响应必须含 `X-Case7-Push: 1` | 是 |
| 官方 PhotoFrame URL Rotation | E1002 | `GET /api/devices/<id>/photoframe`，JPEG/ETag | 否 |
| Waveshare Demo direct push | 310B | `POST /dataUP`，raw 24-bit BMP | 是（需 STA 可达） |

Waveshare `ESP32-S3-PhotoPainter-Demo` 不是上述官方 PhotoFrame 固件的同一版本。它默认提供 `POST /dataUP`，接收 raw BMP，并默认以 AP 模式运行。因此，服务器不能根据设备名称、历史 IP、`503` 或 `404` 自动猜测协议，也不能在 JPEG 失败后静默改发 BMP。

## 2. 固件身份与 hash 证据

官方 Release 的版本和板卡镜像必须逐字记录。当前本地核验过的文件包括：

```text
上游 PhotoPainter 7.3 镜像
photoframe-firmware-waveshare_photopainter_73-merged.bin
release: v2.18.0
source commit: 6a4eeac8591325e0000eb6d4ec3422a4425b33c1
sha256: 41a680d59ae65f37ef581fd66568a988fd0e64469617651b1a1ec98e77fd30b3

Seeed reTerminal E1002 镜像
photoframe-firmware-seeedstudio_reterminal_e1002-merged.bin
release: v2.18.0
sha256: aaefd9086742353cce34ab863781d6c5e0b6b8663a383200a24c95ee1720cd0f

Waveshare Demo 示例镜像（本地压缩包检查，非官方 PhotoFrame Release）
ESP32-S3-PhotoPainter-Fac.bin
sha256: 9608d69c82decc15d533a695831b9699a1ed1becaeb481675a863eb7a4db74e9
```

最后一项只表示本地 Demo 文件的 hash，不表示用户设备当前刷入了该文件。设备实况需要另存刷写日志、串口启动信息，或在明确 IP 上执行只读 API 检查。任何 hash 不一致都应标为“固件身份未确认”。

官方 PhotoFrame 镜像的端点字符串包含 `/api/display-image`、`/api/rotate` 和 `/api/system-info`；Demo 镜像包含 `/dataUP`，不包含这些官方端点。Case7 修改固件额外包含 `/api/case7/push`。二进制字符串检查只能辅助审计，不能替代设备实际 HTTP 响应。

## 3. 历史 E1002：官方 PhotoFrame direct push

### 3.1 设备要求

本节只描述上游资料中的接口合同。它不能证明当前 E1002 已运行该端点；没有设备端 HTTP 响应和固件身份记录时，禁止把 `photoframe_api` 当成已支持协议。

- E1002 已刷入与板卡匹配的官方 PhotoFrame 镜像。
- E1002 与 310B 位于同一个可路由的局域网，且操作者已确认当前 E1002 IP。
- PhotoFrame HTTP 服务处于唤醒状态。深度睡眠会停止网络服务，310B 无法主动唤醒设备。
- 服务器使用设备根 URL，不要把 `/api/display-image` 再写一遍。

### 3.2 只读识别

在获得真实 IP 后，先保存响应，不要扫描地址段：

```bash
curl -i --connect-timeout 5 --max-time 10 \
  http://<E1002-IP>/api/system-info | tee e1002-system-info.txt
```

若该端点不存在，不能据此认定设备是 Demo；应结合设备网页、刷写文件 hash 和串口日志继续确认。没有证据时停止主动推送配置。

### 3.3 直接发送一张 JPEG

官方接口使用 raw body，不是 multipart 表单：

```bash
curl -i --connect-timeout 10 --max-time 60 \
  -X POST http://<E1002-IP>/api/display-image \
  -H 'Content-Type: image/jpeg' \
  --data-binary @photo.jpg
```

成功响应通常是 `2xx`，正文类似：

```json
{"status":"success","message":"Image displayed successfully"}
```

设备忙或尚未初始化时可能返回 `503`。官方 E1002 实测 `/api/display-image` 请求可能在约 30 秒后才返回，因此默认使用 60 秒超时、单次尝试；只有操作者显式提高重试次数时才会重复发送。服务把 HTTP 状态、错误正文、photo ID 和时间写入设备状态；同一 cron 时隙一旦记录 2xx，就以 `last_success_slot` 阻止后续渲染/ETag 变化触发第二次物理刷新。`2xx` 只证明 HTTP handler 接收并交给设备处理，不能单独证明电子纸彩色屏已经完成刷新。

### 3.4 Case7 配置

先创建或确认 `photoframe` 设备，再把操作者确认的根 URL 写入设备记录：

```bash
curl -X PATCH http://192.168.1.135:7860/api/admin/devices/<device_id> \
  -H 'Content-Type: application/json' \
  -d '{"push":{"enabled":true,"base_url":"http://<E1002-IP>","protocol":"photoframe_api","timeout_seconds":60,"attempts":1}}'
```

立即测试一张：

```bash
curl -X POST http://192.168.1.135:7860/api/admin/devices/<device_id>/push \
  -H 'Content-Type: application/json' -d '{"force":false,"force_send":true}'
curl http://192.168.1.135:7860/api/admin/devices/<device_id>/state
```

`force=true` 会推进选择器到下一张；`force_send=true` 只重发当前选择，适合
首次配置或重复测试而不跳过播放列表首张。

五分钟固定播放列表脚本会在 `--push-url` 存在时使用同一 direct push 客户端：

```bash
bash scripts/setup_photoframe_test.sh \
  --source shared/incoming/photoframe-test --limit 20 \
  --profile-id seeedstudio_reterminal_e1002 \
  --server-url http://127.0.0.1:7860 \
  --public-server-url http://192.168.1.135:7860 \
  --push-url http://<E1002-IP> --push-protocol photoframe_api --push-now
```

`--push-url` 缺失时，脚本只建立服务器播放列表，不会假装已经向设备推送。调度器按 `*/5 * *` 每个有效时隙最多发送一次；服务重启后通过 SQLite/设备状态恢复当前选择。

## 4. 历史 E1002：Case7 修改固件主动推送

当 E1002 当前固件没有可用的 direct-push 接口时，使用本仓库 `esp32/patches/` 中针对上游 PhotoFrame `6a4eeac` 的 ESP-IDF 补丁重新构建。补丁新增：

```http
POST /api/case7/push
Content-Type: image/jpeg
X-Case7-Push: 1
```

成功响应必须包含：

```http
X-Case7-Push: 1
```

服务器客户端会校验这个响应标记；缺失时即使 HTTP 状态是 `200` 也记为失败，不会把未修改固件、代理页或其他 HTTP 服务当成接收端。补丁首次成功处理请求后关闭并持久化 `deep_sleep_enabled`，因为服务器主动连接无法唤醒深度睡眠中的设备。固件补丁不会改变 URL Rotation 的行为。

构建和刷写前必须保存原始镜像、设备分区信息和回滚方式。完整步骤见 [esp32/README.md](../esp32/README.md)。本仓库没有在当前 E1002 上自动刷写或宣称实机通过。

启用该协议：

```bash
curl -X PATCH http://192.168.1.135:7860/api/admin/devices/<device_id> \
  -H 'Content-Type: application/json' \
  -d '{"push":{"enabled":true,"base_url":"http://<E1002-IP>","protocol":"case7_push","timeout_seconds":60,"attempts":1}}'
curl -X POST http://192.168.1.135:7860/api/admin/devices/<device_id>/push \
  -H 'Content-Type: application/json' -d '{"force":false,"force_send":true}'
```

## 5. URL Rotation 兼容模式

URL Rotation 是设备主动拉取，不是主动推送。设备网页中配置：

```json
{
  "auto_rotate": true,
  "rotate_cron": ["*/5 * *"],
  "rotation_mode": "url",
  "image_url": "http://192.168.1.135:7860/api/devices/<device_id>/photoframe",
  "deep_sleep_enabled": false
}
```

设备向 310B 发出 GET，并携带显示尺寸、方向和 `If-None-Match`；同一选择 revision 返回 `304 Not Modified`。`/api/rotate` 只请求设备立即执行自身轮播，不会让 310B 主动建立到设备的连接。URL Rotation 可以在 direct push 不可用时作为明确选择的兼容方案，但不能在服务器端自动切换。

## 6. Waveshare Demo 固件的边界

Demo 固件的源码和本地镜像显示：

- 仅注册 `POST /dataUP`；
- 请求体直接写成 `/sdcard/02_sys_ap_img/user_send.bmp`；
- 后续显示逻辑读取 BMP 并执行六色转换；
- 默认 `WIFI_MODE_AP`，SSID `esp_network`，密码 `1234567890`。

因此，若实际设备确认是 Demo：

1. 不能把 JPEG POST 到 `/api/display-image`，因为该端点不存在；
2. 不能把 Demo 的 AP 地址当作与 310B 同一 LAN 的设备地址；
3. Case7 已提供显式 `waveshare_dataup` 适配器，会把按需渲染的 JPEG 在内存中转换为 800x480 24-bit BMP 后 raw POST 到 `/dataUP`；这属于另一种协议，仍需单独确认 STA 可达性和实机验收；
4. 或者修改/替换 ESP32 固件，增加 STA 配网和 direct push handler。修改固件前必须保留原始镜像和回滚方法。

Case7 客户端实现了三个显式适配器：`photoframe_api` 发送官方 raw JPEG，
`case7_push` 发送修改固件要求的 raw JPEG 并校验响应标记，
`waveshare_dataup` 发送 Demo 所需的 raw BMP。没有显式配置时推送保持关闭；
配置错误只报告“协议不匹配”，不会重试或自动切换另一个端点。例如 Demo 配置为：

```bash
curl -X PATCH http://192.168.1.135:7860/api/admin/devices/<device_id> \
  -H 'Content-Type: application/json' \
  -d '{"push":{"enabled":true,"base_url":"http://<E1002-IP>","protocol":"waveshare_dataup"}}'
```

## 7. 五分钟主动推送验收记录

每次真实联调至少保存以下字段：

```text
board_ip
device_ip_source          # 设备网页、路由器租约或串口日志
firmware_filename
firmware_sha256
firmware_protocol         # official_display_image / case7_push / official_url_rotation / waveshare_demo
device_system_info_path
first_push_http_status
first_push_response_body
push_photo_id
push_etag
push_timestamp
device_last_status
device_last_error
screen_observation
serial_refresh_log
sleep_power_mode
```

验收顺序：

1. 在不写设备的情况下记录 IP 来源和固件 hash；
2. 只读请求固件识别端点，保存原始响应；
3. 发送一张合成无人物 JPEG，保存 HTTP 交换和服务器状态；
4. 观察 E1002 面板或读取串口刷新日志；
5. 再启用 `*/5 * *`，至少观察三个时隙；
6. 分别测试设备忙、断网、服务重启和深度睡眠；
7. 若任何步骤协议不匹配，停止并标记失败，不切换到未确认的端点。

### 7.1 2026-08-23 E1002 实机记录（历史证据）

本次通过 `COM13` 串口和同网段板端请求完成了真实联调，证据边界如下：

```text
  board_ip                    192.168.8.180
device_ip                   192.168.1.117
device_ip_source            COM13 启动日志（STA DHCP）+ /api/system-info
device_id                   ac276ea6a8b0
firmware                    PhotoFrame v2.18.0
board_name                  seeedstudio_reterminal_e1002
firmware_commit             6a4eeac（上游发布版本；未从设备读取二进制 hash）
firmware_protocol           official_display_image
display                     800x480 spectra6
deep_sleep_enabled          false（通过设备 /api/config 写入并 GET 确认）
server_rotation_cron        */5 * *
push_timeout_attempts       60 seconds / 1 attempt
```

第一次完整成功发送选择的是 `photo_id=15`，服务器响应为 `HTTP 200`、
`protocol=photoframe_api`、`format=jpeg`、`bytes=16694`、`attempts=1`，
ETag 为 `"81e45fc64cad41c61589c55fc3f66168"`。COM13 随后记录了
`Image received successfully`、`Starting display update: 192000 bytes` 和
`Display update complete`。这证明官方 HTTP handler 已接收 JPEG 并完成驱动更新流程。

恢复服务器 `*/5 * *` 后，`photo_id=17` 在
`2026-08-23-22-30:*/5 * *` 时隙记录为 `last_status=ok`，并写入
`last_success_slot`。重启服务后在同一时隙连续读取状态 9 秒，
`last_request`、photo ID 和 `last_success_slot` 均未改变；板端主动推送回归测试
13 项、设备注册/迁移测试 7 项全部通过。因此没有再次发起同一时隙的物理刷新。

串口日志证明的是数据接收和电子纸驱动刷新完成，不等同于肉眼颜色、残影或长期
电源稳定性验收；本次也没有保存人物照片截图。服务器 `/api/health` 当前仍为
`degraded`，原因是板端三个 NPU OM 尚未准入，这与已验证的 JPEG 直推链路是独立问题。

## 8. 安全与运行限制

主动推送 URL 由 LAN 管理员配置，服务不提供设备发现，也不把设备真实文件路径、内部注册
信息或任意 shell 暴露给 E1002。服务仍只建议运行在可信局域网，禁止公网端口映射。渲染结果
只存在请求内存，不创建 JPEG、BMP、EPDGZ 或其他持久化图片缓存。

相关代码和接口：`photoframe_push.py`、`device_registry.py`、`display_policy.py`、`smart_selector.py`、`app.py` 中的 `push_due()`/`push_device()`，以及 [03-album-server-api-and-esp32-protocol.md](./03-album-server-api-and-esp32-protocol.md)。
