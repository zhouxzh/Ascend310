#!/usr/bin/env python3
"""
Case 8: MobileNetV3-Small training on HaGRID gesture dataset.

Downloads a 10-class subset of HaGRID, trains the model, and saves the
best checkpoint to models/gesture_mobilenetv3.pth.

Usage:
    python3 train.py                  # full train
    python3 train.py --epochs 10      # quick test
    python3 train.py --data-dir /path/to/hagrid   # use existing download
"""

import argparse
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torchvision.models as models
import torchvision.transforms as T
from PIL import Image
from torch.utils.data import DataLoader, Dataset, random_split

from config import (
    BATCH_SIZE,
    HAGRID_GESTURES,
    IMAGE_SIZE,
    LEARNING_RATE,
    MODEL_DIR,
    NUM_CLASSES,
    NUM_EPOCHS,
    PTH_MODEL_PATH,
    TRAIN_SPLIT,
)

# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------

# HaGRID dataset structure (after extracting the .tar.gz):
#   hagrid/hagrid_<version>/
#     call/          ← per-gesture folders
#     dislike/
#     ...
#     annotations/   ← JSON annotation files (optional for classification)

def find_hagrid_root(base: str) -> str:
    """Locate the HaGRID root directory (contains gesture folders)."""
    root = Path(base)
    if not root.exists():
        return ""

    # Directly contains gesture folders
    if any((root / g).is_dir() for g in HAGRID_GESTURES):
        return str(root)

    # Maybe inside hagrid/hagrid_<version>/
    for sub in root.iterdir():
        if sub.is_dir() and any((sub / g).is_dir() for g in HAGRID_GESTURES):
            return str(sub)

    return ""


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class HaGRIDDataset(Dataset):
    """Loads gesture images from per-class folders."""

    def __init__(self, root: str, transform=None, max_per_class: int = 800):
        self.samples = []
        self.transform = transform

        for class_idx, gesture in enumerate(HAGRID_GESTURES):
            gesture_dir = Path(root) / gesture
            if not gesture_dir.is_dir():
                print(f"  WARNING: {gesture_dir} not found, skipping class "
                      f"'{gesture}'")
                continue
            images = sorted(gesture_dir.glob("*.jpg")) + \
                     sorted(gesture_dir.glob("*.jpeg")) + \
                     sorted(gesture_dir.glob("*.png"))
            if max_per_class and len(images) > max_per_class:
                images = images[:max_per_class]
            for img_path in images:
                self.samples.append((str(img_path), class_idx))

        # Build label map for verification
        class_counts = {}
        for _, cid in self.samples:
            class_counts[cid] = class_counts.get(cid, 0) + 1

        print(f"[Dataset] {len(self.samples)} images across "
              f"{len(class_counts)} classes")
        for cid, name in enumerate(HAGRID_GESTURES):
            cnt = class_counts.get(cid, 0)
            print(f"  {cid}: {name:15s}  {cnt:5d} images")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        return img, label


# ---------------------------------------------------------------------------
# Training pipeline
# ---------------------------------------------------------------------------

def build_transforms(train: bool = True):
    if train:
        return T.Compose([
            T.RandomResizedCrop(IMAGE_SIZE, scale=(0.7, 1.0)),
            T.RandomHorizontalFlip(),
            T.RandomRotation(15),
            T.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406],
                        std=[0.229, 0.224, 0.225]),
        ])
    else:
        return T.Compose([
            T.Resize((IMAGE_SIZE, IMAGE_SIZE)),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406],
                        std=[0.229, 0.224, 0.225]),
        ])


def train_one_epoch(model, loader, criterion, optimizer, device):
    model.train()
    running_loss = 0.0
    correct = 0
    total = 0

    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)

        optimizer.zero_grad()
        outputs = model(images)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        running_loss += loss.item() * images.size(0)
        _, preds = outputs.max(1)
        correct += preds.eq(labels).sum().item()
        total += labels.size(0)

    return running_loss / total, correct / total


