# torch_npu编译错误集合

```python
import os
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import TensorDataset, DataLoader

device = torch.device("npu")


class LeNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 6, kernel_size=5)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(6, 16, kernel_size=5)
        self.fc1 = nn.Linear(16 * 4 * 4, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, 10)

    def forward(self, x):
        x = torch.relu(self.conv1(x))
        x = self.pool(x)
        x = torch.relu(self.conv2(x))
        x = self.pool(x)
        x = x.view(x.size(0), -1)
        x = torch.relu(self.fc1(x))
        x = torch.relu(self.fc2(x))
        x = self.fc3(x)
        return x

def load_mnist_from_raw(root_dir):
    raw = os.path.join(root_dir, "MNIST", "raw")

    # 读取 IDX 格式（大端）
    def read_images(p):
        with open(p, "rb") as f:
            data = f.read()
        magic = int.from_bytes(data[0:4], "big"); assert magic == 2051
        num = int.from_bytes(data[4:8], "big")
        rows = int.from_bytes(data[8:12], "big")
        cols = int.from_bytes(data[12:16], "big")
        imgs = np.frombuffer(data, dtype=np.uint8, offset=16)
        imgs = imgs.reshape(num, 1, rows, cols).astype(np.float32) / 255.0
        return imgs

    def read_labels(p):
        with open(p, "rb") as f:
            data = f.read()
        magic = int.from_bytes(data[0:4], "big"); assert magic == 2049
        num = int.from_bytes(data[4:8], "big")
        labels = np.frombuffer(data, dtype=np.uint8, offset=8)
        assert labels.shape[0] == num
        return labels.astype(np.int64)

    x_train = torch.from_numpy(read_images(os.path.join(raw, "train-images-idx3-ubyte")))
    y_train = torch.from_numpy(read_labels(os.path.join(raw, "train-labels-idx1-ubyte")))
    x_test = torch.from_numpy(read_images(os.path.join(raw, "t10k-images-idx3-ubyte")))
    y_test = torch.from_numpy(read_labels(os.path.join(raw, "t10k-labels-idx1-ubyte")))
    return (x_train, y_train), (x_test, y_test)

def train_and_eval(epochs=10, batch_size=256, lr=0.01):
    # 数据根目录：chapter3/LeNet/data/MNIST/raw
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_root = os.path.join(script_dir, "data")
    (x_train, y_train), (x_test, y_test) = load_mnist_from_raw(data_root)

    train_loader = DataLoader(TensorDataset(x_train, y_train), batch_size=batch_size, shuffle=True)
    test_loader = DataLoader(TensorDataset(x_test, y_test), batch_size=batch_size, shuffle=False)

    model = LeNet().to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.SGD(model.parameters(), lr=lr, momentum=0.9)

    train_acc_history = []
    test_acc_history = []

    for epoch in range(1, epochs + 1):
        # 训练并统计训练集精度
        model.train()
        correct = 0; total = 0
        for inputs, labels in train_loader:
            inputs = inputs.to(device); labels = labels.to(device)
            outputs = model(inputs)
            loss = criterion(outputs, labels)
            optimizer.zero_grad(); loss.backward(); optimizer.step()
            preds = outputs.argmax(dim=1)
            correct += (preds == labels).sum().item()
            total += labels.size(0)
        train_acc = correct / total
        train_acc_history.append(train_acc)

        # 测试集精度
        model.eval()
        correct_t = 0; total_t = 0
        with torch.no_grad():
            for inputs, labels in test_loader:
                inputs = inputs.to(device); labels = labels.to(device)
                outputs = model(inputs)
                preds = outputs.argmax(dim=1)
                correct_t += (preds == labels).sum().item()
                total_t += labels.size(0)
        test_acc = correct_t / total_t
        test_acc_history.append(test_acc)

        print(f"Epoch {epoch}: Train Acc {train_acc:.4f} | Test Acc {test_acc:.4f}")

    # 画精度曲线
    try:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(6, 4))
        plt.plot(train_acc_history, label="Train Acc")
        plt.plot(test_acc_history, label="Test Acc")
        plt.xlabel("Epoch"); plt.ylabel("Accuracy")
        plt.title("LeNet MNIST Accuracy")
        plt.legend()
        out_path = os.path.join(script_dir, "accuracy_curve.png")
        plt.savefig(out_path, bbox_inches="tight")
        print(f"Saved accuracy curve to: {out_path}")
    except Exception as e:
        print(f"Plotting skipped: {e}")

if __name__ == "__main__":
    train_and_eval(epochs=10, batch_size=256, lr=0.01)
```


