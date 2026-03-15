import os
import torch
import torch.distributed as dist
import torch.optim as optim
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.tensorboard import SummaryWriter
from torch.nn.parallel import DistributedDataParallel as DDP

# 引入 pycocotools 用于计算 COCO mAP
from pycocotools.cocoeval import COCOeval

# 引入项目中的模型和工具
from ssd.model import SSD320, MobileNet, Loss
from ssd.utils import dboxes320_coco, Encoder, visualize_sample


def tencent_trick(model):
    """
    Divide parameters into 2 groups.
    First group is BNs and all biases.
    Second group is the remaining model's parameters.
    Weight decay will be disabled in first group (aka tencent trick).
    """
    decay, no_decay = [], []
    for name, param in model.named_parameters():
        if not param.requires_grad:
            continue
        if len(param.shape) == 1 or name.endswith(".bias"):
            no_decay.append(param)
        else:
            decay.append(param)
    return [
        {'params': no_decay, 'weight_decay': 0.0},
        {'params': decay},
    ]


def apply_linear_warmup(optimizer, base_lr, global_step, warmup_steps):
    if warmup_steps <= 0:
        return

    if global_step < warmup_steps:
        lr_scale = float(global_step + 1) / float(warmup_steps)
        for param_group in optimizer.param_groups:
            param_group['lr'] = base_lr * lr_scale
    elif global_step == warmup_steps:
        for param_group in optimizer.param_groups:
            param_group['lr'] = base_lr


def validate_and_visualize(ssd_model, epoch, val_loader, eval_encoder, coco_gt, category_names, device, writer, args):
    print(f"Epoch {epoch + 1} 结束, 开始评估验证集 mAP 并进行可视化...")
    # Validate with the underlying module to avoid DDP collectives on rank-0-only eval.
    eval_model = ssd_model.module if hasattr(ssd_model, "module") else ssd_model
    eval_model.eval()

    viz_dir = f"viz_results/ssd_{args.backbone}/epoch_{epoch + 1}"
    if not os.path.exists(viz_dir):
        os.makedirs(viz_dir)

    viz_count = 0
    results_coco = []

    with torch.no_grad():
        for _, (v_images, v_boxes_list, v_labels_list, v_img_ids) in enumerate(val_loader):
            v_images = v_images.to(device)

            locs, confs = eval_model(v_images)
            results = eval_encoder.decode_batch(locs, confs)

            if viz_count < 10:
                for b in range(len(v_images)):
                    if viz_count >= 10:
                        break

                    p_boxes, p_labels, p_scores = results[b]

                    visualize_sample(
                        v_images[b],
                        v_boxes_list[b],
                        v_labels_list[b],
                        p_boxes,
                        p_labels,
                        p_scores,
                        category_names,
                        os.path.join(viz_dir, f"val_{viz_count}.jpg"),
                    )
                    viz_count += 1

            for b in range(len(results)):
                p_boxes, p_labels, p_scores = results[b]

                if p_boxes.numel() == 0:
                    continue

                p_boxes *= 320.0

                img_id = v_img_ids[b]
                if torch.is_tensor(img_id):
                    img_id = img_id.item()

                for i in range(len(p_boxes)):
                    box = p_boxes[i].tolist()
                    results_coco.append(
                        {
                            "image_id": img_id,
                            "category_id": p_labels[i].item(),
                            "bbox": [box[0], box[1], box[2] - box[0], box[3] - box[1]],
                            "score": p_scores[i].item(),
                        }
                    )

        if results_coco:
            print(f"收集到 {len(results_coco)} 条预测结果，正在计算 mAP...")
            coco_dt = coco_gt.loadRes(results_coco)
            coco_eval = COCOeval(coco_gt, coco_dt, 'bbox')
            coco_eval.evaluate()
            coco_eval.accumulate()
            coco_eval.summarize()
            val_map = coco_eval.stats[0]
            print(f"Epoch [{epoch + 1}/{args.epochs}] mAP: {val_map:.4f}")
            writer.add_scalar('Val/mAP', val_map, epoch)
            return val_map

        print(f"Epoch {epoch + 1}: 未检测到任何目标，results_coco 为空。")
        return None

