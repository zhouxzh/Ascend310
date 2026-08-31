# 相册服务器 API 与 ESP32 协议

*手机、触摸屏、Waveshare PhotoPainter 与 Seeed reTerminal E1002 的 HTTP 合同；当前实测终端为 Waveshare，E1002 仅作历史对照。*

---

## 🌐 访问边界

服务监听 `7860`，当前 LAN profile 不要求账号或管理令牌。管理接口和设备取图接口均直接
在可信局域网内提供；因此不得端口映射、反向代理到公网或把服务放入不可信网络。设备
enable/disable 状态始终由服务端执行。

普通上传原图保存在系统用户目录 `~/Pictures/ai-album/imports/`，临时 multipart 文件保存在
`~/Pictures/ai-album/.upload-tmp/`，均与仓库发布目录隔离。`shared/photos/` 只承载 COCO-CN
和旧版本数据的兼容读取。JPEG/E6 内容按请求生成，不产生持久化派生图片缓存。

新终端的 IPv4 地址必须按 [PhotoPainter 串口读取 IP 与 Wi-Fi 配网手册](./13-photopainter-serial-ip-and-wifi.md)
确认；`photoframe.local` 是可选 mDNS 别名，不是固定地址或登录入口。

## 📱 手机与触摸屏 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | NPU、模型、索引、天气和设备状态 |
| `GET` | `/api/models` | 已准入模型合同 |
| `GET` | `/api/index/stats` | 照片、人脸和 embedding 数量 |
| `GET` | `/api/photos` | 分页图库 |
| `GET` | `/api/photos/{id}/preview` | 浏览器/触摸屏按需 JPEG 预览 |
| `GET` | `/api/photos/{id}/file` | 原图下载（保留原始容器和 MIME） |
| `POST` | `/api/photos/upload` | multipart 上传并创建串行索引任务 |
| `GET` | `/api/jobs/{job_id}` | 查询上传/索引进度 |
| `POST` | `/api/search/text` | 中文/英文文本检索 |
| `GET/PATCH` | `/api/config` | revision 乐观锁配置 |
| `GET` | `/api/display/current` | 当前推荐照片和显示状态 |
| `POST` | `/api/display/select` | 指定照片设为主屏 |
| `POST` | `/api/display/control` | `next`、`previous`、`pause`、`resume` |
| `GET` | `/api/admin/touchscreen` | 本机 HDMI 触摸屏的虚拟设备、显示配置和当前状态 |
| `PATCH` | `/api/admin/touchscreen` | 更新本机触摸屏名称和专属显示设置 |
| `POST` | `/api/admin/touchscreen/advance` | 本机触摸屏 `next`、`previous`、`pause` 或 `resume` |
| `GET` | `/api/admin/devices` | 查看设备管理视图 |
| `POST` | `/api/admin/devices/register` | 验证设备连通性、身份和 URL Rotation 后原子注册（配置完成返回 `202`，等待设备拉图） |
| `POST` | `/api/admin/devices` | 旧入口；PhotoFrame 请求固定返回 `400/not_registered`，不会创建记录，必须改用 `/register` |
| `POST` | `/api/admin/devices/{id}/provision-pull` | 一次性验证官方 PhotoFrame 并写入 URL Rotation 配置 |
| `POST` | `/api/admin/devices/{id}/playlist` | 设置固定照片顺序、`*/5 * *` 时隙和立即首张 |
| `PATCH` | `/api/admin/devices/{id}` | 修改设备启停、轮播、渲染和播放列表策略；管理页面固定为设备主动拉取 |
| `POST` | `/api/admin/devices/{id}/push` | 旧式服务器主动发送兼容接口；不属于注册页面或正常轮播流程 |
| `GET` | `/api/admin/devices/{id}/state` | 返回照片 ID、时隙、ETag 和设备请求状态 |
| `DELETE` | `/api/admin/devices/{id}?confirm=true` | 删除远端设备注册记录及其显示状态；不删除照片 |

### 注册后的唯一取图方式

