# PhotoPainter 7.3 英寸接入手册

*Waveshare PhotoPainter 六色 ESP32-S3 终端的固件、配对与设备主动 URL Rotation 配置。*

## 固定硬件 profile

本手册的 Waveshare 目标是 [ESP32-S3-PhotoPainter 官方产品页](https://www.waveshare.com/product/displays/e-paper/epaper-1/esp32-s3-photopainter.htm) 及其 [官方 Wiki](https://www.waveshare.com/wiki/ESP32-S3-PhotoPainter)：7.3 英寸 E6 六色（黑、白、绿、蓝、红、黄）、800x480；Wiki Mode 1 明确接受 800x480 或 480x800 图像，因此 Case7 将内容方向记为 `landscape` 或 `portrait`。对照设备 [Seeed Studio reTerminal E1002 官方 Wiki](https://wiki.seeedstudio.com/getting_started_with_reterminal_e1002/) 为 7.3 英寸 ACeP / Spectra 6 全彩、800x480；Case7 为它固定横屏 `landscape`。Case7 profile 只接受方向名 `landscape`/`portrait`；不支持 360°、180°或 90°/270°安装旋转选项，E1002 的 `portrait` 请求必须拒绝。Seeed 的厂商资料只确认面板规格，横屏限制是本项目策略；规格资料不等同于固件接口或面板刷新验收。

---

## 🧩 固件与硬件边界

固定使用上游 PhotoFrame `v2.18.0`，提交 `6a4eeac`，合并镜像：

```text
photoframe-firmware-waveshare_photopainter_73-merged.bin
sha256: 41a680d59ae65f37ef581fd66568a988fd0e64469617651b1a1ec98e77fd30b3
```

以上版本和 hash 必须以 [官方 Release](https://github.com/aitjcize/esp32-photoframe/releases) 页面为准。Case7 不维护 ESP32 固件分支，也不猜测屏幕引脚。

### 2026-08-30 当前微雪设备实刷记录

本次目标只有新的 Waveshare ESP32-S3-PhotoPainter，Seeed reTerminal E1002 未连接、未刷写、未配置。实测证据如下：

```text
target_profile: waveshare_photopainter_73
serial_port: COM17 (Windows 控制机)
chip: ESP32-S3 rev 0.2
mac: a4:cb:8f:da:a1:dc
flash: 16MB
flash_tool: esptool v5.3.1
release: v2.18.0
asset_sha256: 41a680d59ae65f37ef581fd66568a988fd0e64469617651b1a1ec98e77fd30b3
flash_result: Hash of data verified
boot_log: C:\Users\zhoux\Downloads\waveshare_photopainter_boot.log
```

刷写前串口启动日志显示原有应用为 `xiaozhi 2.0.1`（ESP-IDF v5.5.3）。完整 16MB 原始 Flash 备份曾尝试读取，但因串口传输中断而未完成；失败日志不能作为回滚镜像。MAC 只用于识别设备，不是网页地址。

Seeed reTerminal E1002 使用的是同一 Release 的另一块板卡镜像，文件名和已下载文件的校验值如下；这不是 PhotoPainter 7.3 镜像的替代 hash：

```text
photoframe-firmware-seeedstudio_reterminal_e1002-merged.bin
sha256: aaefd9086742353cce34ab863781d6c5e0b6b8663a383200a24c95ee1720cd0f
```

只有实际刷入并启动了对应镜像，才能使用该镜像声明的 HTTP API。Release 页面、文件 hash、设备 `/api/system-info` 响应和串口启动日志应作为独立证据保存；不能从设备 IP 或一次 HTTP 错误推断固件型号。

PhotoPainter 的六色转换由终端固件负责；它不能替代 Orange Pi SPI 直连微雪 E6 的六色 dry-run/硬件路径。

## 🔧 刷写与配网

```bash
sha256sum photoframe-firmware-waveshare_photopainter_73-merged.bin
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 921600 \
  write_flash 0x0 photoframe-firmware-waveshare_photopainter_73-merged.bin
```

首次启动通过设备 AP/网页连接 2.4 GHz Wi-Fi。刷写前保存原配置；遇到启动或供电问题时先断开锂电池，仅使用稳定 USB 供电恢复。

刷写后的首次启动若出现 `No WiFi credentials found - Starting AP mode`，设备会广播形如
`PhotoFrame - AA1DC` 的开放配网 AP。连接该 AP 后在设备网页中填写家庭 2.4 GHz Wi-Fi；AP 名称不是家庭局域网 IP。配网完成后必须从串口日志读取 `sta ip: ...`，再用该 IPv4 地址访问网页。不要把 `photoframe.local` 当作必需登录入口，Windows 的 mDNS 解析可能不可用。完整命令见 [串口/IP 手册](./13-photopainter-serial-ip-and-wifi.md)。

本次实测的配网后日志为：`sta ip: 192.168.1.137`、`HTTP server started`、`Web interface available at: http://192.168.1.137`。该地址来自本次 DHCP 租约，换路由器或重置 Wi-Fi 后必须重新读取，不能永久写死。

### 当前 URL Rotation 配置核验（只读）

2026-08-30 从同一局域网对上述地址执行了只读请求。设备的 `/api/system-info` 返回 HTTP
200，设备硬件 ID 为 `a4cb8fdaa1dc`，固件为 `v2.18.0`；`/api/config` 也返回 HTTP 200，
但当时的配置为：

```text
auto_rotate: false
rotation_mode: storage
image_url: https://loremflickr.com/800/480
deep_sleep_enabled: true
```

这说明该设备当时尚未主动拉取 Case7。服务器注册记录的 `device_id`（例如
`d97a7d87ef08d4c9`）是 310B 侧 ID，与设备自己的硬件 ID 不同；取图 URL 必须使用前者。
这是一次历史只读核验，不代表当前 DHCP 地址仍可达，也不代表配置已经写入。

当前 Case7 的设备卡片提供 **验证并登记设备**：填写确认的 `http://<ESP32-IP>` 后，310B
会串行验证 `/api/system-info`、写入并读回 `/api/config`，再请求 `/api/rotate`。地址必须是
私网 IPv4 根 URL；服务不会扫描局域网或猜测设备 IP。该步骤的成功只说明设备网页可达且固件
接受了 URL Rotation 配置，返回 `202`/`awaiting_pull`；必须等待后续 ESP32 到 310B 的实际 GET
才会把状态变为 `pulled`。该 GET 还必须来自登记时验证过的 ESP32 IPv4 并携带完整协商头；也仍
不能单独证明电子纸完成物理刷新。

### 2026-08-30/31 原子注册实测

本次实测验证了“不可达不登记、真实拉图才连通”的边界：

1. PhotoPainter 处于休眠时提交 `POST /api/admin/devices/register`，服务器返回
   `502` 和 `registration_status=not_registered`；临时记录被删除，设备列表没有新增不可达设备。
2. 按 KEY 唤醒后，同一地址 `http://192.168.1.137` 的 `/api/system-info` 返回 `200`，
   `board_name=waveshare_photopainter_73`、硬件 ID `a4cb8fdaa1dc`、固件 `v2.18.0`、
   分辨率 `800x480`。服务器写入并回读 URL Rotation 配置，控制面返回 `202`/`awaiting_pull`。
3. 随后板端日志记录了 `192.168.1.137` 发起的带官方固件/显示能力头的
   `GET /api/devices/cc64200c84de283e/photoframe`，状态 `200`。设备状态接口因此变为
   `pull_provision.status=pulled`，并保存 `last_request_client=192.168.1.137`、
   `last_request_firmware=v2.18.0` 和 `last_request_display=800x480 landscape`。

   这组字段是服务器收到真实 PhotoFrame 拉图的证据；浏览器或 curl 访问不会触发该状态。
   当次 `/api/rotate` 请求曾超时，服务器将其记录为“立即刷新未确认”，不把超时误判为注册失败；
   后续真实 GET 仍可完成连接确认。彩色电子纸是否完成物理刷新，仍需观察屏幕或串口刷新日志单独验收。

如果设备确实运行提供 `/api/config` 的 PhotoFrame 固件，可在确认 IP 后执行下面的命令；
初次联调建议关闭深度睡眠：

```bash
curl -X PATCH http://192.168.1.137/api/config \
  -H 'Content-Type: application/json' \
  --data-raw '{"auto_rotate":true,"rotate_cron":["*/30 * *"],"rotation_mode":"url","image_url":"http://192.168.1.135:7860/api/devices/<device_id>/photoframe","deep_sleep_enabled":false}'
curl http://192.168.1.137/api/config
```

`<device_id>` 替换为设备卡片上的 310B 注册 ID。若要每 10 分钟更新，先把 310B 设备策略
的 `rotation_cron` 改为 `*/10 * *`，再把设备端 `rotate_cron` 改成同样值；两端字段名称
不同。若设备网页没有 URL Rotation/图片 URL，或接口不接受这些字段，则当前固件不能由
服务器补开主动拉取，必须刷入或修改明确支持该协议的固件。

## 🔗 PhotoFrame URL 拉取与兼容接口

上游 PhotoFrame `v2.18.0` 的 API 文档列出 URL Rotation 拉取和服务器主动显示接口，两条链路的方向不同；这只是协议参考，不代表当前微雪设备已经完成网络或屏幕验收。Case7 的设备注册页面只使用第一行的 URL Rotation。当前设备必须先通过刷写记录、串口日志和明确 IP 上的 HTTP 响应确认，不能仅凭型号或 Release 文件名启用接口：

| 模式 | 请求方向 | 设备端点 | Case7 用途 | 睡眠要求 |
| --- | --- | --- | --- | --- |
| URL Rotation（当前注册方式） | PhotoPainter -> 310B | `GET /api/devices/<id>/photoframe` | 返回 JPEG、ETag/304 和配置头 | 已实测设备主动 GET；物理刷新待单独确认 |
| 旧式服务器主动发送（维护兼容） | 310B -> PhotoPainter | `POST /api/display-image` | 仅保留低层兼容接口，不出现在注册 UI | 当前设备未作为本次注册流程验收 |

`/api/rotate` 只触发设备自身轮播，不是服务器推送接口。设备在深度睡眠时，310B 无法通过
网络将它唤醒；应由设备按自己保存的轮播计划醒来并发起 URL 请求。

### 维护兼容接口

以下内容只适用于已有历史记录或协议排障，不属于正常注册、配对或五分钟轮播步骤。服务器主动
发送的最小低层联调命令（仅在 `/api/system-info` 已确认板型后替换 `<WAVESHARE-IP>`）为：

```bash
curl -X POST http://<WAVESHARE-IP>/api/display-image \
  -H 'Content-Type: image/jpeg' --data-binary @photo.jpg
```

官方端点忙时可返回 `503`；此路径不应改用未知端点。Waveshare 提供的另一套
`ESP32-S3-PhotoPainter-Demo` 固件（常见文件名 `ESP32-S3-PhotoPainter-Fac.bin`）只提供
`POST /dataUP` raw BMP，并默认运行 AP 模式。若设备实际刷的是这套固件，必须显式采用 Demo
协议并发送 800x480 24-bit BMP，且先解决 STA/局域网可达性；或者修改/替换固件增加 direct
push。Case7 不把这两套固件的接口混用。

## 🔗 Case7 配对

在手机或触摸屏的“设备”面板创建 `photoframe` 设备，明确选择
`waveshare_photopainter_73`，配置名称和内容方向。设备传输方式没有可选项，固定为设备主动
URL 拉取；服务器返回：

```text
device_id
http://192.168.1.135:7860/api/devices/<device_id>/photoframe
```

优先在同一设备卡片填写 ESP32 网页地址并点击 **验证并登记**，让 310B 写入并读回 URL
Rotation。当前服务在可信局域网内公开提供取图接口，不需要填写认证头或设备令牌。若 ESP32
不可达，不能在 Case7 中把它标为已验证；可以暂时在 PhotoFrame 本机网页手动填写取图 URL
进行独立排障，待设备地址可达后再执行“验证并登记”。无论哪种方式，都必须等待 ESP32 后续
主动 GET；只有该请求才会产生 `pulled` 证据。

### 五分钟固定播放列表与 URL Rotation

对 PhotoPainter 的联调使用服务器保存的固定播放列表。板端脚本建立播放列表和首张选择；
PhotoPainter 在自己的五分钟时隙主动向取图 URL 发出请求，服务器只响应这次请求：

```bash
cd /home/HwHiAiUser/Documents/ai-album/current
bash scripts/setup_photoframe_test.sh \
  --source shared/incoming/photoframe-test --limit 20 \
  --profile-id waveshare_photopainter_73 \
  --server-url http://127.0.0.1:7860 \
  --public-server-url http://192.168.1.135:7860
```

脚本输出类似下面的 URL 和固件配置片段：

```text
http://192.168.1.135:7860/api/devices/<device_id>/photoframe
rotation_cron: */5 * *
```

```json
{
  "auto_rotate": true,
  "rotate_cron": ["*/5 * *"],
  "rotation_mode": "url",
  "image_url": "http://192.168.1.135:7860/api/devices/<device_id>/photoframe",
  "deep_sleep_enabled": false
}
```

当前微雪设备已经完成固件刷写和 `/api/system-info` 识别，但 URL Rotation 和真实面板刷新仍需在
`192.168.1.137`（或重新读取的当前地址）上单独验收。`--device-url ... --rotate-now` 只用于
设备端 `/api/rotate`；它不是服务器向 ESP32 发送图片的接口。测试阶段关闭深度睡眠，确认 URL
拉取稳定后再单独验证睡眠轮播。该流程不保存派生图片缓存。

成功返回图片的 `200` 响应会携带与固件 URL Rotation 兼容的配置同步头；`304`
只表示图片内容未变，只返回 ETag 和缓存控制头：

```http
X-Config-Payload: {"config":{"auto_rotate":true,"rotate_cron":["*/5 * *"]}}
```

## 🧪 验证顺序

1. 先记录实际固件文件名、SHA-256、设备 IP 和 URL Rotation 配置。
2. URL Rotation 模式首次 GET 返回 `200 image/jpeg`，相同 ETag 请求返回 `304`。
3. 修改设备策略或当前照片后，下一次有效拉取产生新的选择 revision。
4. 服务重启后设备继续使用已持久化的选择 revision。
5. 禁用设备后服务器端内容接口返回 `404`；重新启用后恢复。
6. 仅维护历史兼容接口时，单独记录其 `POST` HTTP 结果和屏幕观察；该记录不能替代 URL
   Rotation 或真实电子纸刷新验收。

HTTP 合同通过只代表服务器协议正确，不代表真实彩色屏的色彩、刷新时间或电源稳定性通过。
