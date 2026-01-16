import numpy as np
import onnxruntime as ort
import cv2

class MTCNNOnnx:
    """
    MTCNN face detector using ONNX models
    """
    def __init__(self, pnet_path, rnet_path, onet_path):
        # Load ONNX models
        self.pnet = ort.InferenceSession(pnet_path)
        self.rnet = ort.InferenceSession(rnet_path)
        self.onet = ort.InferenceSession(onet_path)
        
        # Thresholds for each network
        self.thresholds = [0.6, 0.7, 0.7]
        
        # Factor for creating image pyramid
        self.factor = 0.709
        
        # Minimum face size
        self.min_face_size = 20

    def detect_faces(self, image):
        """
        Detect faces in the given image
        
        Args:
            image: Input image in BGR format (OpenCV format)
            
        Returns:
            List of detected faces with bounding boxes and confidence scores
        """
        # Convert BGR to RGB
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
        
        # Convert to the format expected by the main application
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
        # Normalize image
        img_norm = (img.astype(np.float32) - 127.5) / 128.0
        
        # Create image pyramid
        scales = self._calculate_scales(h, w)
        boxes = []
        
        for scale in scales:
            # Resize image
            hs = int(np.ceil(h * scale))
            ws = int(np.ceil(w * scale))
            img_resized = cv2.resize(img_norm, (ws, hs))
            
            # Prepare input for PNet
            img_input = np.transpose(img_resized, (2, 0, 1))
            img_input = np.expand_dims(img_input, axis=0).astype(np.float32)
            
            # Run PNet
            outputs = self.pnet.run(None, {'input': img_input})
            prob = outputs[1][0, 1, :, :]  # Probability map
            reg = outputs[0][0, :, :, :]   # Regression map
            
            # Generate boxes
            boxes_scale = self._generate_bbox(prob, reg, scale, self.thresholds[0])
            boxes.extend(boxes_scale)
        
        if len(boxes) == 0:
            return np.array([])
            
        # Non-maximum suppression
        boxes = np.array(boxes)
        pick = self._nms(boxes, 0.5)
        return boxes[pick]

    def _stage2(self, img, boxes):
        """Stage 2: RNet for refinement"""
        if len(boxes) == 0:
            return np.array([])
            
        # Prepare input patches
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
        
        # Run RNet
        outputs = self.rnet.run(None, {'input': patches})
        prob = outputs[1][:, 1]  # Probability
        reg = outputs[0]         # Regression
        
        # Filter by threshold
        keep = prob > self.thresholds[1]
        boxes = boxes[keep]
        prob = prob[keep]
        reg = reg[keep]
        
        if len(boxes) == 0:
            return np.array([])
            
        # Apply regression
        boxes = self._apply_regression(boxes, reg)
        
        # Add probability scores
        boxes = np.column_stack([boxes, prob])
        
        # Non-maximum suppression
        pick = self._nms(boxes, 0.7)
        return boxes[pick]

    def _stage3(self, img, boxes):
        """Stage 3: ONet for final detection"""
        if len(boxes) == 0:
            return np.array([])
            
        # Prepare input patches
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
        
        # Run ONet
        outputs = self.onet.run(None, {'input': patches})
        prob = outputs[1][:, 1]  # Probability
        reg = outputs[0]         # Regression
        
        # Filter by threshold
        keep = prob > self.thresholds[2]
        boxes = boxes[keep]
        prob = prob[keep]
        reg = reg[keep]
        
        if len(boxes) == 0:
            return np.array([])
            
        # Apply regression
        boxes = self._apply_regression(boxes, reg)
        
        # Add probability scores
        boxes = np.column_stack([boxes, prob])
        
        # Non-maximum suppression
        pick = self._nms(boxes, 0.7)
        return boxes[pick]

    def _calculate_scales(self, h, w):
        """Calculate scales for image pyramid"""
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
        """Generate bounding boxes from probability and regression maps"""
        stride = 2
        cellsize = 12
        
        # Find locations where probability > threshold
        t_index = np.where(prob > threshold)
        
        if t_index[0].size == 0:
            return []
            
        # Calculate bounding box coordinates
        dx1, dy1, dx2, dy2 = [reg[i, t_index[0], t_index[1]] for i in range(4)]
        reg_score = prob[t_index[0], t_index[1]]
        
        x1 = np.round((stride * t_index[1] + 1) / scale)
        y1 = np.round((stride * t_index[0] + 1) / scale)
        x2 = np.round((stride * t_index[1] + 1 + cellsize) / scale)
        y2 = np.round((stride * t_index[0] + 1 + cellsize) / scale)
        
        # Apply regression
        w = x2 - x1 + 1
        h = y2 - y1 + 1
        x1 = x1 + dx1 * w
        y1 = y1 + dy1 * h
        x2 = x2 + dx2 * w
        y2 = y2 + dy2 * h
        
        boxes = np.column_stack([x1, y1, x2, y2, reg_score])
        return boxes

    def _apply_regression(self, boxes, reg):
        """Apply regression to refine bounding boxes"""
        w = boxes[:, 2] - boxes[:, 0] + 1
        h = boxes[:, 3] - boxes[:, 1] + 1
        
        boxes[:, 0] = boxes[:, 0] + reg[:, 0] * w
        boxes[:, 1] = boxes[:, 1] + reg[:, 1] * h
        boxes[:, 2] = boxes[:, 2] + reg[:, 2] * w
        boxes[:, 3] = boxes[:, 3] + reg[:, 3] * h
        
        return boxes

    def _nms(self, boxes, threshold):
        """Non-maximum suppression"""
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