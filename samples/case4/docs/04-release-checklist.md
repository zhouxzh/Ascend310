# 04 - 发布与人工测试清单

## 1. 版本状态

- [x] 源码入口为 `palmprint_workbench`。
- [x] 不提供 setup、启动、同步或诊断 shell 包装器。
- [x] 生产接口只使用 NPU/mixed_fp16。
- [x] `manual_test` profile 已定义六个模型。
- [ ] 五个 CompNet 完成人工功能验收。
- [ ] 五个 CompNet 完成后续稳定性和生产准入复核。

当前版本冻结。人工测试期间不升级依赖、驱动、CANN、应用版本、APP_VERSION 或 release ID。

## 2. 本地门禁

```bash
python -m compileall -q app.py palmprint_workbench tools
python -m pytest -ra tests
cd frontend
npm ci
npm test
npm run build
cd ..
python -m tools.board.verify_frontend_assets --dist frontend/dist --strict
python -m palmprint_workbench.tools.verify_assets --strict
```

确认 `release_manifest.json`、`om_manifest.json`、registry 和候选清单的哈希与当前源码一致。

## 3. 手动同步

源码包和板端资产包分开同步，使用明确的 `USER@HOST`、版本目录和 `rsync`，不使用 `--delete`。排除 `data/`、模板、报告、缓存、模型构建目录和 `.env`。

板端手动启动：

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

## 4. 启动检查

- [ ] `/api/health` 返回服务、运行时、模型和模板 readiness。
- [ ] `/api/bootstrap` 返回 CCNet 和五个 CompNet，全部为 NPU/mixed_fp16。
- [ ] CompNet 显示 `manual_test_pending=true`。
- [ ] `/api/candidates` 只读，不能启动候选比较任务。
- [ ] 首页和静态 bundle 加载成功。

## 5. 六模型人工测试

每个模型都执行并记录：

- [ ] 模型切换和 canonical ID；
- [ ] 上传合成 ROI/匿名测试图识别；
- [ ] 三个模板注册；
- [ ] 同 namespace 查询和命中；
- [ ] 删除模板并确认为空；
- [ ] 服务重启后的模板状态；
- [ ] OM SHA-256、HTTP 状态、耗时和进程状态。

内测模板是发行目录内的明文 `PWST1` embedding 特征；图片归档位于 `data/captures/`，包含原图、ROI 和 JSON 索引。连续预览帧不保存，删除模板不会删除图片证据。`data/` 和 `reports/` 必须排除在 Git、HF 和源码同步包之外。当前版本不启用加密，也不区分 profile；切换到未来生产版本前应另行设计隐私和密钥策略。

人工测试数据检查与清理：

```bash
find "$PALMPRINT_ROOT/data/captures" -type f
rm -rf "$PALMPRINT_ROOT/data/captures"
rm -rf "$PALMPRINT_ROOT/data/templates"
```

以上删除只允许操作者手动执行，服务不会自动清理历史图片。

## 6. 触摸屏和摄像头

- [ ] 在 1280×720、1024×768、375×812 检查四页布局。
- [ ] 按钮、状态文本、错误提示和刷新操作不重叠或溢出。
- [ ] `/api/cameras` 返回实际节点和分辨率。
- [ ] 1280×720 MJPG 连续预览 100 帧。
- [ ] 拍照识别、关闭预览、设备释放、再次打开均成功。

## 7. 失败处理

遇到识别失败、模板不可见、摄像头无法释放、服务意外退出、ACL 异常、设备 reset、模板损坏或 SHA 不一致：

1. 停止当前模型测试；
2. 保存时间、模型 ID、PID、HTTP 响应和脱敏报告摘要；
3. 不升级任何依赖、驱动、CANN 或应用版本；
4. 回退到 CCNet 并建立问题记录；
5. 等人工测试阶段结束后，再规划下一版本修复。

`Health: Alarm` 只作为设备诊断字段，不能单独判定失败；具体运行时异常按上述流程处理。

## 8. 回滚

只停止确认过的新服务 PID，切换上一份已核验源码目录；不删除模型、数据集、模板或报告。回滚后重新检查 `/api/health`、`/api/bootstrap` 和首页。

## 9. 发布结论

人工测试完成前，本版本只称为“manual test release”。所有结果写入脱敏人工测试摘要；人工通过后才另行更新准入证据、release manifest 和版本号。

## 10. 全候选测试清单

- [ ] `candidate_manifest.json` 28 项均有静态审计记录。
- [ ] 每项已记录固定 revision、许可证、输入输出契约和资产 SHA-256，或明确 `blocked_*`/`not_applicable` 原因。
- [ ] embedding、classifier、ROI、掌静脉和 SDK/代码使用各自 adapter 与质量指标。
- [ ] 具有 OM 的候选在 Ascend 310B4 / 8T 上完成单候选 ACL smoke；不在本地伪造板端结果。
- [ ] 可运行 NPU 候选完成 10 次生命周期和资源回落检查。
- [ ] 记录温度、功耗、NPU 内存、大页、Health、退出码、dmesg 和 LPM/AICore/RAS 增量。
- [ ] `reports/candidates/<candidate-id>/<run-id>/` 产生完整报告，`reports/candidates/index.json` 覆盖 28 项。
- [ ] 生产 registry、前端模型列表和模板 namespace 未因候选测试自动改变。
- [ ] 2026-09-10 staging direct smoke：CCNet 及五个 CompNet 的 OM 校验、有限输出和直接 ACL 生命周期通过；但 CCNet 诊断 trace 发现新增 `err_ret=-512`/driver cleanup，完整诊断门禁阻断，详见 `docs/evidence/candidate-campaign-20260910.md`。
- [x] 2026-09-11 正确加载 CANN 后重测：六个实际运行候选均完成 10/10 独立 ACL load/run/close，输出有限且 reset/finalize 返回 0；六个诊断 trace 均发现同一 `err_ret=-512`/driver cleanup 事件，因此最终诊断准入阻断，未替代数据集质量和性能门禁。

编排入口：

```bash
python -m tools.offline.candidate_campaign inventory --all
python -m tools.offline.candidate_campaign local --all
python -m tools.offline.candidate_campaign report --all
```

板端地址为 `192.168.8.178` 时，先手动激活 conda/CANN，再用 `tools/board/collect_npu_trace.py` 包裹单候选命令。报告中出现 `139`、`err_ret=-512`、设备 reset、资源清理失败或无法解释的残留时，停止该候选，回退 CCNet 并保持版本冻结。
