import os
import argparse
import re
import torch
import torch.distributed as dist
import sys
from torch.utils.data import DataLoader
from torch.utils.data.distributed import DistributedSampler

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ssdlite.data_hf import download_and_load_coco, get_train_loader, get_val_dataloader, get_coco_ground_truth
from ssdlite.train import setup_training, train_freeze_backbone_phase, train_full_model_phase
from pycocotools.coco import COCO


def init_distributed_if_needed(args):
    world_size = int(os.environ.get("WORLD_SIZE", "1"))
    rank = int(os.environ.get("RANK", "0"))
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))

    args.world_size = world_size
    args.rank = rank
    args.local_rank = local_rank
    args.distributed = False

    if args.ddp:
        if not str(args.device).startswith("cuda"):
            raise RuntimeError("启用 DDP 时 --device 必须是 cuda。")
        if not torch.cuda.is_available():
            raise RuntimeError("已启用 CUDA/DDP，但当前环境未检测到 CUDA，程序退出。")
        if world_size <= 1:
            raise RuntimeError("已启用 DDP，但 WORLD_SIZE<=1。请使用 torchrun --nproc_per_node=2（或更多）启动。")
        torch.cuda.set_device(local_rank)
        dist.init_process_group(backend="nccl", init_method="env://")
        args.distributed = True
        args.device = "cuda"
    else:
        if world_size > 1:
            raise RuntimeError("检测到 WORLD_SIZE>1 但 --ddp 被禁用。请启用 --ddp 或改用单进程启动。")
        if str(args.device).startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("已指定 --device cuda，但当前环境未检测到 CUDA，程序退出。")
    return args


def get_args():
    parser = argparse.ArgumentParser(description="Train SSDLite320 with timm MobileNet backbone on COCO")

    # Single-GPU friendly defaults
    parser.add_argument("--batch-size", type=int, default=64, help="Batch size")
    parser.add_argument("--epochs", type=int, default=600, help="Total epochs")
    parser.add_argument("--lr", type=float, default=0.001, help="Base learning rate")
    parser.add_argument("--momentum", type=float, default=0.9, help="SGD momentum")
    parser.add_argument("--weight-decay", type=float, default=4e-5, help="Weight decay")
    parser.add_argument("--num-workers", type=int, default=16, help="Number of workers for data loading")
    parser.add_argument("--prefetch-factor", type=int, default=1, help="Dataloader prefetch factor")
    parser.add_argument("--pin-memory", action=argparse.BooleanOptionalAction, default=True, help="Pin memory")

    # Training strategy
    parser.add_argument("--warmup-epochs", type=int, default=2, help="Warmup epochs (0 to disable)")
    parser.add_argument("--freeze-backbone-epochs", type=int, default=2, help="Freeze backbone for first N epochs")
    parser.add_argument("--patience", type=int, default=30, help="Early stopping patience (in eval rounds)")
    parser.add_argument("--min-delta", type=float, default=1e-4, help="Minimum mAP improvement to reset patience")
    parser.add_argument("--eval-interval", type=int, default=1, help="Run validation every N epochs")

    # Model options
    parser.add_argument("--num-classes", type=int, default=81, help="Number of classes including background")
    parser.add_argument("--pretrained-backbone", action=argparse.BooleanOptionalAction, default=True, help="Use timm pretrained backbone weights")

    parser.add_argument("--device", type=str, default="cuda", help="Device (cuda/cpu)")
    parser.add_argument("-ddp", "--ddp", action=argparse.BooleanOptionalAction, default=True, help="Enable distributed data parallel (required by default)")

    parser.add_argument("--backbone", type=str, default="mobilenetv2", help="Model backbone")
    parser.add_argument("--augment", action='store_true', default=True, help="Use data augmentation")
    parser.add_argument("--restart", action='store_true', help="Resume training from the latest checkpoint in checkpoints/")
    parser.add_argument(
        "--export-best-onnx",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Export ONNX from checkpoints/ssd_<backbone>_best.pth instead of the in-memory model",
    )

    args = parser.parse_args()
    return args


def validate_training_args(args):
    if args.epochs <= 0:
        raise ValueError("--epochs 必须大于 0。")
    if args.freeze_backbone_epochs < 0:
        raise ValueError("--freeze-backbone-epochs 不能为负数。")
    if args.warmup_epochs < 0:
        raise ValueError("--warmup-epochs 不能为负数。")
    if args.epochs < args.freeze_backbone_epochs:
        raise ValueError("--freeze-backbone-epochs 不能大于 --epochs。")

    # Warmup is only applied during freeze phase.
    if args.warmup_epochs > args.freeze_backbone_epochs:
        raise ValueError(
            "当前策略下 warmup 仅在冻结阶段生效，因此 --warmup-epochs 不能大于 --freeze-backbone-epochs。"
        )


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
    model_dir = "checkpoints"
    if not os.path.exists(model_dir):
        return None, 0

    pattern = re.compile(rf"ssd_{backbone}_(\d+)\.pth")

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
        return latest_checkpoint, max_epoch + 1

    print(f"No checkpoint found for backbone {backbone}.")
    return None, 0


