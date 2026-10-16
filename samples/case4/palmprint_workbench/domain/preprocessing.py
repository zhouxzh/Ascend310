"""Palm ROI extraction shared by all recognition backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from ..config import (
    FIXED_ROI_CENTER_X,
    FIXED_ROI_CENTER_Y,
    FIXED_ROI_SIDE_RATIO,
    MAX_CLIPPED_RATIO,
    MAX_ROI_BRIGHTNESS,
    MIN_ROI_BRIGHTNESS,
    MIN_SHARPNESS,
    ROI_PIPELINE_ID,
    ROI_SIZE,
)


@dataclass(frozen=True)
class RoiResult:
    ok: bool
    roi: np.ndarray | None = None
    preview: np.ndarray | None = None
    reason: str = ""
    quality: dict[str, float] = field(default_factory=dict)
    geometry: dict[str, Any] = field(default_factory=dict)
    pipeline_id: str = ROI_PIPELINE_ID


class PalmPreprocessor:
    """Extract an aligned grayscale ROI or normalize an existing ROI."""

    def __init__(self, roi_size: int = ROI_SIZE) -> None:
        self.roi_size = int(roi_size)

    def extract(
        self,
        image_rgb: np.ndarray,
        *,
        assume_roi: bool = False,
        background_rgb: np.ndarray | None = None,
        roi_config: dict[str, float] | None = None,
    ) -> RoiResult:
        error = self._validate_image(image_rgb)
        if error:
            return RoiResult(False, reason=error)
        rgb = np.ascontiguousarray(image_rgb[:, :, :3], dtype=np.uint8)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        if assume_roi:
            roi = cv2.resize(gray, (self.roi_size, self.roi_size), interpolation=cv2.INTER_AREA)
            return RoiResult(
                True,
                roi=roi,
                preview=cv2.cvtColor(roi, cv2.COLOR_GRAY2RGB),
                quality={"sharpness": self._sharpness(roi), "hand_area_ratio": 1.0},
                geometry={
                    "center_x": 0.5,
                    "center_y": 0.5,
                    "angle_deg": 0.0,
                    "crop_side": float(min(gray.shape)),
                    "corners": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                    "roi_mode": "pre_cropped",
                },
            )
        # ``background_rgb`` remains accepted for offline diagnostics and old
        # callers, but production capture never uses segmentation to locate a
        # hand.  The user places the palm inside the guide rectangle instead.
        del background_rgb
        crop, corners, geometry = self._fixed_crop(gray, roi_config)
        preview = rgb.copy()
        if corners.size:
            cv2.polylines(preview, [corners.astype(np.int32)], True, (34, 197, 94), 3)
        if crop is None:
            return RoiResult(
                False,
                preview=preview,
                reason="固定采集框超出图像范围",
                geometry=geometry,
            )

        roi = cv2.resize(crop, (self.roi_size, self.roi_size), interpolation=cv2.INTER_AREA)
        gray_roi = np.asarray(roi, dtype=np.uint8)
        sharpness = self._sharpness(gray_roi)
        brightness = float(np.mean(gray_roi))
        clipped_low = float(np.mean(gray_roi <= 4))
        clipped_high = float(np.mean(gray_roi >= 251))
        contrast = float(np.std(gray_roi))
        quality = {
            "sharpness": sharpness,
            "brightness": brightness,
            "contrast": contrast,
            "clipped_low_ratio": clipped_low,
            "clipped_high_ratio": clipped_high,
            "roi_area_ratio": float(crop.shape[0] * crop.shape[1] / gray.size),
        }
        geometry = {**geometry, "roi_mode": "fixed_guide", "pipeline_id": ROI_PIPELINE_ID}
        if sharpness < MIN_SHARPNESS:
            return RoiResult(False, roi=roi, preview=preview, reason=f"图像清晰度不足：{sharpness:.1f}", quality=quality, geometry=geometry)
        if brightness < MIN_ROI_BRIGHTNESS or brightness > MAX_ROI_BRIGHTNESS:
            return RoiResult(False, roi=roi, preview=preview, reason=f"图像亮度不合适：{brightness:.1f}", quality=quality, geometry=geometry)
        if clipped_low + clipped_high > MAX_CLIPPED_RATIO:
            return RoiResult(False, roi=roi, preview=preview, reason="图像过曝或欠曝比例过高", quality=quality, geometry=geometry)
        return RoiResult(True, roi=roi, preview=preview, quality=quality, geometry=geometry)

    @staticmethod
    def _fixed_crop(
        gray: np.ndarray,
        roi_config: dict[str, float] | None = None,
    ) -> tuple[np.ndarray | None, np.ndarray, dict[str, Any]]:
        height, width = gray.shape[:2]
        settings = roi_config or {}
        side_ratio = float(settings.get("side_ratio", FIXED_ROI_SIDE_RATIO))
        center_x = float(settings.get("center_x", FIXED_ROI_CENTER_X))
        center_y = float(settings.get("center_y", FIXED_ROI_CENTER_Y))
        side_ratio = min(max(side_ratio, 0.40), 1.0)
        side = max(16, int(round(min(width, height) * side_ratio)))
        center = np.array([center_x * width, center_y * height], dtype=np.float32)
        half = side / 2.0
        x1, y1 = int(round(center[0] - half)), int(round(center[1] - half))
        x2, y2 = x1 + side, y1 + side
        padding = {
            "left": max(0, -x1), "top": max(0, -y1),
            "right": max(0, x2 - width), "bottom": max(0, y2 - height),
        }
        corners = np.array(
            [[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32
        )
        geometry = {
            "center_x": float(center[0] / width),
            "center_y": float(center[1] / height),
            "angle_deg": 0.0,
            "crop_side": float(side),
            "side_ratio": float(side_ratio),
            "edge_padding_px": float(max(padding.values())),
            "corners": [[float(point[0] / width), float(point[1] / height)] for point in corners],
        }
        if max(padding.values()) > 0:
            return None, corners, geometry
        return gray[y1:y2, x1:x2], corners, geometry

    @staticmethod
    def _validate_image(image: Any) -> str:
        if image is None:
            return "Select an image or capture one with the camera"
        if not isinstance(image, np.ndarray):
            return "Invalid image format"
        if image.ndim != 3 or image.shape[2] < 3:
            return "An RGB image is required"
        if image.size == 0:
            return "Image is empty"
        return ""

    @staticmethod
    def _segment(gray: np.ndarray) -> np.ndarray:
        blurred = cv2.GaussianBlur(gray, (7, 7), 0)
        _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if cv2.countNonZero(mask) / mask.size > 0.55:
            mask = cv2.bitwise_not(mask)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    @staticmethod
    def _segment_background(rgb: np.ndarray, background_rgb: np.ndarray) -> np.ndarray:
        """Return the foreground changed since the session's empty-frame reference.

        The reference stays only in the enrollment session's memory.  Otsu is
        applied to the RGB distance image, with a noise floor so camera sensor
        noise and minor auto-exposure changes cannot become a hand contour.
        """

        difference = cv2.absdiff(rgb, np.ascontiguousarray(background_rgb[:, :, :3], dtype=np.uint8))
        distance = np.max(difference, axis=2).astype(np.uint8)
        blurred = cv2.GaussianBlur(distance, (7, 7), 0)
        otsu_threshold, _ = cv2.threshold(
            blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        _, mask = cv2.threshold(blurred, max(18.0, float(otsu_threshold)), 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    @staticmethod
    def _finger_valleys(contour: np.ndarray) -> list[tuple[int, int, float]]:
        hull = cv2.convexHull(contour, returnPoints=False)
        if hull is None or len(hull) < 3:
            return []
        try:
            defects = cv2.convexityDefects(contour, hull)
        except cv2.error:
            return []
        if defects is None:
            return []
        x, y, width, height = cv2.boundingRect(contour)
        min_depth = max(8.0, 0.035 * max(width, height))
        max_y = y + int(0.72 * height)
        points = []
        for item in defects[:, 0]:
            far = contour[int(item[2])][0]
            depth = float(item[3]) / 256.0
            if depth >= min_depth and int(far[1]) <= max_y:
                points.append((int(far[0]), int(far[1]), depth))
        return sorted(points, key=lambda point: point[0])

    @staticmethod
    def _select_reference_valleys(
        valleys: list[tuple[int, int, float]],
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        strongest = sorted(valleys, key=lambda point: point[2], reverse=True)[:4]
        pair = max(
            ((a, b) for index, a in enumerate(strongest) for b in strongest[index + 1 :]),
            key=lambda item: abs(item[0][0] - item[1][0]),
        )
        left, right = sorted(pair, key=lambda point: point[0])
        return (left[0], left[1]), (right[0], right[1])

    @staticmethod
    def _aligned_crop(
        gray: np.ndarray,
        left: tuple[int, int],
        right: tuple[int, int],
    ) -> tuple[np.ndarray | None, np.ndarray, dict[str, Any]]:
        delta_x = float(right[0] - left[0])
        delta_y = float(right[1] - left[1])
        distance = float(np.hypot(delta_x, delta_y))
        if distance < 24:
            return None, np.empty((0, 2), dtype=np.float32), {}
        angle = float(np.degrees(np.arctan2(delta_y, delta_x)))
        midpoint = np.array(
            [(left[0] + right[0]) / 2, (left[1] + right[1]) / 2], dtype=np.float32
        )
        side = max(48, int(round(distance * 1.28)))
        center = midpoint + np.array([-delta_y, delta_x], dtype=np.float32) / distance * (
            0.48 * distance
        )
        matrix = cv2.getRotationMatrix2D(tuple(center), angle, 1.0)
        rotated = cv2.warpAffine(
            gray,
            matrix,
            (gray.shape[1], gray.shape[0]),
            borderMode=cv2.BORDER_REPLICATE,
        )
        center_x, center_y = (int(round(value)) for value in center)
        half = side // 2
        x1, y1, x2, y2 = center_x - half, center_y - half, center_x + half, center_y + half
        height, width = rotated.shape[:2]
        padding = {
            "left": max(0, -x1),
            "top": max(0, -y1),
            "right": max(0, x2 - width),
            "bottom": max(0, y2 - height),
        }
        edge_padding = max(padding.values())
        local = np.array(
            [[-half, -half], [half, -half], [half, half], [-half, half]],
            dtype=np.float32,
        )
        theta = np.radians(-angle)
        rotation = np.array(
            [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]]
        )
        corners = local @ rotation.T + center
        image_height, image_width = gray.shape[:2]
        geometry = {
            "center_x": float(center[0] / image_width),
            "center_y": float(center[1] / image_height),
            "angle_deg": angle,
            "crop_side": float(side),
            "edge_padding_px": float(edge_padding),
            "corners": [
                [float(point[0] / image_width), float(point[1] / image_height)] for point in corners
            ],
        }
        # A palm held close to the guide boundary can make an otherwise valid
        # crop miss the frame by a few pixels.  Preserve such an ROI with edge
        # replication, but do not accept a genuinely truncated hand.
        if edge_padding > max(8, int(round(side * 0.08))):
            return None, corners, geometry
        if edge_padding:
            rotated = cv2.copyMakeBorder(
                rotated,
                padding["top"],
                padding["bottom"],
                padding["left"],
                padding["right"],
                borderType=cv2.BORDER_REPLICATE,
            )
            x1 += padding["left"]
            x2 += padding["left"]
            y1 += padding["top"]
            y2 += padding["top"]
        return rotated[y1:y2, x1:x2], corners, geometry

    @staticmethod
    def _sharpness(gray: np.ndarray) -> float:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
