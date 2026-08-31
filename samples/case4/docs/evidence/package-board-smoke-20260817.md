# 板端源码包冒烟与人工测试记录

此文件是人工测试记录模板和部署边界说明，不是完整设备日志。完整日志、真实图像、模板和运行报告保留在板端私有目录。

## 当前版本

- 设备：Ascend 310B4 / 8T
- profile：`manual_test`
- 默认模型：`ccnet`
- 可测试模型：CCNet + 五个 canonical CompNet
- 精度：NPU `mixed_fp16`
- 模型资产：按 `om_manifest.json` 固定 revision、字节数和 SHA-256 核验

## 记录表

| 项目 | 状态 | 备注 |
| --- | --- | --- |
| `import acl` | 待人工记录 | 使用板端 conda/CANN shell |
| `/api/health` | 待人工记录 | 需记录 template/model readiness |
| `/api/bootstrap` | 待人工记录 | 应包含六个 NPU/mixed_fp16 模型 |
| 前端四页 | 待人工记录 | 1280×720、1024×768、375×812 |
| 六模型上传识别 | 待人工记录 | 使用合成 ROI 或匿名测试图 |
| 模板注册/查询/删除 | 待人工记录 | 使用外部加密 key |
| 摄像头 100 帧 | 待人工记录 | 记录实际分辨率和设备释放 |

## 通用故障边界

服务异常退出、ACL 错误、设备 reset、模板损坏和摄像头无法释放都按 `docs/04-release-checklist.md` 处理：停止当前测试、保存脱敏摘要、回退 CCNet、冻结版本，不在本版本内升级或修复。设备健康告警只记录，不单独判定模型结果。

## 隐私

禁止把真实掌纹图像、姓名、embedding、模板 key、完整 dmesg 或板端报告复制到源码包、截图或公开仓库。
