# PhotoPainter 部署与验收

*310B 服务、PhotoPainter 实机和 E6 路径的分层验收清单。*

---

## 🖥️ 310B 服务验收

```bash
bash scripts/collect_system_status.sh
curl http://127.0.0.1:7860/api/health
curl http://127.0.0.1:7860/api/models
curl http://127.0.0.1:7860/api/index/stats
```

确认 `ready`、PyACL 可用和三个模型已准入。完整 COCO-CN 验收才要求 500 张图像及每模型 500 条 embedding；当前操作板 `192.168.1.135` 仅保留 20 张受管照片和每模型 20 条 embedding，英文/中文 NPU 搜索与服务重启已复核通过。部署过程不得覆盖 `models/`、`data/`、`photos/` 或 `reports/`。

## 🔋 PhotoPainter 实机步骤

1. 记录官方固件版本、Release SHA-256 和刷写日志。
2. 断开电池，仅 USB 供电刷写并完成 2.4 GHz Wi-Fi 配网。
3. 记录设备 ID、屏幕尺寸、RSSI、服务器 URL 和轮播策略。
4. 验证首次 `200`、相同 ETag 的 `304` 和策略变化后的新 `200`。
5. 验证日期/天气叠加、cover/fit、断网重试和服务重启恢复。
6. 禁用设备，确认拉图返回 `404`；重新启用后确认恢复。
7. 按上游硬件说明分别测试 USB 和电池供电，未确认前不同时接入。

## 当前目标：Waveshare PhotoPainter（2026-08-30）

本轮实际设备是新的 Waveshare ESP32-S3-PhotoPainter 7.3 英寸，不是 Seeed Studio
reTerminal E1002。刷写和网络证据已经完成，真实彩色屏刷新仍需单独记录：

| 项目 | 本次实测值 |
| --- | --- |
| profile | `waveshare_photopainter_73` |
| 固件 | `esp32-photoframe v2.18.0`，`board waveshare_photopainter_73` |
| 串口 | Windows `COM17`，ESP32-S3 USB Serial/JTAG |
| STA 地址 | `192.168.1.137`（DHCP，后续可能变化） |
| 设备网页 | `http://192.168.1.137/`，`/api/system-info` 返回 HTTP 200 |
| 分辨率/面板 | `800x480`，PhotoPainter E6 六色路径 |
| 刷写证据 | Release SHA-256 校验通过，esptool 输出 `Hash of data verified` |
| 证据日志 | `C:\Users\zhoux\Downloads\waveshare_photopainter_boot.log`、`waveshare_photopainter_sta_boot.log` |

每次重启、换路由器或重新配网后，先按 [串口/IP 手册](./13-photopainter-serial-ip-and-wifi.md)
打开串口监视器，读取新的 `sta ip:`，再从同一局域网电脑执行：

```bash
curl -i --connect-timeout 5 --max-time 10 http://<WAVESHARE-IP>/api/system-info
curl -I --connect-timeout 5 --max-time 10 http://<WAVESHARE-IP>/
```

只有当响应中的 `board_name`、`version` 和尺寸与实物一致时，才继续进行 JPEG 推送或
URL Rotation。`photoframe.local` 是可选 mDNS 名称，解析失败不影响验收；不能把它当成
登录账号或固定地址。服务器端的 PhotoFrame 注册、设备状态和 ETag 证据应与屏幕肉眼刷新
结果分开记录。当前地址为 DHCP 租约，不能写死到长期配置中。

### 历史对照：E1002 五分钟主动推送验收

本节只适用于历史 Seeed Studio reTerminal E1002 批次，不适用于当前 Waveshare 设备。主动推送必须先确认设备实际运行的固件和地址。不要把历史 `192.168.1.117`、`photoframe.local` 或任意 ARP 条目当作 E1002 地址；应从设备网页、路由器租约或串口日志取得当前 IP。对官方 PhotoFrame 固件，可先执行只读识别：

