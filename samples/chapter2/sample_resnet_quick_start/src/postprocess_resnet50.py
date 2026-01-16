# src/postprocess_resnet50.py
import os
import argparse
import json
import numpy as np

parser = argparse.ArgumentParser(description="将ResNet50的 BIN 输出解码为ImageNet标签。")
parser.add_argument("--bin", required=True, help="msame 输出BIN文件的路径。")
parser.add_argument("--topk", type=int, default=5, help="需要打印的最高置信度数量。")
args = parser.parse_args()

logits = np.fromfile(args.bin, dtype=np.float32).reshape(1, -1)
probs = np.exp(logits - logits.max(axis=-1, keepdims=True))
probs = probs / probs.sum(axis=-1, keepdims=True)
probs = probs[0]

labels_path = os.path.join("src", "imagenet_class_index.json")
if not os.path.exists(labels_path):
    raise FileNotFoundError("请先将imagenet_class_index.json下载到src目录。")

with open(labels_path, "r", encoding="utf-8") as f:
    data = json.load(f)
labels = [data[str(i)][1] for i in range(len(data))]

topk = np.argsort(probs)[::-1][:args.topk]
print(f"Decoded {args.bin} (Top-{args.topk})")
for rank, idx in enumerate(topk, start=1):
    label = labels[idx] if idx < len(labels) else f"cls_{idx}"
    print(f"{rank:>2d}: class={idx:<4d} prob={probs[idx]:.6f} label={label}")