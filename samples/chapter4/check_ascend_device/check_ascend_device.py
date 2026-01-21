import acl  # 导入核心库

# 1. 初始化 ACL 环境
# 配置文件路径传入空字符串，表示使用默认配置
ret = acl.init("") 
if ret != 0:
    print(f"ACL init failed, ret={ret}")
    exit(1)

# 2. 获取可用 Ascend 设备数量
count, ret = acl.rt.get_device_count()
if ret == 0:
    print(f"Found {count} Ascend devices.")
else:
    print(f"Get device count failed, ret={ret}")

# 3. 去初始化 (释放资源)
acl.finalize()