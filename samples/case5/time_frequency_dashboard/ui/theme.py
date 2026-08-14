"""Instrument-style palette and stylesheet."""

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
    QWidget {{ background: {BACKGROUND}; color: {TEXT}; font-size: 16px; }}
    QMainWindow {{ background: {BACKGROUND}; }}
    QFrame#topBar, QWidget#controlRail, QWidget#sdrControlRail,
    QFrame#statusBar, QFrame#sdrStatusBar, QFrame#sdrResultsRail {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 4px;
    }}
    QFrame#section {{ background: transparent; border: 0; border-bottom: 1px solid {BORDER}; }}
    QLabel#sectionTitle {{ color: {MUTED}; font-size: 14px; font-weight: 600; }}
    QLabel#badge {{ background: {SURFACE_RAISED}; border: 1px solid {BORDER}; padding: 7px 10px; }}
    QLabel#metricValue {{ color: {TEXT}; font-size: 18px; font-weight: 600; }}
    QPushButton, QToolButton, QComboBox, QSpinBox, QDoubleSpinBox {{
        min-height: 42px; background: {SURFACE_RAISED}; border: 1px solid {BORDER};
        padding: 5px 10px;
    }}
    QPushButton:pressed, QToolButton:pressed {{ background: #385159; }}
    QPushButton:disabled, QToolButton:disabled, QComboBox:disabled,
    QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: #6e7b80; background: #172024; }}
    QTabBar::tab {{ min-height: 46px; min-width: 120px; padding: 6px 18px; }}
    QTabBar::tab:selected {{ color: {VOLTAGE}; border-bottom: 3px solid {VOLTAGE}; }}
    QTabWidget::pane {{ border: 0; }}
    QCheckBox {{ spacing: 10px; min-height: 38px; }}
    """
