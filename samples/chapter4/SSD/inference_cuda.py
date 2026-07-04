import os
import torch
import onnxruntime as ort
import numpy as np
from datasets import load_dataset
import torchvision.transforms.functional as F
from tqdm import tqdm
import argparse
import time
import json
# 引入项目中的工具用于解码
from ssd.utils import dboxes300_coco, Encoder, visualize_sample
# 引入 pycocotools 用于评估 mAP
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval

from ssd.data_hf import get_coco_ground_truth

def load_coco_val():
    dataset_name = "detection-datasets/coco"
    cache_directory = "./data"
    print(f"正在加载验证数据集: {dataset_name} ...")
    dataset = load_dataset(dataset_name, split='val', cache_dir=cache_directory)
    return dataset

def preprocess(image, img_size=300):
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    orig_w, orig_h = image.size
    image_resized = image.resize((img_size, img_size))
    
    # 标准化
    img_tensor = F.to_tensor(image_resized)
    img_tensor = F.normalize(img_tensor, mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    
    # 添加 batch 维度
    img_numpy = img_tensor.unsqueeze(0).numpy()
    return img_numpy, orig_w, orig_h

def get_gt_data(item, orig_w, orig_h):
    """
    解析 COCO 格式的 Ground Truth，并缩放到 300x300 的 LTRB 格式，
    以便 visualize_sample 使用。
    """
    objects = item['objects']
    if len(objects['bbox']) == 0:
        return torch.tensor([]), torch.tensor([])

    boxes = np.array(objects['bbox']) # [x, y, w, h] from HF dataset
    labels = np.array(objects['category']) + 1 # SSD category=0 is background
    
    # 计算缩放比例
    scale_x = 300.0 / orig_w
    scale_y = 300.0 / orig_h
    
    # 缩放 boxes
    boxes[:, 0] *= scale_x # x
    boxes[:, 1] *= scale_y # y
    boxes[:, 2] *= scale_x # w
    boxes[:, 3] *= scale_y # h
    
    return torch.from_numpy(boxes).float(), torch.from_numpy(labels).long()

def visualize_validation_predictions(args, session, encoder, val_dataset):
    input_name = session.get_inputs()[0].name
    
    # 2. 选取前 10 个样本
    print("正在选取前 10 个样本进行连续推理和可视化...")
    indices = range(10)
    
    # 获取类别名称
    cats = val_dataset.features['objects']['category'].feature.names
    cats = ['background'] + cats # SSD category=0 is background
    
    save_folder = f"viz_results/inference/{args.backbone}"
    os.makedirs(save_folder, exist_ok=True)
    
    for idx in indices:
        item = val_dataset[idx]
        image = item['image']
        image_id = item['image_id']
        
        # 3. 预处理
        img_input, orig_w, orig_h = preprocess(image)
        
        # 用于可视化的 Tensor (需要去 batch 维度, 3x300x300)
        img_tensor = torch.from_numpy(img_input[0])
        
        # 4. 推理
        # 模型输出通常为: boxes (locs), scores (confs)
        # 这里的输出名称取决于导出时的设置，通常是 'boxes', 'scores'
        outs = session.run(['boxes', 'scores'], {input_name: img_input})
        locs, confs = torch.from_numpy(outs[0]), torch.from_numpy(outs[1])
        
        # 5. 解码预测结果
        # decoder.decode_batch 返回的是 list of tuples: (boxes, labels, scores)
        # boxes 是 normalized [0, 1]
        results = encoder.decode_batch(locs, confs, criteria=0.5, max_output=200)
        p_boxes, p_labels, p_scores = results[0]
        
        # 6. 获取 Ground Truth
        gt_boxes, gt_labels = get_gt_data(item, orig_w, orig_h)
        
        # 7. 可视化
        # visualize_sample 会自动把 normalized p_boxes 放大到 300x300，
        # 并且在图片上画出 gt (绿色) 和 pred (红色)
        save_path = f"{save_folder}/vis_{idx}.jpg"
        
        visualize_sample(
            img_tensor,
            gt_boxes,
            gt_labels,
            p_boxes,
            p_labels,
            p_scores,
            cats,
            save_path
        )
        print(f"已处理图片 ID: {image_id}, 结果保存至 {save_path}")

def evaluate_dataset(val_dataset, session, encoder, gt_file):
    
    
    print("\n开始评估全量数据集 mAP 和 推理帧率 ...")
    input_name = session.get_inputs()[0].name
    results = []
    
    # 计时开始
    start_time = time.time()
    inference_process_time = 0.0
    
    for item in tqdm(val_dataset, desc="Evaluating"):
        image = item['image']
        image_id = item['image_id']
        
        # 预处理
        t0 = time.time()
        img_input, orig_w, orig_h = preprocess(image)
        
        # 推理
        outs = session.run(['boxes', 'scores'], {input_name: img_input})
        locs = torch.from_numpy(outs[0])
        confs = torch.from_numpy(outs[1])
        
        # 解码
        decoded_results = encoder.decode_batch(locs, confs, criteria=0.5, max_output=200)
        inference_process_time += (time.time() - t0)
        
        # 格式化结果
        p_boxes, p_labels, p_scores = decoded_results[0]
        
        if p_boxes.numel() > 0:
            # 还原到原图尺寸
            p_boxes *= 300.0
            
            # 转换为 COCO 格式 [x, y, w, h]
            p_boxes[:, 2] -= p_boxes[:, 0]
            p_boxes[:, 3] -= p_boxes[:, 1]
            
            for box, label, score in zip(p_boxes.tolist(), p_labels.tolist(), p_scores.tolist()):
                results.append({
                    "image_id": image_id,
                    "category_id": label,
                    "bbox": [round(x, 3) for x in box],
                    "score": round(score, 5)
                })

    total_time = time.time() - start_time
    count = len(val_dataset)
    fps_total = count / total_time
    fps_inference = count / inference_process_time if inference_process_time > 0 else 0
    
    print(f"\n================ 性能测试结果 ================")
    print(f"处理图片数量: {count}")
    print(f"总耗时: {total_time:.2f}s")
    print(f"全流程 FPS: {fps_total:.2f}")
    print(f"纯推理+解码 FPS: {fps_inference:.2f}")
    print(f"============================================")
    
    # 保存结果并计算 mAP
    res_file = "inference_results.json"
    with open(res_file, "w") as f:
        json.dump(results, f)
    
    if os.path.exists(gt_file):
        try:
            print(f"正在使用 {gt_file} 计算 mAP ...")
  
            cocoGt = COCO(gt_file)
            cocoDt = cocoGt.loadRes(res_file)
            
            cocoEval = COCOeval(cocoGt, cocoDt, 'bbox')
            # 仅评估验证集中的图片ID
            imgIds = sorted([item['image_id'] for item in val_dataset])
            cocoEval.params.imgIds = imgIds
            
            cocoEval.evaluate()
            cocoEval.accumulate()
            cocoEval.summarize()
            
        except ImportError:
            print("未检测到 pycocotools，无法计算 mAP。")
        except Exception as e:
            print(f"计算 mAP 时发生错误: {e}")
    else:
        print(f"未找到 GT 文件 {gt_file}，无法计算 mAP。")

 
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backbone", default="resnet50", help="backbone name")
    args = parser.parse_args()
    
    # 1. 加载数据集
    val_dataset = load_coco_val()

    # 2. 准备 ONNX 模型路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    onnx_model_path = os.path.join(script_dir, "models", f"ssd300_{args.backbone}.onnx")

    # 3. 准备解码工具
    dboxes = dboxes300_coco()
    encoder = Encoder(dboxes)
    
    # 1. 准备 ONNX Session
    if not os.path.exists(onnx_model_path):
        print(f"ONNX 模型文件不存在: {onnx_model_path}")
        exit(1)

    sess_options = ort.SessionOptions()
    providers = ['CUDAExecutionProvider']
    try:
        session = ort.InferenceSession(onnx_model_path, sess_options, providers=providers)
    except Exception as e:
        print(f"无法加载 ONNX 模型: {e}")
        exit(1)
    
    visualize_validation_predictions(args, session, encoder, val_dataset)
    # 生成 GT 文件以确保准确性
    gt_file = get_coco_ground_truth(val_dataset)
    
    evaluate_dataset(val_dataset, session, encoder, gt_file)
