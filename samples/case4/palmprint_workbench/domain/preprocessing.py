"""Palm ROI extraction shared by all recognition backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from ..config import MIN_HAND_AREA_RATIO, MIN_SHARPNESS, ROI_SIZE


@dataclass(frozen=True)
class RoiResult:
    ok: bool
    roi: np.ndarray | None = None
    preview: np.ndarray | None = None
    reason: str = ""
    quality: dict[str, float] = field(default_factory=dict)


class PalmPreprocessor:
    """Extract an aligned grayscale ROI or normalize an existing ROI."""

    def __init__(self, roi_size: int = ROI_SIZE) -> None:
        self.roi_size = int(roi_size)

    def extract(self, image_rgb: np.ndarray, *, assume_roi: bool = False) -> RoiResult:
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
            )

        mask = self._segment(gray)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return RoiResult(False, reason="No palm contour detected")
        contour = max(contours, key=cv2.contourArea)
        area_ratio = float(cv2.contourArea(contour) / gray.size)
        if area_ratio < MIN_HAND_AREA_RATIO:
            return RoiResult(False, reason="Palm region is too small; move closer to the camera")

        valleys = self._finger_valleys(contour)
        if len(valleys) < 2:
            return RoiResult(False, reason="Finger-valley localization failed; spread the fingers")
        left, right = self._select_reference_valleys(valleys)
        crop, corners = self._aligned_crop(gray, left, right)
        if crop is None:
            return RoiResult(False, reason="Palm ROI falls outside the image")

        roi = cv2.resize(crop, (self.roi_size, self.roi_size), interpolation=cv2.INTER_AREA)
        sharpness = self._sharpness(roi)
        preview = rgb.copy()
        cv2.polylines(preview, [corners.astype(np.int32)], True, (34, 197, 94), 3)
        quality = {"sharpness": sharpness, "hand_area_ratio": area_ratio}
        if sharpness < MIN_SHARPNESS:
            return RoiResult(
                False,
                roi=roi,
                preview=preview,
                reason=f"Image is blurry: sharpness {sharpness:.1f} < {MIN_SHARPNESS:.1f}",
                quality=quality,
            )
        return RoiResult(True, roi=roi, preview=preview, quality=quality)

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
    ) -> tuple[np.ndarray | None, np.ndarray]:
        delta_x = float(right[0] - left[0])
        delta_y = float(right[1] - left[1])
        distance = float(np.hypot(delta_x, delta_y))
        if distance < 24:
            return None, np.empty((0, 2), dtype=np.float32)
        angle = float(np.degrees(np.arctan2(delta_y, delta_x)))
        midpoint = np.array(
            [(left[0] + right[0]) / 2, (left[1] + right[1]) / 2], dtype=np.float32
        )
        side = max(48, int(round(distance * 1.28)))
        center = midpoint + np.array([-delta_y, delta_x], dtype=np.float32) / distance * (
            0.48 * distance
        )
        matrix = cv2.getRotationMatrix2D(tuple(center), angle, 1.0)
        rotated = cv2.warpAffine(gray, matrix, (gray.shape[1], gray.shape[0]))
        center_x, center_y = (int(round(value)) for value in center)
        half = side // 2
        x1, y1, x2, y2 = center_x - half, center_y - half, center_x + half, center_y + half
        if x1 < 0 or y1 < 0 or x2 > rotated.shape[1] or y2 > rotated.shape[0]:
            return None, np.empty((0, 2), dtype=np.float32)
        local = np.array(
            [[-half, -half], [half, -half], [half, half], [-half, half]],
            dtype=np.float32,
        )
        theta = np.radians(-angle)
        rotation = np.array(
            [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]]
        )
        corners = local @ rotation.T + center
        return rotated[y1:y2, x1:x2], corners

    @staticmethod
    def _sharpness(gray: np.ndarray) -> float:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
