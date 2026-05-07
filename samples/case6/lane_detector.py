"""
Case 6: Smart Car Perception — classic CV lane detection.

Pure OpenCV pipeline: grayscale → Gaussian → Canny → ROI → Hough → fit.
No deep learning — lane lines are geometric features best extracted with
classic computer vision.
"""

import cv2
import numpy as np

from config import (
    CANNY_LOW,
    CANNY_HIGH,
    GAUSSIAN_KERNEL,
    HOUGH_RHO,
    HOUGH_THETA,
    HOUGH_THRESHOLD,
    HOUGH_MIN_LINE_LEN,
    HOUGH_MAX_LINE_GAP,
    ROI_BOTTOM,
    ROI_TOP,
    LANE_COLOR,
    LANE_OVERLAY_ALPHA,
)


class LaneDetector:
    """Detect lane lines in a road image using classic CV.

    Pipeline:
        1. Grayscale conversion
        2. Gaussian blur (noise reduction)
        3. Canny edge detection
        4. Region-of-interest mask (trapezoid, keep road area)
        5. Hough line transform
        6. Separate left/right lanes by slope
        7. Average + extrapolate to full lane lines
        8. Draw lane overlay on original image
    """

    def __init__(self):
        self.left_lines = []
        self.right_lines = []

    def detect(self, image_bgr):
        """Run full lane detection pipeline.

        Args:
            image_bgr: BGR image (H, W, 3)

        Returns:
            dict with keys:
                left_lane: (x1, y1, x2, y2) or None
                right_lane: (x1, y1, x2, y2) or None
                left_slope: float or None
                right_slope: float or None
                num_lines_found: total Hough lines before filtering
        """
        h, w = image_bgr.shape[:2]

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, GAUSSIAN_KERNEL, 0)
        edges = cv2.Canny(blurred, CANNY_LOW, CANNY_HIGH)

        masked = self._apply_roi(edges, h, w)

        lines = cv2.HoughLinesP(
            masked,
            HOUGH_RHO,
            HOUGH_THETA * np.pi / 180,
            HOUGH_THRESHOLD,
            minLineLength=HOUGH_MIN_LINE_LEN,
            maxLineGap=HOUGH_MAX_LINE_GAP,
        )

        left, right = [], []
        if lines is not None:
            for line in lines:
                x1, y1, x2, y2 = line[0]
                if x2 - x1 == 0:
                    continue
                slope = (y2 - y1) / (x2 - x1)
                if abs(slope) < 0.4:
                    continue  # near-horizontal, not a lane
                if slope < 0:
                    left.append((x1, y1, x2, y2, slope))
                else:
                    right.append((x1, y1, x2, y2, slope))

        left_lane = self._fit_lane(left, h)
        right_lane = self._fit_lane(right, h)

        self.left_lines = [left_lane] if left_lane else []
        self.right_lines = [right_lane] if right_lane else []

        return {
            "left_lane": left_lane,
            "right_lane": right_lane,
            "left_slope": None if not left else np.mean([l[4] for l in left]),
            "right_slope": None if not right else np.mean([l[4] for l in right]),
            "num_lines_found": len(lines) if lines is not None else 0,
        }

    def draw_overlay(self, image_bgr, result=None):
        """Draw lane overlay on a copy of the image.

        Args:
            image_bgr: original BGR image
            result: dict from detect(). If None, uses last detection.

        Returns:
            BGR image with lane overlay drawn
        """
        if result is None:
            left = self.left_lines[0] if self.left_lines else None
            right = self.right_lines[0] if self.right_lines else None
        else:
            left = result.get("left_lane")
            right = result.get("right_lane")

        overlay = image_bgr.copy()

        if left is not None:
            cv2.line(overlay, (left[0], left[1]), (left[2], left[3]),
                     LANE_COLOR, 8)
        if right is not None:
            cv2.line(overlay, (right[0], right[1]), (right[2], right[3]),
                     LANE_COLOR, 8)

        # Blend overlay with original
        output = cv2.addWeighted(image_bgr, 1 - LANE_OVERLAY_ALPHA,
                                 overlay, LANE_OVERLAY_ALPHA, 0)

        # Draw filled lane area between detected lanes
        if left is not None and right is not None:
            pts = np.array([
                [left[0], left[1]],
                [left[2], left[3]],
                [right[2], right[3]],
                [right[0], right[1]],
            ], dtype=np.int32)
            cv2.fillPoly(output, [pts], (0, 255, 0))

        return output

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_roi(self, edges, height, width):
        """Mask to keep only the road region (trapezoid)."""
        mask = np.zeros_like(edges)
        top = int(height * ROI_TOP)
        bottom = int(height * ROI_BOTTOM)

        pts = np.array([[
            [0, bottom],
            [0, top],
            [width, top],
            [width, bottom],
        ]], dtype=np.int32)
        cv2.fillPoly(mask, pts, 255)
        return cv2.bitwise_and(edges, mask)

    def _fit_lane(self, line_segments, img_height):
        """Average a set of line segments and extrapolate to full lane.

        Args:
            line_segments: list of (x1, y1, x2, y2, slope)
            img_height: image height in pixels

        Returns:
            (x1, y1, x2, y2) of the fitted lane line, or None
        """
        if not line_segments:
            return None

        # Weight longer segments more heavily
        weights = []
        xs = []
        ys = []
        for x1, y1, x2, y2, slope in line_segments:
            length = np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            weights.extend([length, length])
            xs.extend([x1, x2])
            ys.extend([y1, y2])

        if len(xs) < 2:
            return None

        # Fit line through all points, weighted by segment length
        weights = np.array(weights)
        xs = np.array(xs)
        ys = np.array(ys)

        # Weighted linear regression: x = slope * y + intercept
        # (use y as independent variable — lanes are near-vertical)
        A = np.vstack([ys, np.ones_like(ys)]).T
        W = np.diag(weights)
        try:
            coeffs = np.linalg.solve(A.T @ W @ A, A.T @ W @ xs)
            slope, intercept = coeffs
        except np.linalg.LinAlgError:
            return None

        y1 = int(img_height)
        y2 = int(img_height * ROI_TOP)
        x1 = int(slope * y1 + intercept)
        x2 = int(slope * y2 + intercept)

        return (x1, y1, x2, y2)
