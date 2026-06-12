# Chapter 8 Calibration Directory

本目录保存量化实验使用的校准样本清单。进入 `samples/chapter8` 后运行：

```bash
python3 01_collect_calibration_list.py --count 50
```

脚本会生成：

- `generated_rgb/*.npy`
- `calib_list.txt`
- `calibration_manifest.json`

正式项目中可以把 `generated_rgb/` 替换为真实验证集图片，但预处理流程必须与部署推理保持一致。
