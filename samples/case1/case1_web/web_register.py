#!/usr/bin/env python3
"""
Web版人脸注册工具
用于批量注册人脸图片到系统中
"""

import cv2
import os
import argparse
from mtcnn.mtcnn import MTCNN
import numpy as np
from pathlib import Path

def register_faces_from_folder(name, folder_path, output_dir='datasets'):
    """
    从文件夹中的图片注册人脸
    
    Args:
        name: 人员姓名
        folder_path: 包含人脸图片的文件夹路径
        output_dir: 输出目录
    """
    if not os.path.exists(folder_path):
        print(f"错误: 文件夹不存在 - {folder_path}")
        return False
    
    # 创建输出目录
    save_path = os.path.join(output_dir, name)
    if not os.path.exists(save_path):
        os.makedirs(save_path)
        print(f"创建目录: {save_path}")
    
    # 初始化MTCNN
    print("初始化人脸检测器...")
    detector = MTCNN()
    
    # 支持的图片格式
    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif'}
    
    # 获取所有图片文件
    image_files = []
    for ext in image_extensions:
        image_files.extend(Path(folder_path).glob(f'*{ext}'))
        image_files.extend(Path(folder_path).glob(f'*{ext.upper()}'))
    
    if not image_files:
        print(f"错误: 在文件夹 {folder_path} 中未找到图片文件")
        return False
    
    print(f"找到 {len(image_files)} 个图片文件")
    
    saved_count = 0
    for i, img_path in enumerate(image_files):
        print(f"处理图片 {i+1}/{len(image_files)}: {img_path.name}")
        
        try:
            # 读取图片
            img = cv2.imread(str(img_path))
            if img is None:
                print(f"  跳过: 无法读取图片")
                continue
            
            # 检测人脸
            faces = detector.detect_faces(img)
            
            if not faces:
                print(f"  跳过: 未检测到人脸")
                continue
            
            # 保存检测到的人脸
            for j, face in enumerate(faces):
                x, y, w, h = face['box']
                confidence = face['confidence']
                
                if confidence < 0.9:  # 置信度阈值
                    print(f"  跳过人脸 {j+1}: 置信度过低 ({confidence:.2f})")
                    continue
                
                # 提取人脸区域
                x1, y1, x2, y2 = max(0, x), max(0, y), x + w, y + h
                face_img = img[y1:y2, x1:x2]
                
                if face_img.size == 0:
                    continue
                
                # 保存人脸图片
                face_filename = f"{name}_{saved_count + 1}.jpg"
                face_path = os.path.join(save_path, face_filename)
                cv2.imwrite(face_path, face_img)
                
                print(f"  保存人脸: {face_filename} (置信度: {confidence:.2f})")
                saved_count += 1
                
        except Exception as e:
            print(f"  错误: 处理图片时出错 - {e}")
            continue
    
    print(f"\n注册完成!")
    print(f"- 姓名: {name}")
    print(f"- 保存位置: {save_path}")
    print(f"- 成功保存: {saved_count} 张人脸图片")
    
    return saved_count > 0

def register_single_image(name, image_path, output_dir='datasets'):
    """
    从单张图片注册人脸
    
    Args:
        name: 人员姓名
        image_path: 图片路径
        output_dir: 输出目录
    """
    if not os.path.exists(image_path):
        print(f"错误: 图片文件不存在 - {image_path}")
        return False
    
    # 创建输出目录
    save_path = os.path.join(output_dir, name)
    if not os.path.exists(save_path):
        os.makedirs(save_path)
    
    # 初始化MTCNN
    print("初始化人脸检测器...")
    detector = MTCNN()
    
    # 读取图片
    img = cv2.imread(image_path)
    if img is None:
        print(f"错误: 无法读取图片 - {image_path}")
        return False
    
    # 检测人脸
    print("检测人脸...")
    faces = detector.detect_faces(img)
    
    if not faces:
        print("错误: 未检测到人脸")
        return False
    
    print(f"检测到 {len(faces)} 个人脸")
    
    # 保存所有检测到的人脸
    saved_count = 0
    for i, face in enumerate(faces):
        x, y, w, h = face['box']
        confidence = face['confidence']
        
        print(f"人脸 {i+1}: 置信度 {confidence:.2f}")
        
        if confidence < 0.9:
            print(f"  跳过: 置信度过低")
            continue
        
        # 提取人脸区域
        x1, y1, x2, y2 = max(0, x), max(0, y), x + w, y + h
        face_img = img[y1:y2, x1:x2]
        
        if face_img.size == 0:
            continue
        
        # 保存人脸图片
        face_filename = f"{name}_{saved_count + 1}.jpg"
        face_path = os.path.join(save_path, face_filename)
        cv2.imwrite(face_path, face_img)
        
        print(f"  保存: {face_filename}")
        saved_count += 1
    
    print(f"\n注册完成! 保存了 {saved_count} 张人脸图片")
    return saved_count > 0

def main():
    parser = argparse.ArgumentParser(description='Web版人脸注册工具')
    parser.add_argument('name', help='人员姓名')
    parser.add_argument('--folder', '-f', help='包含人脸图片的文件夹路径')
    parser.add_argument('--image', '-i', help='单张图片路径')
    parser.add_argument('--output', '-o', default='datasets', help='输出目录 (默认: datasets)')
    
    args = parser.parse_args()
    
    if not args.folder and not args.image:
        print("错误: 请指定 --folder 或 --image 参数")
        parser.print_help()
        return False
    
    if args.folder and args.image:
        print("错误: --folder 和 --image 参数不能同时使用")
        return False
    
    print("=" * 50)
    print("Web版人脸注册工具")
    print("=" * 50)
    
    success = False
    if args.folder:
        success = register_faces_from_folder(args.name, args.folder, args.output)
    elif args.image:
        success = register_single_image(args.name, args.image, args.output)
    
    if success:
        print("\n✓ 注册成功! 现在可以在Web界面中进行人脸识别了。")
    else:
        print("\n✗ 注册失败!")
    
    return success

if __name__ == '__main__':
    import sys
    success = main()
    sys.exit(0 if success else 1)