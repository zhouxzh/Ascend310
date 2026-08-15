from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    find_spec("PySide6") is None,
    reason="PySide6 is a board-side UI dependency",
)


@pytest.fixture()
def qt_app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from time_frequency_dashboard.ui.theme import stylesheet

    app.setStyleSheet(stylesheet())
    yield app


def test_touch_theme_exposes_readable_control_metrics(qt_app):
    from PySide6.QtWidgets import QCheckBox, QPushButton, QTabWidget

    from time_frequency_dashboard.ui.theme import (
        CHECKBOX_HEIGHT,
        CONTROL_HEIGHT,
        TAB_HEIGHT,
    )

    button = QPushButton("连接")
    checkbox = QCheckBox("显示 CH2")
    tabs = QTabWidget()
    tabs.addTab(QPushButton("内容"), "波形")
    for widget in (button, checkbox, tabs.tabBar()):
        widget.ensurePolished()
    assert button.minimumHeight() >= CONTROL_HEIGHT
    assert checkbox.minimumHeight() >= CHECKBOX_HEIGHT
    # The stylesheet applies the target height to QTabBar::tab rather than
    # the container QTabBar itself.
    assert tabs.tabBar().tabSizeHint(0).height() >= TAB_HEIGHT


def test_hantek_workspace_uses_scrollable_rail_at_target_size(qt_app):
    from PySide6.QtCore import Qt
    from time_frequency_dashboard.ui.hantek_workspace import HantekWorkspace

    workspace = HantekWorkspace(object(), Path("missing-bridge"), False)
    workspace.resize(1920, 1080)
    workspace.show()
    qt_app.processEvents()

    assert workspace.control_scroll.widget() is workspace.controls
    assert workspace.control_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    assert workspace.control_scroll.width() >= 300
    workspace.set_compact(True)
    assert workspace.control_scroll.width() == 292
    workspace.close()


def test_sdr_workspace_keeps_controls_and_results_readable(qt_app):
    from time_frequency_dashboard.ui.sdr_workspace import SdrWorkspace
    from time_frequency_dashboard.ui.theme import TABLE_ROW_HEIGHT

    workspace = SdrWorkspace()
    workspace.resize(1920, 1080)
    workspace.show()
    qt_app.processEvents()

    assert workspace.control_scroll.widget() is workspace.controls
    assert workspace.results_frame.width() >= 340
    assert workspace.results.table.verticalHeader().defaultSectionSize() >= TABLE_ROW_HEIGHT
    workspace.set_compact(True)
    assert workspace.results_frame.width() == 320
    workspace.close()


def test_plot_axes_use_explicit_touch_display_font(qt_app):
    pytest.importorskip("pyqtgraph")
    from time_frequency_dashboard.ui.plot_views import SpectrumPlot

    view = SpectrumPlot()
    if view.plot is None:
        pytest.skip("PyQtGraph is unavailable")
    tick_font = view.plot.getPlotItem().getAxis("bottom").style["tickFont"]
    assert tick_font.pixelSize() >= 13
    view.close()
