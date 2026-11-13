"""
全局主题
=========
集中式设计令牌 + QSS 样式表，替代散落的 inline setStyleSheet。
调用 ``apply_theme(app)`` 即可一键换肤。
"""

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QPalette, QColor
from PyQt6.QtCore import Qt


# ═══════════════════════════════════════════════════
#  设计令牌 (Design Tokens)
# ═══════════════════════════════════════════════════

class Tokens:
    """颜色 / 尺寸 / 字体常量"""

    # ---- 主色 ----
    PRIMARY       = "#3b82f6"
    PRIMARY_HOVER = "#2563eb"
    PRIMARY_PRESS = "#1d4ed8"
    PRIMARY_LIGHT = "#eff6ff"

    # ---- 语义色 ----
    SUCCESS       = "#22c55e"
    SUCCESS_LIGHT = "#f0fdf4"
    WARNING       = "#f59e0b"
    WARNING_LIGHT = "#fffbeb"
    DANGER        = "#ef4444"
    DANGER_LIGHT  = "#fef2f2"
    INFO          = "#3b82f6"
    INFO_LIGHT    = "#eff6ff"

    # ---- 中性色 ----
    BG            = "#f1f5f9"   # 主背景
    SURFACE       = "#ffffff"   # 卡片/面板
    BORDER        = "#e2e8f0"   # 边框
    BORDER_LIGHT  = "#f1f5f9"
    DIVIDER       = "#e2e8f0"

    # ---- 文字 ----
    TEXT_PRIMARY   = "#0f172a"  # 标题
    TEXT_SECONDARY = "#475569"  # 正文
    TEXT_TERTIARY  = "#94a3b8"  # 辅助/占位
    TEXT_DISABLED  = "#cbd5e1"

    # ---- 圆角 ----
    RADIUS_SM = "4px"
    RADIUS_MD = "8px"
    RADIUS_LG = "12px"

    # ---- 间距 ----
    PAD_SM = "6px"
    PAD_MD = "12px"
    PAD_LG = "16px"

    # ---- 阴影 (用 border 模拟) ----
    SHADOW = "0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06)"

    # ---- 字体 ----
    FONT_FAMILY = "Microsoft YaHei"
    FONT_SIZE   = 10      # pt，正文
    FONT_SIZE_SM = 9      # pt，辅助
    FONT_SIZE_LG = 12     # pt，标题


# ═══════════════════════════════════════════════════
#  全局 QSS 样式表
# ═══════════════════════════════════════════════════

