# Case7 HTTP API

_面向手机、触摸屏和 ESP32 客户端的当前 HTTP 路由参考。_

---

服务默认监听 `http://<BOARD_IP>:7860`。本文只记录当前代码提供的接口；请求体和响应字段以运行中的 OpenAPI 与测试为最终依据。

## 系统状态

```bash
curl http://<BOARD_IP>:7860/api/health
curl http://<BOARD_IP>:7860/api/models
curl http://<BOARD_IP>:7860/api/index/stats
curl http://<BOARD_IP>:7860/api/device-profiles
```

| 路由 | 方法 | 用途 |
| --- | --- | --- |
| `/api/health` | GET | NPU、模型和服务健康状态 |
| `/api/models` | GET | 模型 ID、维度和准入状态 |
| `/api/index/stats` | GET | 照片、人脸和 embedding 统计 |
| `/api/device-profiles` | GET | 两类 ESP32 固定 profile |

## 照片和任务

```bash
curl http://<BOARD_IP>:7860/api/photos
curl -F "files=@/path/photo.jpg" http://<BOARD_IP>:7860/api/photos/upload
curl http://<BOARD_IP>:7860/api/jobs/<job_id>
curl http://<BOARD_IP>:7860/api/photos/<photo_id>/preview
curl http://<BOARD_IP>:7860/api/photos/<photo_id>/file
curl -X DELETE 'http://<BOARD_IP>:7860/api/photos/<photo_id>?confirm=true'
```

| 路由 | 方法 | 关键字段 |
| --- | --- | --- |
| `/api/photos` | GET | `face_filter`、`limit` |
| `/api/photos/upload` | POST | multipart `files`，可多文件 |
| `/api/jobs/{job_id}` | GET | `status`、进度、`photo_ids`、错误 |
| `/api/photos/{id}/preview` | GET | `width`、`height`，内存 JPEG |
| `/api/photos/{id}/file` | GET | 原图下载 |
| `/api/photos/{id}` | DELETE | `confirm=true` 才执行 |

## 语义搜索

```bash
curl -X POST http://<BOARD_IP>:7860/api/search/text \
  -H 'Content-Type: application/json' \
  -d '{"query":"雪景","model":"auto","top_k":12}'
```

请求支持 `query`、`model`、`top_k`、尺寸过滤和人脸过滤。`model=auto` 对中日韩文字选择 Chinese-CLIP，其他文本选择 MobileCLIP。响应中的每个结果包含 `photo_id`、文件名、得分、模型 ID 和预览 URL。

## 配置

```bash
curl http://<BOARD_IP>:7860/api/config
curl -X PATCH http://<BOARD_IP>:7860/api/config \
  -H 'Content-Type: application/json' \
  -d '{"revision":1,"display":{"show_filename":false}}'
```

配置使用 `revision` 乐观锁。revision 不匹配返回 `409`；未知字段、错误类型或未准入模型返回 `400`。需要重启的 E6 参数会在响应中标记 `restart_required`。

## 本机显示

```bash
curl http://<BOARD_IP>:7860/api/display/current
curl -X POST http://<BOARD_IP>:7860/api/display/select \
  -H 'Content-Type: application/json' -d '{"photo_id":123}'
curl -X POST http://<BOARD_IP>:7860/api/display/control \
  -H 'Content-Type: application/json' -d '{"action":"pause"}'
curl -X POST http://<BOARD_IP>:7860/api/display/control \
  -H 'Content-Type: application/json' -d '{"action":"resume"}'
curl http://<BOARD_IP>:7860/api/display/status
```

`next`、`pause`、`resume` 会持久化到本机显示状态。`/api/display/content` 是按需生成的 JPEG 或 E6 dry-run 内容接口。

## 设备管理

只读发现和探测：

```bash
curl http://<BOARD_IP>:7860/api/admin/devices/discover
curl -X POST http://<BOARD_IP>:7860/api/admin/devices/probe \
  -H 'Content-Type: application/json' \
  -d '{"device_url":"http://<ESP32_IP>"}'
```

验证并注册：

```bash
curl -X POST http://<BOARD_IP>:7860/api/admin/devices/register \
  -H 'Content-Type: application/json' \
  -d '{
    "device_url":"http://<ESP32_IP>",
    "profile_id":"waveshare_photopainter_73",
    "name":"客厅相册",
    "display":{"orientation":"landscape"}
  }'
```

管理和状态：

| 路由 | 方法 | 用途 |
| --- | --- | --- |
| `/api/admin/devices` | GET | 列出本机和远端设备 |
| `/api/admin/devices/{id}` | PATCH | 更新名称、启停、profile、策略 |
| `/api/admin/devices/{id}` | DELETE | 删除注册，必须带 `confirm=true` |
| `/api/admin/devices/{id}/state` | GET | 当前照片、时隙、ETag 和拉取状态 |
| `/api/admin/devices/{id}/playlist` | POST | 设置播放列表和 cron |
| `/api/admin/devices/{id}/advance` | POST | 手动推进一张 |
| `/api/admin/touchscreen` | GET/PATCH | 配置本机触摸屏 |

删除远端注册前必须显式确认；只删除设备记录和显示状态，不删除照片：

```bash
curl -X DELETE 'http://<BOARD_IP>:7860/api/admin/devices/<device_id>?confirm=true'
```

## ESP32 拉图接口

```bash
curl -i http://<BOARD_IP>:7860/api/devices/<device_id>/manifest
curl -i http://<BOARD_IP>:7860/api/devices/<device_id>/content
curl -i http://<BOARD_IP>:7860/api/devices/<device_id>/photoframe \
  -H 'If-None-Match: "previous-etag"' \
  -H 'X-Display-Width: 800' \
  -H 'X-Display-Height: 480' \
  -H 'X-Display-Orientation: landscape'
curl -X POST http://<BOARD_IP>:7860/api/devices/<device_id>/heartbeat \
  -H 'Content-Type: application/json' -d '{"status":"displayed"}'
```

| 路由 | 方法 | 用途 |
| --- | --- | --- |
| `/api/devices/{id}/manifest` | GET | 返回设备 profile、当前 revision 和取图 URL |
| `/api/devices/{id}/content` | GET | 按设备能力返回 JPEG 或 E6 内容 |
| `/api/devices/{id}/photoframe` | GET | PhotoFrame URL Rotation JPEG，支持 ETag/304 |
| `/api/devices/{id}/heartbeat` | POST | 写入设备在线和显示状态 |

PhotoFrame 请求返回 JPEG、ETag、`X-Config-Payload` 和私有缓存语义。相同 ETag 返回 `304`；没有可用照片、设备被禁用或 profile 不匹配时返回 `404` 或 `400`。

设备接口不把服务器真实文件路径返回给客户端。设备 profile、方向和启停状态由管理接口维护。
