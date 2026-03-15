import numpy as np
import itertools
from math import sqrt
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# Helper to replace F.softmax
def softmax(x, axis=-1):
    x_max = np.max(x, axis=axis, keepdims=True)
    e_x = np.exp(x - x_max)
    return e_x / np.sum(e_x, axis=axis, keepdims=True)

def calc_iou(box1, box2):
    """ Calculation of IoU based on two boxes numpy array,
        input:
            box1 (N, 4)
            box2 (M, 4)
        output:
            IoU (N, M)
    """
    # Check if empty
    if box1.size == 0 or box2.size == 0:
        return np.zeros((box1.shape[0], box2.shape[0])) 

    be1 = np.expand_dims(box1, 1) # (N, 1, 4)
    be2 = np.expand_dims(box2, 0) # (1, M, 4)

    # Left Top & Right Bottom
    lt = np.maximum(be1[:,:,:2], be2[:,:,:2])
    rb = np.minimum(be1[:,:,2:], be2[:,:,2:])

    delta = rb - lt
    delta = np.maximum(delta, 0)
    intersect = delta[:,:,0] * delta[:,:,1]

    delta1 = be1[:,:,2:] - be1[:,:,:2]
    area1 = delta1[:,:,0] * delta1[:,:,1]
    delta2 = be2[:,:,2:] - be2[:,:,:2]
    area2 = delta2[:,:,0] * delta2[:,:,1]

    iou = intersect / (area1 + area2 - intersect + 1e-10) # Avoid div by zero
    return iou


