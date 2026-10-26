"""悬浮窗主题：暖白 + 香槟金，半透明卡片，圆角。"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme, setThemeColor

GOLD = "#c9a227"
GOLD_DEEP = "#8a6d3b"
GOLD_SOFT = "#b08d57"
GOLD_PALE = "#f3e8cf"

INK = "#2b2a26"
INK_2 = "#3f3c36"
MUTED = "#6f6a5f"
FAINT = "#a29a8c"
HAIRLINE = "#e8dfcf"

SURFACE = "#fffdf8"
SURFACE_2 = "#fbf8f2"
SCREEN = "#f3f0e9"

OK = "#3f7d58"
WARN = "#b07d2b"
ERROR = "#b4493f"

RADIUS = 16


def apply_theme(app: QApplication) -> None:
    """qfluentwidgets 只负责控件与亚克力，颜色由我们的 QSS 接管。"""
    setTheme(Theme.LIGHT)
    setThemeColor(QColor(GOLD), save=False)
    font = QFont("Microsoft YaHei UI")
    if not font.exactMatch():
        font = QFont("Microsoft YaHei")
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)


STYLESHEET = f"""
* {{
    outline: none;
}}

QWidget {{
    color: {INK};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
}}

#RootCard {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 rgba(255, 253, 248, 0.96),
        stop:1 rgba(247, 241, 230, 0.96));
    border: 1px solid {HAIRLINE};
    border-radius: {RADIUS}px;
}}

#TitleBar {{
    background: transparent;
    border-bottom: 1px solid rgba(232, 223, 207, 0.7);
}}

#Brand {{
    color: {GOLD_DEEP};
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 1px;
}}

#Subtitle {{
    color: {FAINT};
    font-size: 11px;
}}

#Card {{
    background: rgba(255, 255, 255, 0.82);
    border: 1px solid {HAIRLINE};
    border-radius: 14px;
}}

#Card[selected="true"] {{
    background: rgba(247, 236, 211, 0.95);
    border: 1px solid {GOLD};
}}

#CompactRow {{
    background: rgba(255, 255, 255, 0.70);
    border: 1px solid transparent;
    border-radius: 12px;
}}

#CompactRow:hover {{
    background: rgba(255, 255, 255, 0.92);
    border: 1px solid {HAIRLINE};
}}

#CompactRow[selected="true"] {{
    background: rgba(247, 236, 211, 0.95);
    border: 1px solid {GOLD};
}}

#GameName {{
    color: {GOLD_DEEP};
    font-size: 15px;
    font-weight: 700;
}}

#ProjectName {{
    color: {FAINT};
    font-size: 11px;
}}

#StatusText {{
    font-size: 12px;
    font-weight: 600;
}}

#Metric {{
    color: {MUTED};
    font-size: 11px;
}}

#MetricStrong {{
    color: {INK_2};
    font-size: 12px;
    font-weight: 600;
}}

#SectionTitle {{
    color: {INK};
    font-size: 14px;
    font-weight: 700;
}}

#PageTitle {{
    color: {INK};
    font-size: 22px;
    font-weight: 700;
}}

#PageHint {{
    color: {FAINT};
    font-size: 12px;
}}

#StatValue {{
    color: {INK};
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
    font-size: 20px;
    font-weight: 700;
}}

#StatLabel {{
    color: {FAINT};
    font-size: 11px;
}}

#Pill {{
    color: {GOLD_DEEP};
    background: {GOLD_PALE};
    border-radius: 9px;
    padding: 2px 9px;
    font-size: 11px;
}}

#StatusPill[state="running"] {{
    color: #2f6547;
    background: #e5f2e9;
}}

#StatusPill[state="stopped"] {{
    color: #5f594e;
    background: #ece6da;
}}

#StatusPill[state="warn"] {{
    color: #8a5a16;
    background: #fbf0d9;
}}

#StatusPill[state="error"] {{
    color: #8f342d;
    background: #f9e6e3;
}}

#PathLabel {{
    color: {MUTED};
    font-size: 11px;
}}

#LinkLabel {{
    color: {GOLD_DEEP};
    font-size: 12px;
}}

#TinyLabel {{
    color: {FAINT};
    font-size: 10px;
}}

QTabWidget::pane {{
    border: none;
    background: transparent;
}}

QTabBar {{
    background: transparent;
}}

QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 7px 16px;
    margin-right: 4px;
    border-radius: 9px;
    font-size: 12px;
}}

QTabBar::tab:hover {{
    color: {GOLD_DEEP};
    background: rgba(243, 232, 207, 0.55);
}}

QTabBar::tab:selected {{
    color: {GOLD_DEEP};
    background: {GOLD_PALE};
    font-weight: 700;
}}

QTableWidget {{
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid {HAIRLINE};
    border-radius: 10px;
    gridline-color: transparent;
    selection-background-color: rgba(201, 162, 39, 0.25);
    selection-color: {INK};
    alternate-background-color: rgba(250, 247, 240, 0.75);
}}

QHeaderView::section {{
    background: transparent;
    color: {FAINT};
    border: none;
    border-bottom: 1px solid {HAIRLINE};
    padding: 8px;
    font-size: 11px;
    font-weight: 600;
}}

QTableWidget::item {{
    padding: 6px;
    border: none;
}}

QPlainTextEdit {{
    background: #fffefa;
    border: 1px solid {HAIRLINE};
    border-radius: 10px;
    color: {INK_2};
    selection-background-color: rgba(201, 162, 39, 0.3);
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI";
    font-size: 12px;
}}

QLineEdit {{
    background: rgba(255, 255, 255, 0.8);
    border: 1px solid {HAIRLINE};
    border-radius: 8px;
    padding: 5px 10px;
    color: {INK};
    selection-background-color: rgba(201, 162, 39, 0.3);
}}

QLineEdit:focus {{
    border: 1px solid {GOLD};
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: #ece6da;
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: {GOLD};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: #fffdf8;
    border: 2px solid {GOLD};
    width: 12px;
    margin: -6px 0;
    border-radius: 8px;
}}

QScrollArea {{
    border: none;
    background: transparent;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: rgba(138, 109, 59, 0.28);
    border-radius: 4px;
    min-height: 28px;
}}

QScrollBar::handle:vertical:hover {{
    background: rgba(138, 109, 59, 0.5);
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
}}

QScrollBar::handle:horizontal {{
    background: rgba(138, 109, 59, 0.28);
    border-radius: 4px;
}}

QToolTip {{
    background: #fffdf8;
    color: {INK};
    border: 1px solid {HAIRLINE};
    padding: 5px 8px;
    border-radius: 6px;
}}

QMenu {{
    background: #fffdf8;
    border: 1px solid {HAIRLINE};
    border-radius: 10px;
    padding: 6px;
}}

QMenu::item {{
    padding: 6px 18px;
    border-radius: 6px;
}}

QMenu::item:selected {{
    background: {GOLD_PALE};
    color: {GOLD_DEEP};
}}

QDialog {{
    background: {SURFACE};
}}

#DialogCard {{
    background: {SURFACE};
    border: 1px solid {HAIRLINE};
    border-radius: 14px;
}}

#DialogTitle {{
    color: {GOLD_DEEP};
    font-size: 16px;
    font-weight: 700;
}}

#DialogHint {{
    color: {FAINT};
    font-size: 11px;
}}

#DialogError {{
    color: {ERROR};
    font-size: 11px;
}}
"""