def _build_qss() -> str:
    T = Tokens
    return f"""
    /* ============================================
       全局基础
       ============================================ */

    * {{
        font-family: "{T.FONT_FAMILY}", "Segoe UI", sans-serif;
    }}

    QMainWindow {{
        background-color: {T.BG};
    }}

    QMainWindow > QWidget {{
        background-color: {T.BG};
    }}

    QWidget {{
        color: {T.TEXT_PRIMARY};
        font-size: {T.FONT_SIZE}pt;
    }}

    /* 容器类控件设置背景 */
    QFrame, QGroupBox, QTabWidget::pane {{
        background-color: {T.BG};
    }}

    /* QLabel 继承 QFrame，需显式透明，避免被上面的背景规则刷成灰底块 */
    QLabel {{
        background-color: transparent;
    }}

    /* ============================================
       菜单栏
       ============================================ */

    QMenuBar {{
        background-color: {T.SURFACE};
        color: {T.TEXT_PRIMARY};
        border-bottom: 1px solid {T.BORDER};
        padding: 2px 0px;
        font-size: {T.FONT_SIZE}pt;
    }}

    QMenuBar::item {{
        padding: 6px 12px;
        background: transparent;
        border-radius: {T.RADIUS_SM};
        margin: 2px 1px;
    }}

    QMenuBar::item:selected {{
        background-color: {T.PRIMARY_LIGHT};
        color: {T.PRIMARY};
    }}

    QMenu {{
        background-color: {T.SURFACE};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_MD};
        padding: 4px 0px;
    }}

    QMenu::item {{
        padding: 8px 32px 8px 16px;
        margin: 1px 4px;
        border-radius: {T.RADIUS_SM};
    }}

    QMenu::item:selected {{
        background-color: {T.PRIMARY_LIGHT};
        color: {T.PRIMARY};
    }}

    QMenu::separator {{
        height: 1px;
        background: {T.BORDER};
        margin: 4px 12px;
    }}

    /* ============================================
       工具栏
       ============================================ */

    QToolBar {{
        background-color: {T.SURFACE};
        border-bottom: 1px solid {T.BORDER};
        padding: 4px 8px;
        spacing: 6px;
    }}

    QToolBar::separator {{
        width: 1px;
        background: {T.BORDER};
        margin: 6px 4px;
    }}

    QToolBar QToolButton {{
        background-color: transparent;
        color: {T.TEXT_SECONDARY};
        border: 1px solid transparent;
        border-radius: {T.RADIUS_SM};
        padding: 6px 12px;
        font-size: {T.FONT_SIZE}pt;
    }}

    QToolBar QToolButton:hover {{
        background-color: {T.PRIMARY_LIGHT};
        color: {T.PRIMARY};
        border-color: {T.PRIMARY}40;
    }}

    QToolBar QToolButton:pressed {{
        background-color: {T.PRIMARY}20;
    }}

    /* ============================================
       标签页
       ============================================ */

    QTabWidget::pane {{
        background-color: {T.SURFACE};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_MD};
        top: -1px;
    }}

    QTabBar::tab {{
        background-color: transparent;
        color: {T.TEXT_SECONDARY};
        border: none;
        padding: 10px 20px;
        margin-right: 2px;
        border-bottom: 2px solid transparent;
        font-size: {T.FONT_SIZE}pt;
    }}

    QTabBar::tab:hover {{
        color: {T.PRIMARY};
        background-color: {T.PRIMARY_LIGHT};
        border-radius: {T.RADIUS_SM} {T.RADIUS_SM} 0 0;
    }}

    QTabBar::tab:selected {{
        color: {T.PRIMARY};
        font-weight: bold;
        border-bottom: 2px solid {T.PRIMARY};
    }}

    /* ============================================
       分组框 (卡片风格)
       ============================================ */

    QGroupBox {{
        background-color: {T.SURFACE};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_MD};
        margin-top: 12px;
        padding: {T.PAD_LG};
        padding-top: {T.PAD_LG};
        font-weight: bold;
        font-size: {T.FONT_SIZE}pt;
        color: {T.TEXT_PRIMARY};
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: {T.PAD_MD};
        top: 0px;
        padding: 0px {T.PAD_SM};
        background-color: {T.SURFACE};
        color: {T.TEXT_PRIMARY};
        font-size: {T.FONT_SIZE}pt;
    }}

    /* ============================================
       按钮
       ============================================ */

    QPushButton {{
        background-color: {T.SURFACE};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_SM};
        padding: 8px 20px;
        font-size: {T.FONT_SIZE}pt;
        min-height: 18px;
    }}

    QPushButton:hover {{
        background-color: {T.BG};
        border-color: {T.PRIMARY}60;
        color: {T.PRIMARY};
    }}

    QPushButton:pressed {{
        background-color: {T.BORDER};
    }}

    QPushButton:disabled {{
        background-color: {T.BG};
        color: {T.TEXT_DISABLED};
        border-color: {T.BORDER_LIGHT};
    }}

    QPushButton:default {{
        background-color: {T.PRIMARY};
        color: white;
        border: 1px solid {T.PRIMARY};
        font-weight: bold;
    }}

    QPushButton:default:hover {{
        background-color: {T.PRIMARY_HOVER};
        border-color: {T.PRIMARY_HOVER};
        color: white;
    }}

    QPushButton:default:pressed {{
        background-color: {T.PRIMARY_PRESS};
    }}

    QPushButton:default:disabled {{
        background-color: {T.PRIMARY}60;
        color: {T.TEXT_DISABLED};
    }}

    /* ============================================
       输入框
       ============================================ */

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background-color: {T.SURFACE};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_SM};
        padding: 6px 10px;
        font-size: {T.FONT_SIZE}pt;
        min-height: 20px;
        selection-background-color: {T.PRIMARY}30;
        selection-color: {T.TEXT_PRIMARY};
    }}

    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {T.PRIMARY};
    }}

    QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
        border-color: {T.PRIMARY}80;
    }}

    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
        background-color: {T.BG};
        color: {T.TEXT_TERTIARY};
    }}

    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}

    QComboBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {T.TEXT_TERTIARY};
        margin-right: 6px;
    }}

    QComboBox::down-arrow:hover {{
        border-top-color: {T.PRIMARY};
    }}

    QComboBox QAbstractItemView {{
        background-color: {T.SURFACE};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_SM};
        selection-background-color: {T.PRIMARY_LIGHT};
        selection-color: {T.PRIMARY};
        outline: none;
        padding: 4px;
    }}

    QComboBox QAbstractItemView::item {{
        padding: 6px 8px;
        min-height: 24px;
    }}

    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 20px;
        border-left: 1px solid {T.BORDER};
        border-bottom: 1px solid {T.BORDER};
        border-top-right-radius: {T.RADIUS_SM};
        background: {T.BG};
    }}

    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 20px;
        border-left: 1px solid {T.BORDER};
        border-top: 1px solid {T.BORDER};
        border-bottom-right-radius: {T.RADIUS_SM};
        background: {T.BG};
    }}

    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: none;
        border-left: 3px solid transparent;
        border-right: 3px solid transparent;
        border-bottom: 4px solid {T.TEXT_TERTIARY};
    }}

    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: none;
        border-left: 3px solid transparent;
        border-right: 3px solid transparent;
        border-top: 4px solid {T.TEXT_TERTIARY};
    }}

    /* ============================================
       复选框 / 单选框
       ============================================ */

    QCheckBox, QRadioButton {{
        spacing: 8px;
        color: {T.TEXT_PRIMARY};
    }}

    QCheckBox::indicator, QRadioButton::indicator {{
        width: 18px;
        height: 18px;
    }}

    QCheckBox::indicator {{
        border: 2px solid {T.BORDER};
        border-radius: 4px;
        background-color: {T.SURFACE};
    }}

    QCheckBox::indicator:hover {{
        border-color: {T.PRIMARY};
    }}

    QCheckBox::indicator:checked {{
        background-color: {T.PRIMARY};
        border-color: {T.PRIMARY};
        image: none;
    }}

    QRadioButton::indicator {{
        border: 2px solid {T.BORDER};
        border-radius: 11px;
        background-color: {T.SURFACE};
    }}

    QRadioButton::indicator:hover {{
        border-color: {T.PRIMARY};
    }}

    QRadioButton::indicator:checked {{
        background-color: {T.PRIMARY};
        border-color: {T.PRIMARY};
    }}

    /* ============================================
       滑块
       ============================================ */

    QSlider::groove:horizontal {{
        height: 6px;
        background: {T.BORDER};
        border-radius: 3px;
    }}

    QSlider::handle:horizontal {{
        background: {T.PRIMARY};
        width: 18px;
        height: 18px;
        margin: -6px 0;
        border-radius: 9px;
        border: 2px solid {T.SURFACE};
    }}

    QSlider::handle:horizontal:hover {{
        background: {T.PRIMARY_HOVER};
        border-color: {T.PRIMARY_LIGHT};
    }}

    QSlider::sub-page:horizontal {{
        background: {T.PRIMARY};
        border-radius: 3px;
    }}

    /* ============================================
       表格
       ============================================ */

    QTableWidget, QTableView {{
        background-color: {T.SURFACE};
        alternate-background-color: {T.BG};
        color: {T.TEXT_PRIMARY};
        gridline-color: {T.BORDER};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_MD};
        selection-background-color: {T.PRIMARY_LIGHT};
        selection-color: {T.PRIMARY};
        outline: none;
        font-size: {T.FONT_SIZE}pt;
    }}

    QTableWidget::item, QTableView::item {{
        padding: 6px 8px;
        border-bottom: 1px solid {T.BORDER};
    }}

    QTableWidget::item:selected, QTableView::item:selected {{
        background-color: {T.PRIMARY_LIGHT};
        color: {T.PRIMARY};
    }}

    QHeaderView::section {{
        background-color: {T.BG};
        color: {T.TEXT_SECONDARY};
        padding: 8px 8px;
        border: none;
        border-bottom: 2px solid {T.BORDER};
        border-right: 1px solid {T.BORDER};
        font-weight: bold;
        font-size: {T.FONT_SIZE_SM}pt;
    }}

    QHeaderView::section:last {{
        border-right: none;
    }}

    /* ============================================
       树形控件
       ============================================ */

    QTreeWidget, QTreeView {{
        background-color: {T.SURFACE};
        alternate-background-color: {T.BG};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_MD};
        selection-background-color: {T.PRIMARY_LIGHT};
        selection-color: {T.PRIMARY};
        outline: none;
        font-size: {T.FONT_SIZE}pt;
    }}

    QTreeWidget::item, QTreeView::item {{
        padding: 4px 8px;
        border-radius: {T.RADIUS_SM};
        margin: 1px 4px;
    }}

    QTreeWidget::item:selected, QTreeView::item:selected {{
        background-color: {T.PRIMARY_LIGHT};
        color: {T.PRIMARY};
    }}

    QTreeWidget::item:hover, QTreeView::item:hover {{
        background-color: {T.BG};
    }}

    /* ============================================
       列表
       ============================================ */

    QListWidget, QListView {{
        background-color: {T.SURFACE};
        alternate-background-color: {T.BG};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_MD};
        selection-background-color: {T.PRIMARY_LIGHT};
        selection-color: {T.PRIMARY};
        outline: none;
        padding: 4px;
        font-size: {T.FONT_SIZE}pt;
    }}

    QListWidget::item, QListView::item {{
        padding: 6px 8px;
        border-radius: {T.RADIUS_SM};
        margin: 1px 0px;
    }}

    QListWidget::item:selected, QListView::item:selected {{
        background-color: {T.PRIMARY_LIGHT};
        color: {T.PRIMARY};
    }}

    QListWidget::item:hover, QListView::item:hover {{
        background-color: {T.BG};
    }}

    /* ============================================
       文本编辑 / 浏览器
       ============================================ */

    QTextEdit, QPlainTextEdit, QTextBrowser {{
        background-color: {T.SURFACE};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_MD};
        padding: 8px;
        font-size: {T.FONT_SIZE}pt;
        selection-background-color: {T.PRIMARY}30;
        selection-color: {T.TEXT_PRIMARY};
    }}

    QTextEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus {{
        border-color: {T.PRIMARY};
    }}

    /* ============================================
       进度条
       ============================================ */

    QProgressBar {{
        background-color: {T.BORDER};
        border: none;
        border-radius: {T.RADIUS_SM};
        text-align: center;
        height: 22px;
        font-size: {T.FONT_SIZE_SM}pt;
        color: {T.TEXT_SECONDARY};
        font-weight: bold;
    }}

    QProgressBar::chunk {{
        background-color: {T.PRIMARY};
        border-radius: {T.RADIUS_SM};
    }}

    /* ============================================
       滚动条
       ============================================ */

    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 4px 2px;
        border-radius: 5px;
    }}

    QScrollBar::handle:vertical {{
        background: {T.TEXT_TERTIARY}60;
        min-height: 30px;
        border-radius: 5px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: {T.TEXT_TERTIARY};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}

    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px 4px;
        border-radius: 5px;
    }}

    QScrollBar::handle:horizontal {{
        background: {T.TEXT_TERTIARY}60;
        min-width: 30px;
        border-radius: 5px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background: {T.TEXT_TERTIARY};
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}

    /* ============================================
       分割器
       ============================================ */

    QSplitter::handle {{
        background-color: {T.BORDER};
    }}

    QSplitter::handle:horizontal {{
        width: 1px;
    }}

    QSplitter::handle:vertical {{
        height: 1px;
    }}

    QSplitter::handle:hover {{
        background-color: {T.PRIMARY};
    }}

    /* ============================================
       状态栏
       ============================================ */

    QStatusBar {{
        background-color: {T.SURFACE};
        border-top: 1px solid {T.BORDER};
        color: {T.TEXT_SECONDARY};
        font-size: {T.FONT_SIZE_SM}pt;
        padding: 2px 8px;
    }}

    QStatusBar::item {{
        border: none;
    }}

    QStatusBar QLabel {{
        color: {T.TEXT_SECONDARY};
        padding: 2px 8px;
        font-size: {T.FONT_SIZE_SM}pt;
        background: transparent;
    }}

    /* ============================================
       工具提示
       ============================================ */

    QToolTip {{
        background-color: {T.SURFACE};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_SM};
        padding: 6px 10px;
        font-size: {T.FONT_SIZE_SM}pt;
    }}

    /* ============================================
       对话框按钮区
       ============================================ */

    QDialogButtonBox QPushButton {{
        min-width: 80px;
    }}

    /* ============================================
       表单标签
       ============================================ */

    QFormLayout {{
        spacing: 8px;
    }}

    QFormLayout QLabel {{
        color: {T.TEXT_SECONDARY};
        padding: 2px 0px;
    }}

    /* ============================================
       选项卡内部面板去掉多余背景
       ============================================ */

    QTabWidget QTabWidget::pane {{
        border: none;
        border-top: 1px solid {T.BORDER};
    }}
    """


