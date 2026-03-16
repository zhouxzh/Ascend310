from math import sqrt

import numpy as np


def softmax(x, axis=-1):
    x_max = np.max(x, axis=axis, keepdims=True)
    e_x = np.exp(x - x_max)
    return e_x / np.sum(e_x, axis=axis, keepdims=True)


def _topk_indices(scores, limit):
    if limit <= 0 or scores.size <= limit:
        return np.arange(scores.size, dtype=np.int64)
    partition = np.argpartition(scores, -limit)[-limit:]
    return partition[np.argsort(scores[partition])[::-1]]


def _empty_decoded_output():
    return (
        np.empty((0, 4), dtype=np.float32),
        np.empty((0,), dtype=np.int64),
        np.empty((0,), dtype=np.float32),
    )

def calc_iou(box1, box2):
    """Compute pairwise IoU for two groups of boxes in ltrb format.

    Args:
        box1: Array of shape (N, 4).
        box2: Array of shape (M, 4).

    Returns:
        Array of shape (N, M).
    """
    if box1.size == 0 or box2.size == 0:
        return np.zeros((box1.shape[0], box2.shape[0]), dtype=np.float32)

    top_left = np.maximum(box1[:, None, :2], box2[None, :, :2])
    bottom_right = np.minimum(box1[:, None, 2:], box2[None, :, 2:])
    overlap_size = np.clip(bottom_right - top_left, 0.0, None)
    intersection = overlap_size[..., 0] * overlap_size[..., 1]

    box1_size = np.clip(box1[:, 2:] - box1[:, :2], 0.0, None)
    box2_size = np.clip(box2[:, 2:] - box2[:, :2], 0.0, None)
    box1_area = box1_size[:, 0] * box1_size[:, 1]
    box2_area = box2_size[:, 0] * box2_size[:, 1]

    union = box1_area[:, None] + box2_area[None, :] - intersection
    return intersection / (union + 1e-10)


def _calc_iou_with_box(boxes, box):
    """Compute IoU between many boxes and one reference box."""
    top_left = np.maximum(boxes[:, :2], box[:2])
    bottom_right = np.minimum(boxes[:, 2:], box[2:])
    overlap_size = np.clip(bottom_right - top_left, 0.0, None)
    intersection = overlap_size[:, 0] * overlap_size[:, 1]

    boxes_size = np.clip(boxes[:, 2:] - boxes[:, :2], 0.0, None)
    box_size = np.clip(box[2:] - box[:2], 0.0, None)
    boxes_area = boxes_size[:, 0] * boxes_size[:, 1]
    box_area = box_size[0] * box_size[1]

    union = boxes_area + box_area - intersection
    return intersection / (union + 1e-10)


def _nms(boxes, scores, iou_threshold, presorted=False):
    if len(boxes) == 0:
        return np.empty((0,), dtype=np.int64)

    if presorted:
        order = np.arange(scores.size, dtype=np.int64)
    else:
        order = np.argsort(scores)[::-1]

    keep = []
    while order.size > 0:
        index = order[0]
        keep.append(index)
        if order.size == 1:
            break

        remaining = order[1:]
        iou = _calc_iou_with_box(boxes[remaining], boxes[index])
        order = remaining[iou <= iou_threshold]

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

    def decode_batch(self, bboxes_in, scores_in, criteria=0.45, max_output=200, score_threshold=0.05, allowed_class_ids=None):
        bboxes, probs = self.scale_back_batch(bboxes_in, scores_in)

        output = []
        for bbox, prob in zip(bboxes, probs):
            output.append(
                self.decode_single(
                    bbox,
                    prob,
                    criteria,
                    max_output,
                    score_threshold=score_threshold,
                    allowed_class_ids=allowed_class_ids,
                )
            )
        return output

    def decode_single(self, bboxes_in, scores_in, criteria, max_output, max_num=200, score_threshold=0.05, allowed_class_ids=None):
        if allowed_class_ids is None:
            class_ids = np.arange(1, scores_in.shape[1], dtype=np.int64)
        else:
            class_ids = np.asarray(sorted(set(int(class_id) for class_id in allowed_class_ids)), dtype=np.int64)
            class_ids = class_ids[(class_ids > 0) & (class_ids < scores_in.shape[1])]

        if class_ids.size == 0:
            return _empty_decoded_output()

        foreground_scores = scores_in[:, class_ids]
        if foreground_scores.size == 0:
            return _empty_decoded_output()

        best_class_indices = np.argmax(foreground_scores, axis=1)
        best_scores = foreground_scores[np.arange(foreground_scores.shape[0]), best_class_indices]
        keep_score = best_scores >= score_threshold
        if not np.any(keep_score):
            return _empty_decoded_output()

        candidate_boxes = bboxes_in[keep_score, :]
        candidate_scores = best_scores[keep_score]
        candidate_labels = class_ids[best_class_indices[keep_score]]

        candidate_limit = max(max_output, max_num)
        candidate_indices = _topk_indices(candidate_scores, candidate_limit)
        candidate_boxes = candidate_boxes[candidate_indices]
        candidate_scores = candidate_scores[candidate_indices]
        candidate_labels = candidate_labels[candidate_indices]

        selected_boxes = []
        selected_scores = []
        selected_labels = []
        for class_id in np.unique(candidate_labels):
            class_mask = candidate_labels == class_id
            class_boxes = candidate_boxes[class_mask]
            class_scores = candidate_scores[class_mask]

            class_indices = _topk_indices(class_scores, max_num)
            class_boxes = class_boxes[class_indices, :]
            class_scores = class_scores[class_indices]

            keep_nms = _nms(class_boxes, class_scores, criteria, presorted=True)
            if keep_nms.size == 0:
                continue

            selected_boxes.append(class_boxes[keep_nms])
            selected_scores.append(class_scores[keep_nms])
            selected_labels.append(np.full(keep_nms.shape, class_id, dtype=np.int64))

        if not selected_boxes:
            return _empty_decoded_output()

        boxes_out = np.concatenate(selected_boxes, axis=0)
        labels_out = np.concatenate(selected_labels, axis=0)
        scores_out = np.concatenate(selected_scores, axis=0)

        final_indices = _topk_indices(scores_out, max_output)
        return boxes_out[final_indices, :], labels_out[final_indices], scores_out[final_indices]


