# Local Test Data

The internal-test service creates `templates/` and `captures/` below this
directory at runtime. `templates/` contains plaintext `PWST1` embedding stores;
`captures/` contains user-triggered original images, ROI images, metadata, and
an atomic JSON index. These directories are intentionally ignored by Git and
must be excluded from GitHub, Hugging Face, and release synchronization.

Continuous camera preview frames are not archived. Template deletion removes
the embedding record but keeps capture evidence until an operator explicitly
cleans it.

## 公开掌纹数据集下载清单

本节整理自 [fingerprint-datasets 上游清单](https://github.com/robertvazan/fingerprint-datasets#birjand-university-mobile-palmprint-database) 中公开区的掌纹数据集。链接是原始来源或其下载入口，不代表本项目获得再分发授权；使用前仍需确认数据集许可和研究用途限制。

| 数据集 | 内容简介 | 规模与格式 | 下载链接 | 当前状态 |
| --- | --- | --- | --- | --- |
| Tsinghua Palmprint Database (THUPALMLAB) | 使用接触式掌纹扫描仪采集的掌纹图像，适合做掌纹识别、匹配和检索实验。 | 80 人，左右手共 160 个掌，每掌 8 次采集，共 1,280 张；BMP，2040×2040，500 dpi。 | [清华大学数据集页面](https://ivg.au.tsinghua.edu.cn/dataset/THUPALMLAB.php)；[直接下载 THUPALMLAB.rar](https://ivg.au.tsinghua.edu.cn/dataset/samples_THUPALMLAB/THUPALMLAB.rar) | 已下载到 `data/downloads/THUPALMLAB.rar`。页面注明仅限非商业研究/教学使用。 |
| COEP Palm Print Database | 非接触式右手掌照片，包含姿态、光照和背景变化，适合验证移动/非接触式掌纹方案。 | 168 人，每人 8 张，共约 1,344 张；彩色 JPEG，1600×1200。 | [COEP 官方介绍页](https://www.coep.org.in/resources/coeppalmprintdatabase)；[上游记录的直接压缩包](https://www.coep.org.in/page_assets/250/database.zip) | 上游标为可免费下载，但本次核验原始压缩包返回 404，暂未下载；需要人工寻找官方新地址或联系维护方。 |
| Birjand University Mobile Palmprint Database (BMPD) | 使用移动设备采集的非接触式手掌/掌纹照片，包含不同采集会话，适合移动端掌纹识别和跨会话评测。 | Kaggle 元数据记录为 41 名伊朗女性、左右手、两次会话，共 1,640 张 JPEG；具体图像尺寸不完全固定。 | [Kaggle 数据集页面](https://www.kaggle.com/datasets/mahdieizadpanah/birjand-university-mobile-palmprint-databasebmpd)；[Kaggle 直接下载](https://www.kaggle.com/datasets/mahdieizadpanah/birjand-university-mobile-palmprint-databasebmpd/download?datasetVersionNumber=1) | 已下载到 `data/downloads/Birjand-BMPD.zip`。Kaggle 许可字段为 Unknown，禁止在未核实条款前公开再分发。 |

## 项目原有数据集与下载方法

下面三套是仓库原来就有、并且已下载到本地的数据集。结构化元数据和固定 revision 见 [`dataset_manifest.json`](../dataset_manifest.json)；表中的命令只做手动下载，不会在服务启动或 Python import 时联网。

| 数据集 | 简介 | 来源与下载方式 | 本地归档与完整性 |
| --- | --- | --- | --- |
| Tongji Contactless Palmprint | 非接触式掌纹数据；600 个身份、2 次会话、每个身份每次 10 张，共 12,000 张。 | [直接下载 Tongji.tar.gz](https://blog-images-1257621236.cos.ap-shanghai.myqcloud.com/edcc_data/all/Tongji.tar.gz)；使用 `curl -L --fail --retry 3 -o data/downloads/Tongji.tar.gz "https://blog-images-1257621236.cos.ap-shanghai.myqcloud.com/edcc_data/all/Tongji.tar.gz"`。 | `data/downloads/Tongji.tar.gz`；119,490,938 bytes；SHA-256 `0440959b4b2e50d00c78c686e9f0840b45005c952c032e8b53e9e34831222f84`。 |
| PolyU Multispectral Palmprint | 多光谱掌纹数据；500 个身份、2 次会话、B/G/I/R 四种光谱，共 24,000 张。 | [直接下载 PolyU.tar.gz](https://blog-images-1257621236.cos.ap-shanghai.myqcloud.com/edcc_data/all/PolyU.tar.gz)；使用 `curl -L --fail --retry 3 -o data/downloads/PolyU.tar.gz "https://blog-images-1257621236.cos.ap-shanghai.myqcloud.com/edcc_data/all/PolyU.tar.gz"`。 | `data/downloads/PolyU.tar.gz`；247,486,526 bytes；SHA-256 `f5f8709db93299826bb7fedde42aaf5478633ced7a813730618b7b5fd872fcff`。 |
| PalmMatchDB | Hugging Face 上的掌纹图像集合；当前审计到 10,528 张图像，包含 PNG/JPEG 和身份/左右手路径线索，但压缩包没有明确的 session 字段，因此不能直接当作跨会话评测集。 | [Hugging Face 数据集页面](https://huggingface.co/datasets/aspmirlab/PalmMatchDB)；按固定 revision `adc051fa4fe169c807f18864fe5ef01480acc84b` 使用 `hf download aspmirlab/PalmMatchDB --repo-type dataset --revision adc051fa4fe169c807f18864fe5ef01480acc84 --local-dir data/datasets/PalmMatchDB`。如需本地 ZIP，应由下载后的目录自行打包；原始 ZIP 生成地址未被记录。 | `data/downloads/PalmMatchDB.zip`；1,265,221,178 bytes；SHA-256 `b19786aadc31c7b08e8294e9e56be5118d299b9f4780389fe2935a2a2149c2f6`；数据集卡标注 Apache-2.0，但下游条款仍需复核。 |

### 下载后校验

Linux/板端可运行：

```bash
sha256sum data/downloads/Tongji.tar.gz
sha256sum data/downloads/PolyU.tar.gz
sha256sum data/downloads/PalmMatchDB.zip
```

Windows PowerShell 可运行：

```powershell
Get-FileHash data/downloads/Tongji.tar.gz -Algorithm SHA256
Get-FileHash data/downloads/PolyU.tar.gz -Algorithm SHA256
Get-FileHash data/downloads/PalmMatchDB.zip -Algorithm SHA256
```

### 本地归档与范围说明

- 本项目已存在的 `data/downloads/Tongji.tar.gz`、`PolyU.tar.gz` 和 `PalmMatchDB.zip` 已在上表记录来源、下载方法和 SHA-256，不重复下载。
- `THUPALMLAB.rar` 和 `Birjand-BMPD.zip` 只保存在本地 `data/downloads/`，不提交 Git，也不自动上传到 GitHub、Hugging Face 或其他公共存储。
- 上游清单中的 CASIA、NIST 等掌纹相关条目属于授权、申请或受限获取资源，不纳入本节“可直接下载”列表；需要时应按其官方申请流程获取。
- COEP 链接状态是一次网络核验结果，可能随网站迁移变化；在获得新官方地址前不要把镜像或非官方转载当作可信来源。
