## resnet-ssd 昇腾310B推理优化

### 预处理是在cpu完成的


```bash
atc --model=models/ssd_resnet50.onnx --framework=5 --output=models/ssd_resnet50 --input_shape="input:1,3,300,300" --soc_version=Ascend310B4
```

```pyhon
def preprocess(image, img_size=300):
    if image.mode != "RGB":
        image = image.convert("RGB")

    orig_w, orig_h = image.size
    image_resized = image.resize((img_size, img_size))

    img_array = np.array(image_resized, dtype=np.float32) / 255.0
    img_array = img_array.transpose(2, 0, 1)

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)

    img_tensor = (img_array - mean) / std
    img_numpy = np.expand_dims(img_tensor, axis=0)
    return img_numpy, orig_w, orig_h
```


```bash
================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 382.14s
全流程 FPS: 12.96
纯预处理耗时: 66.85s, FPS: 74.08
纯推理耗时: 132.48s, FPS: 37.38
纯解码耗时: 134.30s, FPS: 36.87
推理+解码总耗时: 266.78s, FPS: 18.56
预处理+推理+解码耗时: 333.63s, FPS: 14.84
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.33s)
creating index...
index created!
Loading and preparing results...
DONE (t=6.21s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=119.94s).
Accumulating evaluation results...
DONE (t=18.85s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.252
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.425
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.261
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.084
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.308
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.426
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.238
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.347
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.364
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.166
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.446
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.542
```

### 利用AIPP进行预处理

首先创建一个ATC转换所需要的配置文件`ssd_resnet50_aipp.cfg`

```bash
atc --model=models/ssd_resnet50.onnx --framework=5 --output=models/ssd_resnet50 --input_shape="input:1,3,300,300" --soc_version=Ascend310B4 --insert_op_conf=models/ssd_resnet50_aipp.cfg
```

```text
aipp_op {
    aipp_mode: static
    related_input_rank: 0

    input_format: RGB888_U8
    src_image_size_w: 300
    src_image_size_h: 300

    # mean_chn_* 在该版本 ATC 中需为整数
    mean_chn_0: 124
    mean_chn_1: 116
    mean_chn_2: 104

    var_reci_chn_0: 0.0171247538
    var_reci_chn_1: 0.0175070028
    var_reci_chn_2: 0.0174291939

    rbuv_swap_switch: false
    ax_swap_switch: false
}
```

```python
# 预处理函数：调整图像大小并转换为 uint8 格式，适配 AIPP 输入要求
def preprocess(image, img_size=300):
    if image.mode != "RGB":
        image = image.convert("RGB")

    orig_w, orig_h = image.size
    image_resized = image.resize((img_size, img_size))

    # AIPP 输入用 uint8 原图数据（RGB）
    img_array = np.array(image_resized, dtype=np.uint8)   # HWC
    img_numpy = np.expand_dims(img_array, axis=0)         # NHWC
    img_numpy = np.ascontiguousarray(img_numpy)

    return img_numpy, orig_w, orig_h
```

```bash
================ 性能测试结果 ================
处理图片数量: 4952
总耗时: 360.67s
全流程 FPS: 13.73
纯预处理耗时: 48.55s, FPS: 101.99
纯推理耗时: 126.79s, FPS: 39.06
纯解码耗时: 135.97s, FPS: 36.42
推理+解码总耗时: 262.76s, FPS: 18.85
预处理+推理+解码耗时: 311.31s, FPS: 15.91
============================================
正在使用 coco_gt_temp.json 计算 mAP ...
loading annotations into memory...
Done (t=0.33s)
creating index...
index created!
Loading and preparing results...
DONE (t=6.21s)
creating index...
index created!
Running per image evaluation...
Evaluate annotation type *bbox*
DONE (t=121.57s).
Accumulating evaluation results...
DONE (t=19.08s).
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.254
 Average Precision  (AP) @[ IoU=0.50      | area=   all | maxDets=100 ] = 0.425
 Average Precision  (AP) @[ IoU=0.75      | area=   all | maxDets=100 ] = 0.262
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.084
 Average Precision  (AP) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.308
 Average Precision  (AP) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.430
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=  1 ] = 0.239
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets= 10 ] = 0.349
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=   all | maxDets=100 ] = 0.366
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= small | maxDets=100 ] = 0.166
 Average Recall     (AR) @[ IoU=0.50:0.95 | area=medium | maxDets=100 ] = 0.448
 Average Recall     (AR) @[ IoU=0.50:0.95 | area= large | maxDets=100 ] = 0.546
```