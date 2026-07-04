import os
import argparse
import re
import torch
import sys

# Add the current directory to sys.path to ensure we can import ssd modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ssd.data_hf import download_and_load_coco, get_train_loader, get_val_dataloader, get_coco_ground_truth
from ssd.train import train
from ssd.train import export_onnx_model
from pycocotools.coco import COCO
    
def get_args():
    parser = argparse.ArgumentParser(description="Visualize COCO Ground Truth directly from HF Dataset")
    
    # Default settings
    parser.add_argument("--batch-size", type=int, default=128, help="Batch size")
    parser.add_argument("--epochs", type=int, default=65, help="Total epochs")
    parser.add_argument("--lr", type=float, default=2.6e-3, help="Base learning rate")
    parser.add_argument("--momentum", type=float, default=0.9, help="SGD momentum")
    parser.add_argument("--weight-decay", type=float, default=0.0005, help="Weight decay")
    parser.add_argument("--num-workers", type=int, default=4, help="Number of workers for data loading")
    parser.add_argument("--multistep", nargs='+', type=int, default=[43, 54], help="Epochs to decay learning rate")
    
    # Device setting
    device_default = "cuda" if torch.cuda.is_available() else "cpu"
    parser.add_argument("--device", type=str, default=device_default, help="Device (cuda/cpu)")
    
    parser.add_argument("--backbone", type=str, default="resnet50", help="Model backbone")
    parser.add_argument("--augment", action='store_true', default=True, help="Use data augmentation")
    
    # New argument for restarting training
    parser.add_argument("--restart", action='store_true', help="Resume training from the latest checkpoint in models/")
    
    return parser.parse_args()

def get_category_names(dataset):
    try:
        features = dataset['train'].features
        if 'objects' in features:
            objects_feat = features['objects']
            
            def safe_get(obj, key):
                if isinstance(obj, dict):
                    return obj.get(key)
                if hasattr(obj, key):
                    return getattr(obj, key)
                if key == 'feature' and hasattr(obj, 'feature'):
                    return obj.feature
                return None

            category_feat = safe_get(objects_feat, 'category')
            if category_feat is None:
                inner_feat = safe_get(objects_feat, 'feature')
                if inner_feat:
                    category_feat = safe_get(inner_feat, 'category')

            if category_feat:
                cat_inner = safe_get(category_feat, 'feature')
                target_feat = cat_inner if cat_inner is not None else category_feat
                names = safe_get(target_feat, 'names')
                if names and isinstance(names, list):
                    return names

        print("Warning: Could not find category names in dataset features.")
        return None

    except Exception as e:
        print(f"Error extracting category names: {e}")
        return None

def find_latest_checkpoint(backbone):
    """
    Find the latest checkpoint for the given backbone in the checkpoints/ directory.
    Assumes filename format: ssd300_{backbone}_{epoch}.pth
    """
    model_dir = "checkpoints"
    if not os.path.exists(model_dir):
        return None, 0
    
    # Pattern to match: ssd300_{backbone}_{epoch}.pth
    pattern = re.compile(rf"ssd300_{backbone}_(\d+)\.pth")
    
    max_epoch = -1
    latest_checkpoint = None
    
    for filename in os.listdir(model_dir):
        match = pattern.match(filename)
        if match:
            epoch = int(match.group(1))
            if epoch > max_epoch:
                max_epoch = epoch
                latest_checkpoint = os.path.join(model_dir, filename)
    
    if latest_checkpoint:
        print(f"Found latest checkpoint: {latest_checkpoint} (Epoch {max_epoch})")
        # We start from the next epoch
        return latest_checkpoint, max_epoch + 1
    else:
        print(f"No checkpoint found for backbone {backbone}.")
        return None, 0

if __name__ == "__main__":
    # 1. Get arguments
    args = get_args()

    # 2. Load Data
    print("Loading training data...")
    full_dataset = download_and_load_coco()
    train_loader = get_train_loader(full_dataset, args.batch_size, num_workers=args.num_workers, args=args)
    
    print("Loading validation data...")
    val_loader = get_val_dataloader(full_dataset, args.batch_size, num_workers=args.num_workers)
    
    # 3. Prepare COCO Ground Truth
    gt_file = get_coco_ground_truth(full_dataset['val'])
    coco_gt = COCO(gt_file)
    # Get category names
    category_names = get_category_names(full_dataset)
    if category_names:
        category_names = ['BACKGROUND'] + category_names
    
    # 4. Handle Restart/Resume
    resume_checkpoint = None
    start_epoch = 0
    if args.restart:
        resume_checkpoint, start_epoch = find_latest_checkpoint(args.backbone)
    
    # 5. Start Training
    ssd_model = train(args, train_loader, val_loader, coco_gt, category_names, resume_checkpoint, start_epoch)
    
    # Export ONNX model after training
    # Note: export_onnx_model is a helper in ssd.train, but we might want to call it here or it is already called in train?
    # Original code called it after train returns.
    # We can import it from ssd.train if needed, or rely on train returning the model.
    
    # Let's import export_onnx_model from ssd.train as well to keep the flow

    os.makedirs("models", exist_ok=True)
    export_onnx_model(ssd_model, args.device, onnx_path=f"models/ssd300_{args.backbone}.onnx")