# ═══════════════════════════════════════════════════
#  调色板 (供 Qt 原生控件回退)
# ═══════════════════════════════════════════════════

def _build_palette() -> QPalette:
    T = Tokens
    pal = QPalette()

    pal.setColor(QPalette.ColorRole.Window,          QColor(T.BG))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(T.TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Base,            QColor(T.SURFACE))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(T.BG))
    pal.setColor(QPalette.ColorRole.Text,            QColor(T.TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Button,          QColor(T.SURFACE))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(T.TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(T.PRIMARY))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Link,            QColor(T.PRIMARY))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(T.SURFACE))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(T.TEXT_PRIMARY))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(T.TEXT_TERTIARY))

    # Disabled
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(T.TEXT_DISABLED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(T.TEXT_DISABLED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(T.TEXT_DISABLED))

    return pal


# ═══════════════════════════════════════════════════
#  公共 API
# ═══════════════════════════════════════════════════

def apply_theme(app: QApplication):
    """一键应用全局主题（在 app 创建后、窗口显示前调用）"""
    app.setStyle("Fusion")
    app.setPalette(_build_palette())
    app.setStyleSheet(_build_qss())

    # 全局字体
    font = QFont(Tokens.FONT_FAMILY)
    font.setPointSize(Tokens.FONT_SIZE)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
