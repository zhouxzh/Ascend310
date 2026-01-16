import cv2
import yaml
import argparse
import time
import numpy as np
from models.utils.acl_inference import AclInference
from models.tracking.deepsort import DeepSORT
from models.utils.postprocess import non_max_suppression, scale_coords

def main(config_path):
    # 加载配置
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # 1. 初始化昇腾推理引擎
    acl_inference = AclInference(config['model']['detection_model'])

    # 2. 加载跟踪算法
    tracker = DeepSORT(max_age=config['tracking']['max_age'], min_hits=config['tracking']['min_hits'])

    # 3. 打开视频流
    video_source = config['video']['source']
    cap = cv2.VideoCapture(video_source)

    if not cap.isOpened():
        print(f"无法打开视频源: {video_source}")
        return

    # 视频保存设置
    if config['video']['save_video']:
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out = cv2.VideoWriter(f"{config['video']['output_dir']}/output.mp4", fourcc, fps, (width, height))

    prev_time = 0
    img_size = config['model']['input_shape']

    # 4. 循环处理每一帧
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # 预处理: 将图像调整为模型输入尺寸
        img0 = frame.copy()
        img = letterbox(frame, new_shape=img_size)[0]
        img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR to RGB, HWC to CHW
        img = np.ascontiguousarray(img)

        # - 目标检测 (使用昇腾NPU)
        detections_raw = acl_inference.inference([img])
        
        # - 后处理
        pred = detections_raw[0] # 假设第一个输出是检测结果
        pred = non_max_suppression(pred, config['model']['conf_thres'], config['model']['iou_thres'])
        
        detections = []
        if pred[0] is not None and len(pred[0]):
            det = pred[0]
            det[:, :4] = scale_coords(img.shape[1:], det[:, :4], img0.shape).round()
            for *xyxy, conf, cls in reversed(det):
                detections.append([*xyxy, conf])

        # - 跟踪算法更新
        tracked_objects = tracker.update(np.array(detections), frame)

        # - 绘制跟踪轨迹
        for obj in tracked_objects:
            x1, y1, x2, y2 = map(int, obj.bbox)
            track_id = obj.track_id
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(frame, f"ID: {track_id}", (x1, y1 - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        # - 显示结果
        if config['display']['show_fps']:
            curr_time = time.time()
            fps = 1 / (curr_time - prev_time)
            prev_time = curr_time
            cv2.putText(frame, f"FPS: {int(fps)}", (10, 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
        
        cv2.imshow("Real-time Object Tracking", frame)

        if config['video']['save_video']:
            out.write(frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # 5. 释放资源
    cap.release()
    if config['video']['save_video']:
        out.release()
    cv2.destroyAllWindows()
    acl_inference.release()

def letterbox(img, new_shape=(640, 640), color=(114, 114, 114), auto=True, scaleFill=False, scaleup=True):
    shape = img.shape[:2]  # current shape [height, width]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    # Scale ratio (new / old)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    if not scaleup:
        r = min(r, 1.0)

    # Compute padding
    ratio = r, r  # width, height ratios
    new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
    dw, dh = new_shape[1] - new_unpad[0], new_shape[0] - new_unpad[1]  # wh padding
    if auto:  # minimum rectangle
        dw, dh = np.mod(dw, 32), np.mod(dh, 32)  # wh padding
    elif scaleFill:  # stretch
        dw, dh = 0.0, 0.0
        new_unpad = (new_shape[1], new_shape[0])
        ratio = new_shape[1] / shape[1], new_shape[0] / shape[0]  # width, height ratios

    dw /= 2  # divide padding into 2 sides
    dh /= 2

    if shape[::-1] != new_unpad:
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return img, ratio, (dw, dh)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, default='configs/config.yaml', help='配置文件路径')
    args = parser.parse_args()
    main(args.config)