管理页面使用原子注册接口 `POST /api/admin/devices/register`。请求必须包含
`device_url`（串口日志确认的 ESP32 私网 IPv4 根地址）和明确的 `profile_id`。服务先访问
`/api/system-info`，核验官方 PhotoFrame 身份、分辨率和所选产品型号；随后读取、写入并回读
`/api/config`，可选触发 `/api/rotate`。这些网络步骤全部成功后才持久化有效注册并返回 HTTP
`202`（`registration_status=awaiting_pull`）；地址不可达、固件不匹配或配置核验失败时返回 `400/502`，响应带
`registration_status=not_registered`，临时记录会被删除。`202` 只表示“310B 已验证设备并写入
URL Rotation”，不等于电子纸已经完成物理刷新；只有设备随后携带官方固件和显示能力头访问取图 URL，
状态才会变为“设备已拉图”。管理页面不会把旧的浏览器请求当成设备证据。

历史 `POST /api/admin/devices` 不再创建 `pending_connection` 记录；对 PhotoFrame 请求固定返回
`400` 和 `registration_status=not_registered`，并指向 `/api/admin/devices/register`。数据库中若仍有
旧版本留下的 `pending_connection`，只能作为历史状态只读展示，不能当作当前注册成功。远端设备传输方式固定为“设备主动拉取”：
ESP32 按自己的 URL Rotation 时隙向 310B 的取图 URL 发起 `GET`。注册不会扫描局域网或猜测
ESP32 地址。

历史记录中的 `push.enabled=false` 只是旧字段的默认值；当前注册页面不把它作为用户选项，
它不表示设备被禁用或注册失败。设备是否可取图只由 `enabled` 和设备本身是否已经保存 URL
Rotation 配置决定。

### 设置设备主动拉取（URL Rotation）

1. 从 ESP32 串口启动日志或其本机网页取得当前 IPv4；不要把历史 DHCP 地址或
   `photoframe.local` 解析结果当作当前地址。
2. 在设备页的 **验证并注册 ESP32 电子相册** 中填写 `http://<ESP32-IP>`，点击
   **验证并注册设备**。服务只接受 RFC1918 私网 IPv4 的根 URL 和 80 端口，并串行执行：
   `GET /api/system-info` 身份/尺寸核验、`GET /api/config`、`PATCH /api/config`、配置读回核验，
   最后可选 `POST /api/rotate`。它不会扫描网段、跟随重定向或访问任意 URL。
3. 成功后状态为 **已验证配置 · 等待设备拉图**，不是“已显示”。服务器只把 TCP 来源与已登记
   ESP32 地址一致、且包含完整协商头的请求计为拉图证据。设备必须自己发出包含
   `X-Firmware-Version`、`X-Display-Width`、`X-Display-Height` 和
   `X-Display-Orientation` 的取图请求后，才会显示 **设备已拉图**。首次联调由服务写入
   `deep_sleep_enabled=false`；待链路稳定后再单独测试睡眠。
4. 若无法从 310B 访问 ESP32，可在设备网页或设备端命令手动写入相同配置：

   ```bash
   curl -X PATCH http://<ESP32-IP>/api/config \
     -H 'Content-Type: application/json' \
     --data-raw '{"auto_rotate":true,"rotate_cron":["*/10 * *"],"rotation_mode":"url","image_url":"http://192.168.1.135:7860/api/devices/<device_id>/photoframe","deep_sleep_enabled":false}'
   curl http://<ESP32-IP>/api/config
   ```

   首次联调建议关闭深度睡眠；确认每次唤醒能取到图片后再按固件说明恢复睡眠。固件字段
   `rotate_cron` 与 Case7 服务策略字段 `rotation_cron` 名称不同，不要混用。
5. 点击 Case7 卡片的“推进下一张”只更新该设备的持久化选择，ESP32 下一次访问取图 URL
   时会取得新照片。首个请求应为 `200 image/jpeg`，同一 ETag 的再次请求应为 `304`。

如果 ESP32 本机网页没有 URL Rotation、图片 URL 或相应 API 字段，则当前固件不支持设备
主动拉取，不能仅在 310B 页面上补开；需要刷入或修改明确支持该协议的固件，并重新记录
固件版本与 SHA-256。

