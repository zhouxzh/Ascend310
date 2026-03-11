import torch
from torch.utils.data import DataLoader, Dataset
from datasets import load_dataset
# 使用 v2 transforms 进行目标检测增强
from torchvision.transforms import v2
# Also import transforms for legacy compatibility or just use v2 everywhere
from torchvision import tv_tensors
import json
from pycocotools.coco import COCO
from .utils import dboxes300_coco, Encoder

# ------------------------------------------------------------------
# Dataset & Loaders
# ------------------------------------------------------------------
def download_and_load_coco():
    dataset_name = "detection-datasets/coco"
    cache_directory = "./data"
    print(f"Loading dataset: {dataset_name} ...")
    try:
        dataset = load_dataset(dataset_name, cache_dir=cache_directory)
        return dataset
    except Exception as e:
        print(f"Error loading dataset: {e}")
        return None

def val_collate_fn(batch):
    images, boxes, labels, img_ids = zip(*batch)
    return torch.stack(images, 0), boxes, labels, img_ids

class HFToSSDDataset(Dataset):
    def __init__(self, hf_dataset, img_size=300, is_train=True, args=None):
        self.ds = hf_dataset
        self.img_size = img_size
        self.is_train = is_train
        self.args = args
        
        # Initialize custom encoder
        # 使用 utils.py 中的 Encoder 和 DefaultBoxes
        self.dboxes = dboxes300_coco()
        self.box_coder = Encoder(self.dboxes)
        
        # Define Normalize transform (used in both train and val)
        self.normalize_transform = v2.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        
        # Training Augmentation Logic merged here
        if self.is_train and self.args.augment:
            # Augmentation enabled
            print("Data Augmentation is enabled (args.augment=True), using photometric distortions, random cropping, and horizontal flipping.")
            self.train_transform = v2.Compose([
                v2.RandomPhotometricDistort(),
                v2.RandomIoUCrop(),
                v2.RandomHorizontalFlip(),
                v2.Resize((img_size, img_size)),
                v2.ToImage(),
                v2.ToDtype(torch.float32, scale=True),
                self.normalize_transform,
                v2.SanitizeBoundingBoxes(),
            ])
        else:
            # Augmentation disabled: Resize + Normalize only
            print("Data Augmentation is disabled (args.augment=False), using simple resize and normalize.")
            self.train_transform = v2.Compose([
                v2.Resize((img_size, img_size)),
                v2.ToImage(),
                v2.ToDtype(torch.float32, scale=True),
                self.normalize_transform,
                v2.SanitizeBoundingBoxes(), # Ensure boxes are clipped if resize happens
            ])

        # Validation Transform
        self.trans_val = v2.Compose([
            v2.Resize((img_size, img_size)),
            v2.ToImage(),
            v2.ToDtype(torch.float32, scale=True),
            self.normalize_transform
        ])

    def __len__(self):
        return len(self.ds)

    def __getitem__(self, idx):
        item = self.ds[idx]
        image = item['image']
        if image.mode != 'RGB':
            image = image.convert('RGB')
        orig_w, orig_h = image.size
        
        objects = item['objects']
        boxes = []
        labels = []
        
        if len(objects['bbox']) > 0:
            for bbox, cat in zip(objects['bbox'], objects['category']):
                # 修改适配 HuggingFace dataset bbox 格式: xmin, ymin, xmax, ymax
                xmin, ymin, xmax, ymax = bbox
                boxes.append([xmin, ymin, xmax, ymax])
                labels.append(cat + 1)
            boxes_t = torch.tensor(boxes, dtype=torch.float32)
            labels_t = torch.tensor(labels, dtype=torch.long)
        else:
            boxes_t = torch.empty((0, 4), dtype=torch.float32)
            labels_t = torch.empty((0), dtype=torch.long)

        if self.is_train:
            # Prepare inputs for v2 transforms
            w, h = image.size
            if boxes_t.numel() == 0:
                 boxes_tv = tv_tensors.BoundingBoxes([], format="XYXY", canvas_size=(h, w))
                 labels_in = torch.tensor([], dtype=torch.long)
            else:
                 boxes_tv = tv_tensors.BoundingBoxes(boxes_t, format="XYXY", canvas_size=(h, w))
                 labels_in = labels_t
            
            inputs = {"image": image, "boxes": boxes_tv, "labels": labels_in}
            
            # Apply training transforms
            output = self.train_transform(inputs)
            
            img_tensor = output["image"]
            out_boxes = output["boxes"]
            labels_aug = output["labels"]

            # Convert BoundingBoxes back to normalized Tensor [0, 1] for encoder
            h_out, w_out = img_tensor.shape[-2:]
            boxes_norm = out_boxes.as_subclass(torch.Tensor)
            
            if boxes_norm.numel() > 0:
                scale_tensor = torch.tensor([w_out, h_out, w_out, h_out], dtype=torch.float32, device=boxes_norm.device)
                boxes_norm = boxes_norm / scale_tensor
                boxes_norm = boxes_norm.clamp(0, 1)

            # Encode using utils.Encoder
            # utils.Encoder.encode 接受 (bboxes_in, labels_in)
            # bboxes_in 要求是 normalized LTRB (xmin, ymin, xmax, ymax)，boxes_norm 已经是这个格式
            if boxes_norm.size(0) > 0:
                encoded_locs, encoded_labels = self.box_coder.encode(boxes_norm, labels_aug)
            else:
                # 如果没有 ground truth (例如被裁剪掉)，直接返回默认框和背景标签
                # dboxes_xywh shape is [1, 8732, 4], squeeze to [8732, 4]
                encoded_locs = self.box_coder.dboxes_xywh.squeeze(0).clone()
                encoded_labels = torch.zeros(self.box_coder.nboxes, dtype=torch.long)
            
            return img_tensor, encoded_locs, encoded_labels
        else:
            # Validation
            img_tensor = self.trans_val(image)
            gt_boxes = boxes_t.clone()
            if gt_boxes.numel() > 0:
                gt_boxes[:, [0, 2]] /= orig_w
                gt_boxes[:, [1, 3]] /= orig_h
                # Scale back to img_size
                gt_boxes[:, [0, 2]] *= self.img_size
                gt_boxes[:, [1, 3]] *= self.img_size
                
            image_id = item.get('image_id', idx)
            return img_tensor, gt_boxes, labels_t, image_id

