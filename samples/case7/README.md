# Ascend 310B 智能相册服务器

*Case7 的可执行运行手册；系统架构、模型原理和迁移方法见 [完整理论教程](../../src/experiment/case7.md)。*

---

## 🚀 项目能力

- Orange Pi AIpro / Ascend 310B4 上的 NPU-only FastAPI 相册服务器
- 手机上传、中文/英文语义搜索、配置、图库和设备管理
- 10 寸 QDtech MPI1001 触摸屏照片优先界面
- MobileCLIP-S0、Chinese-CLIP RN50、ResNet50 三个隔离向量空间
- Waveshare ESP32-S3-PhotoPainter 与 Seeed Studio reTerminal E1002 的受管理 JPEG URL 拉取与 ETag 条件下载
- E6 800x480 六色 192000-byte dry-run

### 固定电子相册设备 profile

Case7 当前只记录以下两种 7.3 英寸终端：

| 设备 | 官方规格 | 允许方向 |
| --- | --- | --- |
| [Waveshare ESP32-S3-PhotoPainter](https://www.waveshare.com/product/displays/e-paper/epaper-1/esp32-s3-photopainter.htm) / [Wiki](https://www.waveshare.com/wiki/ESP32-S3-PhotoPainter) | E6 六色（黑、白、绿、蓝、红、黄），800x480；官方 Wiki Mode 1 接受 800x480 或 480x800 图像 | `landscape`、`portrait` 内容 |
| [Seeed Studio reTerminal E1002 Wiki](https://wiki.seeedstudio.com/getting_started_with_reterminal_e1002/) | ACeP / E Ink Spectra 6 全彩，800x480 | Case7 策略仅 `landscape` |

设备 profile 只使用 `landscape` 或 `portrait`。不提供、不记录 360°、180°、90°/270°安装旋转选项；E1002 请求 `portrait` 必须拒绝。Seeed 的厂商资料确认的是固定 `800x480` 面板，E1002 的横屏限制是 Case7 当前设备策略，不是对其固件能力的推断。上述规格也不代表当前固件协议或真实面板刷新已经验收。管理 API 注册时必须明确提交 `profile_id`，不会把缺少型号的历史记录自动标成 Waveshare。

服务不回退 CPU/PyTorch 推理；CPU 仅用于解码、OpenCV Haar 人数计数、FAISS、JPEG/E6 准备和离线 ONNX 参考。服务只建议在可信局域网运行，禁止公网暴露。

## 🧪 本机检查

开发机不运行 CANN、ATC、ACL、OM 推理或 `npu-smi`：

```bash
python -m unittest discover -s tests -v
python -m py_compile app.py server_config.py photo_index.py smart_selector.py
node --check web/app.js
git diff --check
```

## 🖥️ 板端部署

目标板：`HwHiAiUser@192.168.1.135`，目录：`/home/HwHiAiUser/Documents/ai-album`。

```bash
bash scripts/deploy_ascend8t.sh --ssh-target HwHiAiUser@192.168.1.135
bash scripts/deploy_ascend8t.sh --ssh-target HwHiAiUser@192.168.1.135 --apply
```

发布脚本使用 `releases/<release-id>`、`current` 和 `shared`，不使用 `--delete`，不覆盖模型、数据、照片或报告。

板端服务：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd /home/HwHiAiUser/Documents/ai-album/current
bash setup.sh board
bash scripts/run_smart_album_service.sh --root "$PWD"
curl http://127.0.0.1:7860/api/health
```

`Health: Alarm` 只作诊断记录；实际 PyACL、模型、服务和 API 失败才是阻断项。

## 📱 手机和触摸屏

手机打开 `http://192.168.1.135:7860/`。触摸屏 kiosk：

```bash
DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority \
bash scripts/launch_touchscreen_kiosk.sh
```

触摸屏首页显示当前照片、天气和紧凑工具栏。默认本机选图间隔为 60 秒，电脑浏览器每 30 秒轮询一次元数据；ETag 未变化时不会重新下载或编码 JPEG。文件名位于工具栏内，可在设置页关闭水印；8 秒无操作时工具栏和文件名一起隐藏。电子纸物理刷新单独默认为 30 分钟，可在设置中改为 10 分钟。详细操作见 [07-touchscreen-ui-and-operations.md](docs/07-touchscreen-ui-and-operations.md)。

设备页将两类显示终端分开管理：**本机触摸屏相册**是固定身份为 `local-touchscreen` 的 HDMI 虚拟设备，可单独设置名称、启停、换图间隔、显示方向、重复抑制和文件名水印；它不需要 IP、设备令牌或网络轮询。**ESP32 电子相册**使用“验证并注册 ESP32 设备”创建远端 PhotoFrame/电子纸设备，传输方式固定为“设备主动拉取”。注册时必须填写串口日志得到的 ESP32 地址；310B 会先访问设备网页、核验 PhotoFrame 身份和型号、写入并读回 URL Rotation 配置，然后返回 HTTP `202`（`registration_status=awaiting_pull`）。任一步失败都返回“未注册”，不会留下孤立的设备记录。`202` 只表示“310B 已验证设备地址并完成控制面配置”，不等于电子纸已经完成物理刷新；只有设备随后带官方固件/显示能力请求访问取图 URL，卡片才显示“设备已拉图”。若设备网页不支持该 API，必须先刷入或修改明确支持 URL Rotation 的固件。两类设备不会相互覆盖设置。远端卡片中的“禁用设备”是可恢复停用；“删除注册”需两次确认并移除设备记录及轮播状态，但不会删除任何照片。

## 📤 首次上传照片

新系统或刚重新烧录的开发板不会自动拥有照片；看到“相册还没有可显示的照片”是正常的空库状态。首次导入可直接用手机或触摸屏完成：

1. 在同一局域网打开 `http://192.168.1.135:7860/`，触摸屏则打开 kiosk 页面。
2. 点击底部 **上传**，选择一张/多张 `JPG/JPEG/PNG/BMP/WebP` 照片，或直接选择一个文件夹；文件夹中的非图片文件会自动忽略。
3. 直接点击 **上传并建立 NPU 索引**。服务优先读取照片 EXIF 中的拍摄时间，缺失时使用服务器上传时间；常规上传不要求也不提供手工标签。
4. 观察“文件传输”和“NPU 索引”两个进度条，等待任务显示 `completed` 后再打开 **图库**；如果首页仍为空，点击 **下一张/刷新推荐** 触发首张选择。索引条会依次显示哈希、受管导入、图片校验、各模型编码和 FAISS 收尾，最终以 `status` 和任务摘要为准。

普通网页/API 上传不设置单张文件大小或单次张数上限；仍保留单张解码像素不超过 50 MP 的内存保护，并要求图片能够正常解码。实际可上传总量受浏览器、局域网带宽、服务器磁盘和单线程索引速度限制；前端会自动分批提交大文件夹。任务完成后请检查摘要中的 `indexed`、`duplicates`、`skipped` 和 `photo_ids`，因为损坏或重复文件不一定新增照片。服务器按内容 SHA-256 去重，并把新原图保存到系统用户目录 `~/Pictures/ai-album/imports/`，上传临时文件位于 `~/Pictures/ai-album/.upload-tmp/`；这两个目录不在仓库发布目录内。`shared/photos/` 仅保留 COCO-CN 和旧版本数据供兼容读取，不要把个人照片直接复制到那里，也不会生成持久化缩略图或 JPEG 缓存。默认上传任务使用三个已准入 NPU 模型建立 embedding；如果模型检查未通过，任务会显示 `failed` 和具体错误，不会回退到 CPU。

没有浏览器时也可以用命令行上传。接口返回 `job_id` 后查询任务，直到状态为 `completed`：

```bash
curl -F "files=@/path/to/photo.jpg" \
  http://192.168.1.135:7860/api/photos/upload
curl http://192.168.1.135:7860/api/jobs/<job_id>
```

若页面仍为空，先检查 `GET /api/index/stats` 返回的 `available_photos`，再检查 `GET /api/display/current`；若任务为 `failed`，查看响应中的 `error` 字段。`400` 通常表示格式、空请求或不支持的扩展名；若前置代理返回 `413`，则是代理自身的请求体策略，不是 Case7 上传接口的限制。历史数据库中的 `tags` 字段仅为迁移兼容元数据，常规上传不写入它。API 字段和完整示例见 [03-album-server-api-and-esp32-protocol.md](docs/03-album-server-api-and-esp32-protocol.md)，照片生命周期限制见 [09-index-storage-and-photo-lifecycle.md](docs/09-index-storage-and-photo-lifecycle.md)。

## 🖼️ PhotoPainter 五分钟 URL 拉取测试

当前实测目标是新的 Waveshare ESP32-S3-PhotoPainter。设备地址必须先从串口启动日志读取；
`photoframe.local` 只是可选 mDNS 别名，打不开时直接使用串口日志中的 IPv4 地址。完整步骤见
[PhotoPainter 串口读取 IP 与 Wi-Fi 配网](docs/13-photopainter-serial-ip-and-wifi.md)。

Case7 的设备注册和设备页只提供一种远端传输方式：**设备主动 URL 拉取**。310B 为已注册设备
生成 `/api/devices/<id>/photoframe`，PhotoFrame 在自己的 URL Rotation 时隙发起 `GET`，服务器
返回 JPEG、ETag 和 `304 Not Modified`。310B 不扫描局域网或猜测设备地址。

在设备页的 **验证并登记** 区域填写串口日志得到的 ESP32 IPv4，例如
`http://192.168.1.137`，再点击 **验证并登记**。310B 只会对 RFC1918 私网 IPv4 的
80 端口执行一次受限操作：读取 `/api/system-info` 验证官方 PhotoFrame、读取并写入
`/api/config`、读取回配置核验，然后请求 `/api/rotate`。它会写入 `auto_rotate=true`、
`rotation_mode=url`、本设备的图片 URL，并在首次联调时关闭深度睡眠。成功配置不等于屏幕已刷新；
只有设备随后携带固件版本和显示能力头访问取图 URL，页面才显示 **设备已拉图**。KEY 只能唤醒
设备或重置睡眠计时，不能替代这一配置步骤。

Seeed Studio reTerminal E1002 只保留为历史对照，使用时必须显式填写
`--profile-id seeedstudio_reterminal_e1002`，不能套用当前 PhotoPainter 的地址或固件结论。

测试批次固定为电脑目录中按文件名排序的 `CIMG2780.JPG` 至 `CIMG2799.JPG`；先将这 20 张图片复制到板端受管的 `shared/incoming/photoframe-test/`，再在板端执行：

```bash
cd /home/HwHiAiUser/Documents/ai-album/current
bash scripts/setup_photoframe_test.sh \
  --source shared/incoming/photoframe-test \
  --limit 20 \
  --profile-id waveshare_photopainter_73 \
  --server-url http://127.0.0.1:7860 \
  --public-server-url http://192.168.1.135:7860
```

脚本按文件名排序校验 20 张图片，等待串行 NPU 索引任务完成，创建或更新 PhotoFrame 播放列表，并将 `*/5 * *` 和首张选择写入 `shared/reports/`。随后在设备卡片执行 **验证并登记**；该操作会把同一轮播计划写入已确认的 PhotoFrame，并返回“已验证配置 · 等待设备主动拉图”。无法从 310B 访问 ESP32 时，只能在 PhotoFrame 本机网页保存下面的同一配置进行独立排障，不能把它当作 Case7 已验证注册：

```json
{
  "auto_rotate": true,
  "rotate_cron": ["*/5 * *"],
  "rotation_mode": "url",
  "image_url": "http://192.168.1.135:7860/api/devices/<device_id>/photoframe",
  "deep_sleep_enabled": false
}
```

`<device_id>` 必须替换为 Case7 设备卡片显示的注册 ID，不是 ESP32 的硬件 ID。URL Rotation 使用同一时隙的 `304`；首次请求应返回 `200 image/jpeg`。协议、固件识别、串口取 IP 和刷写边界见 [04 PhotoPainter 接入](docs/04-photopainter-7in3-integration.md)、[13 PhotoPainter 串口读取 IP 与 Wi-Fi 配网](docs/13-photopainter-serial-ip-and-wifi.md) 与 [esp32/README.md](esp32/README.md)。脚本不创建图片缓存，也不会删除受管原图。

### 历史兼容接口

仓库仍保留旧式服务器主动发送和 Waveshare Demo `POST /dataUP` 的兼容实现，用于已有记录或
维护排障；它们不出现在“注册 ESP32 设备”页面，也不属于上述五分钟轮播流程。需要审计历史
接口时参见 [11 PhotoFrame 主动推送与固件协议](docs/11-photoframe-active-push.md)，并先确认实际
固件与端点。该兼容路径没有作为当前 PhotoPainter 的屏幕刷新验收结论。

运行前应确认 `/api/health` 为 `ready` 且三个模型均为 `admitted`；自动索引没有准入模型时会明确失败，不会回退到 CPU 或把人脸/元数据处理冒充 embedding。

## 🧠 模型与 COCO-CN

模型和数据资产不提交 Git。下载使用 HF 镜像，ATC 必须单线程：

```bash
export HF_ENDPOINT=https://hf-mirror.com
python prepare_models.py download --model all --hf-endpoint https://hf-mirror.com
python prepare_models.py export --model all
python prepare_models.py check --model all
```

板端转换和准入见 [08-model-pipeline-and-npu-admission.md](docs/08-model-pipeline-and-npu-admission.md)。COCO-CN 固定 500 张测试协议见 [01-coco-cn-test-protocol.md](docs/01-coco-cn-test-protocol.md)。

## 📚 工程文档

- [00 GitHub 参考与文档地图](docs/00-github-research-and-porting-plan.md)
- [01 COCO-CN 固定测试协议](docs/01-coco-cn-test-protocol.md)
- [02 板端部署与验收](docs/02-ascend310b4-deployment-and-acceptance.md)
- [03 API 与 ESP32 协议](docs/03-album-server-api-and-esp32-protocol.md)
- [04 PhotoPainter 接入](docs/04-photopainter-7in3-integration.md)
- [05 设备策略、渲染与安全](docs/05-device-policy-rendering-and-security.md)
- [06 PhotoPainter 部署与验收](docs/06-photopainter-deployment-and-acceptance.md)
- [07 触摸屏 UI 与操作](docs/07-touchscreen-ui-and-operations.md)
- [08 模型流水线与 NPU 准入](docs/08-model-pipeline-and-npu-admission.md)
- [09 索引与照片生命周期](docs/09-index-storage-and-photo-lifecycle.md)
- [10 智能选图与天气](docs/10-smart-selection-and-weather.md)
- [11 PhotoFrame 主动推送与固件协议](docs/11-photoframe-active-push.md)
- [12 MobileCLIP 8T/20T 跨板兼容性验证](docs/12-mobileclip-cross-board-compatibility.md)
- [13 PhotoPainter 串口读取 IP 与 Wi-Fi 配网](docs/13-photopainter-serial-ip-and-wifi.md)

完整理论教程包含架构图、模型结构图、NPU 迁移方法、数据流、代码导读、测试方法和限制说明： [src/experiment/case7.md](../../src/experiment/case7.md)。