上传示例（网页还支持选择整个文件夹；浏览器端会自动分批提交）：

```bash
curl -F "files=@photo.jpg" \
  http://192.168.1.135:7860/api/photos/upload
curl http://192.168.1.135:7860/api/jobs/<job_id>
```

当 `index.auto_index_uploads=true`（默认）时，任务必须使用已准入的
MobileCLIP、Chinese-CLIP 和 ResNet50 NPU 模型；若注册表为空或模型 hash/准入
检查未通过，接口仍返回 job，但 job 会以明确错误结束，不会把仅有元数据或
CPU 结果标记为 NPU embedding。只有明确关闭自动索引的离线维护场景才允许
不生成 embedding。

常规上传的照片数据由 `files` multipart 字段提供。服务自行读取 EXIF 拍摄时间，缺失时使用
上传时间；不再把 `tags` 作为用户填写的上传字段。历史 SQLite 中保留的 `tags` 仅用于
兼容旧记录，不能据此推断新照片具有自动图像标签。

### 图库预览与 MPO 原图

`/api/photos` 和 `/api/search/text` 返回的 `url`/`preview_url` 指向
`/api/photos/{id}/preview?width=480&height=360`。该接口在单次请求内读取原图，
应用 EXIF 方向并在内存中缩放为标准 `image/jpeg`，响应为
`Content-Disposition: inline`。这是为了兼容相机导入的 MPO 文件：MPO 原文件虽然
包含 JPEG 帧，但以 `image/mpo` 和下载响应头直接嵌入 `<img>` 时，并非所有
Firefox/嵌入式浏览器都能显示。预览不会写入缩略图、JPEG 或其他服务器缓存；
`file_url` 仍指向 `/file`，供需要保留原始字节的下载和审计使用。预览尺寸限制为宽
`32..1600`、高 `32..1200`，单次响应最多 2 MiB，不能用来绕过原图导入校验。

### 固定显示设备 profile