def get_train_loader(full_dataset, batch_size, num_workers=4, args=None):
    if full_dataset is None:
        raise RuntimeError("Failed to load dataset")
    train_ds_hf = full_dataset['train']
    ssd_dataset_train = HFToSSDDataset(train_ds_hf, is_train=True, args=args)
    train_loader = DataLoader(ssd_dataset_train, batch_size=batch_size, shuffle=True, num_workers=num_workers, pin_memory=True)
    return train_loader

def get_val_dataloader(full_dataset, batch_size, num_workers=4):
    val_ds_hf = full_dataset['val']
    ssd_dataset_val = HFToSSDDataset(val_ds_hf, is_train=False)
    val_loader = DataLoader(ssd_dataset_val, batch_size=batch_size, shuffle=False, num_workers=num_workers, pin_memory=True, collate_fn=val_collate_fn)
    return val_loader

def get_coco_ground_truth(val_ds_hf):
    print("Preparing COCO Ground Truth from HF dataset...")
    coco_gt_dict = {"images": [], "annotations": [], "categories": []}
    cat_ids = set()
    
    for i in range(len(val_ds_hf)):
        item = val_ds_hf[i]
        img_id = item.get('image_id', i)
        w, h = item['image'].size
        coco_gt_dict["images"].append({"id": img_id, "width": 300, "height": 300})
        
        objects = item.get('objects', {})
        if len(objects.get('bbox', [])) > 0:
            for bbox, cat in zip(objects['bbox'], objects['category']):
                # Source is [xmin, ymin, xmax, ymax], Target COCO JSON is [x, y, w, h]
                # Scale to 300x300
                xmin, ymin, xmax, ymax = bbox
                
                bx = xmin * 300 / w
                by = ymin * 300 / h
                bw = (xmax - xmin) * 300 / w
                bh = (ymax - ymin) * 300 / h
                
                coco_gt_dict["annotations"].append({
                    "id": len(coco_gt_dict["annotations"]), 
                    "image_id": img_id,
                    "category_id": cat + 1, 
                    "bbox": [bx, by, bw, bh], 
                    "area": bw*bh, 
                    "iscrowd": 0
                })
                cat_ids.add(cat + 1)
                
    for cid in cat_ids: 
        coco_gt_dict["categories"].append({"id": cid, "name": str(cid)})
    
    gt_file = "coco_gt_temp.json"
    with open(gt_file, "w") as f: 
        json.dump(coco_gt_dict, f)
    
    return gt_file