```bash
  File "/home/HwHiAiUser/Documents/samples/chapter3/LeNet/./lenet_npu.py", line 124, in <module>
    train_and_eval(epochs=10, batch_size=256, lr=0.01)
  File "/home/HwHiAiUser/Documents/samples/chapter3/LeNet/./lenet_npu.py", line 88, in train_and_eval
    correct += (preds == labels).sum().item()
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
RuntimeError: The Inner error is reported as above. The process exits for this inner error, and the current working operator name is MaxPoolWithArgmaxV1.
Since the operator is called asynchronously, the stacktrace may be inaccurate. If you want to get the accurate stacktrace, please set the environment variable ASCEND_LAUNCH_BLOCKING=1.
Note: ASCEND_LAUNCH_BLOCKING=1 will force ops to run in synchronous mode, resulting in performance degradation. Please unset ASCEND_LAUNCH_BLOCKING in time after debugging.
[ERROR] 2025-12-20-22:25:54 (PID:321725, Device:0, RankID:-1) ERR00100 PTA call acl api failed.
[PID: 321725] 2025-12-20-22:25:54.410.921 Unsupported_Operator(EZ3002): Optype [MaxPoolWithArgmaxV1] of Ops kernel [AIcoreEngine] is unsupported. Reason: [tbe-custom]:op type MaxPoolWithArgmaxV1 is not found in this op store.[tbe-custom]:op type MaxPoolWithArgmaxV1 is not found in this op store.[Dynamic shape check]: data type DT_FLOAT of input [x] is not supported. All supported data type and format of tensor input0.x is: Data Type: {DT_FLOAT16}Format:{NC1HWC0}[Static shape check]:data type DT_FLOAT of input [x] is not supported. All supported data type and format of tensor input0.x is: Data Type: {DT_FLOAT16}Format:{NC1HWC0}.
        Possible Cause: The operator type is unsupported in the operator information library due to specification mismatch.
        Solution: Submit an issue to request for support at https://gitee.com/ascend, or remove this type of operators from your model.
        TraceBack (most recent call last):
        Optype [TransData] of Ops kernel [AIcoreEngine] is unsupported. Reason: [tbe-custom]:op type TransData is not found in this op store.[tbe-custom]:op type TransData is not found in this op store.[Dynamic shape check]: The format and dtype is not precisely equivalent to format and dtype in op information library[Static shape check]:The format and dtype is not precisely equivalent to format and dtype in op information library.
        Optype [TransData] of Ops kernel [DNN_VM_HOST_CPU_OP_STORE] is unsupported. Reason: Transdata op, groups should be greater than 1, but now is 1.
        Optype [TransData] of Ops kernel [aicpu_ascend_kernel] is unsupported. Reason: Transdata op, groups should be greater than 1, but now is 1.
        No supported Ops kernel and engine are found for [MaxPoolWithArgmaxV15], optype [MaxPoolWithArgmaxV1].
        No supported Ops kernel and engine are found for [trans_TransData_6], optype [TransData].
        Failed to select engine for [trans_TransData_6][TransData].[FUNC:operator()][FILE:engine_place.cc][LINE:150]
        Failed to select engine for [MaxPoolWithArgmaxV15][MaxPoolWithArgmaxV1].[FUNC:operator()][FILE:engine_place.cc][LINE:150]
        RunAllSubgraphs failed, graph=online.[FUNC:RunAllSubgraphs][FILE:engine_place.cc][LINE:123]
        build graph failed, graph id:4, ret:4294967295[FUNC:BuildModelWithGraphId][FILE:ge_generator.cc][LINE:1594]
        [Build][SingleOpModel]call ge interface generator.BuildSingleOpModel failed. ge result = 4294967295[FUNC:ReportCallError][FILE:log_inner.cpp][LINE:162]
        [Build][Op]Fail to build op model[FUNC:ReportInnerError][FILE:log_inner.cpp][LINE:146]
        build op model failed, result = 500002[FUNC:ReportInnerError][FILE:log_inner.cpp][LINE:146]
```
