# 设备策略、渲染与安全

*Waveshare PhotoPainter 与 Seeed reTerminal E1002 的轮播策略、按需渲染、ETag 和访问边界。*

---

## 🔒 LAN 访问模型

当前手机、触摸屏和设备 URL 使用可信局域网开放模式，不要求管理员令牌；历史 `shared/secrets/admin.token` 不再被运行时读取。服务禁止公网暴露。

当前设备接口不使用设备令牌。设备注册会先访问用户明确提供的 ESP32 `device_url`，核验
PhotoFrame 身份并写入、读回 URL Rotation；控制面验证成功后才保留设备 ID、能力、策略和
配置审计，接口返回 `202`/`awaiting_pull`。这仍不等于设备已经拉图：只有后续带完整固件与
显示能力头的 PhotoFrame GET 才将 `pull_provision.status` 持久化为 `pulled`。设备禁用后所有
manifest/content/photoframe 接口返回 `404`，设备记录保留用于审计。

`pulled` 是服务器收到设备取图请求的协议证据，不是电子纸物理刷新证明。服务器还校验 TCP
来源 IPv4 与登记时验证过的 `device_url` 一致，浏览器预览、curl 或仅伪造请求头不会改变该状态；
反向代理不得改写来源地址。无设备令牌的局域网部署仍不提供密码学身份认证，因此不得暴露公网。

本机 HDMI 触摸屏是特殊的虚拟设备 `local-touchscreen`。它仅代表运行 Case7 的开发板本机显示
输出：没有网络地址、manifest/content 拉取或设备心跳。其设备身份和物理能力
与 ESP32 注册表一起持久化，但轮播启停、间隔、方向、重复抑制和文件名水印只能通过
`/api/admin/touchscreen` 修改；不能把本机屏幕作为远端 ESP32 的网络终端。

## ⏰ 设备策略

