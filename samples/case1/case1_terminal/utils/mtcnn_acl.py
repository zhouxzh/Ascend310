import numpy as np
import cv2
from .model_processor import ModelProcessor

class MTCNNAcl:
    """
    MTCNN face detector using Ascend NPU
    """
    def __init__(self, acl_resource, pnet_path, rnet_path, onet_path):
        self.pnet = ModelProcessor(acl_resource, pnet_path)
        self.rnet = ModelProcessor(acl_resource, rnet_path)
        self.onet = ModelProcessor(acl_resource, onet_path)
        self.pnet.load_model()
        self.rnet.load_model()
        self.onet.load_model()

        self.thresholds = [0.6, 0.7, 0.7]
        self.factor = 0.709
        self.min_face_size = 20

    def detect_faces(self, image):
        """
        Detect faces in the given image
        
        Args:
            image: Input image in BGR format (OpenCV format)
            
        Returns:
            List of detected faces with bounding boxes and confidence scores
        """
        if len(image.shape) == 3 and image.shape[2] == 3:
            img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            img = image
            
        h, w = img.shape[:2]
        
        # Stage 1: PNet
        boxes = self._stage1(img, h, w)
        if len(boxes) == 0:
            return []
            
        # Stage 2: RNet
        boxes = self._stage2(img, boxes)
        if len(boxes) == 0:
            return []
            
        # Stage 3: ONet
        boxes = self._stage3(img, boxes)
        
        faces = []
        for box in boxes:
            x1, y1, x2, y2, confidence = box[:5]
            faces.append({
                'box': [int(x1), int(y1), int(x2-x1), int(y2-y1)],
                'confidence': float(confidence)
            })
            
        return faces

    def _stage1(self, img, h, w):
        """Stage 1: PNet for proposal generation"""
        img_norm = (img.astype(np.float32) - 127.5) / 128.0
        
        scales = self._calculate_scales(h, w)
        boxes = []
        
        for scale in scales:
            hs = int(np.ceil(h * scale))
            ws = int(np.ceil(w * scale))
            img_resized = cv2.resize(img_norm, (ws, hs))
            
            img_input = np.transpose(img_resized, (2, 0, 1))
            img_input = np.expand_dims(img_input, axis=0).astype(np.float32)
            
            outputs = self.pnet.predict(img_input)
            prob = outputs[1][0, 1, :, :]
            reg = outputs[0][0, :, :, :]
            
            boxes_scale = self._generate_bbox(prob, reg, scale, self.thresholds[0])
            boxes.extend(boxes_scale)
        
        if len(boxes) == 0:
            return np.array([])
            
        boxes = np.array(boxes)
        pick = self._nms(boxes, 0.5)
        return boxes[pick]

    def _stage2(self, img, boxes):
        """Stage 2: RNet for refinement"""
        if len(boxes) == 0:
            return np.array([])
            
        patches = []
        for box in boxes:
            x1, y1, x2, y2 = box[:4].astype(int)
            patch = img[y1:y2, x1:x2]
            if patch.size > 0:
                patch = cv2.resize(patch, (24, 24))
                patch = (patch.astype(np.float32) - 127.5) / 128.0
                patch = np.transpose(patch, (2, 0, 1))
                patches.append(patch)
        
        if len(patches) == 0:
            return np.array([])
            
        patches = np.array(patches).astype(np.float32)
        
        outputs = self.rnet.predict(patches)
        prob = outputs[1][:, 1]
        reg = outputs[0]
        
        keep = prob > self.thresholds[1]
        boxes = boxes[keep]
        prob = prob[keep]
        reg = reg[keep]
        
        if len(boxes) == 0:
            return np.array([])
            
        boxes = self._apply_regression(boxes, reg)
        
        boxes = np.column_stack([boxes, prob])
        
        pick = self._nms(boxes, 0.7)
        return boxes[pick]

    def _stage3(self, img, boxes):
        """Stage 3: ONet for final detection"""
        if len(boxes) == 0:
            return np.array([])
            
        patches = []
        for box in boxes:
            x1, y1, x2, y2 = box[:4].astype(int)
            patch = img[y1:y2, x1:x2]
            if patch.size > 0:
                patch = cv2.resize(patch, (48, 48))
                patch = (patch.astype(np.float32) - 127.5) / 128.0
                patch = np.transpose(patch, (2, 0, 1))
                patches.append(patch)
        
        if len(patches) == 0:
            return np.array([])
            
        patches = np.array(patches).astype(np.float32)
        
        outputs = self.onet.predict(patches)
        prob = outputs[1][:, 1]
        reg = outputs[0]
        
        keep = prob > self.thresholds[2]
        boxes = boxes[keep]
        prob = prob[keep]
        reg = reg[keep]
        
        if len(boxes) == 0:
            return np.array([])
            
        boxes = self._apply_regression(boxes, reg)
        
        boxes = np.column_stack([boxes, prob])
        
        pick = self._nms(boxes, 0.7)
        return boxes[pick]

    def _calculate_scales(self, h, w):
        min_size = min(h, w)
        m = 12.0 / self.min_face_size
        min_size *= m
        
        scales = []
        factor_count = 0
        while min_size >= 12:
            scales.append(m * (self.factor ** factor_count))
            min_size *= self.factor
            factor_count += 1
            
        return scales

    def _generate_bbox(self, prob, reg, scale, threshold):
        stride = 2
        cellsize = 12
        
        t_index = np.where(prob > threshold)
        
        if t_index[0].size == 0:
            return []
            
        dx1, dy1, dx2, dy2 = [reg[i, t_index[0], t_index[1]] for i in range(4)]
        reg_score = prob[t_index[0], t_index[1]]
        
        x1 = np.round((stride * t_index[1] + 1) / scale)
        y1 = np.round((stride * t_index[0] + 1) / scale)
        x2 = np.round((stride * t_index[1] + 1 + cellsize) / scale)
        y2 = np.round((stride * t_index[0] + 1 + cellsize) / scale)
        
        w = x2 - x1 + 1
        h = y2 - y1 + 1
        x1 = x1 + dx1 * w
        y1 = y1 + dy1 * h
        x2 = x2 + dx2 * w
        y2 = y2 + dy2 * h
        
        boxes = np.column_stack([x1, y1, x2, y2, reg_score])
        return boxes

    def _apply_regression(self, boxes, reg):
        w = boxes[:, 2] - boxes[:, 0] + 1
        h = boxes[:, 3] - boxes[:, 1] + 1
        
        boxes[:, 0] = boxes[:, 0] + reg[:, 0] * w
        boxes[:, 1] = boxes[:, 1] + reg[:, 1] * h
        boxes[:, 2] = boxes[:, 2] + reg[:, 2] * w
        boxes[:, 3] = boxes[:, 3] + reg[:, 3] * h
        
        return boxes

    def _nms(self, boxes, threshold):
        if len(boxes) == 0:
            return []
            
        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 2]
        y2 = boxes[:, 3]
        scores = boxes[:, 4]
        
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = scores.argsort()[::-1]
        
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            
            w = np.maximum(0.0, xx2 - xx1 + 1)
            h = np.maximum(0.0, yy2 - yy1 + 1)
            inter = w * h
            
            ovr = inter / (areas[i] + areas[order[1:]] - inter)
            
            inds = np.where(ovr <= threshold)[0]
            order = order[inds + 1]
            
        return keep

    def __del__(self):
        self.pnet.unload_model()
        self.rnet.unload_model()
        self.onet.unload_model()