def setup_training(args, resume_checkpoint=None):
    distributed = dist.is_available() and dist.is_initialized()
    main_process = (not distributed) or dist.get_rank() == 0

    writer = SummaryWriter(log_dir=f"logs/{args.backbone}") if main_process else None

    batch_size = args.batch_size
    epochs = args.epochs
    n_gpu = dist.get_world_size() if distributed else 1

    lr = args.lr * (batch_size * n_gpu) / 32
    if main_process:
        print(f"Batch Size(per proc): {batch_size}, World Size: {n_gpu}, Calculated Learning Rate: {lr}")

    dboxes = dboxes320_coco()
    eval_encoder = Encoder(dboxes)

    local_rank = getattr(args, 'local_rank', 0)
    if args.device.startswith('cuda') and torch.cuda.is_available():
        device = torch.device(f"cuda:{local_rank}")
    else:
        device = torch.device(args.device)
    if main_process:
        print(f"使用设备: {device}")

    base_model = SSD320(
        backbone=MobileNet(backbone=args.backbone, weights='IMAGENET1K_V1' if args.pretrained_backbone else None),
        num_classes=args.num_classes,
    )

    if resume_checkpoint:
        if main_process:
            print(f"Resuming training from checkpoint: {resume_checkpoint}")
        if os.path.exists(resume_checkpoint):
            checkpoint_state = torch.load(resume_checkpoint, map_location=device)
            base_model.load_state_dict(checkpoint_state)
            if main_process:
                print("Checkpoint loaded successfully.")
        else:
            if main_process:
                print(f"Checkpoint file not found: {resume_checkpoint}")

    base_model.to(device)

    criterion = Loss(dboxes).to(device)
    return {
        'distributed': distributed,
        'main_process': main_process,
        'writer': writer,
        'lr': lr,
        'device': device,
        'eval_encoder': eval_encoder,
        'base_model': base_model,
        'criterion': criterion,
        'best_map': -1.0,
        'no_improve': 0,
    }


def train_freeze_backbone_phase(
    args,
    train_loader,
    val_loader,
    coco_gt,
    category_names,
    train_state,
):
    distributed = train_state['distributed']
    main_process = train_state['main_process']
    writer = train_state['writer']
    device = train_state['device']
    eval_encoder = train_state['eval_encoder']
    base_model = train_state['base_model']
    criterion = train_state['criterion']
    base_lr = train_state['lr']
    epoch_start = 0
    epoch_end = args.freeze_backbone_epochs

    for p in base_model.feature_extractor.feature_extractor.parameters():
        p.requires_grad_(False)

    local_rank = getattr(args, 'local_rank', 0)
    if distributed:
        ssd_model = DDP(
            base_model,
            device_ids=[local_rank],
            output_device=local_rank,
            find_unused_parameters=True,
        )
    else:
        ssd_model = base_model

    if distributed and main_process:
        print(
            f"阶段 freeze_backbone: 启用 DDP, find_unused_parameters=True, "
            f"epoch=[{epoch_start + 1}, {epoch_end}]"
        )
    elif main_process:
        print(f"阶段 freeze_backbone: epoch=[{epoch_start + 1}, {epoch_end}]")

    optimizer = optim.SGD(
        tencent_trick(ssd_model),
        lr=base_lr,
        momentum=args.momentum,
        weight_decay=args.weight_decay,
    )
    warmup_steps = args.warmup_epochs * len(train_loader)

    amp_enabled = device.type == 'cuda'
    scaler = torch.amp.GradScaler('cuda', enabled=amp_enabled)
    global_step = 0

    if main_process:
        print("冻结 backbone，仅训练检测头。")

    for epoch in range(epoch_start, epoch_end):
        if hasattr(train_loader, 'sampler') and hasattr(train_loader.sampler, 'set_epoch'):
            train_loader.sampler.set_epoch(epoch)

        ssd_model.train()
        for batch_idx, (images, plocs, plabels) in enumerate(train_loader):
            apply_linear_warmup(optimizer, base_lr, global_step, warmup_steps)

            images = images.to(device)
            plocs = plocs.to(device)
            plabels = plabels.to(device)

            optimizer.zero_grad()
            with torch.amp.autocast(device_type=device.type, enabled=amp_enabled):
                loc_preds, conf_preds = ssd_model(images)
                loc_preds = loc_preds.float()
                conf_preds = conf_preds.float()

                gloc = plocs.transpose(1, 2).contiguous()
                glabel = plabels
                loss = criterion(loc_preds, conf_preds, gloc, glabel)

            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(ssd_model.parameters(), max_norm=2.0)
            scaler.step(optimizer)
            scaler.update()

            global_step += 1
            if batch_idx % 10 == 0 and writer is not None:
                writer.add_scalar('Train/Loss', loss.item(), global_step)
                writer.add_scalar('Train/LR', optimizer.param_groups[0]['lr'], global_step)

        if main_process and (epoch + 1) % args.eval_interval == 0:
            validate_and_visualize(
                ssd_model,
                epoch,
                val_loader,
                eval_encoder,
                coco_gt,
                category_names,
                device,
                writer,
                args,
            )

        if main_process:
            os.makedirs("checkpoints", exist_ok=True)
            last_path = f"checkpoints/ssd_{args.backbone}_{epoch}.pth"
            torch.save(base_model.state_dict(), last_path)

    train_state['ssd_model'] = ssd_model


