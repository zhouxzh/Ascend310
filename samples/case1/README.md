# Case 1 · 人脸考勤（face-attendance）

本样例在昇腾 310B 开发板上演示人脸检测、特征提取、相似度比对和本地考勤
记录。服务端采用 FastAPI，界面采用 React；稳定目录标识仍为 `case1`，便于
书稿、站点和已有部署脚本引用。

**功能关键词：** 人脸检测、特征比对、PyACL、边缘服务、考勤记录

## 运行边界

样例用于教学和受控实验，不是生产级身份认证系统。陌生人自动登记分支被保留
用于展示完整数据流，但不代表经过同意的注册。不要在未授权的摄像头、照片或
人员数据上运行；模型、照片、数据库和板端报告均属于本地运行资产，不提交到
版本库。

## 目录结构

```text
case1/
├── app.py                         # FastAPI/Uvicorn 启动入口
├── face_attendance/               # 推理、摄像头、数据库和服务组件
├── frontend/                      # React + Vite 源码；dist 为构建产物
├── scripts/                       # 模型准备、合同检查和运行数据迁移
├── tests/                         # 纯 Python 与板端测试
├── models/                        # ONNX/OM 本地资产（不提交二进制）
├── data/                          # 数据库、头像和抓拍文件
├── reports/                       # 本地验证报告（不提交真实数据）
└── docs/                          # 目录、模型、架构和板端验收说明
```

`frontend/dist` 由前端构建生成，开发板只需要静态产物，不需要 Node.js。旧的
页面书签 `/users_page` 与 `/attendance_page` 由 FastAPI 回退到 React 应用。

## 系统结构

### 运行组件

1. **FastAPI 应用**：负责请求校验、资源路径边界、API 响应和静态资源托管；
2. **硬件工作线程**：串行使用摄像头、PyACL context 和两个 OM，避免请求之间
   竞争 NPU 或视频设备；SQLite 由进程内锁和事务边界保护；
3. **React 界面**：提供用户管理、设备抓拍、手动打卡、MJPEG 预览和今日记录；
4. **SQLite**：保存用户名称、特征 BLOB、头像资源标识和考勤事件。

PyACL、OpenCV 和 SQLite 是阻塞调用。FastAPI 路由不能因为使用 ASGI 就无条件
改成并发的 `async` 硬件操作；应用使用单 worker 和受控队列，服务关闭时显式
停止线程、摄像头、模型和 ACL 资源。

### 模型角色

| 角色 | 准备阶段 | 运行阶段 | 当前实现合同 |
| --- | --- | --- | --- |
| 人脸检测 | `models/det_500m.onnx` | `models/face_detection.om` | `1×3×640×640` 浮点输入 |
| 特征提取 | `models/w600k_mbf.onnx` | `models/face_recognition.om` | `1×3×112×112` 浮点输入，输出特征向量 |

文件名不等于论文名称。输出数量、形状、数据类型和后处理假设必须用
`scripts/check_onnx.py`、`scripts/check_onnx_out.py` 及板端运行检查确认。