设备 profile 固定为：Waveshare ESP32-S3-PhotoPainter 7.3 英寸 E6 六色（黑、白、绿、蓝、红、黄）800x480（官方[产品页](https://www.waveshare.com/product/displays/e-paper/epaper-1/esp32-s3-photopainter.htm)和[Wiki](https://www.waveshare.com/wiki/ESP32-S3-PhotoPainter)；Wiki Mode 1 接受 800x480 或 480x800 图像），内容支持 `landscape`/`portrait`；Seeed Studio reTerminal E1002 7.3 英寸 ACeP / Spectra 6 全彩 800x480（[官方 Wiki](https://wiki.seeedstudio.com/getting_started_with_reterminal_e1002/)），在 Case7 中固定为 `landscape`。方向只使用这两个枚举，不支持 360°、180°或 90°/270°安装旋转；E1002 的 `portrait` 必须拒绝。Seeed 资料确认的是面板规格，横屏限制是 Case7 当前策略。

管理注册必须明确选择 `waveshare_photopainter_73` 或
`seeedstudio_reterminal_e1002`。旧记录若没有 profile 会保留原尺寸并标记
`profile_required=true`，不会根据 800x480 这一共同尺寸推断厂商；在重新确认实物型号前，
该记录不允许使用 profile 专属的方向、播放列表或设备主动拉取策略。
`POST /api/devices/handshake` 也不能绕过该规则：新 PhotoFrame 必须提供其中一个 profile，
并且 `codecs` 必须精确为 `["jpeg"]`。

| 字段 | 说明 |
| --- | --- |
| `auto_rotate` | 是否按 cron 自动轮播 |
| `rotation_cron` | 三字段 minute/hour/day-of-week 规则 |
| `crop_mode` | `cover` 填满或 `fit` 留白 |
| `overlay_date` | 是否叠加服务器时区日期 |
| `overlay_weather` | 是否叠加最后有效天气 |
| `orientation_mode` | `auto` 先纠正 EXIF 并保持照片横竖方向；`match_display` 只对输出图片内容在照片与目标屏幕方向相反时旋转 90 度，不表示设备支持安装角度旋转 |
| `orientation` | `landscape` 或 `portrait`；E1002 仅允许 `landscape` |
| `policy_revision` | 策略修改递增版本 |
| `selection_mode` | `smart` 或固定顺序的 `playlist` |
| `playlist_photo_ids` | 播放列表中的整数照片 ID，按配置顺序循环 |

电子纸设备默认 cron 为 `*/30 * *`（每 30 分钟）；允许明确改为 `*/10 * *`（每 10 分钟）。解析器支持 `*`、列表、范围和步长；无效规则保存时拒绝，不写半成品配置。触摸屏选图和电脑网页轮询不使用这个电子纸 cron，而分别由 `display.touchscreen_interval_seconds=60` 和 `display.remote_refresh_seconds=30` 控制。

`local-touchscreen` 的 `enabled` 开关只控制本机自动选图。`pause`/`resume` 写入
`display.touchscreen_enabled`，并不会禁用任何 ESP32、改写其 cron，或触发外网/局域网请求；
手动 `next`/`previous` 仍使用本机持久化显示历史。远端 ESP32 的启停、cron、播放列表、
渲染叠加和设备主动拉取请求状态则保存在各自的设备记录中，互不共享 selection slot。

历史 E1002 五分钟测试将策略切换为 `selection_mode=playlist`、`rotation_cron=["*/5 * *"]`；当前 Waveshare PhotoPainter 也可使用同一策略，但必须先确认其 profile 和 URL Rotation 固件接口。初次配置时从串口或设备本机网页取得 IP，作为注册时的 `device_url`；310B 会在验证并登记阶段访问该地址并写入配置，但不会扫描或轮询其他地址。设备随后才从服务器取图；同一时隙的重试复用 `display_state` 中的照片和设备选择 revision；进入新时隙才推进列表。`start_immediately=true` 会在配置时选择第一张。播放列表中的照片失效时跳过该项；如果全部失效，接口返回 `404`，不会悄悄从其他图库照片中回退。

### 历史兼容字段与维护接口

历史迁移记录可以包含 `push` 对象，但当前注册页面不会创建或编辑它，也不会将它作为设备
传输模式。字段仅供已存在的低层维护接口读取：

| 字段 | 说明 |
| --- | --- |
| `push.enabled` | 旧式 310B 向设备发送时的开关；当前注册流程固定为 `false` |
| `push.base_url` | 历史维护时由操作者确认的设备根 URL，不能自动发现 |
| `push.timeout_seconds` | 历史单次 HTTP 超时，范围 1-60 秒 |
| `push.attempts` | 历史单时隙有限重试次数，范围 1-3 |
| `push.last_status` / `last_error` | 历史发送证据，不代表屏幕已经刷新 |

旧式主动发送调度器每个有效 cron 时隙最多发送一次，并使用同一设备的选择 revision；天气刷新
和手动上一张/下一张不会触发固件协议猜测。网络错误、`503` 或设备睡眠只写入
`last_status/last_error`，不会自动改成 URL Rotation 或 `/dataUP`。该路径不出现在设备注册
页面，不能代替设备主动 URL 拉取的验收。

## 🖼️ 按需渲染

每次请求按以下顺序执行：

1. 读取 EXIF 方向并校正；
2. 按设备 `orientation` 目标渲染；E1002 的 `portrait` 请求在策略校验阶段拒绝；
3. 在明确选择 `orientation_mode=match_display` 时，使源图方向匹配目标屏幕；默认 `auto` 不会把竖拍照片盲转成横图；
4. 按设备尺寸执行 `cover` 或 `fit`；
5. 按策略叠加日期和天气；
6. 递减 JPEG 质量和尺寸，直到满足 `max_bytes`。

屏幕方向由 profile 的 `orientation` 确定，不能由照片宽高推断，也不通过安装角度字段表达。`match_display` 只校正最终编码帧的横竖比例；它不是 90°/180°/270° 的设备旋转设置。E6 线协议仍固定 800x480，PhotoPainter 的竖屏能力需要对应设备实机确认。

触摸屏的 `display.orientation_mode`/`display.rotation` 与 E6 的
`epaper.orientation_mode`/`epaper.rotation` 是两套独立配置。E6 设备的能力只描述
固定的 `800x480/e6` 线协议，不会覆盖服务器的 `epaper` 策略；因此修改触摸屏方向时，
E6 内容字节和 ETag 不变，仍可返回 `304`。

渲染使用单锁限制峰值内存，结果只存在响应内存中，不写缩略图、JPEG、EPDGZ、E6 帧文件、Redis 或其他图片缓存。PhotoPainter 收到 JPEG 后继续负责六色校准、抖动和刷新。

## 🏷️ ETag 与响应头

JPEG URL Rotation 的 ETag 包含照片 SHA-256、设备 ID、输出尺寸、旋转、方向模式、目标方向、字节上限、裁剪方式、日期/天气开关、策略 revision、选择 revision 和天气/日期时间输入。完全一致时返回 `304`，避免重复编码；策略、照片、天气、方向模式或尺寸改变时返回新的 `200`。E6 帧没有日期或天气叠加，因此它的 ETag 只由照片 SHA-256 和 E6 渲染选项组成；仅刷新天气或触摸屏逻辑 revision 时，同一帧仍返回 `304`，避免额外电子纸刷新。

旧式服务器主动 `POST /api/display-image` 不使用条件 GET，因此不会收到 `304`；其 ETag 仅作为
审计和日志关联字段。该兼容行为不参与当前设备注册或 URL Rotation 流程。

```text
Cache-Control: private, max-age=0, must-revalidate
Vary: X-Display-Width, X-Display-Height, X-Display-Orientation, If-None-Match
ETag: "<opaque-value>"
X-Config-Payload: {"config":{"auto_rotate":true,"rotate_cron":["*/5 * *"]}}
```

响应不泄露真实路径、内部注册信息或模型资产路径。详细 HTTP 字段见 [03-album-server-api-and-esp32-protocol.md](./03-album-server-api-and-esp32-protocol.md)。
