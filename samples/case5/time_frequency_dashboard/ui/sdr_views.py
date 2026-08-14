"""PyQtGraph SDR views that render live IQ and reviewed-model inputs.

These widgets only visualize data supplied by the SDR service.  In particular,
``SpectrogramDetectionPlot`` renders the exact CPU FFTW image that is passed to
a spectrogram detector; it does not claim to be an NPU FFT or calibrated RF
measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

try:  # pyqtgraph is optional in the host development environment.
    import pyqtgraph as pg
    from pyqtgraph import PlotWidget
except ImportError:  # pragma: no cover - exercised when board UI extras are absent
    pg = None
    PlotWidget = None

from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .plot_views import MissingPlotWidget
from .theme import CURRENT, GOOD, MUTED, SURFACE, VOLTAGE


MAX_DISPLAY_SAMPLES = 4096
MAX_DETECTION_OVERLAYS = 64
MAX_RECENT_DETECTIONS = 64


@dataclass(frozen=True)
class PhysicalDetectionBox:
    """A model-image detection mapped into the current RF capture window."""

    label: str
    confidence: float
    image_box_xyxy: tuple[float, float, float, float]
    time_start_s: float
    time_end_s: float
    frequency_low_hz: float
    frequency_high_hz: float
    batch_sequence: int | None = None
    source_sample_offset: int | None = None


def _as_finite_complex_samples(samples: Any) -> np.ndarray:
    values = np.asarray(samples, dtype=np.complex64).reshape(-1)
    if values.size and not np.all(np.isfinite(values)):
        raise ValueError("I/Q display samples contain NaN or Inf")
    return values


def sample_iq_for_display(samples: Any, maximum: int = MAX_DISPLAY_SAMPLES) -> np.ndarray:
    """Return evenly spaced I/Q points without changing the acquisition window."""
    if maximum <= 0:
        raise ValueError("maximum display samples must be positive")
    values = _as_finite_complex_samples(samples)
    if values.size <= maximum:
        return values
    indices = np.linspace(0, values.size - 1, maximum, dtype=np.int64)
    return np.ascontiguousarray(values[indices], dtype=np.complex64)


def time_axis_for_display(
    *, source_sample_count: int, drawn_sample_count: int, sample_rate_hz: float
) -> np.ndarray:
    """Map a bounded draw buffer back onto the original fixed IQ window."""
    if source_sample_count <= 0 or drawn_sample_count <= 0:
        return np.empty(0, dtype=np.float64)
    if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0.0:
        raise ValueError("sample rate must be finite and positive")
    duration_s = (int(source_sample_count) - 1) / float(sample_rate_hz)
    return np.linspace(0.0, duration_s, num=int(drawn_sample_count), dtype=np.float64)


def constellation_samples_from_model_input(model_input: Any, fallback_samples: Any) -> np.ndarray:
    """Use normalized model IQ when available, otherwise the capture IQ.

    Spectrogram models have no I/Q tensor after preprocessing, so their
    constellation necessarily stays at the capture-IQ stage.  The caller can
    use this distinction in its visible caption.
    """
    if model_input is not None:
        values = np.asarray(model_input)
        if values.ndim == 3 and values.shape[1] == 2:
            real = np.asarray(values[:, 0, :], dtype=np.float32).reshape(-1)
            imag = np.asarray(values[:, 1, :], dtype=np.float32).reshape(-1)
            combined = real.astype(np.complex64) + 1j * imag.astype(np.complex64)
            return sample_iq_for_display(combined)
    return sample_iq_for_display(fallback_samples)


def normalize_spectrogram_image(image: Any) -> np.ndarray:
    """Extract a finite 2D model-input image from common NCHW/NHW shapes."""
    values = np.asarray(image, dtype=np.float32)
    if values.ndim == 4:
        if values.shape[0] != 1:
            raise ValueError("spectrogram display expects a single model image")
        values = values[0]
    if values.ndim == 3:
        if values.shape[0] in (1, 3, 4):
            values = values[0]
        elif values.shape[-1] in (1, 3, 4):
            values = values[..., 0]
        else:
            raise ValueError("spectrogram image must be CHW or HWC")
    if values.ndim != 2 or values.shape[0] <= 0 or values.shape[1] <= 0:
        raise ValueError("spectrogram image must be a non-empty 2D image")
    if not np.all(np.isfinite(values)):
        raise ValueError("spectrogram image contains NaN or Inf")
    return np.ascontiguousarray(values, dtype=np.float32)


def map_detection_box(
    detection: Mapping[str, Any],
    *,
    image_width: int,
    image_height: int,
    batch_duration_ms: float,
    center_frequency_hz: float,
    sample_rate_hz: float,
    batch_sequence: int | None = None,
    source_sample_offset: int | None = None,
) -> PhysicalDetectionBox | None:
    """Map a clipped YOLO image box to time and nominal RF frequency.

    The detector sees an image with time along x and a vertically flipped,
    fft-shifted frequency axis.  Consequently image y=0 is nominally
    ``+sample_rate / 2`` relative to the tuned centre frequency.
    """
    if image_width <= 0 or image_height <= 0:
        raise ValueError("spectrogram dimensions must be positive")
    try:
        duration_ms = float(batch_duration_ms)
        center_hz = float(center_frequency_hz)
        sample_rate = float(sample_rate_hz)
    except (TypeError, ValueError) as exc:
        raise ValueError("capture duration, centre frequency, and sample rate must be numeric") from exc
    if (
        not np.isfinite(duration_ms)
        or not np.isfinite(center_hz)
        or not np.isfinite(sample_rate)
        or duration_ms <= 0.0
        or center_hz <= 0.0
        or sample_rate <= 0.0
    ):
        raise ValueError("capture duration, centre frequency, and sample rate must be finite and positive")
    raw_box = detection.get("box_xyxy")
    if not isinstance(raw_box, Sequence) or len(raw_box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in raw_box)
        confidence = float(detection.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    values = np.asarray((x0, y0, x1, y1, confidence), dtype=np.float64)
    if not np.all(np.isfinite(values)):
        return None
    x0, x1 = sorted((float(np.clip(x0, 0.0, image_width)), float(np.clip(x1, 0.0, image_width))))
    y0, y1 = sorted((float(np.clip(y0, 0.0, image_height)), float(np.clip(y1, 0.0, image_height))))
    if x1 <= x0 or y1 <= y0:
        return None
    duration_s = duration_ms / 1_000.0
    time_start = x0 / image_width * duration_s
    time_end = x1 / image_width * duration_s
    # y grows downward in the model image, while physical frequency decreases.
    frequency_high = center_hz + (0.5 - y0 / image_height) * sample_rate
    frequency_low = center_hz + (0.5 - y1 / image_height) * sample_rate
    return PhysicalDetectionBox(
        label=str(detection.get("label", "signal")),
        confidence=confidence,
        image_box_xyxy=(x0, y0, x1, y1),
        time_start_s=time_start,
        time_end_s=time_end,
        frequency_low_hz=frequency_low,
        frequency_high_hz=frequency_high,
        batch_sequence=batch_sequence,
        source_sample_offset=source_sample_offset,
    )


class IqTimePlot(QWidget):
    """Two trace, uncalibrated I/Q display capped at ``MAX_DISPLAY_SAMPLES``."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._samples = np.empty(0, dtype=np.complex64)
        self._sample_rate_hz = 1.0
        self._source_sample_count = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if pg is None:
            layout.addWidget(MissingPlotWidget())
            self.plot = None
            self.i_curve = None
            self.q_curve = None
            return
        self.plot = PlotWidget()
        self.plot.setBackground(SURFACE)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "未校准 I/Q", units="[-1, 1]")
        self.plot.setLabel("bottom", "时间", units="s")
        self.plot.getPlotItem().hideButtons()
        self.plot.addLegend(offset=(10, 10))
        self.i_curve = self.plot.plot(pen=pg.mkPen(VOLTAGE, width=1.5), name="I")
        self.q_curve = self.plot.plot(pen=pg.mkPen(CURRENT, width=1.5), name="Q")
        layout.addWidget(self.plot)

    def set_samples(
        self,
        samples: Any,
        sample_rate_hz: float,
        source_sample_count: int | None = None,
    ) -> None:
        self._samples = sample_iq_for_display(samples)
        self._sample_rate_hz = max(float(sample_rate_hz), 1.0)
        self._source_sample_count = (
            self._samples.size if source_sample_count is None else max(int(source_sample_count), self._samples.size)
        )
        if self.i_curve is None or self.q_curve is None:
            return
        if self._samples.size == 0:
            self.i_curve.clear()
            self.q_curve.clear()
            return
        time_axis = time_axis_for_display(
            source_sample_count=self._source_sample_count,
            drawn_sample_count=self._samples.size,
            sample_rate_hz=self._sample_rate_hz,
        )
        self.i_curve.setData(time_axis, self._samples.real)
        self.q_curve.setData(time_axis, self._samples.imag)

    def clear(self) -> None:
        self.set_samples(np.empty(0, dtype=np.complex64), 1.0)

    def reset_view(self) -> None:
        if self.plot is not None:
            self.plot.enableAutoRange()