class Encoder(object):
    """
        Transform between (bboxes, lables) <-> SSD output
    """

    def __init__(self, dboxes):
        self.dboxes = dboxes(order="ltrb")
        self.dboxes_xywh = dboxes(order="xywh")
        self.nboxes = self.dboxes.shape[0]
        # Expand for broadcasting in Scale Back if needed, mirroring unsqueeze(0)
        self.dboxes_xywh = np.expand_dims(self.dboxes_xywh, axis=0) 
        self.scale_xy = dboxes.scale_xy
        self.scale_wh = dboxes.scale_wh

    def encode(self, bboxes_in, labels_in, criteria = 0.5):
        ious = calc_iou(bboxes_in, self.dboxes)
        
        # Dim 0 is bboxes_in, Dim 1 is dboxes
        best_dbox_idx = np.argmax(ious, axis=0)
        best_dbox_ious = np.max(ious, axis=0)
        
        best_bbox_idx = np.argmax(ious, axis=1)
        # best_bbox_ious = np.max(ious, axis=1) # Unused

        # Set best ious 2.0
        best_dbox_ious[best_bbox_idx] = 2.0

        idx = np.arange(0, best_bbox_idx.shape[0], dtype=np.int64)
        best_dbox_idx[best_bbox_idx[idx]] = idx

        # filter IoU > 0.5
        masks = best_dbox_ious > criteria
        labels_out = np.zeros(self.nboxes, dtype=np.int64)
        labels_out[masks] = labels_in[best_dbox_idx[masks]]
        
        bboxes_out = self.dboxes.copy()
        bboxes_out[masks, :] = bboxes_in[best_dbox_idx[masks], :]
        
        # Transform format to xywh format
        x = 0.5*(bboxes_out[:, 0] + bboxes_out[:, 2])
        y = 0.5*(bboxes_out[:, 1] + bboxes_out[:, 3])
        w = -bboxes_out[:, 0] + bboxes_out[:, 2]
        h = -bboxes_out[:, 1] + bboxes_out[:, 3]
        
        bboxes_out[:, 0] = x
        bboxes_out[:, 1] = y
        bboxes_out[:, 2] = w
        bboxes_out[:, 3] = h
        return bboxes_out, labels_out

    def scale_back_batch(self, bboxes_in, scores_in):
        """
            Do scale and transform from xywh to ltrb
        """
        # (Batch, 4, N) -> (Batch, N, 4)
        if bboxes_in.shape[1] == 4:
            bboxes_in = np.transpose(bboxes_in, (0, 2, 1))
        
        # (Batch, Classes, N) -> (Batch, N, Classes)
        if scores_in.shape[1] != self.nboxes and scores_in.shape[2] == self.nboxes:
             scores_in = np.transpose(scores_in, (0, 2, 1))

        # Check shapes to be safe about dboxes broadcasting
        # self.dboxes_xywh shape: (1, 8732, 4)
        
        bboxes_in[:, :, :2] = self.scale_xy*bboxes_in[:, :, :2]
        bboxes_in[:, :, 2:] = self.scale_wh*bboxes_in[:, :, 2:]

        bboxes_in[:, :, :2] = bboxes_in[:, :, :2]*self.dboxes_xywh[:, :, 2:] + self.dboxes_xywh[:, :, :2]
        bboxes_in[:, :, 2:] = np.exp(bboxes_in[:, :, 2:])*self.dboxes_xywh[:, :, 2:]

        # Transform format to ltrb
        l = bboxes_in[:, :, 0] - 0.5*bboxes_in[:, :, 2]
        t = bboxes_in[:, :, 1] - 0.5*bboxes_in[:, :, 3]
        r = bboxes_in[:, :, 0] + 0.5*bboxes_in[:, :, 2]
        b = bboxes_in[:, :, 1] + 0.5*bboxes_in[:, :, 3]

        bboxes_in[:, :, 0] = l
        bboxes_in[:, :, 1] = t
        bboxes_in[:, :, 2] = r
        bboxes_in[:, :, 3] = b

        return bboxes_in, softmax(scores_in, axis=-1)

    def decode_batch(self, bboxes_in, scores_in,  criteria = 0.45, max_output=200):
        bboxes, probs = self.scale_back_batch(bboxes_in, scores_in)

        output = []
        for bbox, prob in zip(bboxes, probs):
            output.append(self.decode_single(bbox, prob, criteria, max_output))
        return output

    # perform non-maximum suppression
    def decode_single(self, bboxes_in, scores_in, criteria, max_output, max_num=200):
        # bboxes_in: (N, 4)
        # scores_in: (N, Classes)

        bboxes_out = []
        scores_out = []
        labels_out = []

        # Loop over classes, skipping background (index 0)
        for i in range(scores_in.shape[1]):
            if i == 0: continue

            score = scores_in[:, i]
            mask = score > 0.05
            
            bboxes_cls = bboxes_in[mask, :]
            score_cls = score[mask]
            
            if score_cls.size == 0: continue

            # argsort produces indices that sort the array
            score_idx_sorted = np.argsort(score_cls)
            
            # select max_output indices
            score_idx_sorted = score_idx_sorted[-max_num:]
            candidates = []

            while score_idx_sorted.size > 0:
                score_idx_sorted = np.asarray(score_idx_sorted).reshape(-1)
                idx = int(score_idx_sorted[-1])
                candidates.append(int(idx))
                
                if score_idx_sorted.size == 1: break

                # Everything except the best one
                others = score_idx_sorted[:-1]

                bboxes_sorted = bboxes_cls[others, :]
                bboxes_idx = bboxes_cls[idx, :].reshape(1, 4)
                
                iou_sorted = calc_iou(bboxes_sorted, bboxes_idx).reshape(-1)
                
                # We want to KEEP boxes with IoU < criteria (small overlap)
                keep_mask = np.asarray(iou_sorted < criteria).reshape(-1)
                score_idx_sorted = others[keep_mask]

            candidates = np.array(candidates, dtype=np.int64)
            bboxes_out.append(bboxes_cls[candidates, :])
            scores_out.append(score_cls[candidates])
            labels_out.extend([i]*len(candidates))

        if not bboxes_out:
            return [np.array([]) for _ in range(3)]

        bboxes_out = np.concatenate(bboxes_out, axis=0)
        labels_out = np.array(labels_out, dtype=np.int64)
        scores_out = np.concatenate(scores_out, axis=0)

        # Sort by score for final output
        final_indices = np.argsort(scores_out)
        final_indices = final_indices[-max_output:]
        
        return bboxes_out[final_indices, :], labels_out[final_indices], scores_out[final_indices]


