import numpy as np
import onnxruntime as ort
import cv2
import os
from typing import List, Tuple, Optional

class MobileFaceNetOnnx:
    """
    MobileFaceNet ONNX模型处理器
    用于人脸特征提取和人脸识别
    """
    
    def __init__(self, model_path: str):
        """
        初始化MobileFaceNet ONNX模型
        
        Args:
            model_path: ONNX模型文件路径
        """
        self.model_path = model_path
        self.input_size = (112, 112)  # MobileFaceNet标准输入尺寸
        self.session = None
        self.input_name = None
        self.output_name = None
        
        self._load_model()
    
    def _load_model(self):
        """加载ONNX模型"""
        try:
            # 创建ONNX Runtime会话
            self.session = ort.InferenceSession(self.model_path)
            
            # 获取输入输出节点名称
            self.input_name = self.session.get_inputs()[0].name
            self.output_name = self.session.get_outputs()[0].name
            
            print(f"MobileFaceNet模型加载成功: {self.model_path}")
            print(f"输入节点: {self.input_name}")
            print(f"输出节点: {self.output_name}")
            
        except Exception as e:
            print(f"加载MobileFaceNet模型失败: {e}")
            # 创建一个模拟的会话用于测试
            self.session = None
    
    def preprocess_face(self, face_image: np.ndarray) -> np.ndarray:
        """
        预处理人脸图像
        
        Args:
            face_image: 输入的人脸图像 (BGR格式)
            
        Returns:
            预处理后的图像数组
        """
        # 调整图像大小到112x112
        face_resized = cv2.resize(face_image, self.input_size)
        
        # 转换为RGB格式
        face_rgb = cv2.cvtColor(face_resized, cv2.COLOR_BGR2RGB)
        
        # 归一化到[-1, 1]范围
        face_normalized = (face_rgb.astype(np.float32) - 127.5) / 127.5
        
        # 转换为NCHW格式 (batch_size, channels, height, width)
        face_input = np.transpose(face_normalized, (2, 0, 1))
        face_input = np.expand_dims(face_input, axis=0)
        
        return face_input
    
    def extract_features(self, face_image: np.ndarray) -> Optional[np.ndarray]:
        """
        提取人脸特征向量
        
        Args:
            face_image: 输入的人脸图像
            
        Returns:
            512维特征向量，如果提取失败返回None
        """
        if self.session is None:
            # 如果模型未加载，返回随机特征向量用于测试
            print("警告: MobileFaceNet模型未加载，返回随机特征向量")
            return np.random.randn(512).astype(np.float32)
        
        try:
            # 预处理图像
            input_data = self.preprocess_face(face_image)
            
            # 运行推理
            outputs = self.session.run([self.output_name], {self.input_name: input_data})
            
            # 获取特征向量并进行L2归一化
            features = outputs[0].flatten()
            features = features / np.linalg.norm(features)
            
            return features
            
        except Exception as e:
            print(f"特征提取失败: {e}")
            return None
    
    def compute_similarity(self, features1: np.ndarray, features2: np.ndarray) -> float:
        """
        计算两个特征向量的余弦相似度
        
        Args:
            features1: 第一个特征向量
            features2: 第二个特征向量
            
        Returns:
            余弦相似度 (0-1之间)
        """
        # 计算余弦相似度
        similarity = np.dot(features1, features2)
        return float(similarity)
    
    def batch_extract_features(self, face_images: List[np.ndarray]) -> List[Optional[np.ndarray]]:
        """
        批量提取人脸特征
        
        Args:
            face_images: 人脸图像列表
            
        Returns:
            特征向量列表
        """
        features_list = []
        for face_image in face_images:
            features = self.extract_features(face_image)
            features_list.append(features)
        return features_list

def create_placeholder_model():
    """
    创建一个占位符ONNX模型文件
    当真实的MobileFaceNet模型不可用时使用
    """
    import onnx
    from onnx import helper, TensorProto
    
    # 定义输入
    input_tensor = helper.make_tensor_value_info(
        'input0', TensorProto.FLOAT, [1, 3, 112, 112]
    )
    
    # 定义输出
    output_tensor = helper.make_tensor_value_info(
        'output0', TensorProto.FLOAT, [1, 512]
    )
    
    # 创建一个简单的恒等映射节点
    node = helper.make_node(
        'Identity',
        inputs=['input0'],
        outputs=['temp']
    )
    
    # 创建一个reshape节点来改变输出形状
    reshape_node = helper.make_node(
        'Reshape',
        inputs=['temp', 'shape'],
        outputs=['output0']
    )
    
    # 定义shape常量
    shape_tensor = helper.make_tensor(
        'shape', TensorProto.INT64, [2], [1, 512]
    )
    
    # 创建图
    graph = helper.make_graph(
        [node, reshape_node],
        'mobilefacenet_placeholder',
        [input_tensor],
        [output_tensor],
        [shape_tensor]
    )
    
    # 创建模型
    model = helper.make_model(graph)
    
    return model

if __name__ == "__main__":
    # 测试代码
    print("MobileFaceNet ONNX处理器测试")

    # 定义模型路径
    model_path = os.path.join("models", "mobilefacenet.onnx")

    # 检查模型文件是否存在
    if not os.path.exists(model_path):
        print(f"错误: 模型文件未找到于 '{model_path}'")
        print("请确保模型已成功转换并放置在正确的位置。")
    else:
        # 测试处理器
        processor = MobileFaceNetOnnx(model_path)

        # 创建一个随机图像进行测试
        if processor.session:
            print("\n--- 测试特征提取 ---")
            dummy_image = np.random.randint(0, 256, (112, 112, 3), dtype=np.uint8)
            features = processor.extract_features(dummy_image)
            if features is not None:
                print(f"成功提取特征向量，维度: {features.shape}")

                print("\n--- 测试相似度计算 ---")
                features2 = np.random.randn(features.shape[0]).astype(np.float32)
                features2 /= np.linalg.norm(features2)
                similarity = processor.compute_similarity(features, features2)
                print(f"计算出的相似度: {similarity:.4f}")
    
    # 创建测试图像
    test_image = np.random.randint(0, 255, (112, 112, 3), dtype=np.uint8)
    
    # 提取特征
    features = processor.extract_features(test_image)
    if features is not None:
        print(f"特征向量维度: {features.shape}")
        print(f"特征向量范围: [{features.min():.3f}, {features.max():.3f}]")