def find_best_checkpoint(backbone):
    best_checkpoint = os.path.join("checkpoints", f"ssd_{backbone}_best.pth")

    if os.path.exists(best_checkpoint):
        print(f"Found best checkpoint: {best_checkpoint}")
        return best_checkpoint

    print(f"Best checkpoint not found for backbone {backbone}: {best_checkpoint}")
    return None


def export_onnx_model(args, train_state, checkpoint_path=None):
    export_model = train_state['base_model'] if checkpoint_path else train_state.get('ssd_model', train_state['base_model'])
    device = train_state['device']
    onnx_path = f"models/ssd_{args.backbone}.onnx"

    if checkpoint_path is not None:
        checkpoint_state = torch.load(checkpoint_path, map_location=device)
        export_model.load_state_dict(checkpoint_state)
        print(f"Loaded checkpoint for ONNX export: {checkpoint_path}")

    print(f"正在导出 ONNX 模型至 {onnx_path}...")
    export_model = export_model.module if hasattr(export_model, "module") else export_model
    export_model.eval()
    dummy_input = torch.randn(1, 3, 320, 320).to(device)
    try:
        torch.onnx.export(
            export_model,
            dummy_input,
            onnx_path,
            verbose=False,
            input_names=['input'],
            output_names=['boxes', 'scores'],
            opset_version=11,
        )
        print(f"ONNX 模型已导出至: {onnx_path}")
    except Exception as e:
        print(f"导出 ONNX 失败: {e}")


if __name__ == "__main__":
    args = get_args()
    validate_training_args(args)
    args = init_distributed_if_needed(args)

    is_main_process = (not args.distributed) or args.rank == 0

    if args.distributed:
        print(f"[Rank {args.rank}] DDP 初始化完成: local_rank={args.local_rank}, world_size={args.world_size}")

    if args.export_best_onnx:
        best_checkpoint = find_best_checkpoint(args.backbone)
        if best_checkpoint is None:
            raise FileNotFoundError(
                f"未找到 best checkpoint: checkpoints/ssd_{args.backbone}_best.pth"
            )

        train_state = setup_training(args, resume_checkpoint=best_checkpoint)
        writer = train_state.get('writer')
        if writer is not None:
            writer.close()

        if is_main_process:
            print("--export-best-onnx 已启用，跳过训练，直接导出 best checkpoint 为 ONNX。")
            export_onnx_model(args, train_state, checkpoint_path=best_checkpoint)

        if args.distributed and dist.is_initialized():
            dist.barrier()
            dist.destroy_process_group()
        sys.exit(0)

    if is_main_process:
        print("Loading training data...")
    full_dataset = download_and_load_coco()
    train_loader = get_train_loader(full_dataset, args.batch_size, num_workers=args.num_workers, args=args)

    if args.distributed:
        train_sampler = DistributedSampler(
            train_loader.dataset,
            num_replicas=args.world_size,
            rank=args.rank,
            shuffle=True,
            drop_last=False,
        )
        train_loader = DataLoader(
            train_loader.dataset,
            batch_size=args.batch_size,
            sampler=train_sampler,
            num_workers=args.num_workers,
            pin_memory=args.pin_memory,
            collate_fn=train_loader.collate_fn,  # 保留原始 collate_fn
            persistent_workers=(args.num_workers > 0),
            prefetch_factor=(args.prefetch_factor if args.num_workers > 0 else None),
        )

    if is_main_process:
        print("Loading validation data...")
    val_loader = get_val_dataloader(full_dataset, args.batch_size, num_workers=args.num_workers)

    gt_file = get_coco_ground_truth(full_dataset['val'])
    coco_gt = COCO(gt_file)

    category_names = get_category_names(full_dataset)
    if category_names:
        category_names = ['BACKGROUND'] + category_names

    resume_checkpoint = None
    start_epoch = 0
    if args.restart:
        resume_checkpoint, start_epoch = find_latest_checkpoint(args.backbone)

    train_state = setup_training(args, resume_checkpoint=resume_checkpoint)

    run_freeze_phase = (not args.restart) and args.freeze_backbone_epochs > 0

    if run_freeze_phase:
        train_freeze_backbone_phase(
            args=args,
            train_loader=train_loader,
            val_loader=val_loader,
            coco_gt=coco_gt,
            category_names=category_names,
            train_state=train_state,
        )

    full_start_epoch = args.freeze_backbone_epochs if run_freeze_phase else start_epoch
    train_full_model_phase(
        args=args,
        train_loader=train_loader,
        val_loader=val_loader,
        coco_gt=coco_gt,
        category_names=category_names,
        train_state=train_state,
        start_epoch=full_start_epoch,
    )

    writer = train_state.get('writer')
    if writer is not None:
        writer.close()

    if is_main_process:
        export_onnx_model(args, train_state)

    if args.distributed and dist.is_initialized():
        dist.barrier()
        dist.destroy_process_group()
