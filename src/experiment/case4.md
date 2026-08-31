# 04 - Ascend 310B4 掌纹识别工作台手动测试教程

## 1. 案例目标

本案例在 Ascend 310B4 / 8T 开发板上运行 FastAPI + React 掌纹识别工作台。生产推理固定使用 NPU `mixed_fp16`；CPU、EDCC、origin 和离线候选比较不进入在线接口。

本版本是冻结的人工测试发布。CCNet 为默认/回滚模型；`manual_test` profile 可逐一测试五个 CompNet。五个 CompNet 的状态为 `manual_test_pending`，不能把人工测试前的状态写成稳定验收通过。

## 2. 手动环境

在开发板同一个 shell 中执行：

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

源码不会自动安装依赖、下载数据、构建 EDCC、转换模型或修改系统环境。OM 从固定来源取得后，必须按 `om_manifest.json` 核对字节数和 SHA-256。

## 3. API 和界面

启动后检查 `/api/health`、`/api/bootstrap`、`/api/candidates` 和 `/`。manual_test profile 的 bootstrap 应返回 CCNet 与五个 canonical CompNet，均为 `npu/mixed_fp16`。

触摸屏逐页检查：系统状态、上传识别、模板注册、候选审计。测试 1280×720、1024×768 和 375×812；按钮不重叠，状态文本不溢出，API 不可用时显示空服务状态。

## 4. 六模型人工测试

canonical ID 为：

- `ccnet`
- `compnet_tongji_600`
- `compnet_iitd_460`
- `compnet_rest_358`
- `compnet_xjtu_flash_200`
- `compnet_xjtu_natural_200`

每个模型执行：上传合成 ROI 或匿名测试图、识别、注册三个模板、查询、命中识别、删除、重启后再查询。模板 namespace 固定为 `<model_id>__npu__mixed_fp16`。

每次记录模型 ID、OM SHA、请求时间、HTTP 状态、模型耗时、模板状态和服务 PID。当前内测版本会把用户主动触发的原图、ROI 和 JSON 索引自动保存到发行目录的 `data/captures/`，把明文 `PWST1` embedding 模板保存到 `data/templates/`；连续预览帧不保存。删除模板不会删除图片证据。真实掌纹图像、姓名、embedding 和完整报告不得进入源码或截图，也不得同步到 GitHub/HF。

查看和清理内测图片由操作者手动执行：

```bash
find "$PALMPRINT_ROOT/data/captures" -type f
rm -rf "$PALMPRINT_ROOT/data/captures"
rm -rf "$PALMPRINT_ROOT/data/templates"
```

## 5. 摄像头测试

先检查 `/api/cameras`，优先选择 1280×720 MJPG。连续预览 100 帧，记录首帧时间、稳定帧间隔和实际输出尺寸；拍照识别后关闭预览，确认设备释放，再重新打开一次。若画面滞后，先降低预览分辨率并确认没有其他进程占用设备。

## 6. 常见问题

| 现象 | 处理 |
| --- | --- |
| `import acl` 失败 | 重新加载同一 shell 的 conda/CANN，确认 Python 和 `acl.__file__` 来自同一环境 |
| 模型不在列表 | 确认 `PALMPRINT_PROFILE=manual_test`、registry、OM SHA 和 bootstrap |
| 模板不存在 | 确认注册和识别使用同一模型、精度和当前发行目录的 `data/templates` |
| 模板文件损坏 | 停止服务，备份 `data/templates`，再使用 `.pstore.bak`/`.previous` 恢复 |
| 摄像头卡顿 | 检查 MJPG、实际分辨率和设备占用，先使用 960×540 或 1280×720 |
| 服务健康但识别失败 | 执行真实上传或摄像头 smoke；health 不主动加载 NPU |
| ACL 异常退出、设备 reset 或进程意外退出 | 停止测试，保存时间/PID/模型/摘要，重启当前版本或回退 CCNet；不要升级版本 |
| `Health: Alarm` | 只记录设备状态，不单独判断模型或程序结果 |

## 7. 版本冻结

人工测试期间不升级 Python、依赖、驱动、CANN 或应用版本。发现问题时停止当前模型、记录复现步骤、回退 CCNet，并在人工测试结束后单独规划下一版本。人工测试通过后，再更新准入证据、release manifest、版本号和完整验收记录。
