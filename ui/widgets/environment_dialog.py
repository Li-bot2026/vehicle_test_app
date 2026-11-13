"""
环境切换对话框
===============
切换测试环境（开发 / 测试 / 预生产），管理环境配置。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem,
    QGroupBox, QFormLayout, QLineEdit, QMessageBox,
    QDialogButtonBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from loguru import logger

from config.settings import ConfigManager
from .dialog_utils import allow_resize


class EnvironmentDialog(QDialog):
    """
    环境切换对话框
    ---------------
    展示可用测试环境，支持切换和查看详情。
    """

    def __init__(self, config_mgr: ConfigManager, parent=None):
        super().__init__(parent)
        self._config_mgr = config_mgr
        self.setWindowTitle("测试环境管理")
        self.resize(600, 450)
        allow_resize(self)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("当前环境: " + self._config_mgr.config.environment.name)
        title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        # 环境列表
        self._env_list = QListWidget()
        self._env_list.itemSelectionChanged.connect(self._on_env_selected)
        layout.addWidget(self._env_list)

        # 环境详情
        detail_group = QGroupBox("环境详情")
        self._detail_form = QFormLayout(detail_group)

        self._name_input = QLineEdit()
        self._detail_form.addRow("名称:", self._name_input)

        self._desc_input = QLineEdit()
        self._detail_form.addRow("描述:", self._desc_input)

        self._net_input = QLineEdit()
        self._detail_form.addRow("网络配置:", self._net_input)
        layout.addWidget(detail_group)

        # 按钮
        btn_row = QHBoxLayout()
        self._btn_switch = QPushButton("✅ 切换到该环境")
        self._btn_switch.clicked.connect(self._on_switch)
        btn_row.addWidget(self._btn_switch)
        btn_row.addStretch()

        self._btn_close = QPushButton("关闭")
        self._btn_close.clicked.connect(self.close)
        btn_row.addWidget(self._btn_close)
        layout.addLayout(btn_row)

        self._load_environments()

    def _load_environments(self):
        """加载环境列表"""
        self._env_list.clear()
        presets = self._config_mgr.export_presets()
        for env_name in presets.keys():
            item = QListWidgetItem(f"🌍 {env_name}")
            item.setData(Qt.ItemDataRole.UserRole, env_name)
            if env_name == self._config_mgr.config.environment.name:
                item.setSelected(True)
            self._env_list.addItem(item)

    def _on_env_selected(self):
        """选中环境时显示详情"""
        items = self._env_list.selectedItems()
        if not items:
            return
        env_name = items[0].data(Qt.ItemDataRole.UserRole)
        presets = self._config_mgr.export_presets()
        preset = presets.get(env_name, {})

        self._name_input.setText(env_name)
        exec_cfg = preset.get("execution", {})
        self._desc_input.setText(
            f"重试 {exec_cfg.get('retry_count', '-')} 次 / "
            f"单步超时 {exec_cfg.get('step_timeout', '-')}s / "
            f"日志 {preset.get('log', {}).get('level', '-')}"
        )
        net_cfg = preset.get("network", {})
        self._net_input.setText(
            f"{net_cfg.get('host', '-')}:{net_cfg.get('port', '-')}"
        )

    def _on_switch(self):
        """切换环境"""
        items = self._env_list.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "请选择要切换的环境")
            return
        env_name = items[0].data(Qt.ItemDataRole.UserRole)
        self._config_mgr.switch_environment(env_name)
        self._config_mgr.save()
        QMessageBox.information(self, "切换成功", f"已切换到环境: {env_name}")
        self.accept()