```bash
curl -i http://<E1002-IP>/api/system-info
```

确认响应属于官方 PhotoFrame 后，在板端使用显式 `--push-url`：

```bash
bash scripts/setup_photoframe_test.sh \
  --source shared/incoming/photoframe-test --limit 20 \
  --profile-id seeedstudio_reterminal_e1002 \
  --server-url http://127.0.0.1:7860 \
  --public-server-url http://192.168.1.135:7860 \
  --push-url http://<E1002-IP> --push-protocol photoframe_api --push-now
```

记录 `POST /api/display-image` 的 HTTP 状态、响应正文、photo ID、ETag 关联值、设备 `last_status/last_error` 和实际屏幕变化。`2xx` 只证明设备 HTTP handler 接受了图片；必须另存屏幕观察或串口刷新日志，才可声称显示成功。设备深度睡眠时主动推送必然无法建立连接。

若 `/api/system-info` 不存在而设备网页/固件识别为 Waveshare Demo，则不能发送 JPEG 到 `/api/display-image`。Demo 只接受 raw 800x480 24-bit BMP 的 `POST /dataUP`，且默认 AP 模式；应显式采用 Demo 适配器并完成 STA 网络改造，或刷写支持 direct push 的固件。不要用失败响应自动切换协议。

### 历史对照：E1002 五分钟 URL Rotation 兼容验收

将电脑目录中的 `CIMG2780.JPG` 至 `CIMG2799.JPG` 复制到 `/home/HwHiAiUser/Documents/ai-album/shared/incoming/photoframe-test/`，在 `current` 发布目录运行：

```bash
bash scripts/setup_photoframe_test.sh \
  --source shared/incoming/photoframe-test --limit 20 \
  --profile-id seeedstudio_reterminal_e1002 \
  --server-url http://127.0.0.1:7860 \
  --public-server-url http://192.168.1.135:7860
```

记录脚本输出的 `device_id`、`image_url` 和报告路径（含逐文件 SHA、photo ID、playlist、state 以及索引前后统计），在确认官方 URL Rotation 固件后于 E1002 本机网页填入 URL。验收至少包含：首次 `200`、同一时隙 `304`、连续三个五分钟时隙显示不同 photo ID、服务重启后当前照片保持、设备 `last_request/last_status` 更新。`--device-url <E1002-IP> --rotate-now` 只用于已确认 URL Rotation 的首次立即刷新，不代表服务器主动推送。

脚本会先检查 `/api/health` 为 `ready` 且三个 NPU 模型均为 `admitted`；若注册表为空会在上传前退出，保留输入目录，不产生“仅元数据成功”的假播放列表。

## 📌 2026-08-23 历史协议批次

该协议批次来自 **2026-08-23 的历史板** `HwHiAiUser@192.168.1.135`，设备 ID `514e8560e4f4731b`；报告为：
`shared/reports/photoframe-test-20260823-190312-7149.json`。

该 IP 后来被当前操作板复用；本节仅保留当时的协议实测，不能与当前板的 20 张图库、NPU 搜索或服务重启复核混为同一轮证据。

| 项目 | 实测值 |
| --- | --- |
| 输入批次 | `CIMG2780.JPG` 至 `CIMG2799.JPG`，20 张 |
| 上传/元数据任务 | 20/20，119.123 秒，18 张有人脸 |
| 播放列表 | photo ID `1..20`，`selection_mode=playlist`，`*/5 * *` |
| 首次 URL 拉图 | `200 image/jpeg`，800x480，ETag 有效 |
| 同 ETag 重试 | `304 Not Modified`，设备状态记录 `not_modified` |
| 19:10 新时隙 | photo ID `3`，selection revision `5`，返回新的 `200` 和 ETag |
| 配置同步 | `X-Config-Payload` 下发 `auto_rotate=true` 与 `rotate_cron=["*/5 * *"]` |

