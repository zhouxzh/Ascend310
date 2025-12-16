import cv2
import os
import numpy as np
from PIL import Image

def preprocess_data(video_path, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    cap = cv2.VideoCapture(video_path)
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # 视频帧提取
        frame_filename = os.path.join(output_dir, f"frame_{frame_count:04d}.jpg")
        cv2.imwrite(frame_filename, frame)

        # 数据增强示例 (翻转)
        flipped_frame = cv2.flip(frame, 1)
        flipped_filename = os.path.join(output_dir, f"frame_{frame_count:04d}_flipped.jpg")
        cv2.imwrite(flipped_filename, flipped_frame)

        frame_count += 1

    cap.release()
    print(f"从 {video_path} 提取了 {frame_count} 帧并进行了预处理。")

if __name__ == "__main__":
    # 此处为示例，应替换为实际的数据集路径
    preprocess_data("test_video.mp4", "preprocessed_data")