class DefaultBoxes(object):
    def __init__(self, fig_size, feat_size, steps, scales, aspect_ratios, scale_xy=0.1, scale_wh=0.2):
        self.feat_size = feat_size
        self.fig_size = fig_size
        self.scale_xy_ = scale_xy
        self.scale_wh_ = scale_wh
        self.steps = steps
        self.scales = scales

        feature_map_scales = fig_size / np.array(steps, dtype=np.float32)
        self.aspect_ratios = aspect_ratios

        default_boxes = []
        for layer_index, feature_size in enumerate(self.feat_size):
            layer_sizes = self._build_layer_sizes(layer_index)
            center_coordinates = self._build_center_coordinates(
                feature_size,
                feature_map_scales[layer_index],
            )

            repeated_centers = np.tile(center_coordinates, (layer_sizes.shape[0], 1))
            repeated_sizes = np.repeat(layer_sizes, center_coordinates.shape[0], axis=0)
            default_boxes.append(np.concatenate((repeated_centers, repeated_sizes), axis=1))

        self.dboxes = np.concatenate(default_boxes, axis=0).astype(np.float32, copy=False)
        np.clip(self.dboxes, 0.0, 1.0, out=self.dboxes)

        self.dboxes_ltrb = np.empty_like(self.dboxes)
        half_sizes = 0.5 * self.dboxes[:, 2:]
        self.dboxes_ltrb[:, :2] = self.dboxes[:, :2] - half_sizes
        self.dboxes_ltrb[:, 2:] = self.dboxes[:, :2] + half_sizes

    def _build_layer_sizes(self, layer_index):
        base_scale = self.scales[layer_index] / self.fig_size
        next_scale = self.scales[layer_index + 1] / self.fig_size
        interpolated_scale = sqrt(base_scale * next_scale)

        layer_sizes = [(base_scale, base_scale), (interpolated_scale, interpolated_scale)]
        for aspect_ratio in self.aspect_ratios[layer_index]:
            ratio_sqrt = sqrt(aspect_ratio)
            width = base_scale * ratio_sqrt
            height = base_scale / ratio_sqrt
            layer_sizes.append((width, height))
            layer_sizes.append((height, width))

        return np.asarray(layer_sizes, dtype=np.float32)

    def _build_center_coordinates(self, feature_size, feature_map_scale):
        centers = (np.arange(feature_size, dtype=np.float32) + 0.5) / feature_map_scale
        grid_x, grid_y = np.meshgrid(centers, centers, indexing="xy")
        return np.stack((grid_x.ravel(), grid_y.ravel()), axis=1)

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
    """Default boxes for the original SSD300 design described in the SSD paper."""
    figsize = 300
    feat_size = [38, 19, 10, 5, 3, 1]
    steps = [8, 16, 32, 64, 100, 300]
    scales = [21, 45, 99, 153, 207, 261, 315]
    aspect_ratios = [[2], [2, 3], [2, 3], [2, 3], [2], [2]]
    dboxes = DefaultBoxes(figsize, feat_size, steps, scales, aspect_ratios)
    return dboxes


def dboxes320_coco(min_ratio=0.1, max_ratio=0.9):
    """Default boxes for torchvision SSDLite models such as MobileNet-SSD."""
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