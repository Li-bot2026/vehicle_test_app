"""
对话框尺寸辅助
==============
统一让对话框支持拖动改变大小、最大化，并显示右下角缩放手柄，
避免内容较多的页面在小屏或放大字体后无法调整。
"""

from PyQt6.QtCore import Qt


def allow_resize(dialog) -> None:
    """给对话框加上最大化/最小化按钮与右下角缩放手柄"""
    try:
        dialog.setSizeGripEnabled(True)
    except AttributeError:
        pass
    flags = dialog.windowFlags()
    dialog.setWindowFlags(
        flags
        | Qt.WindowType.WindowMaximizeButtonHint
        | Qt.WindowType.WindowMinimizeButtonHint
    )
