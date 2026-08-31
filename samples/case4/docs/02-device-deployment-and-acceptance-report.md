# 02 - 设备部署与人工功能验收

## 1. 设备边界

目标设备为 Ascend 310B4 / 8T。服务通过手动激活 conda/CANN 后启动，板端不需要 Node.js，也不会自动安装依赖、下载数据、转换模型或构建 EDCC。

源码包和板端资产包分开同步。OM、数据集、模板和原始报告不进入 Git；同步源码时不使用 `rsync --delete`。

## 2. 手动启动

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd <release-root>
export PALMPRINT_ROOT="$PWD"
export ACL_PYTHON_SITE=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages
export PYTHONPATH="$PALMPRINT_ROOT:$ACL_PYTHON_SITE${PYTHONPATH:+:$PYTHONPATH}"
export PALMPRINT_PROFILE=manual_test
unset PALMPRINT_REQUIRE_TEMPLATE_ENCRYPTION
unset PALMPRINT_TEMPLATE_KEY_FILE
unset PALMPRINT_TEMPLATE_DIR
python -c "import sys, acl; print(sys.executable); print(acl.__file__)"
python -m palmprint_workbench.tools.verify_assets --strict
python -m palmprint_workbench.api --host 0.0.0.0 --port 7860
```

启动后检查：

```bash
curl --fail http://127.0.0.1:7860/api/health
curl --fail http://127.0.0.1:7860/api/bootstrap
curl --fail http://127.0.0.1:7860/api/candidates
curl --fail http://127.0.0.1:7860/
```

`bootstrap` 在 manual_test profile 应返回 CCNet 和五个 CompNet，均为 `npu/mixed_fp16`；CompNet 的状态必须包含 `manual_test_pending=true`。

## 3. HTTP 和模板功能

逐模型执行以下闭环：

1. 选择 canonical model ID。
2. 上传合成 ROI 或匿名测试图并执行识别。
3. 注册三个样本，查询模板，使用同一模型识别命中。
4. 删除模板并确认列表为空。
5. 重启服务后重新检查模板状态。

模板 namespace 必须为 `<model_id>__npu__mixed_fp16`。当前内测版本固定在发行目录保存明文模板和图片：`data/templates/` 保存 `PWST1` embedding 模板，`data/captures/` 保存用户主动触发的原图、ROI 和 `index.json`。连续预览帧不落盘；删除模板只删除 embedding，关联图片仍保留。当前版本不读取密钥或外部模板目录，不适用于生产部署。

检查归档：

```bash
find "$PALMPRINT_ROOT/data/captures" -type f
```

同步源码时必须排除 `data/` 和 `reports/`。切换发行目录后若要继续内测，人工复制整个 `data/` 目录；程序不会自动迁移或删除历史图片。

## 4. 触摸屏和前端

在 1280×720、1024×768、375×812 三种尺寸检查：

- 系统状态、上传识别、模板注册和候选审计四页均能打开；
- 模型切换后名称、精度和状态同步；
- 操作按钮不重叠，长状态文本不越界；
- 刷新、返回、关闭和错误提示可触摸操作；
- API 不可用时显示空服务状态，不显示伪造模型。

## 5. 摄像头

通过 `/api/cameras` 确认节点和实际分辨率，优先使用 1280×720 MJPG。连续预览至少 100 帧，记录首帧时间、稳定帧间隔和实际输出尺寸；拍照识别后关闭预览，确认设备释放，再重新打开一次。摄像头切换、分辨率切换和浏览器刷新必须验证新的预览会话不会被旧请求关闭；物理热插拔后还要验证设备列表刷新、句柄自动重建和再次打开。

## 6. 人工测试记录

每个模型单独记录：

| 字段 | 内容 |
| --- | --- |
| model_id | canonical ID |
| OM SHA-256 | 与 `om_manifest.json` 一致 |
| 时间 | 请求开始和结束时间 |
| HTTP | 状态码和脱敏响应摘要 |
| 识别 | 命中/未命中、模型耗时 |
| 模板 | 注册、查询、删除、重启恢复 |
| 摄像头 | 首帧、稳定帧、关闭释放 |
| 进程 | 是否保持运行 |

使用合成 ROI 或匿名测试图，不保存真实掌纹图像到源码仓库。

## 7. 常见问题

- PyACL 不可用：重新加载同一 shell 的 conda/CANN，确认 `import acl`。
- 模型不在列表：确认 `PALMPRINT_PROFILE=manual_test`、registry、OM SHA 和 bootstrap。
- 模板不存在：确认注册和识别使用同一模型、精度、key 和模板目录。
- 摄像头卡顿：确认 MJPG、实际分辨率和设备占用，先降低预览分辨率。
- ACL 异常退出、设备 reset 或服务意外退出：停止当前测试，保存时间/PID/模型/报告摘要，重启当前版本或回退 CCNet；不升级版本。
- `Health: Alarm`：仅记录设备状态，不单独判断模型或程序结果。

## 8. 当前阶段结论

本文件定义人工测试方法，不宣称五个 CompNet 已通过稳定性或跨域正式验收。当前版本冻结；人工测试出现问题时先记录和回退，下一版本另行规划。
