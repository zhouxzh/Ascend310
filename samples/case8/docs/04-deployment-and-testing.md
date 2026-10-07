# case8 部署与测试

## 🖥️ 推荐环境

case8 的硬件相关操作必须在 Ascend 310B 开发板上执行。本地 Windows 工作
区只负责源码、文档和模型准备，不能在本地运行 ACL、ATC、DVPP、VENC 或
`npu-smi`。

| 项目 | 推荐/已验证值 |
| --- | --- |
| 板卡 | Orange Pi AI Pro，或官方 Huawei Atlas，Ascend 310B4 / 8T |
| 板端 IP | Orange Pi：`192.168.1.100`；官方 Atlas：`192.168.1.99` |
| 部署目录 | `/home/HwHiAiUser/Documents/case8` |
| 摄像头 | `/dev/video0`，优先 MJPG |
| 模型输入 | 静态 `1x3x640x640` |
| 默认服务 | `0.0.0.0:8080` |
| 推荐旧系统 | `20241128`；CANN 8.0 的 case8 运行已验证 |

完整版本对照和 VENC 限制见 [问题与版本兼容性](05-known-issues-and-version-compatibility.md)。

## 📷 摄像头权限与重新初始化

官方 Atlas 系统中的 V4L2 节点通常属于 `root:video`，权限为 `0660`。使用
`HwHiAiUser` 启动服务前，必须配置用户组，并在当前板端的实际验证流程中重新
插拔摄像头：

```bash
sudo usermod -aG video HwHiAiUser
# 退出当前 SSH 会话后重新登录
# 重新登录后拔下并插回 USB 摄像头，让 UVC 设备重新初始化
id HwHiAiUser
ls -l /dev/video0 /dev/video1
```

`id` 输出应包含 `video`。重新插拔用于让 UVC 设备重新初始化；在本次官方
Atlas 验证中，缺少这一步会出现首帧等待超时。`/dev/video0` 是实际视频采集
节点，`/dev/video1` 可能是 metadata 节点。不要使用 `chmod 666` 替代用户组
配置，也不要把 metadata 节点作为视频源。

## 📦 同步项目

不要把整个用户目录复制到开发板；只同步源码和已核对的模型伴随文件：

```bash
rsync -av \
  samples/case8/ \
  HwHiAiUser@192.168.1.100:/home/HwHiAiUser/Documents/case8/
```

模型权重、数据集和运行报告按需同步，不用宽泛的 `--delete` 覆盖板端目录。

## 🧪 激活板端环境

```bash
ssh HwHiAiUser@192.168.1.100
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd /home/HwHiAiUser/Documents/case8
```

如果板端使用其他已配置的 conda 环境，只替换 `conda activate`，不要跳过
CANN `set_env.sh`。运行前先检查：

```bash
python -c "import acl; print('acl import ok')"
ls -l models/*.om models/*_labels.txt models/*_metadata.json
```

## 🔬 最小测试顺序

### 1. OM 加载和 benchmark

```bash
python scripts/infer_om_camera.py \
  --benchmark-runs 20 \
  --warmup-runs 5 \
  --print-model-info
```

通过标准：能够加载 OM，打印输入输出信息和延迟统计；这只验证 ACL 推理。

### 2. 摄像头推理

```bash
python scripts/infer_om_camera.py \
  --source /dev/video0 \
  --camera-width 1280 \
  --camera-height 720 \
  --camera-fps 30 \
  --max-frames 60 \
  --no-window
```

通过标准：处理指定帧数并打印统计信息，退出时释放摄像头和 ACL 资源。

### 3. 摄像头格式检查

```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

优先选择支持 1280x720@30 的 MJPG；程序日志和 `/stats` 中的实际 FourCC
才是最终采集模式，不以请求参数代替实际结果。

### 4. WebRTC 服务

```bash
python scripts/webrtc_om_app.py
```

浏览器访问 `http://192.168.1.100:8080`，然后按顺序检查：

1. 页面能加载模型列表。
2. 选择模型、摄像头后端和编码器。
3. 选择 `CANN VENC` 建立视频连接。
4. `/stats` 出现实际编码状态、采集帧率和推理指标。
5. 视频连续运行后正常停止，日志没有未释放资源错误。

服务端接口可用 `curl` 检查：

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/models
curl http://127.0.0.1:8080/stats
```

`/health` 返回 `status=ok` 只证明 HTTP 服务在线；不能替代一次真正的
CANN VENC 视频连接验收。

## 🧰 常用启动参数

```bash
python scripts/webrtc_om_app.py \
  --model models/YOLOv10n_gestures.om \
  --source /dev/video0 \
  --camera-width 1280 \
  --camera-height 720 \
  --camera-fps 30 \
  --infer-every-n 1 \
  --bitrate-kbps 4000
```

`--camera-backend` 可选 `opencv` 或 `dvpp`；`--camera-fourcc` 可选 `MJPG`、
`YUYV` 或 `DEFAULT`；`--conf` 和 `--iou` 控制后处理；模型输入尺寸不在
这些参数中，因为它由 OM 固定。

只有在明确测试 CPU 编码时使用：

```bash
python scripts/webrtc_om_app.py --no-hardware-encode
```

这仍然使用 NPU 做 OM 推理，只替换 H.264 编码器。CANN VENC 失败时程序不
会自动执行这条路径。

## ✅ 验收项目

| 层级 | 通过信号 |
| --- | --- |
| 环境 | CANN 已加载，`import acl` 成功 |
| 模型 | OM 加载、输入输出形状和 benchmark 正常 |
| 摄像头 | 实际 FourCC、分辨率和帧率可见，能处理 60 帧 |
| WebRTC | Offer/Answer 成功，浏览器收到 H.264 |
| 持续运行 | 视频连续运行，`/stats` 持续更新 |
| VENC 失败路径 | 页面显示明确错误，连接停止，未切换 CPU |

每次测试应保存命令、时间、环境版本、模型哈希和关键日志。不要用 CPU
编码通过替代 CANN VENC 验收。