| `device_id` profile | 厂商资料 | 面板 | 方向合同 |
| --- | --- | --- | --- |
| `waveshare_photopainter_73` | [Waveshare 产品页](https://www.waveshare.com/product/displays/e-paper/epaper-1/esp32-s3-photopainter.htm) / [Wiki](https://www.waveshare.com/wiki/ESP32-S3-PhotoPainter) | E6 六色（黑、白、绿、蓝、红、黄），800x480；Wiki Mode 1 接受 800x480 或 480x800 图像 | `landscape` 或 `portrait` 内容 |
| `seeedstudio_reterminal_e1002` | [Seeed Studio Wiki](https://wiki.seeedstudio.com/getting_started_with_reterminal_e1002/) | ACeP / Spectra 6 全彩，800x480 | Case7 策略仅 `landscape` |

方向字段只允许 `landscape`、`portrait`；不支持 360°、180°或 90°/270°安装旋转字段。
`seeedstudio_reterminal_e1002` 请求 `portrait` 时必须返回拒绝，不得通过交换宽高或隐式旋转放行。
厂商页面只作为规格来源，不作为当前设备固件接口或实机刷新证据。Seeed 的资料确认的是 `800x480` 面板；E1002 的横屏限制是本项目当前设备策略，不能据此推断其上游固件的全部能力。

历史 `devices.json` 中没有 `profile_id` 的 PhotoFrame 记录不会按尺寸、名称或旧协议
自动猜测为任一厂商。服务会保留原始能力并返回 `profile_required=true`；这类记录不能
修改方向、设置播放列表或使用设备主动拉取策略，必须在设备页按实物重新选择型号，或使用
`PATCH /api/admin/devices/{id}` 显式提交 `profile_id` 后再继续。这样可以避免把 E1002
错误套用到 PhotoPainter 的竖屏合同。

### 本机触摸屏虚拟设备

`local-touchscreen` 表示与 310B HDMI 相连的本机触摸屏，而不是另一台 ESP32 或网络客户端。
它的照片选择历史仍使用本机 `display_state` 保存；设备名和物理显示能力保存在设备注册表，
轮播与渲染行为保存在版本化服务器配置。它没有 URL、IP、manifest、content 轮询或服务器
主动推送接口，也不需要任何设备令牌。

读取当前配置和状态：

```bash
curl http://192.168.1.135:7860/api/admin/touchscreen
```

更新触摸屏名称、自动轮播、换图间隔、方向、重复抑制和文件名水印时，客户端应带回读取到的
`revision`，避免并发管理页面相互覆盖：

```json
{
  "revision": 7,
  "name": "客厅本机触摸屏",
  "enabled": true,
  "interval_seconds": 60,
  "repeat_window": 12,
  "show_filename": true,
  "orientation_mode": "auto",
  "rotation": 0,
  "display": {
    "width": 1920,
    "height": 1080
  }
}
```

其中 `orientation` 只使用 `landscape` 或 `portrait`；修改通过 `PATCH /api/admin/touchscreen` 提交；`POST /api/admin/touchscreen/advance`
的请求体为 `{ "action": "next" }`，也可使用 `previous`、`pause` 或 `resume`。`pause`
只暂停本机触摸屏的自动选图，不会把它转换成远端设备或改变 ESP32 的轮播策略。

### 远端 ESP32 注册

设备页中的“验证并注册 ESP32 设备”调用 `POST /api/admin/devices/register`，当前注册入口只接受
`kind="photoframe"`，且必须显式提供 `profile_id` 和 `device_url`。服务会先实际访问设备并
完成身份/配置回读；失败不会创建有效注册。可选 profile 值只有
`waveshare_photopainter_73` 和 `seeedstudio_reterminal_e1002`；服务不会根据尺寸、名称或
IP 猜测厂商。低层 `POST /api/devices/handshake` 使用相同限制，不能绕过管理页面创建第三种
PhotoFrame 合同；`codecs` 只能是精确的 `["jpeg"]`。创建操作返回 `device_id` 和取图 URL；
当前固件和服务均不使用设备令牌。
远端设备拥有自己的 `policy`、播放列表和请求状态，和 `local-touchscreen` 的本机配置完全
分离。注册页面必须收集用户从串口或设备网页确认的 `device_url`；它不让用户选择传输协议、
超时、重试或服务器主动发送参数，远端传输固定为设备主动 URL Rotation：

```json
{
  "name": "书房 E1002",
  "kind": "photoframe",
  "profile_id": "seeedstudio_reterminal_e1002",
  "device_url": "http://192.168.1.137",
  "display": {
    "width": 800,
    "height": 480,
    "codecs": ["jpeg"],
    "rotation": 0,
    "orientation_mode": "auto"
  },
  "policy": {
    "rotation_cron": ["*/30 * *"]
  }
}
```

`GET /api/devices` 保持设备协议视图，只列出远端设备；设备管理页面的
`GET /api/admin/devices` 会同时显示本机虚拟设备和已注册的 ESP32，方便统一查看，但不改变
两者的协议边界。

设备管理中的 **禁用设备** 使用 `PATCH /api/admin/devices/{id}` 提交
`{"enabled": false}`，记录可随后重新启用。**删除注册**使用
`DELETE /api/admin/devices/{id}?confirm=true`，服务从注册表移除该远端设备，并清理其
`display_state`/`display_history`；原始照片、`photos` 表和所有模型 embedding 不受影响。
为避免触摸屏误操作，网页采用两次点击确认；直接调用 DELETE 时必须显式提供
`confirm=true`，否则返回 `400`。

`GET /api/jobs/{job_id}` 在原有 `status`、`progress` 和 `summary` 之外返回
`phase`、`files_total/files_completed`、`index_files_total/index_files_completed`、
`embedding_total/embedding_completed` 和 `current_model`。阶段依次为
`queued`、`hashing`、`importing`、`validating`、`embedding`、`finalizing`、
`completed` 或 `failed`；这些计数来自实际文件处理和串行 NPU 编码，而不是
前端定时估算。网页使用浏览器上传字节事件显示“文件传输”进度，再轮询该任务
显示“NPU 索引”进度。

批量 PhotoFrame 测试由板端脚本完成。它只接受 `shared/incoming/` 下的文件，按文件名排序取前 20 张（当前固定批次为 `CIMG2780.JPG` 至 `CIMG2799.JPG`），上传后等待任务返回 `photo_ids`，再配置固定顺序播放列表：

```bash
bash scripts/setup_photoframe_test.sh \
  --source shared/incoming/photoframe-test --limit 20 \
  --profile-id waveshare_photopainter_73 \
  --server-url http://127.0.0.1:7860 \
  --public-server-url http://192.168.1.135:7860
```

播放列表请求为 `POST /api/admin/devices/{device_id}/playlist`，请求体包含 `photo_ids`、`rotation_cron: ["*/5 * *"]` 和 `start_immediately: true`。首次有效时隙立即选择第一张，之后每五分钟按固定顺序循环；同一时隙重复拉取仍返回同一 ETag。脚本只保存设备 ID、URL 和状态，不保存设备令牌。

普通 multipart 上传不设置单张文件大小或单次张数上限；服务仍检查扩展名、解码、单张 50 MP 像素保护、路径、符号链接和内容 SHA-256。大批量客户端应自行分批，以免浏览器、代理或磁盘耗尽。删除接口必须显式 `confirm=true`，只允许删除受管上传照片，不删除 COCO-CN 或外部原图。固定的 PhotoFrame 硬件测试脚本另有 20 张、25 MB 和 50 MP 输入门，这些是测试批次约束，不是普通上传 API 的限制。

## 🤖 ESP32 能力握手

首次调用：

```http
POST /api/devices/handshake
Content-Type: application/json
```

```json
{
  "name": "living-room",
  "protocol_version": 1,
  "display": {
    "kind": "lcd",
    "width": 320,
    "height": 240,
    "codecs": ["jpeg"],
    "max_bytes": 200000,
    "rotation": 0
  }
}
```

E6 设备声明 `kind=epaper`、`800x480` 和 `codecs=["e6"]`。PhotoPainter 声明 `kind=photoframe`、`800x480` 和 `codecs=["jpeg"]`。响应包含 `device_id`、`poll_seconds` 和当前 manifest。电子纸设备默认按 30 分钟轮询（可选 10 分钟）；触摸屏和电脑网页使用独立的快速更新周期。

## 🔁 Manifest、ETag 与内容

1. 客户端保存 `device_id` 和 ETag；不保存或发送设备令牌。
2. 周期调用 `GET /api/devices/{device_id}/manifest`。
3. manifest 的 ETag 变化时调用 `/content` 或 `/photoframe`。
4. 请求带 `If-None-Match`，未变化返回 `304 Not Modified`。

设备内容接口支持 `X-Display-Width`、`X-Display-Height` 和 `X-Display-Orientation` 头。接口在可信局域网内公开提供，禁用设备返回 `404`，不得继续取图。

如果客户端要协商竖屏尺寸，应在 `manifest` 和随后内容请求中同时发送同一组
`X-Display-Width`、`X-Display-Height`；宽高必须成对出现。协商始终以已登记的
profile 为上限：只有 `waveshare_photopainter_73` 可以把 `800x480` 改为 `480x800`。
只发送 `X-Display-Orientation: portrait` 时，服务也只会在该 profile 允许时交换宽高；
E1002 会在此之前拒绝请求。manifest 的 ETag 与这组能力绑定，切换横竖屏必须重新取
manifest，不能复用旧 ETag。

## 图片方向协商

`/api/display/content`（触摸屏）可通过 `width`、`height`、`orientation` 和
`orientation_mode` 查询参数请求当前视口的 JPEG；设备接口使用同名的
`X-Display-Width`、`X-Display-Height`、`X-Display-Orientation` 请求头。服务器始终先
应用 JPEG/手机照片的 EXIF Orientation，再应用设备 profile 的 `orientation`。`orientation_mode=auto`
是默认安全模式：保持照片本身的横竖方向，使用 `cover` 或 `fit` 适配目标屏幕；
`orientation_mode=match_display` 才会在源图与目标屏幕方向相反时对**输出图片内容**增加一次
90 度旋转；这不是设备安装角度，也不会放开 profile 的方向限制。
设备只登记 `landscape` 或 `portrait`，不要用安装角度或图片宽高猜测设备 profile。
E1002 的 `portrait` 请求必须拒绝。

响应会返回实际 JPEG 像素方向的 `X-Album-Orientation`、请求显示目标的
`X-Album-Target-Orientation`、`X-Album-Orientation-Mode`、目标尺寸和包含方向
策略的 ETag。`auto` 模式下两种方向可以不同：例如横拍照片在竖屏视口内仍保持横向。
相同照片、方向策略和尺寸重复请求返回 `304 Not Modified`；方向模式、
设备方向或视口改变会生成新的 ETag。E6 content 始终保持协议要求的 800x480、
192000 字节帧，竖装面板仍需硬件侧确认。

| 设备 | 响应 | 服务端职责 | 终端职责 |
| --- | --- | --- | --- |
| LCD | `image/jpeg` | 按能力缩放、旋转、质量和字节上限编码 | 显示 JPEG |
| E6 | 192000 bytes | 800x480 六色帧编码 | SPI/GPIO 刷新 |
| PhotoPainter | bounded JPEG | 服务器策略渲染和 ETag | 六色调色、抖动和刷新 |

## 🛠️ 兼容与维护：旧式服务器主动发送

本节只说明为历史设备记录和维护排障保留的低层接口。它**不出现在设备注册页面**，不属于
当前 PhotoPainter 或 E1002 的正常轮播流程，也不能据此推断任何实机屏幕刷新已经通过。
正常流程始终使用前述 ESP32 主动 URL 拉取。

旧式服务器主动发送要求设备协议和根 URL 由操作者显式配置。服务不会扫描局域网、访问任意
发现地址，或把 URL Rotation 的 GET 端点当成推送端点。配置示例中的 `<DEVICE-IP>` 必须来自
设备串口或网页实测：

```bash
curl -X PATCH http://192.168.1.135:7860/api/admin/devices/<device_id> \
  -H 'Content-Type: application/json' \
  -d '{"push":{"enabled":true,"base_url":"http://<DEVICE-IP>","protocol":"photoframe_api","timeout_seconds":60,"attempts":1}}'
curl -X POST http://192.168.1.135:7860/api/admin/devices/<device_id>/push \
  -H 'Content-Type: application/json' -d '{"force":true}'
```

对官方 PhotoFrame `v2.18.0`，服务器把按需渲染的 JPEG 原始字节发送到：

```http
POST http://<DEVICE-IP>/api/display-image
Content-Type: image/jpeg
```

成功通常返回 `2xx` JSON；设备忙或尚未初始化时可能返回 `503`，服务只做有限重试并把结果写入设备状态。该请求不依赖 ETag/304，ETag 仅作为服务器审计头；同一时隙不会由调度器重复发送。深度睡眠会使设备 HTTP 服务不可达，310B 无法远程唤醒它。

若使用本仓库 `esp32/patches/` 编译的修改固件，协议必须显式设置为 `case7_push`。服务器发送同一 JPEG 到 `/api/case7/push`，并要求响应头 `X-Case7-Push: 1`；缺少该响应头即视为失败，即使状态码为 `2xx`。

Waveshare `ESP32-S3-PhotoPainter-Demo` 是另一套固件：它只注册 `POST /dataUP`，接收 raw 800x480 24-bit BMP 并写入设备 SD 卡。服务器不得把 JPEG 发送到该端点；若要使用 Demo，必须显式设置 `protocol=waveshare_dataup`，确认固件已切换到与 310B 相同的 STA 网络，或先修改/替换固件。Case7 的适配器会在请求内存中把渲染 JPEG 转成 BMP，并且不会静默回退或自动探测端点。

## 🔒 错误与隐私

响应不得泄露真实文件路径、内部注册信息或未授权的模型资产。`304` 响应不重新编码；设备被禁用返回不可用状态；非法尺寸、方向、模型或查询返回明确 `4xx`。完整策略和 ETag 输入见 [05-device-policy-rendering-and-security.md](./05-device-policy-rendering-and-security.md)。