class ConstellationPlot(QWidget):
    """Equal-aspect constellation view centred on zero."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._samples = np.empty(0, dtype=np.complex64)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if pg is None:
            layout.addWidget(MissingPlotWidget())
            self.plot = None
            self.scatter = None
            return
        self.plot = PlotWidget()
        self.plot.setBackground(SURFACE)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "Q")
        self.plot.setLabel("bottom", "I")
        self.plot.getPlotItem().hideButtons()
        self.plot.getViewBox().setAspectLocked(True, ratio=1.0)
        self.scatter = pg.ScatterPlotItem(size=4, pen=None, brush=pg.mkBrush(VOLTAGE + "88"))
        self.plot.addItem(self.scatter)
        layout.addWidget(self.plot)

    def set_samples(self, samples: Any) -> None:
        self._samples = sample_iq_for_display(samples)
        if self.scatter is None:
            return
        if self._samples.size == 0:
            self.scatter.setData([], [])
            return
        self.scatter.setData(self._samples.real, self._samples.imag)
        radius = max(float(np.max(np.abs(self._samples.real))), float(np.max(np.abs(self._samples.imag))), 0.05)
        radius *= 1.08
        self.plot.setXRange(-radius, radius, padding=0.0)
        self.plot.setYRange(-radius, radius, padding=0.0)

    def clear(self) -> None:
        self.set_samples(np.empty(0, dtype=np.complex64))

    def reset_view(self) -> None:
        if self.plot is not None and self._samples.size:
            self.set_samples(self._samples)


class SpectrogramDetectionPlot(QWidget):
    """Exact detector input preview with nominal absolute-frequency overlays."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._image = np.empty((0, 0), dtype=np.float32)
        self._boxes: list[PhysicalDetectionBox] = []
        self._overlay_items: list[Any] = []
        self._center_frequency_hz = 0.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if pg is None:
            layout.addWidget(MissingPlotWidget())
            self.note_label = None
            self.plot = None
            self.image_item = None
            return
        self.note_label = QLabel("CPU FFTW 模型输入；NPU 负责检测。未校准 RF 轴。")
        self.note_label.setStyleSheet(f"color: {MUTED}; padding: 2px 6px;")
        layout.addWidget(self.note_label)
        self.plot = PlotWidget()
        self.plot.setBackground(SURFACE)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("bottom", "批内时间", units="s")
        self.plot.setLabel("left", "Nominal frequency", units="Hz")
        self.plot.getPlotItem().hideButtons()
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.plot.addItem(self.image_item)
        layout.addWidget(self.plot)

    @property
    def physical_boxes(self) -> tuple[PhysicalDetectionBox, ...]:
        return tuple(self._boxes)

    def set_spectrogram(
        self,
        image: Any,
        *,
        detections: Iterable[Mapping[str, Any]] = (),
        batch_duration_ms: float,
        center_frequency_hz: float,
        sample_rate_hz: float,
        batch_sequence: int | None = None,
        source_sample_offset: int | None = None,
    ) -> tuple[PhysicalDetectionBox, ...]:
        try:
            duration_ms = float(batch_duration_ms)
            center_hz = float(center_frequency_hz)
            sample_rate = float(sample_rate_hz)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "spectrogram display duration, centre frequency, and sample rate must be numeric"
            ) from exc
        if (
            not np.isfinite(duration_ms)
            or not np.isfinite(center_hz)
            or not np.isfinite(sample_rate)
            or duration_ms <= 0.0
            or center_hz <= 0.0
            or sample_rate <= 0.0
        ):
            raise ValueError(
                "spectrogram display duration, centre frequency, and sample rate must be finite and positive"
            )
        self._image = normalize_spectrogram_image(image)
        self._center_frequency_hz = center_hz
        height, width = self._image.shape
        mapped: list[PhysicalDetectionBox] = []
        for detection in list(detections)[:MAX_DETECTION_OVERLAYS]:
            if not isinstance(detection, Mapping):
                continue
            mapped_box = map_detection_box(
                detection,
                image_width=width,
                image_height=height,
                batch_duration_ms=duration_ms,
                center_frequency_hz=center_hz,
                sample_rate_hz=sample_rate,
                batch_sequence=batch_sequence,
                source_sample_offset=source_sample_offset,
            )
            if mapped_box is not None:
                mapped.append(mapped_box)
        self._boxes = mapped
        if self.image_item is None:
            return self.physical_boxes
        # Model rows are high-to-low frequency.  Flip them so the physical
        # coordinates rise from -Fs/2 to +Fs/2 in the ordinary plot view.
        self.image_item.setImage(np.flipud(self._image), autoLevels=True)
        duration_s = duration_ms / 1_000.0
        low_frequency_hz = center_hz - sample_rate / 2.0
        self.image_item.setRect(
            QRectF(0.0, low_frequency_hz, duration_s, sample_rate)
        )
        self.plot.setLimits(
            xMin=0.0,
            xMax=duration_s,
            yMin=low_frequency_hz,
            yMax=low_frequency_hz + sample_rate,
        )
        self.note_label.setText(
            "CPU FFTW 模型输入；NPU 负责检测。"
            f"名义中心 {center_hz / 1e6:.6f} MHz，PPM 已配置，未校准 RF 轴。"
        )
        self._render_overlays()
        return self.physical_boxes

    def clear(self, message: str = "当前模型不使用时频输入") -> None:
        self._image = np.empty((0, 0), dtype=np.float32)
        self._boxes = []
        self._remove_overlays()
        if self.image_item is not None:
            self.image_item.clear()
        if self.note_label is not None:
            self.note_label.setText(message)

    def reset_view(self) -> None:
        if self.plot is not None:
            self.plot.enableAutoRange()

    def _remove_overlays(self) -> None:
        if self.plot is None:
            self._overlay_items = []
            return
        for item in self._overlay_items:
            self.plot.removeItem(item)
        self._overlay_items = []

    def _render_overlays(self) -> None:
        self._remove_overlays()
        if self.plot is None:
            return
        for box in self._boxes:
            pen = pg.mkPen(GOOD, width=2)
            curve = self.plot.plot(
                [box.time_start_s, box.time_end_s, box.time_end_s, box.time_start_s, box.time_start_s],
                [
                    box.frequency_low_hz,
                    box.frequency_low_hz,
                    box.frequency_high_hz,
                    box.frequency_high_hz,
                    box.frequency_low_hz,
                ],
                pen=pen,
            )
            caption = pg.TextItem(f"{box.label} {box.confidence:.2f}", color=GOOD, anchor=(0, 1))
            caption.setPos(box.time_start_s, box.frequency_high_hz)
            self.plot.addItem(caption)
            self._overlay_items.extend((curve, caption))


