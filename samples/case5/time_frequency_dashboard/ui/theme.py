"""Instrument-style palette and stylesheet."""

from __future__ import annotations

from PySide6.QtGui import QFont


# The dashboard is primarily operated on a 10-inch 1920x1080 touch display.
# Keep the hierarchy in one place so a board image and an offscreen test use
# the same touch targets instead of inheriting platform-dependent defaults.
BODY_FONT_SIZE = 18
SECONDARY_FONT_SIZE = 15
SECTION_FONT_SIZE = 18
PLOT_FONT_SIZE = 14
CONTROL_HEIGHT = 48
CHECKBOX_HEIGHT = 44
TAB_HEIGHT = 52
TABLE_ROW_HEIGHT = 38
CONTROL_RAIL_WIDTH = 320
RESULTS_RAIL_WIDTH = 360

BACKGROUND = "#101416"
SURFACE = "#192126"
SURFACE_RAISED = "#202d33"
BORDER = "#3b4b52"
TEXT = "#e7eef1"
MUTED = "#a9b6ba"
VOLTAGE = "#36d1c4"
CURRENT = "#f2a65a"
GOOD = "#49c98b"
WARNING = "#e6c45c"
ERROR = "#ed6a67"


def stylesheet() -> str:
    return f"""
    QWidget {{ background: {BACKGROUND}; color: {TEXT}; font-size: {BODY_FONT_SIZE}px; }}
    QMainWindow {{ background: {BACKGROUND}; }}
    QFrame#topBar, QWidget#controlRail, QWidget#sdrControlRail,
    QFrame#statusBar, QFrame#sdrStatusBar, QFrame#sdrResultsRail,
    QScrollArea#controlRailViewport, QScrollArea#sdrControlRailViewport {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 4px;
    }}
    QScrollArea#controlRailViewport QWidget#controlRail,
    QScrollArea#sdrControlRailViewport QWidget#sdrControlRail {{
        background: {SURFACE}; border: 0;
    }}
    QFrame#section {{ background: transparent; border: 0; border-bottom: 1px solid {BORDER}; }}
    QLabel#sectionTitle {{ color: {MUTED}; font-size: {SECTION_FONT_SIZE}px; font-weight: 600; }}
    QLabel#badge {{ background: {SURFACE_RAISED}; border: 1px solid {BORDER}; padding: 8px 12px; }}
    QLabel#metricValue {{ color: {TEXT}; font-size: 20px; font-weight: 600; }}
    QLabel#secondary, QLabel#sdrDeveloperBanner {{ font-size: {SECONDARY_FONT_SIZE}px; }}
    QPushButton, QToolButton, QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
        min-height: {CONTROL_HEIGHT}px; background: {SURFACE_RAISED}; border: 1px solid {BORDER};
        padding: 7px 12px;
    }}
    QPushButton:pressed, QToolButton:pressed {{ background: #385159; }}
    QPushButton:disabled, QToolButton:disabled, QComboBox:disabled,
    QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {{ color: #6e7b80; background: #172024; }}
    QTabBar::tab {{ min-height: {TAB_HEIGHT}px; min-width: 128px; padding: 8px 18px; }}
    QTabBar::tab:selected {{ color: {VOLTAGE}; border-bottom: 3px solid {VOLTAGE}; }}
    QTabWidget::pane {{ border: 0; }}
    QCheckBox {{ spacing: 10px; min-height: {CHECKBOX_HEIGHT}px; }}
    QScrollArea {{ border: 0; background: {SURFACE}; }}
    QScrollBar:vertical {{ width: 18px; background: {SURFACE}; margin: 2px; }}
    QScrollBar::handle:vertical {{ min-height: 44px; background: {BORDER}; border-radius: 8px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QTableWidget {{ font-size: 16px; gridline-color: {BORDER}; }}
    QHeaderView::section {{ min-height: {TABLE_ROW_HEIGHT}px; padding: 5px 6px; }}
    QToolTip {{ font-size: 16px; padding: 6px; }}
    """


def plot_font(size: int = PLOT_FONT_SIZE, *, bold: bool = False) -> QFont:
    """Return a deterministic font for PyQtGraph axes and annotations."""
    font = QFont()
    font.setPixelSize(int(size))
    font.setBold(bool(bold))
    return font
