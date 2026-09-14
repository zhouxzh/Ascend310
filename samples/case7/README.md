# 案例 7：智能相册

Case7 把 Orange Pi AIpro / Ascend 310B 作为局域网相册服务器：手机上传照片，310B 使用 NPU 建立语义索引，触摸屏浏览和控制，ESP32 电子相册通过 URL 主动拉取图片。

本文是从零运行的使用手册。系统架构、模型原理和迁移方法见 [`src/experiment/case7.md`](../../src/experiment/case7.md)；工程专题从 [`docs/README.md`](docs/README.md) 开始。

## 1. 能力和边界

支持：

- 手机、电脑浏览器和 310B 触摸屏访问同一 FastAPI 服务；
- 单张、多张和文件夹上传；
- 中文/英文语义搜索和经典相似图搜索；
- 本机照片选择、上一张、下一张、暂停和天气显示；
- Waveshare PhotoPainter 与 Seeed Studio reTerminal E1002 设备管理；
- ETag/304 条件拉图；
- E6 800×480 六色 dry-run。

不承诺：

- 310B 通过网络唤醒深度休眠的 ESP32；
- 用 `photoframe.local` 区分多台同名设备；
- 未确认固件端点上的服务端直接发送；
- E6 dry-run 等同于真实七色电子纸刷新；
- 服务端 CPU fallback 代替 NPU 模型。

服务只建议在可信局域网使用，不要直接暴露到公网。

## 2. 设备和固定参数

| 设备 | profile_id | 尺寸 | 方向 |
| --- | --- | --- | --- |
| Waveshare ESP32-S3-PhotoPainter 7.3 英寸 | `waveshare_photopainter_73` | 800×480 | 横屏、竖屏 |
| Seeed Studio reTerminal E1002 | `seeedstudio_reterminal_e1002` | 800×480 | 仅横屏 |
| 310B HDMI 触摸屏 | `local-touchscreen` | 以显示器窗口为准 | 由窗口协商 |

Case7 不提供 360 度或任意安装角度。照片按 EXIF 方向归一化，再按设备 profile 进行横屏/竖屏 cover 裁剪。

## 3. 端口和目录

所有手机、触摸屏和 ESP32 服务端请求统一使用 `7860`：

```text
http://<BOARD_IP>:7860/
```

310B 上的用户照片目录：

```text
~/Pictures/ai-album/imports/
~/Pictures/ai-album/.upload-tmp/
```

照片不放在仓库发布目录。`models/`、`data/`、`photos/` 和 `reports/` 是模型、兼容数据和证据资产目录，不要复制个人照片进去。

## 4. 部署和启动

在开发机进入 Case7 目录，先查看部署白名单：

```bash
cd samples/case7
bash scripts/deploy_ascend8t.sh --ssh-target HwHiAiUser@<BOARD_IP>
```

确认目标后应用部署：

```bash
bash scripts/deploy_ascend8t.sh \
  --ssh-target HwHiAiUser@<BOARD_IP> \
  --apply
```

在 310B 上启动：

```bash
cd /home/HwHiAiUser/Documents/ai-album/current
bash scripts/collect_system_status.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
bash setup.sh board
bash scripts/run_smart_album_service.sh --root "$PWD"
```

启动脚本会检查 `acl`、FAISS、OpenCV、FastAPI、multipart 和模型注册表。不要在没有 CANN 环境的 shell 中直接启动生产服务。

## 5. 检查和重启

```bash
curl http://127.0.0.1:7860/api/health
curl http://127.0.0.1:7860/api/models
curl http://127.0.0.1:7860/api/index/stats
curl -I http://127.0.0.1:7860/
```

重启只操作 Case7 自己的 PID：

```bash
bash scripts/run_smart_album_service.sh --root "$PWD" --stop
bash scripts/run_smart_album_service.sh --root "$PWD"
```

默认日志为 `logs/smart_album.log`，PID 文件为 `run/smart_album.pid`。`Health: Alarm` 只记录为硬件诊断，不能单独视为服务失败。

## 6. 打开手机和触摸屏

手机或电脑浏览器访问：

```text
http://<BOARD_IP>:7860/
```

310B 触摸屏启动 kiosk：

```bash
DISPLAY=:0 XAUTHORITY=/home/HwHiAiUser/.Xauthority \
bash scripts/launch_touchscreen_kiosk.sh
```

触摸屏首页照片优先，触摸照片显示控制栏。8 秒无操作后控制栏和文件名水印一起隐藏。底部功能面板提供图库、智能搜索、上传、设备、设置和系统状态。

## 7. 上传照片

在页面底部选择 **上传**，可以选择一张、多张或一个文件夹，然后点击上传。文件夹中的非图片文件会自动忽略。

也可以用 curl：

```bash
curl -F "files=@/path/to/photo.jpg" \
  http://<BOARD_IP>:7860/api/photos/upload
```

接口会立即返回 `job_id`，进度查询见下一节。

## 8. 上传进度和任务状态

页面同时显示文件传输进度和 NPU 索引进度。接口返回 `job_id` 后轮询：

```bash
curl http://<BOARD_IP>:7860/api/jobs/<job_id>
```

任务状态通常依次为 `queued`、`running`、`completed` 或 `failed`；只有 `completed` 后，图库和首页才会使用新照片。

## 9. 保存位置、去重和删除

上传没有单张字节数和单次张数上限，但单张图片解码像素必须不超过 50 MP。实际总量受浏览器、局域网、磁盘和单线程索引速度限制。

服务按 SHA-256 去重，优先读取 EXIF `DateTimeOriginal`，缺失时使用上传时间。损坏图片、路径逃逸、符号链接逃逸和不支持格式会被拒绝或跳过。

