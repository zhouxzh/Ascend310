# MindSpore 本地模型镜像与换板复现

## 目的和边界

本地镜像根固定为 `models/mindspore/<profile>/<revision>/`。该目录由 `.gitignore`
排除：不使用 Git LFS，不提交权重、下载缓存、日志或响应原文。Git 中只跟踪
`configs/mindspore_model_mirror_lock.json`，记录来源板卡、远端路径、字节数、SHA-256、
状态和同步时间。

只复制注册表中具有 40 位不可变 revision 的 `native_mindspore` Profile。条件候选、
没有固定 revision 的 Modelers 工件、远端 `.part` 文件都不能作为可复现模型镜像。

## WSL 同步

Windows 的私钥为 `C:\Users\zhoux\.ssh\id_rsa`。WSL 的 DrvFS 权限通常不满足 OpenSSH
要求；同步脚本只为本次执行创建权限为 `0600` 的临时副本，退出时删除。它不会复制 home
目录，也不用 `--delete`。

```bash
cd /mnt/d/Github/Ascend310/samples/case9
bash scripts/sync_mindspore_model_mirror.sh \
  --board 20t \
  --profile qwen1.5-0.5b-mindspore,qwen1.5-1.8b-mindspore,qwen2-0.5b-instruct-mindspore,qwen2-1.5b-instruct-mindspore,qwen2.5-0.5b-mindspore,qwen2.5-1.5b-mindspore
```

脚本对每个 allowlist 文件执行：

```text
远端 stat + SHA-256
-> 本地 .part
-> rsync --partial --append-verify
-> 本地大小 + SHA-256
-> 原子 rename
```

已存在的同哈希文件记录为 `already-present`。同一路径的本地文件若大小或哈希不一致，
脚本立即停止且不会覆盖。远端只有 `<filename>.part` 时记录为 `partial-not-copied`；不会
传输、不把它列入 `SHA256SUMS.txt`，也不允许用于板端加载。

按单一 profile 重跑同步会只替换同一 `profile/revision/board/local_path` 的记录；其他已同步
profile 的记录保留在清单中。因此补同步 1.8B 权重或仅记录某个远端 `.part` 不会抹掉已有
模型的来源、大小和 SHA-256。

## 输出与新板恢复

成功同步后生成：

- `models/mindspore/bundle-manifest.json`：镜像本身的完整 provenance；
- `models/mindspore/SHA256SUMS.txt`：只包含完整本地文件；
- `configs/mindspore_model_mirror_lock.json`：Git 跟踪的可审计锁；
- `repro/` 下的环境快照、验收 JSON 和日志索引：不包含权重。

将镜像放到新板前，先按锁文件验证 SHA-256，再复制到对应 profile 的 `cache_dir`。随后在
**新板**独立运行环境、Ascend context、JSON/SSE、长输出、稳定性、质量和性能门。20T
结果不能替代 8T，IP 改变但硬件/软件组合不变时则保留原始 provenance，不伪造新测试。
