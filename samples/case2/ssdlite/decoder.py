import itertools
from math import sqrt

import numpy as np


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
    if box1.size == 0 or box2.size == 0:
        return np.zeros((box1.shape[0], box2.shape[0]))

    be1 = np.expand_dims(box1, 1)
    be2 = np.expand_dims(box2, 0)

    lt = np.maximum(be1[:, :, :2], be2[:, :, :2])
    rb = np.minimum(be1[:, :, 2:], be2[:, :, 2:])

    delta = rb - lt
    delta = np.maximum(delta, 0)
    intersect = delta[:, :, 0] * delta[:, :, 1]

    delta1 = be1[:, :, 2:] - be1[:, :, :2]
    area1 = delta1[:, :, 0] * delta1[:, :, 1]
    delta2 = be2[:, :, 2:] - be2[:, :, :2]
    area2 = delta2[:, :, 0] * delta2[:, :, 1]

    iou = intersect / (area1 + area2 - intersect + 1e-10)
    return iou

def _nms(boxes, scores, iou_threshold):
    if len(boxes) == 0:
        return np.empty((0,), dtype=np.int64)

    order = np.argsort(scores)[::-1]
    keep = []
    while order.size > 0:
        index = order[0]
        keep.append(index)
        if order.size == 1:
            break
        iou = calc_iou(boxes[order[1:]], boxes[index].reshape(1, 4)).reshape(-1)
        order = order[np.where(iou <= iou_threshold)[0] + 1]
    return np.array(keep, dtype=np.int64)

class SSDDecoder(object):
    def __init__(self, dboxes):
        self.dboxes = dboxes(order="ltrb")
        self.dboxes_xywh = dboxes(order="xywh")
        self.nboxes = self.dboxes.shape[0]
        self.dboxes_xywh = np.expand_dims(self.dboxes_xywh, axis=0)
        self.scale_xy = dboxes.scale_xy
        self.scale_wh = dboxes.scale_wh

    def scale_back_batch(self, bboxes_in, scores_in):
        if bboxes_in.shape[1] == 4:
            bboxes_in = np.transpose(bboxes_in, (0, 2, 1))

        if scores_in.shape[1] != self.nboxes and scores_in.shape[2] == self.nboxes:
            scores_in = np.transpose(scores_in, (0, 2, 1))

        bboxes_in[:, :, :2] = self.scale_xy * bboxes_in[:, :, :2]
        bboxes_in[:, :, 2:] = self.scale_wh * bboxes_in[:, :, 2:]

        bboxes_in[:, :, :2] = bboxes_in[:, :, :2] * self.dboxes_xywh[:, :, 2:] + self.dboxes_xywh[:, :, :2]
        bboxes_in[:, :, 2:] = np.exp(bboxes_in[:, :, 2:]) * self.dboxes_xywh[:, :, 2:]

        l = bboxes_in[:, :, 0] - 0.5 * bboxes_in[:, :, 2]
        t = bboxes_in[:, :, 1] - 0.5 * bboxes_in[:, :, 3]
        r = bboxes_in[:, :, 0] + 0.5 * bboxes_in[:, :, 2]
        b = bboxes_in[:, :, 1] + 0.5 * bboxes_in[:, :, 3]

        bboxes_in[:, :, 0] = l
        bboxes_in[:, :, 1] = t
        bboxes_in[:, :, 2] = r
        bboxes_in[:, :, 3] = b

        return bboxes_in, softmax(scores_in, axis=-1)

    def decode_batch(self, bboxes_in, scores_in, criteria=0.45, max_output=200):
        bboxes, probs = self.scale_back_batch(bboxes_in, scores_in)

        output = []
        for bbox, prob in zip(bboxes, probs):
            output.append(self.decode_single(bbox, prob, criteria, max_output))
        return output

    def decode_single(self, bboxes_in, scores_in, criteria, max_output, max_num=200):
        selected_boxes = []
        selected_scores = []
        selected_labels = []

        for class_id in range(1, scores_in.shape[1]):
            class_scores = scores_in[:, class_id]
            keep_score = class_scores > 0.05
            class_boxes = bboxes_in[keep_score, :]
            class_scores = class_scores[keep_score]

            if class_scores.size == 0:
                continue

            top_indices = np.argsort(class_scores)[-max_num:]
            class_boxes = class_boxes[top_indices, :]
            class_scores = class_scores[top_indices]

            keep_nms = _nms(class_boxes, class_scores, criteria)
            if keep_nms.size == 0:
                continue

            selected_boxes.append(class_boxes[keep_nms])
            selected_scores.append(class_scores[keep_nms])
            selected_labels.append(np.full(keep_nms.shape, class_id, dtype=np.int64))

        if not selected_boxes:
            return [np.array([]) for _ in range(3)]

        boxes_out = np.concatenate(selected_boxes, axis=0)
        labels_out = np.concatenate(selected_labels, axis=0)
        scores_out = np.concatenate(selected_scores, axis=0)

        final_indices = np.argsort(scores_out)[-max_output:]
        return boxes_out[final_indices, :], labels_out[final_indices], scores_out[final_indices]


class DefaultBoxes(object):
    def __init__(self, fig_size, feat_size, steps, scales, aspect_ratios, scale_xy=0.1, scale_wh=0.2):
        self.feat_size = feat_size
        self.fig_size = fig_size
        self.scale_xy_ = scale_xy
        self.scale_wh_ = scale_wh
        self.steps = steps
        self.scales = scales

        fk = fig_size / np.array(steps)
        self.aspect_ratios = aspect_ratios

        self.default_boxes = []
        for idx, sfeat in enumerate(self.feat_size):
            sk1 = scales[idx] / fig_size
            sk2 = scales[idx + 1] / fig_size
            sk3 = sqrt(sk1 * sk2)
            all_sizes = [(sk1, sk1), (sk3, sk3)]

            for alpha in aspect_ratios[idx]:
                w, h = sk1 * sqrt(alpha), sk1 / sqrt(alpha)
                all_sizes.append((w, h))
                all_sizes.append((h, w))
            for w, h in all_sizes:
                for i, j in itertools.product(range(sfeat), repeat=2):
                    cx, cy = (j + 0.5) / fk[idx], (i + 0.5) / fk[idx]
                    self.default_boxes.append((cx, cy, w, h))

        self.dboxes = np.array(self.default_boxes, dtype=np.float32)
        self.dboxes = np.clip(self.dboxes, 0.0, 1.0)

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
        if order == "ltrb":
            return self.dboxes_ltrb
        if order == "xywh":
            return self.dboxes


def dboxes300_coco():
    figsize = 300
    feat_size = [38, 19, 10, 5, 3, 1]
    steps = [8, 16, 32, 64, 100, 300]
    scales = [21, 45, 99, 153, 207, 261, 315]
    aspect_ratios = [[2], [2, 3], [2, 3], [2, 3], [2], [2]]
    dboxes = DefaultBoxes(figsize, feat_size, steps, scales, aspect_ratios)
    return dboxes


def dboxes320_coco(min_ratio=0.1, max_ratio=0.9):
    figsize = 320
    feat_size = [20, 10, 5, 3, 2, 1]
    steps = [figsize / s for s in feat_size]

    num_layers = len(feat_size)
    scales_norm = [
        min_ratio + (max_ratio - min_ratio) * k / (num_layers - 1)
        for k in range(num_layers)
    ]
    scales_norm.append(1.0)
    scales = [s * figsize for s in scales_norm]

    aspect_ratios = [[2, 3] for _ in range(num_layers)]
    dboxes = DefaultBoxes(figsize, feat_size, steps, scales, aspect_ratios)
    return dboxes