原图保存到 `~/Pictures/ai-album/imports/`，临时上传文件位于 `~/Pictures/ai-album/.upload-tmp/`。删除照片需二次确认，只改变元数据和索引状态，不删除其他原图；服务不生成持久化缩略图、JPEG、EPDGZ 或 E6 帧缓存。

```bash
curl -X DELETE \
  'http://<BOARD_IP>:7860/api/photos/<photo_id>?confirm=true'
```

## 10. 触摸屏图库、搜索和切图

- **图库**：点击缩略图将照片设为本机当前照片。
- **智能搜索**：中文自动使用 Chinese-CLIP，英文自动使用 MobileCLIP；也可以手动选择模型。
- **相似图**：使用 ResNet50 或指定 CLIP 模型，向量空间不会混合。
- **上一张/下一张**：使用本机显示历史或触发下一次 NPU 语义选图。
- **暂停/继续**：只控制本机自动轮播，不影响远端 ESP32。
- **设置**：调整轮播间隔、重复抑制、天气位置和文件名水印。

下一张不会每次请求天气。天气由后台刷新周期控制，切图只使用缓存天气状态。

## 11. 注册 ESP32 电子相册

注册前必须先让设备在线：按设备实体唤醒键或上电，等待串口启动日志出现 STA IPv4，或者从路由器 DHCP 租约读取 IP。深度休眠不能由 310B 网络唤醒。

在设备页执行：

1. 点击“发现局域网电子相册”。
2. 按 IP、硬件 ID、固件信息选择唯一设备。
3. mDNS 不可用时，输入确认的 `http://<ESP32_IP>` 执行探测。
4. 选择 `waveshare_photopainter_73` 或 `seeedstudio_reterminal_e1002`。
5. 点击“验证并注册”。

`photoframe.local` 只是 mDNS 名称，多台设备不能共用它作为唯一注册地址。注册成功只表示 310B 已验证地址、身份和控制配置；设备随后主动请求取图 URL 后，状态才变成 `pulled`。

设备主动拉取地址为：

```text
http://<BOARD_IP>:7860/api/devices/<device_id>/photoframe
```

首次请求返回 `200 image/jpeg`，相同 ETag 返回 `304 Not Modified`。设备被禁用后取图接口返回不可用状态。310B 不保证通过网络唤醒深度休眠设备。

## 12. 设备方向和休眠

- Waveshare 可选择横屏或竖屏；若画面倒置，先检查设备 profile 和固定安装补偿。
- E1002 固定横屏，不能在页面选择竖屏。
- 不使用 360 度旋转选项。
- ESP32 固件是否在休眠后通过按键唤醒，以实际固件行为为准；串口日志是判断设备状态的首选证据。
- URL Rotation 由 ESP32 自己按轮播时隙发起，服务器只按需生成 JPEG。

## 13. 模型、COCO-CN 和 NPU 准入

生产模型必须先完成 ONNX、ATC、ACL 和 hash 准入：

```bash
export HF_ENDPOINT=https://hf-mirror.com
python prepare_models.py download --model all --hf-endpoint https://hf-mirror.com
python prepare_models.py export --model all
python prepare_models.py check --model all
python prepare_models.py validate --model all --admit
```

ATC 和 ACL 只在板端执行，并保持单线程。COCO-CN 固定 500 张测试协议、Recall 和性能记录见 [05-models-and-npu.md](docs/05-models-and-npu.md)。

## 14. E6 dry-run

```bash
python epaper_album.py --photo /path/to/photo.jpg --backend dry-run
```

dry-run 验证 EXIF 旋转、800×480 cover 裁剪、六色量化、4-bit 高半字节优先打包和 192000-byte 帧。没有确认 SPI、GPIO、驱动板和 BUSY 接线前，不得宣称真实电子纸刷新通过。

## 15. 主要故障

| 现象 | 首先检查 |
| --- | --- |
| 首页没有照片 | `/api/index/stats` 和上传任务状态 |
| 下一张无反应 | `/api/display/status`、模型准入和服务日志 |
| 搜索没有结果 | 照片是否完成 NPU 索引、查询语言和模型 ID |
| 设备发现为空 | 设备是否被实体唤醒、串口 IP、DHCP 租约 |
| 注册后没有拉图 | 设备固件是否启用 URL Rotation，状态是否为 `awaiting_pull` |
| 竖屏方向错误 | Waveshare profile、显示方向和安装补偿 |
| E1002 竖屏失败 | E1002 profile 只支持横屏 |
| 电子纸不刷新 | dry-run 与真实硬件验收是两个独立结论 |

## 16. 文档地图

| 主题 | 文档 |
| --- | --- |
| 部署、启动和健康检查 | [01-deployment-and-operation.md](docs/01-deployment-and-operation.md) |
| 照片、索引和生命周期 | [02-photo-library-and-index.md](docs/02-photo-library-and-index.md) |
| 触摸屏和本机显示 | [03-touchscreen-and-display.md](docs/03-touchscreen-and-display.md) |
| ESP32 设备发现和注册 | [04-esp32-device-management.md](docs/04-esp32-device-management.md) |
| 模型和 NPU 准入 | [05-models-and-npu.md](docs/05-models-and-npu.md) |
| API 调用 | [06-api-reference.md](docs/06-api-reference.md) |
| 测试和故障排查 | [07-validation-and-troubleshooting.md](docs/07-validation-and-troubleshooting.md) |
| 理论教程 | [`src/experiment/case7.md`](../../src/experiment/case7.md) |
