import os
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from datasets import load_dataset
from torchvision.transforms import Compose, ToTensor, Normalize, RandomCrop, RandomHorizontalFlip, RandomRotation, ColorJitter # 增加更多增强
from torch.utils.tensorboard import SummaryWriter # 导入 SummaryWriter

# 自定义 ResNet 模型组件
class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(BasicBlock, self).__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x
        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)
        out = self.conv2(out)
        out = self.bn2(out)
        if self.downsample is not None:
            identity = self.downsample(x)
        out += identity
        out = self.relu(out)
        return out

# 移除 Bottleneck 类，ResNet18 使用 BasicBlock
class ResNet(nn.Module):
    def __init__(self, block, layers, num_classes=200):
        super(ResNet, self).__init__()
        self.inplanes = 64
        # 适配 Tiny ImageNet (64x64): 使用 3x3 卷积, stride=1, 移除 MaxPool
        self.conv1 = nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        # self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1) # 移除

        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
        self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
        self.layer4 = self._make_layer(block, 512, layers[3], stride=2)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.dropout = nn.Dropout(p=0.5) # 增加 Dropout 层
        self.fc = nn.Linear(512 * block.expansion, num_classes)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(planes * block.expansion),
            )
        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes))
        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        # x = self.maxpool(x) # 移除
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        x = self.dropout(x) # 应用 Dropout
        x = self.fc(x)
        return x

def train_resnet18_on_tiny_imagenet():
    # 定义保存路径
    data_dir = './data'
    model_dir = './model'
    log_dir = './logs/resnet18_tiny_imagenet' # TensorBoard 日志目录
    os.makedirs(model_dir, exist_ok=True)

    # 初始化 TensorBoard Writer
    writer = SummaryWriter(log_dir)

    # 加载数据集 (指定 cache_dir)
    dataset = load_dataset('zh-plus/tiny-imagenet', cache_dir=data_dir) # 加载完整数据集以获取 train 和 valid
    
    # 数据预处理 (增加数据增强)
    # 训练集：增加随机裁剪、水平翻转、旋转和颜色抖动
    train_transform = Compose([
        RandomCrop(64, padding=4),
        RandomHorizontalFlip(),
        RandomRotation(15), # 增加随机旋转
        ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.1), # 增加颜色抖动
        ToTensor(),
        Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])

    # 验证集：仅保持标准化
    val_transform = Compose([
        ToTensor(),
        Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])
    
    # 自定义数据集类
    class TinyImageNetDataset(torch.utils.data.Dataset):
        def __init__(self, dataset, transform=None):
            self.dataset = dataset
            self.transform = transform
        
        def __len__(self):
            return len(self.dataset)
        
        def __getitem__(self, idx):
            item = self.dataset[idx]
            image = item['image']
            label = item['label']
            image = image.convert('RGB')  # 确保图像为RGB格式
            if self.transform:
                image = self.transform(image)
            return image, label
    
    # 使用不同的 transform
    train_dataset = TinyImageNetDataset(dataset['train'], transform=train_transform)
    val_dataset = TinyImageNetDataset(dataset['valid'], transform=val_transform) # 假设 valid split 存在
    
    train_loader = DataLoader(train_dataset, batch_size=128, shuffle=True) # 将 batch_size 适当调大一点，例如 64 或 128
    val_loader = DataLoader(val_dataset, batch_size=128, shuffle=False)
    
    # 定义模型 (使用自定义 ResNet18: BasicBlock, [2, 2, 2, 2])
    model = ResNet(BasicBlock, [2, 2, 2, 2], num_classes=200)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model.to(device)
    
    # 损失函数和优化器 (更换为 SGD + Momentum + Weight Decay 以抗过拟合)
    # 启用标签平滑 (Label Smoothing) 以防止过拟合
    criterion = nn.CrossEntropyLoss(label_smoothing=0.1)
    # 使用 SGD 替代 Adam，初始学习率设为 0.1，weight_decay=5e-4 用于正则化
    optimizer = optim.SGD(model.parameters(), lr=0.1, momentum=0.9, weight_decay=5e-4)
    # 添加学习率调度器，在第 30, 60, 90 epoch 衰减学习率
    scheduler = optim.lr_scheduler.MultiStepLR(optimizer, milestones=[30, 60, 90], gamma=0.1)
    
    # 训练循环
    num_epochs = 150
    best_acc = 0.0
    
    for epoch in range(num_epochs):
        model.train()
        running_loss = 0.0
        correct_train = 0
        total_train = 0
        
        # 移除 tqdm，使用简单的迭代
        for i, (images, labels) in enumerate(train_loader):
            images, labels = images.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(images)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            
            running_loss += loss.item()
            _, predicted = torch.max(outputs.data, 1)
            total_train += labels.size(0)
            correct_train += (predicted == labels).sum().item()
            
            # 记录 iteration 级别的 loss (可选)
            if i % 100 == 0:
                writer.add_scalar('Training/Iter_Loss', loss.item(), epoch * len(train_loader) + i)
            
        train_loss = running_loss / len(train_loader)
        train_acc = 100. * correct_train / total_train
        
        # 记录 epoch 级别的训练指标
        writer.add_scalar('Training/Epoch_Loss', train_loss, epoch + 1)
        writer.add_scalar('Training/Epoch_Accuracy', train_acc, epoch + 1)

        # 更新学习率并记录
        current_lr = optimizer.param_groups[0]['lr']
        writer.add_scalar('Training/Learning_Rate', current_lr, epoch + 1)
        scheduler.step()
        
        # 验证阶段
        model.eval()
        correct_val = 0
        total_val = 0
        
        with torch.no_grad():
            for images, labels in val_loader:
                images, labels = images.to(device), labels.to(device)
                outputs = model(images)
                _, predicted = torch.max(outputs.data, 1)
                total_val += labels.size(0)
                correct_val += (predicted == labels).sum().item()

        val_acc = 100. * correct_val / total_val
        
        # 记录验证指标
        writer.add_scalar('Validation/Accuracy', val_acc, epoch + 1)
        
        print(f'Epoch {epoch+1}: LR: {current_lr:.6f}, Train Loss: {train_loss:.4f}, Train Acc: {train_acc:.2f}%, Val Acc: {val_acc:.2f}%')

        # 保存 Checkpoint (每个 epoch 保存一次)
        checkpoint_path = os.path.join(model_dir, f'checkpoint_epoch.pth')
        torch.save({
            'epoch': epoch + 1,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimizer.state_dict(),
            'loss': train_loss,
            'val_acc': val_acc
        }, checkpoint_path)
        print(f"Checkpoint saved to {checkpoint_path}")

        # 保存为ONNX (每当验证集准确率提升时保存，或者最后保存)
        if val_acc > best_acc:
             best_acc = val_acc
             # 保存最佳模型逻辑...
    
    # 关闭 writer
    writer.close()

    # 保存为ONNX (输入尺寸调整为 64x64)
    dummy_input = torch.randn(1, 3, 64, 64).to(device)
    onnx_path = os.path.join(model_dir, 'resnet18_tiny_imagenet.onnx')
    torch.onnx.export(model, dummy_input, onnx_path, verbose=True)
    print(f"Model saved as ONNX: {onnx_path}")

if __name__ == '__main__':
    train_resnet18_on_tiny_imagenet()

