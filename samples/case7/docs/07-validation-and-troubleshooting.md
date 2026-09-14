# Case7 验证与故障排查

_区分本地检查、板端验证、设备协议和真实电子纸验收证据。_

---

本文区分本地检查、板端验证和真实硬件验收，避免把一种成功误认为整个系统成功。

## 本地检查

开发机不运行 CANN、ATC、ACL、OM 或 `npu-smi`：

```bash
python -m unittest discover -s tests -v
python -m py_compile app.py config.py server_config.py photo_index.py smart_selector.py display_policy.py
node --check web/app.js
git diff --check
```

如果本地缺少 `acl`、OpenCV 或 FAISS，应记录为本地环境限制，在板端 conda 环境执行对应检查。

## 板端验收顺序

1. `bash scripts/collect_system_status.sh` 保存硬件、CANN、驱动和内存快照。
2. 检查 `import acl`、FAISS、OpenCV、FastAPI 和 multipart。
3. 检查模型注册表和 OM hash。
4. 启动服务并访问 `/api/health`、`/api/models`、`/api/index/stats`。
5. 上传合成图片，等待任务 `completed`。
6. 确认三个模型各自生成向量，执行中文和英文搜索。
7. 检查触摸屏图库、搜索、上传、下一张、暂停和恢复。
8. 唤醒一台 ESP32，完成探测、profile 验证和注册。
9. 确认首次 PhotoFrame 请求 `200`，相同 ETag 请求 `304`。
10. 执行 E6 dry-run；真实七色电子纸刷新另行验收。

## 常见问题

| 现象 | 检查 | 处理 |
| --- | --- | --- |
| 首页显示没有照片 | `/api/index/stats`、`/api/jobs/{id}` | 先上传并等待 NPU 索引完成，再刷新当前显示 |
| 上传卡住 | 浏览器网络、临时目录和磁盘 | 查看任务状态；确认 `~/Pictures/ai-album/.upload-tmp/` 可写 |
| 上传后没有新增照片 | 任务中的 `duplicates`、`skipped` | 内容哈希重复或图片损坏，不是索引失败 |
| 下一张很慢 | 服务日志、NPU 锁和 JPEG 渲染 | 下一张只选择已建立 embedding 的照片，不应每次请求天气 |
| 模型未准入 | `/api/models` 和 registry | 在板端完成 ONNX、ATC、ACL 准入，不启用 CPU fallback |
| 触摸屏偏移或横向滚动 | kiosk URL 和浏览器缩放 | 使用 `launch_touchscreen_kiosk.sh`，检查显示器原生分辨率 |
| 文件名遮挡照片 | `display.show_filename` | 在设置中关闭水印；控制栏隐藏时水印也隐藏 |
| 查询“房子”没有结果 | 实际图库内容和模型状态 | 先确认图片已索引；中文查询走 Chinese-CLIP，英文查询走 MobileCLIP |
| 发现不到 ESP32 | 唤醒状态、串口 IP、DHCP 租约 | 先实体唤醒，再 mDNS 发现或手工探测确认的 IPv4 |
| 注册显示成功但没有拉图 | 设备状态 `awaiting_pull` | 注册只完成控制面；检查设备固件是否真的启用 URL Rotation |
| 设备睡眠后无法更新 | 深度睡眠和按键行为 | 310B 不能网络唤醒；先按实体键或上电，再等待 HTTP 恢复 |
| Waveshare 方向倒置 | profile、方向和设备安装 | 只使用 `landscape`/`portrait`，检查固定硬件安装补偿，不使用任意角度 |
| E1002 选择竖屏 | profile 合同 | E1002 只允许横屏 |
| E6 dry-run 通过但实屏不刷新 | SPI/GPIO/驱动板接线 | dry-run 不等于实屏验收，单独确认硬件映射和 BUSY 信号 |

## 证据分层

| 验证层 | 结论范围 |
| --- | --- |
| Python/JavaScript 语法 | 代码可解析 |
| API 冒烟 | 服务路由和基本依赖可访问 |
| 模型准入 | 指定板卡、CANN 和输入下 ACL 数值满足门槛 |
| COCO-CN Recall | 固定查询集上的检索质量 |
| P50/P95 | 指定硬件和协议的性能 |
| 触摸屏 UI | 指定显示器和浏览器的交互结果 |
| PhotoFrame HTTP | 设备能完成协议请求和 304 |
| 真实电子纸刷新 | 指定屏幕、固件、接线和电源下的物理结果 |

任何报告缺少硬件、软件版本、输入、命令或 hash 时，只能标记为“待验证”。