## API 合同

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/health` | 服务、模型和摄像头状态；不可推理时为 `degraded` |
| `GET` | `/api/users` | 用户列表，不返回 embedding |
| `POST` | `/api/users` | multipart 图像和姓名注册用户 |
| `PUT` | `/api/users/{id}` | 修改用户名称 |
| `DELETE` | `/api/users/{id}` | 删除用户及关联考勤事件 |
| `POST` | `/api/camera/capture` | 板端摄像头抓拍 |
| `POST` | `/api/clockin` | 上传或浏览器图像手动打卡 |
| `GET` | `/api/attendance` | 当天每位用户最近一条记录 |
| `GET` | `/video_feed` | 缓存 JPEG 的 MJPEG 流 |
| `GET` | `/uploads/{resource}` | 受控头像或抓拍资源 |

模型缺失、加载失败或输出不符合合同时，注册和推理接口返回 HTTP `503`；不
生成随机 embedding，也不把失败请求写成成功考勤。

## 快速开始

### 纯 Python 检查

开发机不运行 CANN、PyACL、ATC、OM 或摄像头测试。可先执行：

```bash
cd samples/case1
python -m unittest discover -s tests -p 'test_layout.py'
python -m py_compile app.py face_attendance/*.py
```

### 开发板环境

在 Ascend 310B 上加载与设备匹配的 CANN 环境，然后准备模型。若已有经验证的
OM，可跳过 ATC，但仍要完成文件、合同和 ACL 烟测：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
cd samples/case1
python -m pip install -r requirements.txt
python scripts/prepare_models.py
python scripts/check_onnx.py
python scripts/check_onnx_out.py
```

模型准备和 ATC 只在开发板执行。模型文件位于 `models/`，不会由 Git 跟踪。

### 构建和启动界面

在开发机完成前端构建后，将 `frontend/dist` 与样例源码同步到板端：

```bash
cd frontend
npm ci
npm test
npm run build
cd ..
python app.py --host 127.0.0.1 --port 5000
```

默认只监听本机。可信实验网络中如需从其他设备访问，可显式使用受控的
`--host`，并在实验结束后停止服务；不要开启调试模式、自动重载或多个 worker。

健康检查和界面地址：

```bash
curl http://127.0.0.1:5000/api/health
```

浏览器打开 `http://127.0.0.1:5000/`。在开发板上通过局域网访问时，将地址替换
为板端实际监听地址。

## 用户流程

1. **注册**：在 React 用户管理页输入显示名称，上传合成测试图像或使用设备
   抓拍；服务检测最大人脸并写入特征与头像资源。
2. **自动考勤**：板端摄像头线程定期处理当前帧；已匹配用户可产生
   `camera_auto` 事件，陌生人自动登记分支仅用于教学观察。
3. **手动打卡**：在考勤页上传图像或使用浏览器摄像头；匹配成功时产生
   `manual` 事件，失败时显示 `match=false`。
4. **查看记录**：考勤页显示当天每位用户的最近一条记录。它不是跨日期的完整
   审计历史；需要完整历史时应另行设计分页和日期查询接口。

## 数据和安全注意事项

- 上传文件名由服务端生成，服务端必须限制大小、校验图像解码并约束真实路径；
- API 列表不返回 embedding，头像和抓拍资源不能通过任意路径公开；
- 测试使用合成或经同意的图像，结束后删除数据库、照片和临时目录；
- 日志不写入完整特征、原始图像或调试堆栈；
- 自动登记不是授权流程，不得将其用于真实考勤或身份认证；
- FastAPI 的 OpenAPI 文档和调试端点按部署边界决定是否开放，不应暴露到不可信网络。

## 验证证据

请将结果区分为以下类型，并在报告中记录环境、模型文件、输入协议和原始日志：

| 证据类型 | 可回答的问题 |
| --- | --- |
| 静态检查 | 文件和 ONNX 图是否可解析 |
| 转换检查 | ATC 是否生成目标 OM |
| ACL 烟测 | 板端能否加载并执行一次推理 |
| API 烟测 | FastAPI 路由、错误状态和静态页面是否可用 |
| UI 检查 | React 导航、抓拍、上传和状态展示是否可用 |
| 性能实验 | 在明确协议下的延迟、帧率和资源占用 |

没有固定测试脚本和报告时，不在 README 或教材中填写性能数字。开发机结果
不能替代开发板上的 CANN、摄像头和 NPU 证据。

更多合同与边界说明见：

- [`docs/00-directory-layout.md`](docs/00-directory-layout.md)
- [`docs/01-model-contract.md`](docs/01-model-contract.md)
- [`docs/02-fastapi-react-architecture.md`](docs/02-fastapi-react-architecture.md)
- [`docs/03-board-acceptance.md`](docs/03-board-acceptance.md)