PhotoFrame 功能代码在 `case7-photoframe-20260823m` 之后又加入了严格整数 ID 校验和发布回滚加固；
后续发布不覆盖共享模型、数据、照片或报告资产。当前服务 PID 可由
`cat /home/HwHiAiUser/Documents/ai-album/shared/run/smart_album.pid` 读取。发布后重新验证：首次
`GET /api/devices/514e8560e4f4731b/photoframe` 返回 `200`，同一 ETag 条件请求返回 `304`；随后进入
`2026-08-23-19-45:*/5 * *` 时隙，状态接口显示 `photo_id=6`、selection revision `8`，并再次以同一 ETag
返回 `304`。此次验证仍是服务器 JPEG/HTTP 证据，
不是 E1002 真实屏幕刷新证据。

该历史批次当时的 `shared/models/registry.json` 为空，`embeddings_by_model={}`，因此只证明照片上传、播放列表、按需 JPEG、ETag/304 和 cron 时隙合同；它不构成 NPU 准入证据，也不构成 E1002 彩色屏实机刷新通过。该状态已由
2026-08-27 历史板 `192.168.8.180` 的模型、ACL 准入和索引复核见
[02-ascend310b4-deployment-and-acceptance.md](./02-ascend310b4-deployment-and-acceptance.md) 与
[08-model-pipeline-and-npu-admission.md](./08-model-pipeline-and-npu-admission.md)。

本次复核时电脑没有枚举出用户之前记录的 `COM13`；另有一个 `CH343 (COM16)`，但串口无可识别
启动日志，`esptool` ROM 握手失败（收到应用数据包头），尚不能确认其就是 E1002。历史地址
`192.168.1.117` 和 `photoframe.local` 也不可达；因此没有执行 E1002 `/api/rotate`，没有记录
Wi-Fi RSSI、固件日志或真实面板刷新结果。设备上线后也不能据此推断固件协议；必须先记录实际 IP、固件 hash 和 endpoint 响应，再选择主动推送或 URL Rotation。下列 URL 是 **2026-08-27 历史板** `192.168.8.180` 的复核地址，不是当前配置：
`http://192.168.8.180:7860/api/devices/514e8560e4f4731b/photoframe`，然后再用设备实际 IP 运行
`--device-url <E1002-IP> --rotate-now` 做首次拉图验证。

最终服务器协议复核（发布 `case7-photoframe-20260823v`）再次得到首个 `200 image/jpeg`、
`800x480` 和 ETag `"2431ac2d9e91f8b0206c278d9e34142b"`；带该 ETag 的同槽请求返回 `304`。
这仍然是 310B HTTP 证据，不能替代 E1002 面板刷新证据。

## 🧾 证据字段

```text
board_release
server_pid
firmware_version
firmware_sha256
device_id_hash
display_resolution
wifi_rssi
first_http_status
etag_http_status
policy_change_http_status
revoke_http_status
service_restart_status
display_refresh_result
power_mode
log_paths
```

HTTP、NPU 健康、JPEG 编码和真实彩色屏刷新必须分别记录。协议测试不替代色彩/刷新验收；E6 dry-run 不替代微雪驱动板实屏验收。

## ⚠️ 当前限制

Case7 已实测真实 PhotoPainter 固件识别、URL Rotation 配置回读和设备主动 GET；设备状态可追溯为
`pulled`。这只证明 310B 收到了来自 `192.168.1.137` 的官方拉图请求，不证明彩色电子纸已经完成物理
刷新。`/api/rotate` 的一次超时已按“未确认”记录，后续拉图证据仍有效。旧式服务器主动 POST、供电稳定性、
屏幕色彩/刷新观察和 Demo 固件的 `/dataUP` 适配不属于本次注册验收；Orange Pi 直连 E6 的 800x480
六色帧仍等待驱动板型号和 GPIO/SPI 接线确认。
