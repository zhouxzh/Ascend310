"""
Palmprint ROI detection and preprocessing.

Detects the hand in a camera frame, locates finger valleys via convex-defect
analysis, extracts a square palm ROI, applies CLAHE contrast enhancement, and
checks image quality.
"""

import cv2
import numpy as np

from config import CLAHE_CLIP_LIMIT, CLAHE_GRID_SIZE, ROI_SIZE, LAPLACIAN_BLUR_THRESHOLD


class PalmPreprocessor:
    """Extract and enhance a square palm ROI from a camera frame.

    Designed for a controlled setup: hand held over a darker background,
    fingers spread slightly apart.  Returns None on failure so callers can
    request a re-capture.
    """

    def __init__(self, roi_size=ROI_SIZE):
        self._roi_size = roi_size
        self._clahe = cv2.createCLAHE(
            clipLimit=CLAHE_CLIP_LIMIT,
            tileGridSize=CLAHE_GRID_SIZE,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preprocess(self, image_bgr):
        """Full pipeline: BGR frame → ROI → CLAHE → (roi_size, roi_size, 3).

        Returns None when no valid hand / palm ROI is found.
        """
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        mask = self._segment_hand(gray)
        if mask is None:
            return None

        contours = self._find_hand_contour(mask)
        if contours is None:
            return None

        valley_pts = self._find_valleys(contours)
        if len(valley_pts) < 2:
            return None

        roi = self._extract_roi(image_bgr, valley_pts[0], valley_pts[-1])
        if roi is None:
            return None

        if not self._quality_check(roi):
            return None

        roi = cv2.resize(roi, (self._roi_size, self._roi_size))
        return roi

    def is_palm_present(self, image_bgr):
        """Quick check: is there likely a hand in the frame?"""
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        mask = self._segment_hand(gray)
        if mask is None:
            return False
        area = cv2.countNonZero(mask)
        return area > 5000

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    def _segment_hand(self, gray):
        """Otsu threshold + morphology → binary hand mask."""
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        _, binary = cv2.threshold(
            blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )

        # Assume hand is brighter than background; if >50 % of pixels are
        # white we likely have a dark hand on a light background → invert.
        white_ratio = cv2.countNonZero(binary) / binary.size
        if white_ratio > 0.5:
            binary = cv2.bitwise_not(binary)

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        if cv2.countNonZero(binary) < 5000:
            return None
        return binary

    def _find_hand_contour(self, mask):
        """Return the largest contour (assumed to be the hand)."""
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            return None
        return max(contours, key=cv2.contourArea)

    def _find_valleys(self, contour):
        """Find finger valley points via convexity defects.

        Valleys are defect points that lie between fingers (deep enough,
        pointing roughly toward the wrist).  Sorted left-to-right.
        """
        hull = cv2.convexHull(contour, returnPoints=False)
        if hull is None or len(hull) < 3:
            return []

        try:
            defects = cv2.convexityDefects(contour, hull)
        except cv2.error:
            return []

        if defects is None:
            return []

        # Filter: keep defects deep enough to be finger valleys
        valleys = []
        for d in defects:
            s, e, f, depth = d[0]
            if depth < 3000:
                continue
            pt = tuple(contour[f][0])
            valleys.append(pt)

        return sorted(valleys, key=lambda p: p[0])

    def _extract_roi(self, image_bgr, left_valley, right_valley):
        """Extract a square palm ROI using two valley reference points.

        The ROI centre is offset downward from the valley midpoint, and the
        side length is proportional to the distance between valleys.
        """
        dx = right_valley[0] - left_valley[0]
        dy = right_valley[1] - left_valley[1]
        dist = np.hypot(dx, dy)

        if dist < 30:
            return None

        mx = (left_valley[0] + right_valley[0]) // 2
        my = (left_valley[1] + right_valley[1]) // 2

        # Centre the ROI on the palm (offset down from valleys by ~30 % of dist)
        cx = mx
        cy = my + int(0.30 * dist)
        side = int(dist * 1.2)

        h, w = image_bgr.shape[:2]
        half = side // 2
        x1 = max(cx - half, 0)
        y1 = max(cy - half, 0)
        x2 = min(x1 + side, w)
        y2 = min(y1 + side, h)
        x1 = max(x2 - side, 0)
        y1 = max(y2 - side, 0)

        if y2 - y1 < 20 or x2 - x1 < 20:
            return None

        roi = image_bgr[y1:y2, x1:x2]

        # CLAHE on each channel
        lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = self._clahe.apply(l)
        lab = cv2.merge([l, a, b])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def _quality_check(self, roi):
        """Laplacian variance as a sharpness proxy."""
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        return lap_var >= LAPLACIAN_BLUR_THRESHOLD