def train_full_model_phase(
    args,
    train_loader,
    val_loader,
    coco_gt,
    category_names,
    train_state,
    start_epoch,
):
    distributed = train_state['distributed']
    main_process = train_state['main_process']
    writer = train_state['writer']
    device = train_state['device']
    eval_encoder = train_state['eval_encoder']
    base_model = train_state['base_model']
    criterion = train_state['criterion']
    base_lr = train_state['lr']
    best_map = train_state['best_map']
    no_improve = train_state['no_improve']
    epoch_end = args.epochs

    if start_epoch >= epoch_end:
        return False

    for p in base_model.feature_extractor.feature_extractor.parameters():
        p.requires_grad_(True)

    local_rank = getattr(args, 'local_rank', 0)
    if distributed:
        ssd_model = DDP(
            base_model,
            device_ids=[local_rank],
            output_device=local_rank,
            find_unused_parameters=False,
        )
    else:
        ssd_model = base_model

    if distributed and main_process:
        print(
            f"阶段 full_model: 启用 DDP, find_unused_parameters=False, "
            f"epoch=[{start_epoch + 1}, {epoch_end}]"
        )
    elif main_process:
        print(f"阶段 full_model: epoch=[{start_epoch + 1}, {epoch_end}]")

    optimizer = optim.SGD(
        tencent_trick(ssd_model),
        lr=base_lr,
        momentum=args.momentum,
        weight_decay=args.weight_decay,
    )
    phase_epochs = epoch_end - start_epoch
    scheduler = CosineAnnealingLR(
        optimizer,
        T_max=max(1, phase_epochs),
        eta_min=1e-6,
    )

    amp_enabled = device.type == 'cuda'
    scaler = torch.amp.GradScaler('cuda', enabled=amp_enabled)

    min_delta = args.min_delta
    global_step = start_epoch * len(train_loader)

    if main_process:
        print("解冻 backbone，进行全量训练。")

    should_stop = False
    for epoch in range(start_epoch, epoch_end):
        if hasattr(train_loader, 'sampler') and hasattr(train_loader.sampler, 'set_epoch'):
            train_loader.sampler.set_epoch(epoch)

        ssd_model.train()
        for batch_idx, (images, plocs, plabels) in enumerate(train_loader):
            images = images.to(device)
            plocs = plocs.to(device)
            plabels = plabels.to(device)

            optimizer.zero_grad()
            with torch.amp.autocast(device_type=device.type, enabled=amp_enabled):
                loc_preds, conf_preds = ssd_model(images)
                loc_preds = loc_preds.float()
                conf_preds = conf_preds.float()

                gloc = plocs.transpose(1, 2).contiguous()
                glabel = plabels
                loss = criterion(loc_preds, conf_preds, gloc, glabel)

            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(ssd_model.parameters(), max_norm=2.0)
            scaler.step(optimizer)
            scaler.update()

            global_step += 1
            if batch_idx % 10 == 0 and writer is not None:
                writer.add_scalar('Train/Loss', loss.item(), global_step)
                writer.add_scalar('Train/LR', optimizer.param_groups[0]['lr'], global_step)

        scheduler.step()

        val_map = None
        if main_process and (epoch + 1) % args.eval_interval == 0:
            val_map = validate_and_visualize(
                ssd_model,
                epoch,
                val_loader,
                eval_encoder,
                coco_gt,
                category_names,
                device,
                writer,
                args,
            )

        if main_process:
            os.makedirs("checkpoints", exist_ok=True)
            last_path = f"checkpoints/ssd_{args.backbone}_{epoch}.pth"
            torch.save(base_model.state_dict(), last_path)

        if main_process and val_map is not None:
            if val_map > best_map + min_delta:
                best_map = val_map
                no_improve = 0
                best_path = f"checkpoints/ssd_{args.backbone}_best.pth"
                torch.save(base_model.state_dict(), best_path)
                print(f"保存最佳模型: {best_path}, mAP={best_map:.4f}")
            else:
                no_improve += 1
                print(f"mAP 无提升计数: {no_improve}/{args.patience}")
                if no_improve >= args.patience:
                    print("触发 Early Stopping，提前结束训练。")
                    should_stop = True

        if distributed:
            stop_tensor = torch.tensor(1 if should_stop else 0, device=device, dtype=torch.int)
            dist.all_reduce(stop_tensor, op=dist.ReduceOp.MAX)
            should_stop = stop_tensor.item() > 0

        if should_stop:
            break

    train_state['best_map'] = best_map
    train_state['no_improve'] = no_improve
    train_state['ssd_model'] = ssd_model
    return should_stop
