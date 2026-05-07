#!/usr/bin/env python3
"""
Train GhostNet for palmprint verification with contrastive loss.

The dataset should be organised as per-subject directories under a common
root (e.g. PolyU Palmprint, IITD):

    <data-dir>/
        001/  img1.bmp  img2.bmp  ...
        002/  img1.bmp  img2.bmp  ...
        ...

Usage:
    python3 train.py --data-dir /path/to/PolyU
    python3 train.py --data-dir /path/to/PolyU --epochs 30 --batch-size 32
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
import torch.nn.functional as F
import torch.optim as optim
import torchvision.transforms as T
from PIL import Image
from torch.utils.data import DataLoader, Dataset, random_split

from config import (
    BATCH_SIZE,
    CONTRASTIVE_MARGIN,
    IMAGE_SIZE,
    LEARNING_RATE,
    MODEL_DIR,
    NUM_EPOCHS,
    PTH_MODEL_PATH,
    TRAIN_SPLIT,
)
from ghostnet import ghostnet_1x

# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def _collect_subjects(root):
    """Return a list of (subject_id, [img_path, ...]) for each directory."""
    subjects = []
    for entry in sorted(Path(root).iterdir()):
        if not entry.is_dir():
            continue
        images = (sorted(entry.glob("*.bmp")) +
                  sorted(entry.glob("*.jpg")) +
                  sorted(entry.glob("*.jpeg")) +
                  sorted(entry.glob("*.png")) +
                  sorted(entry.glob("*.tif")) +
                  sorted(entry.glob("*.tiff")))
        if len(images) >= 2:
            subjects.append((entry.name, [str(p) for p in images]))
    return subjects


class PalmprintPairDataset(Dataset):
    """Generates positive / negative palmprint pairs on-the-fly.

    Half the samples are positive (same subject), half negative (different
    subjects).  Images are loaded as RGB and transformed.
    """

    def __init__(self, subjects, transform=None, pairs_per_subject=5):
        self.subjects = subjects  # list of (subj_id, [paths])
        self.transform = transform
        self.num_pairs = len(subjects) * pairs_per_subject

    def __len__(self):
        return self.num_pairs

    def __getitem__(self, idx):
        is_positive = idx % 2 == 0
        seed = idx // 2

        if is_positive:
            # Pick a random subject, then two different images
            rng = random.Random(seed)
            subj_id, paths = rng.choice(self.subjects)
            if len(paths) < 2:
                # Degenerate case — fall back to same image
                img1 = img2 = self._load(paths[0])
            else:
                i1, i2 = rng.sample(range(len(paths)), 2)
                img1 = self._load(paths[i1])
                img2 = self._load(paths[i2])
            label = torch.tensor(1.0)
        else:
            # Pick two different subjects
            rng = random.Random(seed)
            if len(self.subjects) < 2:
                img1 = img2 = self._load(self.subjects[0][1][0])
            else:
                i, j = rng.sample(range(len(self.subjects)), 2)
                img1 = self._load(rng.choice(self.subjects[i][1]))
                img2 = self._load(rng.choice(self.subjects[j][1]))
            label = torch.tensor(0.0)

        if self.transform:
            img1 = self.transform(img1)
            img2 = self.transform(img2)
        return img1, img2, label

    def _load(self, path):
        return Image.open(path).convert("RGB")


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

def build_transforms(train=True):
    if train:
        return T.Compose([
            T.RandomResizedCrop(IMAGE_SIZE, scale=(0.7, 1.0)),
            T.RandomHorizontalFlip(),
            T.RandomRotation(10),
            T.ColorJitter(brightness=0.2, contrast=0.2),
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


# ---------------------------------------------------------------------------
# Loss
# ---------------------------------------------------------------------------

class ContrastiveLoss(nn.Module):
    """Standard contrastive loss with margin.

    L = y * d^2 + (1 - y) * max(0, margin - d)^2
    where d is the Euclidean distance between the two embeddings.
    """

    def __init__(self, margin=CONTRASTIVE_MARGIN):
        super().__init__()
        self.margin = margin

    def forward(self, emb1, emb2, label):
        dist = F.pairwise_distance(emb1, emb2)
        loss_pos = label * dist.pow(2)
        loss_neg = (1 - label) * F.relu(self.margin - dist).pow(2)
        return (loss_pos + loss_neg).mean()


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def train_one_epoch(model, loader, criterion, optimizer, device):
    model.train()
    running_loss = 0.0
    correct = 0
    total = 0

    for img1, img2, labels in loader:
        img1, img2 = img1.to(device), img2.to(device)
        labels = labels.to(device)

        optimizer.zero_grad()
        emb1 = model(img1)
        emb2 = model(img2)
        loss = criterion(emb1, emb2, labels)
        loss.backward()
        optimizer.step()

        running_loss += loss.item() * img1.size(0)

        # Accuracy: distance < margin/2 → predict "same person"
        with torch.no_grad():
            dists = F.pairwise_distance(emb1, emb2)
            preds = (dists < (criterion.margin / 2)).float()
            correct += preds.eq(labels).sum().item()
        total += labels.size(0)

    return running_loss / total, correct / total


@torch.no_grad()
def validate(model, loader, criterion, device):
    model.eval()
    running_loss = 0.0
    correct = 0
    total = 0
    all_dists = []
    all_labels = []

    for img1, img2, labels in loader:
        img1, img2 = img1.to(device), img2.to(device)
        labels = labels.to(device)

        emb1 = model(img1)
        emb2 = model(img2)
        loss = criterion(emb1, emb2, labels)

        running_loss += loss.item() * img1.size(0)
        dists = F.pairwise_distance(emb1, emb2)
        preds = (dists < (criterion.margin / 2)).float()
        correct += preds.eq(labels).sum().item()
        total += labels.size(0)

        all_dists.extend(dists.cpu().numpy().tolist())
        all_labels.extend(labels.cpu().numpy().tolist())

    return running_loss / total, correct / total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Train GhostNet for palmprint verification")
    parser.add_argument("--data-dir", required=True,
                        help="Path to palmprint dataset root")
    parser.add_argument("--epochs", type=int, default=NUM_EPOCHS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=LEARNING_RATE)
    parser.add_argument("--no-cuda", action="store_true")
    parser.add_argument("--output", default=PTH_MODEL_PATH)
    args = parser.parse_args()

    # -- device --
    device = torch.device("cpu")
    if not args.no_cuda and torch.cuda.is_available():
        device = torch.device("cuda")
    print(f"[Train] Device: {device}")

    # -- dataset --
    subjects = _collect_subjects(args.data_dir)
    if len(subjects) < 2:
        print(f"[Train] ERROR: need at least 2 subjects with ≥2 images each")
        print(f"  Found {len(subjects)} subjects in {args.data_dir}")
        sys.exit(1)

    print(f"[Train] {len(subjects)} subjects found")

    # Split by subject
    rng = random.Random(42)
    shuffled = subjects[:]
    rng.shuffle(shuffled)
    n_train = max(int(len(shuffled) * TRAIN_SPLIT), 2)
    train_subjects = shuffled[:n_train]
    val_subjects = shuffled[n_train:]

    train_ds = PalmprintPairDataset(train_subjects, build_transforms(True))
    val_ds = PalmprintPairDataset(val_subjects, build_transforms(False))

    train_loader = DataLoader(train_ds, batch_size=args.batch_size,
                              shuffle=True, num_workers=2, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size,
                            shuffle=False, num_workers=2, pin_memory=True)

    # -- model --
    model = ghostnet_1x().to(device)
    criterion = ContrastiveLoss(CONTRASTIVE_MARGIN)
    optimizer = optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    # -- train --
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    best_acc = 0.0
    header = (f"{'Epoch':>6s}  {'Train Loss':>10s}  {'Train Acc':>9s}"
              f"  {'Val Loss':>8s}  {'Val Acc':>7s}  {'Best':>7s}")
    print(f"\n[Train] {args.epochs} epochs, contrastive margin={CONTRASTIVE_MARGIN}")
    print(header)
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
            torch.save(model.state_dict(), args.output)
            marker = " *"

        elapsed = time.time() - t0
        print(f"{epoch:4d}/{args.epochs}  {train_loss:10.4f}  {train_acc:8.4f}"
              f"  {val_loss:8.4f}  {val_acc:6.4f}  {elapsed:4.1f}s{marker}")

    print(f"\n[Train] Done.  Best val acc: {best_acc:.4f}")
    print(f"[Train] Model saved to: {args.output}")


if __name__ == "__main__":
    main()
