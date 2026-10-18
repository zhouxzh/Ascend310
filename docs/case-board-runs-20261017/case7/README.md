# case7 双板调试记录

## 运行

- 部署目录：`/home/HwHiAiUser/Documents/ai-album/case7`
- 启动脚本：`bash scripts/run_smart_album_service.sh --root "$PWD" --host 0.0.0.0 --port 7860`
- 页面：`http://<board>:7860/`
- active 模型：MobileCLIP-S0、Chinese-CLIP RN50、ResNet50 visual similarity

## 结果

| 项目 | 8T | 20T |
| --- | --- | --- |
| `/api/health` | `status=ready`，`backend=npu` | `status=ready`，`backend=npu` |
| PyACL | 可用 | 可用 |
| admitted models | 3 个 | 3 个 |
| `/api/index/stats` | 0 张照片，空索引 | 0 张照片，空索引 |
| 电子纸后端 | `dry-run` | `dry-run` |
| 页面截图 | 通过 | 通过 |

两板模型注册和页面渲染通过；由于相册没有照片，照片上传、NPU 索引、搜索、电子纸刷新和 PhotoFrame 设备链路均未执行。`dry-run` 不是实体电子纸刷新通过。

## 截图

- [8T 智能相册](board-8t.png)
- [20T 智能相册](board-20t.png)

## 证据

- [8T 服务日志](board-8t.log)
- [20T 服务日志](board-20t.log)
- [health/models/index 和截图原始结果](capture.json)