class SdrInferenceResults(QWidget):
    """Bounded, task-aware table for classification Top-K or detector output."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("等待 NPU 结果")
        self.title.setObjectName("sectionTitle")
        layout.addWidget(self.title)
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("sdrInferenceResults")
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self._recent_detections: list[PhysicalDetectionBox] = []
        self.show_empty("等待 NPU 结果")

    @property
    def recent_detections(self) -> tuple[PhysicalDetectionBox, ...]:
        """A bounded, newest-first history for the current SDR generation."""
        return tuple(self._recent_detections)

    def show_empty(self, text: str) -> None:
        self._recent_detections = []
        self.title.setText(text)
        self.table.setRowCount(0)
        self.table.setHorizontalHeaderLabels(["结果", "置信度", "时间", "频率"])

    def set_classification(self, top_k: Any) -> None:
        self._recent_detections = []
        rows = top_k[0] if isinstance(top_k, Sequence) and top_k and isinstance(top_k[0], Sequence) else top_k
        if not isinstance(rows, Sequence):
            self.show_empty("分类模型未返回 Top-K")
            return
        self.title.setText("NPU Top-K")
        self.table.setHorizontalHeaderLabels(["类别", "置信度", "索引", ""])
        self.table.setRowCount(min(len(rows), 20))
        for row_index, item in enumerate(rows[:20]):
            if not isinstance(item, Mapping):
                continue
            self._set_row(
                row_index,
                (
                    str(item.get("label", "-")),
                    f"{float(item.get('confidence', 0.0)):.3f}",
                    str(item.get("class_index", "-")),
                    "",
                ),
            )

    def set_detections(self, boxes: Sequence[PhysicalDetectionBox]) -> None:
        latest = list(boxes)[:MAX_RECENT_DETECTIONS]
        self._recent_detections = (
            latest + self._recent_detections
        )[:MAX_RECENT_DETECTIONS]
        self.title.setText(f"NPU 检测（最近 {len(self._recent_detections)} 条）")
        self.table.setHorizontalHeaderLabels(["类别", "置信度", "时间", "频率"])
        self.table.setRowCount(min(len(self._recent_detections), 20))
        for row_index, box in enumerate(self._recent_detections[:20]):
            self._set_row(
                row_index,
                (
                    box.label,
                    f"{box.confidence:.3f}",
                    (
                        f"批 {box.batch_sequence} · "
                        if box.batch_sequence is not None
                        else ""
                    )
                    + f"{box.time_start_s * 1e3:.1f}-{box.time_end_s * 1e3:.1f} ms",
                    f"{box.frequency_low_hz / 1e6:.6f}-{box.frequency_high_hz / 1e6:.6f} MHz",
                ),
            )

    def _set_row(self, row: int, values: Sequence[str]) -> None:
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, column, item)