@torch.no_grad()
def validate(model, loader, criterion, device):
    model.eval()
    running_loss = 0.0
    correct = 0
    total = 0

    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)
        outputs = model(images)
        loss = criterion(outputs, labels)

        running_loss += loss.item() * images.size(0)
        _, preds = outputs.max(1)
        correct += preds.eq(labels).sum().item()
        total += labels.size(0)

    return running_loss / total, correct / total


def main():
    parser = argparse.ArgumentParser(
        description="Train MobileNetV3-Small on HaGRID gestures")
    parser.add_argument("--data-dir", default="data/hagrid",
                        help="Path to HaGRID dataset root")
    parser.add_argument("--epochs", type=int, default=NUM_EPOCHS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=LEARNING_RATE)
    parser.add_argument("--no-cuda", action="store_true")
    parser.add_argument("--output", default=PTH_MODEL_PATH)
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Device
    # ------------------------------------------------------------------
    device = torch.device("cpu")
    if not args.no_cuda and torch.cuda.is_available():
        device = torch.device("cuda")
    print(f"[Train] Device: {device}")

    # ------------------------------------------------------------------
    # Dataset
    # ------------------------------------------------------------------
    hagrid_root = find_hagrid_root(args.data_dir)
    if not hagrid_root:
        print(f"[Train] ERROR: HaGRID dataset not found at {args.data_dir}")
        print(f"  Please download it from {HAGRID_REPO}")
        print(f"  Expected structure: <data-dir>/<gesture_folder>/*.jpg")
        print(f"  Gesture folders: {HAGRID_GESTURES}")
        sys.exit(1)

    print(f"[Train] Using dataset at: {hagrid_root}")

    full_ds = HaGRIDDataset(hagrid_root, transform=build_transforms(True))
    if len(full_ds) == 0:
        print("[Train] ERROR: No images found")
        sys.exit(1)

    # Split
    total = len(full_ds)
    n_train = int(total * TRAIN_SPLIT)
    n_val = total - n_train
    train_ds, val_ds = random_split(
        full_ds, [n_train, n_val],
        generator=torch.Generator().manual_seed(42),
    )
    # Validation uses eval transforms
    val_ds.dataset = HaGRIDDataset(hagrid_root,
                                   transform=build_transforms(False))

    train_loader = DataLoader(train_ds, batch_size=args.batch_size,
                              shuffle=True, num_workers=2, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size,
                            shuffle=False, num_workers=2, pin_memory=True)

    # ------------------------------------------------------------------
    # Model
    # ------------------------------------------------------------------
    model = models.mobilenet_v3_small(
        weights=models.MobileNet_V3_Small_Weights.IMAGENET1K_V1
    )
    in_features = model.classifier[3].in_features
    model.classifier[3] = nn.Linear(in_features, NUM_CLASSES)
    model = model.to(device)

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    # ------------------------------------------------------------------
    # Training loop
    # ------------------------------------------------------------------
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    best_acc = 0.0
    best_path = args.output

    print(f"\n[Train] Starting {args.epochs} epochs...")
    print(f"{'Epoch':>6s}  {'Train Loss':>10s}  {'Train Acc':>9s}"
          f"  {'Val Loss':>8s}  {'Val Acc':>7s}  {'Best':>7s}")
    print("-" * 60)

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_loss, train_acc = train_one_epoch(
            model, train_loader, criterion, optimizer, device)
        val_loss, val_acc = validate(
            model, val_loader, criterion, device)
        scheduler.step()

        marker = ""
        if val_acc > best_acc:
            best_acc = val_acc
            torch.save(model.state_dict(), best_path)
            marker = " ★"

        elapsed = time.time() - t0
        print(f"{epoch:4d}/{args.epochs}  {train_loss:10.4f}  {train_acc:8.4f}"
              f"  {val_loss:8.4f}  {val_acc:6.4f}  {elapsed:4.1f}s{marker}")

    print(f"\n[Train] Done. Best val acc: {best_acc:.4f}")
    print(f"[Train] Model saved to: {best_path}")


if __name__ == "__main__":
    main()
