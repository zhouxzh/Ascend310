import os
from huggingface_hub import hf_hub_download
import argparse
import shutil

def download_onnx_model(backbone, target_dir="models"):
    repo_id = "zhouxzh/ssd"
    filename = f"ssd_{backbone}.onnx"
    local_target = os.path.join(target_dir, filename)

    # 如果目标文件已存在，直接返回路径（避免重复下载）
    if os.path.exists(local_target):
        print(f"模型已存在：{local_target}")
        return local_target

    # 确保目标目录存在
    os.makedirs(target_dir, exist_ok=True)

    # 下载到缓存（返回缓存中的路径）
    cached_path = hf_hub_download(repo_id=repo_id, filename=filename)
    print(f"缓存路径：{cached_path}")

    # 复制到目标目录
    shutil.copy2(cached_path, local_target)  # copy2 保留元数据
    print(f"模型已复制到：{local_target}")

    return local_target
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="all", help="backbone name")
    args = parser.parse_args()
    if args.backbone == "all":
        backbones = ["resnet18", "resnet34", "resnet50", "resnet101", "resnet152"]
        for backbone in backbones:
            try:
                download_onnx_model(backbone)
            except Exception as e:
                print(f"下载 {backbone} 失败: {e}")
    else:
        download_onnx_model(args.backbone)