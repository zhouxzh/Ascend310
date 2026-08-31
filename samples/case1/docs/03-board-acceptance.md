# Case 1 板端部署与验收

本文件给出在 Ascend 310B 开发板上验证 Case 1 的顺序。它是操作和证据模板，
不是已经完成的测试报告；只有填写原始命令、输出和报告路径后，才能把某项
标记为通过。

## 前置条件

- 开发板型号、SoC 计算档位和 CANN 版本已记录；
- CANN 环境与 Python 环境在同一 shell 中激活；
- `acl`、OpenCV、FastAPI 和 Uvicorn 可以导入；
- 摄像头设备节点由操作者确认且没有被其他进程占用；
- 两个 OM 已放在样例 `models/`，其来源和合同检查结果可追溯；
- 测试图像为合成图像或经过明确同意的匿名样本。

开发机不执行本文件中的 ACL、OM、ATC 或摄像头步骤。

## 资产和环境检查

在目标板执行并保存输出：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd samples/case1
python -c "import acl, cv2, fastapi, uvicorn; print('runtime imports: ok')"
python scripts/check_onnx.py models/det_500m.onnx models/w600k_mbf.onnx
python scripts/check_onnx_out.py models/det_500m.onnx models/w600k_mbf.onnx
```

若 ONNX 不存在但已有经批准的 OM，必须记录其外部资产位置和摘要；不应从一份
无法追溯的 OM 推断模型合同。若需要转换，在目标板执行 ATC 并保存完整命令和
日志，开发机不运行 ATC。

## 服务烟测

先在隔离的运行数据目录启动单 worker 服务：

```bash
python app.py --host 127.0.0.1 --port 5000
```

另一个终端执行：

```bash
curl -fsS http://127.0.0.1:5000/api/health
curl -fsS http://127.0.0.1:5000/api/users
curl -fsS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:5000/video_feed
```

健康接口应区分服务已启动、模型就绪和摄像头就绪。模型缺失时允许静态页面和
诊断接口返回，但注册、打卡和自动考勤必须返回 `503`；不得写入随机特征。

## 功能闭环

使用合成或经授权的图像，按以下顺序保存 API 响应和日志：

1. 上传图像注册一个带显示名称的用户；
2. 查询用户列表，确认响应不含 embedding；
3. 使用同一图像执行手动打卡，确认 `match`、相似度和事件类型；
4. 读取 `/api/attendance`，确认语义为当天每位用户最近一条记录；
5. 请求摄像头抓拍和 MJPEG 首帧；
6. 观察自动分支，明确记录陌生人自动登记仅是教学策略；
7. 修改和删除用户，确认关联事件按文档合同处理。

不使用真实身份证件照片或未授权人员数据，不把截图、数据库和头像复制回开发机
或提交到版本库。

## 资源释放和并发

停止服务后检查：

- Uvicorn 进程已退出，端口不再监听；
- 摄像头设备已释放，可由另一个进程重新打开；
- 模型、Device buffer、stream、context 和 ACL 已按逆序释放；
- 隔离目录中的数据库、照片和临时资源已按协议删除。

在不改变单 worker 约束的前提下，可启动多个 MJPEG 客户端并同时查询用户列表，
持续至少一个预先规定的短时窗口。验收记录应包含请求数、HTTP 状态、异常数、
摄像头错误和 ACL 错误。未记录原始报告时，不得声称“稳定”或“实时”。

## 结果记录模板

```text
板端型号与计算档位：
CANN/Python 环境：
模型文件与外部摘要：
摄像头设备与实际格式：
静态合同检查：通过 / 未通过（报告路径：）
ATC 转换：通过 / 未执行 / 失败（日志路径：）
ACL 首次推理：通过 / 失败（日志路径：）
FastAPI 健康与接口烟测：通过 / 失败（报告路径：）
React/MJPEG 检查：通过 / 失败（报告路径：）
停止与资源释放：通过 / 失败（报告路径：）
未验证项目与原因：
```

性能、精度和隐私结论必须分别绑定各自的实验协议。一次 HTTP 成功响应只能
证明接口可达，不能证明模型精度；一次 ACL 推理成功也不能证明考勤系统在
长时间运行或多人并发时满足要求。

## 本次板端验证记录

以下记录对应隔离目录中的一次实际检查。原始日志和运行资产在验收完成后按隔离
目录清理协议删除，没有复制到源码仓库；本节只保留可复核的摘要。日期用于证据
索引，不代表教材正文中的版本或代码状态。

| 项目 | 结果 |
| --- | --- |
| 设备 | Ascend 310B4，8T；CANN 环境与 `base` Python 3.9.2 同一 shell 激活 |
| 运行时导入 | FastAPI 0.115.2、Uvicorn 0.32.0、OpenCV 4.10.0、PyACL 可导入 |
| OM/ACL 烟测 | 通过；检测模型加载、特征模型加载、检测调用、512 维特征输出和资源释放均完成 |
| Python/API 合同、生命周期与 SQLite 并发 | `12 passed`（含 `test_layout.py`、`test_api_contract.py`、`test_runtime.py`、`test_database.py`） |
| 健康状态 | `ready=true`；摄像头设备可打开但在测试窗口内没有产生首帧，因此 `camera_ready=false`、`status=degraded` |
| 抓拍与 MJPEG | 在无首帧状态下均按合同返回 HTTP `503`，不返回空成功结果或无限等待的流 |
| 并发检查 | 60 秒；健康接口 60 次 `200`、用户列表 60 次 `200`、视频请求 60 次预期 `503`；未发现 ACL/SQLite/异常堆栈/HTTP `500` |
| 停止与释放 | `SIGTERM` 后服务正常退出，端口释放；连续两次启动/停止均完成两个 OM 加载和 ACL 资源释放 |

本次未能完成真实摄像头首帧、浏览器视频闭环、经同意参与者注册/识别和自动
登记验证，原因是目标板在该时段的视频节点没有输出有效帧。上述结果不能推出
摄像头帧率、识别准确率、端到端延迟或自动考勤有效性；接入并配置摄像头后，
应重新执行“功能闭环”和“MJPEG 首帧”步骤，并保存独立原始报告。
