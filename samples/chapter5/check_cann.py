"""验证 CANN ACL 已安装且 NPU 可达。

在昇腾 310B 上运行：
    export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/aarch64-linux/lib64:/usr/local/Ascend/driver/lib64:$LD_LIBRARY_PATH"
    export PYTHONPATH="/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:$PYTHONPATH"
    python docs/check_cann.py

预期输出：
    ACL init OK  soc=Ascend310B4  cann=8.3.RC1
"""

import acl

# ① 初始化 ACL 运行时 ------------------------------------------------------------
ret = acl.init()
assert ret == 0, f"acl.init() 失败，返回值: {ret}"
print(f"ACL init OK  soc={acl.get_soc_name()}", end="")

# ② 绑定到设备 0 ------------------------------------------------------------------
ret = acl.rt.set_device(0)
assert ret == 0, f"acl.rt.set_device(0) 失败，返回值: {ret}"

ctx, ret = acl.rt.create_context(0)
assert ret == 0, f"acl.rt.create_context(0) 失败，返回值: {ret}"

ret = acl.rt.set_context(ctx)
assert ret == 0, f"acl.rt.set_context() 失败，返回值: {ret}"

# ③ 查询 CANN 版本 ----------------------------------------------------------------
version = acl.get_version()
print(f"  cann={version}")

# ④ DVPP 冒烟测试 — 列出所有 VENC 相关函数 ----------------------------------------
from acl import media

venc_funcs = [x for x in dir(media) if "venc" in x]
print(f"  venc_api={len(venc_funcs)} 个可用函数")
print("\n全部检查通过 — CANN 已就绪。")