class DefaultBoxes(object):
    def __init__(self, fig_size, feat_size, steps, scales, aspect_ratios, \
                       scale_xy=0.1, scale_wh=0.2):

        self.feat_size = feat_size
        self.fig_size = fig_size

        self.scale_xy_ = scale_xy
        self.scale_wh_ = scale_wh

        self.steps = steps
        self.scales = scales

        fk = fig_size/np.array(steps)
        self.aspect_ratios = aspect_ratios

        self.default_boxes = []
        # size of feature and number of feature
        for idx, sfeat in enumerate(self.feat_size):

            sk1 = scales[idx]/fig_size
            sk2 = scales[idx+1]/fig_size
            sk3 = sqrt(sk1*sk2)
            all_sizes = [(sk1, sk1), (sk3, sk3)]

            for alpha in aspect_ratios[idx]:
                w, h = sk1*sqrt(alpha), sk1/sqrt(alpha)
                all_sizes.append((w, h))
                all_sizes.append((h, w))
            for w, h in all_sizes:
                for i, j in itertools.product(range(sfeat), repeat=2):
                    cx, cy = (j+0.5)/fk[idx], (i+0.5)/fk[idx]
                    self.default_boxes.append((cx, cy, w, h))

        self.dboxes = np.array(self.default_boxes, dtype=np.float32)
        self.dboxes = np.clip(self.dboxes, 0.0, 1.0)
        
        # For IoU calculation
        self.dboxes_ltrb = self.dboxes.copy()
        self.dboxes_ltrb[:, 0] = self.dboxes[:, 0] - 0.5 * self.dboxes[:, 2]
        self.dboxes_ltrb[:, 1] = self.dboxes[:, 1] - 0.5 * self.dboxes[:, 3]
        self.dboxes_ltrb[:, 2] = self.dboxes[:, 0] + 0.5 * self.dboxes[:, 2]
        self.dboxes_ltrb[:, 3] = self.dboxes[:, 1] + 0.5 * self.dboxes[:, 3]

    @property
    def scale_xy(self):
        return self.scale_xy_

    @property
    def scale_wh(self):
        return self.scale_wh_

    def __call__(self, order="ltrb"):
        if order == "ltrb": return self.dboxes_ltrb
        if order == "xywh": return self.dboxes


def dboxes300_coco():
    figsize = 300
    feat_size = [38, 19, 10, 5, 3, 1]
    steps = [8, 16, 32, 64, 100, 300]
    scales = [21, 45, 99, 153, 207, 261, 315]
    aspect_ratios = [[2], [2, 3], [2, 3], [2, 3], [2], [2]]
    dboxes = DefaultBoxes(figsize, feat_size, steps, scales, aspect_ratios)
    return dboxes

def visualize_sample(img_tensor, gt_boxes, gt_labels, p_boxes, p_labels, p_scores, category_names, save_path):
    # Denormalize
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
    
    img = img_tensor.copy()
    img = img * std + mean
    
    # CHW -> HWC for matplotlib
    img = np.transpose(img, (1, 2, 0))
    img = np.clip(img, 0, 1)
    
    fig, ax = plt.subplots(1, figsize=(10, 10))
    ax.imshow(img)
    
    # 2. Draw Ground Truth
    for box, lbl in zip(gt_boxes, gt_labels):
        xmin, ymin, xmax, ymax = box
        w, h = xmax - xmin, ymax - ymin
        rect = patches.Rectangle((xmin, ymin), w, h, linewidth=2, edgecolor='lime', facecolor='none')
        ax.add_patch(rect)
        
        cat_n = str(lbl)
        if category_names and lbl < len(category_names):
            cat_n = category_names[lbl]
        ax.text(xmin, ymin, f"GT: {cat_n}", color='lime', fontsize=9, backgroundcolor='black', alpha=0.6)
        
    # 3. Draw Pred
    if p_boxes.size > 0:
        p_boxes_np = p_boxes * 300.0
        p_labels_np = p_labels
        p_scores_np = p_scores
        
        for box, lbl, scr in zip(p_boxes_np, p_labels_np, p_scores_np):
            if scr < 0.4: continue
            
            xmin, ymin, xmax, ymax = box
            w, h = xmax - xmin, ymax - ymin
            rect = patches.Rectangle((xmin, ymin), w, h, linewidth=2, edgecolor='red', facecolor='none')
            ax.add_patch(rect)
            
            cat_n = str(lbl)
            if category_names and lbl < len(category_names):
                cat_n = category_names[lbl]
            ax.text(xmin, ymax, f"Pred: {cat_n} {scr:.2f}", color='white', fontsize=9, backgroundcolor='red', alpha=0.7)
            
    plt.axis('off')
    plt.savefig(save_path, bbox_inches='tight', pad_inches=0)
    plt.close(fig)
