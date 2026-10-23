# Ascend 310B Python environment locks

These files capture the actual `ascend` conda environment on each CANN
compatibility group:

| Lock | SoC / tier | CANN | NPU driver | Source snapshot SHA-256 |
| --- | --- | --- | --- | --- |
| `ascend-py39-cann70.lock` | Ascend310B4 / 8T | 7.0.0 | 23.0.0 | `3354e353fef4c1552cd7c4f58f490036d1c7a85163ba4cb0a6b4ae5d05aaa763` |
| `ascend-py39-cann80.lock` | Ascend310B1 / 20T | 8.0.0 | 25.2.0 | `d5fb5facfa9031fb28a9c64c88a6c0b84d227cb9459b98929b2fa8b86f42c662` |

Both environments use Python 3.9.25 and must run with
`PYTHONNOUSERSITE=1`. The lock files are provisional until the remaining
hardware gates in `docs/ascend-unified-environment-status.md` close.

CANN-owned wheels such as `aclruntime`, `hccl`, `te`, and `op_compile_tool`
are listed as comments because their installation paths are owned by the CANN
installation. Install and source the matching CANN release before applying a
lock:

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python -m pip install -r dependencies/ascend-py39-cann80.lock
```

Do not use `base`, `pip --user`, or mix the CANN 7 and CANN 8 compatibility
groups. MindSpore and MindNLP are intentionally different between the two
locks.
