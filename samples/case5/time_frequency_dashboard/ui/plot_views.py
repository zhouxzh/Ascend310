"""PyQtGraph-backed waveform, dB spectrum and waterfall views."""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

try:  # The board installs this optional plotting dependency.
    import pyqtgraph as pg
    from pyqtgraph import PlotWidget
except ImportError:  # pragma: no cover - exercised by dependency-skip tests
    pg = None
    PlotWidget = None

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import ERROR, MUTED, SURFACE, VOLTAGE


def pyqtgraph_available() -> bool:
    return pg is not None


class MissingPlotWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel("PyQtGraph 未安装，无法显示实时图形")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(f"color: {ERROR}; background: {SURFACE}; padding: 24px;")
        layout.addWidget(label)


class WaveformPlot(QWidget):
    def __init__(self, title: str, color: str, unit: str, parent=None) -> None:
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self._values = np.empty(0, dtype=np.float32)
        self._sample_rate_hz = 1.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if pg is None:
            layout.addWidget(MissingPlotWidget())
            self.plot = None
            self.curve = None
            return
        self.plot = PlotWidget()
        self.plot.setBackground(SURFACE)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", title, units=unit)
        self.plot.setLabel("bottom", "时间", units="s")
        self.plot.getPlotItem().hideButtons()
        self.curve = self.plot.plot(pen=pg.mkPen(color, width=2))
        layout.addWidget(self.plot)

    def set_values(self, values: Optional[np.ndarray], sample_rate_hz: float) -> None:
        self._values = np.empty(0, dtype=np.float32) if values is None else np.asarray(values, dtype=np.float32)
        self._sample_rate_hz = max(float(sample_rate_hz), 1.0)
        if self.curve is None:
            return
        if self._values.size == 0:
            self.curve.clear()
            return
        # Downsample only for painting; acquisition and NPU windows are intact.
        count = min(self._values.size, 5000)
        indices = np.linspace(0, self._values.size - 1, count).astype(np.int64)
        self.curve.setData(indices.astype(np.float64) / self._sample_rate_hz, self._values[indices])

    def reset_view(self) -> None:
        if self.plot is not None:
            self.plot.enableAutoRange()


