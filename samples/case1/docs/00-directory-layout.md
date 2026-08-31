# Case 1 目录与运行边界

本案例的稳定标识是 `case1`，语义名称为 **人脸考勤（face-attendance）**。稳定
标识用于书稿转换、VuePress 导航和既有部署脚本；语义名称用于阅读和检索。

## 目录约定

```text
case1/
├── app.py                         # FastAPI/Uvicorn 启动入口
├── face_attendance/               # 运行时业务包
│   ├── api.py                     # 应用工厂、路由和生命周期
│   ├── inference.py               # PyACL/OM 推理封装
│   ├── camera.py                  # 采集、缓存和自动考勤
│   ├── database.py                # SQLite 持久化
│   └── config.py                  # 样例根目录下的路径配置
├── frontend/                      # React + TypeScript + Vite 源码
│   └── dist/                      # 构建后由 FastAPI 托管的静态资源
├── scripts/                       # 模型准备、合同检查和运行数据迁移
├── tests/                         # 纯 Python 与板端测试
├── models/                        # ONNX/OM 本地资产（不提交二进制）
├── data/                          # attendance.db、uploads 等运行时数据
├── reports/                       # 本地验证报告（不保存真实人脸数据）
└── docs/                          # 本案例工程说明
```

## 模块职责

- `app.py` 只负责解析命令行参数并启动单个 Uvicorn worker；业务路由由
  `face_attendance.api` 提供。
- `face_attendance/inference.py` 管理 ACL、模型、Device buffer 和推理合同。
- `face_attendance/camera.py` 维护摄像头线程和最近 JPEG；硬件操作通过单一
  工作路径串行化。
- `face_attendance/database.py` 只负责参数化 SQLite 读写，不在数据库层执行
  NPU 推理。
- `frontend/` 不保存模型、照片或数据库；构建产物可以复制到开发板，但
  `node_modules` 和本地缓存不属于运行时发布内容。

## 路径和证据边界

`face_attendance.config` 以样例根目录为基准解析 `models/`、`data/` 和
`reports/`，因此从不同当前工作目录启动不会把运行数据写到未知位置。模型、
ONNX、OM、照片、数据库和板端报告均为本地资产；除明确的小型测试夹具外，不应
提交到版本库。

上传资源必须由服务端生成不透明名称，并在规范化后确认仍位于
`data/uploads/`。对外 API 不返回 embedding；资源访问、数据保留和删除应按
实验授权执行。

## 兼容入口和迁移

旧版本可能在样例根目录留下 `attendance.db` 或 `uploads/`。迁移前先运行
`scripts/migrate_runtime_data.py` 的计划模式，再由操作者显式指定 `--apply`；
工具不得删除旧数据。旧页面路径 `/users_page` 与 `/attendance_page` 由新的
React 单页应用回退处理，稳定 URL 不因框架迁移而改变。

目录整理不等于运行时验收。陌生人自动登记、模型缺失时的降级、浏览器媒体
轨道释放和 ACL 生命周期都必须通过相应测试单独确认。
