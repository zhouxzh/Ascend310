# 运行与操作

## 输入源

`--source` 接受摄像头索引或视频路径：

```bash
python scripts/detection_app.py --source 0
python scripts/detection_app.py --source demo/vtest.avi
```

摄像头默认尝试 V4L2 和 MJPEG；`--camera-profile 1280x720@30` 指定期望分辨率/FPS，但设备可能返回不同值，启动时会打印实际值和不匹配 warning。

## 显示与保存

```bash
# 有桌面显示
python scripts/detection_app.py --device cpu --source demo/vtest.avi

# 无头运行
python scripts/detection_app.py --device cpu --source demo/vtest.avi --no-display

# 保存标注视频
python scripts/detection_app.py --device cpu --source demo/vtest.avi --save output/detection.mp4
```

显示窗口按 `q` 退出；`--no-display` 不创建窗口。输出 writer 使用实际采集 FPS，缺失时回退到处理 FPS。

## 跟踪

```bash
python scripts/tracking_app.py --device npu --source 0
python scripts/tracking_app.py --device cpu --source demo/vtest.avi --track-classes person,bus
```

跟踪入口默认 `--device npu`；没有 OM 或 ACL 时会准备失败并返回非零，不会静默切换到 CPU。需要 CPU 调试时显式传 `--device cpu`。

## 计时含义

状态叠加包含 read、pre、infer、decode、draw 的平均耗时。`FPS` 是处理链与采集 FPS 的较小值，不是 NPU 单独吞吐。计时统计从第一帧开始累计，第一帧包含初始化后的首次推理。

## 安全与资源

- 摄像头输入需要设备权限；
- 输出视频写入用户指定路径，失败时应保留错误；
- 退出时释放 capture、writer、窗口和 backend；
- 当前没有网络服务、认证或远程 API。