class SpectrumPlot(QWidget):
    """High-resolution NPU DFT spectrum and an optional NPU peak hold."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._axis = np.empty(0, dtype=np.float32)
        self._values = np.empty(0, dtype=np.float32)
        self._peak_values: Optional[np.ndarray] = None
        self._peak_hold = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if pg is None:
            layout.addWidget(MissingPlotWidget())
            self.plot = None
            self.curve = None
            self.peak_curve = None
            return
        self.position_label = QLabel("频率：-- · 频带能量：--")
        self.position_label.setAlignment(Qt.AlignRight)
        self.position_label.setStyleSheet(f"color: {MUTED}; padding: 2px 6px;")
        layout.addWidget(self.position_label)
        self.plot = PlotWidget()
        self.plot.setBackground(SURFACE)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("left", "频带能量", units="dB re 1 V²（未校准）")
        self.plot.setLabel("bottom", "频率", units="Hz")
        self.plot.getPlotItem().hideButtons()
        self.plot.addLegend(offset=(10, 10))
        self.curve = self.plot.plot(pen=pg.mkPen(VOLTAGE, width=2), name="NPU DFT 频谱")
        self.peak_curve = self.plot.plot(pen=pg.mkPen("#f4f4f4", width=1), name="NPU DFT 峰值保持")
        self.peak_curve.setVisible(False)
        self.v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#9eb3ba", width=1))
        self.h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#9eb3ba", width=1))
        self.plot.addItem(self.v_line, ignoreBounds=True)
        self.plot.addItem(self.h_line, ignoreBounds=True)
        self._mouse_proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved, rateLimit=30, slot=self._mouse_moved
        )
        layout.addWidget(self.plot)

    def set_peak_hold(self, enabled: bool) -> None:
        self._peak_hold = bool(enabled)
        if not self._peak_hold:
            self._peak_values = None
            if self.peak_curve is not None:
                self.peak_curve.clear()
                self.peak_curve.setVisible(False)

    def clear_peak_hold(self) -> None:
        self._peak_values = None
        if self.peak_curve is not None:
            self.peak_curve.clear()

    def set_data(
        self,
        axis: Optional[np.ndarray],
        values: Optional[np.ndarray],
    ) -> None:
        self._axis = np.empty(0, dtype=np.float32) if axis is None else np.asarray(axis, dtype=np.float32)
        self._values = np.empty(0, dtype=np.float32) if values is None else np.asarray(values, dtype=np.float32)
        if self.curve is None:
            return
        if self._axis.size == 0 or self._axis.size != self._values.size:
            self.curve.clear()
        else:
            self.curve.setData(self._axis, self._values)
        if self._axis.size == 0 or self._axis.size != self._values.size:
            return
        if self._peak_hold:
            if self._peak_values is None or self._peak_values.shape != self._values.shape:
                self._peak_values = self._values.copy()
            else:
                self._peak_values = np.maximum(self._peak_values, self._values)
            self.peak_curve.setData(self._axis, self._peak_values)
            self.peak_curve.setVisible(True)

    def _mouse_moved(self, event) -> None:
        if self.plot is None or self._axis.size == 0:
            return
        position = event[0]
        plot_item = self.plot.getPlotItem()
        if not plot_item.sceneBoundingRect().contains(position):
            return
        point = plot_item.vb.mapSceneToView(position)
        index = int(np.argmin(np.abs(self._axis - point.x())))
        x_value, y_value = float(self._axis[index]), float(self._values[index])
        self.position_label.setText(f"频率：{x_value / 1e3:.1f} kHz · 频带能量：{y_value:.1f} dB")
        self.v_line.setPos(x_value)
        self.h_line.setPos(y_value)

    def reset_view(self) -> None:
        if self.plot is not None:
            self.plot.enableAutoRange()


class WaterfallPlot(QWidget):
    """Single-channel dB waterfall with QSpectrumAnalyzer-style histogram levels."""

    levels_changed = Signal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows = np.empty((0, 0), dtype=np.float32)
        self._axis = np.empty(0, dtype=np.float32)
        self._levels = (-120.0, -80.0)
        self._applying_levels = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if pg is None:
            layout.addWidget(MissingPlotWidget())
            self.plot = None
            self.image = None
            self.histogram = None
            return
        self.plot = PlotWidget()
        self.plot.setBackground(SURFACE)
        self.plot.showGrid(x=False, y=False)
        self.plot.setLabel("left", "时间（新→旧）")
        self.plot.setLabel("bottom", "频率", units="Hz")
        self.plot.getPlotItem().hideButtons()
        self.image = pg.ImageItem(axisOrder="row-major")
        self.plot.addItem(self.image)
        self.histogram = pg.HistogramLUTWidget()
        self.histogram.setMinimumWidth(92)
        self.histogram.item.gradient.loadPreset("flame")
        self.histogram.item.setImageItem(self.image)
        self.histogram.item.sigLevelsChanged.connect(self._on_histogram_levels_changed)
        layout.addWidget(self.plot, 1)
        layout.addWidget(self.histogram)

    @property
    def levels(self) -> tuple[float, float]:
        return self._levels

    def set_rows(
        self,
        rows: np.ndarray,
        axis_hz: np.ndarray,
        levels: Optional[Sequence[float]] = None,
    ) -> None:
        self._rows = np.asarray(rows, dtype=np.float32)
        self._axis = np.asarray(axis_hz, dtype=np.float32)
        if levels is not None:
            self.set_levels(float(levels[0]), float(levels[1]))
        if self.image is None:
            return
        if self._rows.ndim != 2 or self._rows.size == 0 or self._axis.size != self._rows.shape[1]:
            self.image.hide()
            return
        self.image.show()
        # Controller rows are old-to-new.  In a normal PyQtGraph view the
        # largest y coordinate is at the top, so the newest row remains top.
        self.image.setImage(self._rows, autoLevels=False, levels=self._levels)
        self._set_image_rect()

    def set_levels(self, low_db: float, high_db: float) -> None:
        low, high = float(low_db), float(high_db)
        if high <= low:
            raise ValueError("waterfall levels must satisfy high > low")
        self._levels = (low, high)
        if self.image is not None and self._rows.size:
            self.image.setLevels(self._levels)
        if self.histogram is not None:
            self._applying_levels = True
            try:
                self.histogram.item.setLevels(*self._levels)
            finally:
                self._applying_levels = False

    def _set_image_rect(self) -> None:
        if self.image is None or self.plot is None or self._axis.size == 0:
            return
        step = float(np.median(np.diff(self._axis))) if self._axis.size > 1 else 1.0
        step = max(step, 1.0)
        left = float(self._axis[0]) - step / 2.0
        width = step * self._axis.size
        height = max(float(self._rows.shape[0]), 1.0)
        self.image.setRect(QRectF(left, 0.0, width, height))
        self.plot.setLimits(xMin=left, xMax=left + width, yMin=0.0, yMax=height)

    def _on_histogram_levels_changed(self, *_args) -> None:
        if self._applying_levels or self.histogram is None:
            return
        levels = self.histogram.item.getLevels()
        if levels is None:
            return
        low, high = float(levels[0]), float(levels[1])
        if high <= low:
            return
        self._levels = (low, high)
        self.levels_changed.emit(low, high)

    def reset_view(self) -> None:
        if self.plot is not None:
            self.plot.enableAutoRange()
