"""
偏好设置对话框
===============
编辑用户偏好：执行参数、日志级别、报告选项、默认连接参数。
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QTabWidget, QWidget,
    QCheckBox, QLineEdit, QDialogButtonBox, QMessageBox
)

from loguru import logger

from config.settings import ConfigManager
from .dialog_utils import allow_resize


class PreferencesDialog(QDialog):
    """
    偏好设置对话框
    ---------------
    分标签页编辑各类配置，保存到全局配置。
    """

    def __init__(self, config_mgr: ConfigManager, parent=None,
                 initial_tab: int = None):
        super().__init__(parent)
        self._cm = config_mgr
        self.setWindowTitle("偏好设置")
        self.resize(560, 560)
        allow_resize(self)
        self._init_ui()
        self._load_config()
        if initial_tab is not None:
            self._tabs.setCurrentIndex(initial_tab)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        self._tabs = tabs

        # --- 执行参数 ---
        exec_page = QWidget()
        exec_form = QFormLayout(exec_page)

        self._retry_spin = QSpinBox()
        self._retry_spin.setRange(0, 10)
        self._retry_spin.setToolTip("用例失败后自动重试的次数")
        exec_form.addRow("失败重试次数:", self._retry_spin)

        self._retry_delay = QDoubleSpinBox()
        self._retry_delay.setRange(0, 60)
        self._retry_delay.setDecimals(1)
        self._retry_delay.setSuffix(" 秒")
        exec_form.addRow("重试间隔:", self._retry_delay)

        self._step_timeout = QDoubleSpinBox()
        self._step_timeout.setRange(1, 600)
        self._step_timeout.setSuffix(" 秒")
        exec_form.addRow("单步超时:", self._step_timeout)

        self._batch_interval = QDoubleSpinBox()
        self._batch_interval.setRange(0, 10)
        self._batch_interval.setDecimals(2)
        self._batch_interval.setSuffix(" 秒")
        exec_form.addRow("批量执行间隔:", self._batch_interval)

        self._stop_on_fail = QCheckBox("失败后停止执行")
        exec_form.addRow("", self._stop_on_fail)

        self._parallel = QCheckBox("多设备并行执行")
        exec_form.addRow("", self._parallel)
        tabs.addTab(exec_page, "执行参数")

        # --- 日志 ---
        log_page = QWidget()
        log_form = QFormLayout(log_page)

        self._log_level = QComboBox()
        self._log_level.addItems(["DEBUG", "INFO", "WARN", "ERROR"])
        log_form.addRow("日志级别:", self._log_level)

        self._log_file = QCheckBox("写入日志文件")
        log_form.addRow("", self._log_file)

        self._audit = QCheckBox("记录审计日志（关键操作）")
        log_form.addRow("", self._audit)
        tabs.addTab(log_page, "日志")

        # --- 报告 ---
        report_page = QWidget()
        report_form = QFormLayout(report_page)

        self._report_dir = QLineEdit()
        report_form.addRow("报告输出目录:", self._report_dir)

        self._include_shot = QCheckBox("报告中包含截图")
        report_form.addRow("", self._include_shot)

        self._include_logs = QCheckBox("报告中包含日志")
        report_form.addRow("", self._include_logs)
        tabs.addTab(report_page, "报告")

        # --- 默认连接 ---
        conn_page = QWidget()
        conn_form = QFormLayout(conn_page)

        self._default_serial = QLineEdit()
        conn_form.addRow("默认串口:", self._default_serial)

        self._default_host = QLineEdit()
        conn_form.addRow("默认网络地址:", self._default_host)
        tabs.addTab(conn_page, "默认连接")

        # --- AI 助手 ---
        ai_page = QWidget()
        ai_form = QFormLayout(ai_page)

        self._ai_enabled = QCheckBox("启用 AI 助手（失败诊断 / 自动修复 / 用例生成）")
        ai_form.addRow("", self._ai_enabled)

        self._ai_base_url = QLineEdit()
        self._ai_base_url.setPlaceholderText("https://api.deepseek.com/v1")
        ai_form.addRow("接口地址:", self._ai_base_url)

        self._ai_api_key = QLineEdit()
        self._ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_api_key.setPlaceholderText("留空则使用内置规则引擎（离线可用）")
        ai_form.addRow("API Key:", self._ai_api_key)

        self._ai_model = QLineEdit()
        self._ai_model.setPlaceholderText("deepseek-chat")
        ai_form.addRow("模型名称:", self._ai_model)

        self._ai_timeout = QSpinBox()
        self._ai_timeout.setRange(5, 300)
        self._ai_timeout.setSuffix(" 秒")
        ai_form.addRow("请求超时:", self._ai_timeout)

        self._ai_auto_diagnose = QCheckBox("用例失败时自动弹出诊断（原因 + 解决方法）")
        ai_form.addRow("", self._ai_auto_diagnose)

        self._ai_auto_repair = QCheckBox("无需确认，直接自动修复并重跑")
        ai_form.addRow("", self._ai_auto_repair)

        self._ai_max_rounds = QSpinBox()
        self._ai_max_rounds.setRange(0, 5)
        self._ai_max_rounds.setToolTip("同一条用例最多自动修复的次数")
        ai_form.addRow("单用例最大修复次数:", self._ai_max_rounds)

        self._ai_offline = QCheckBox("AI 不可用时回退到内置规则引擎")
        ai_form.addRow("", self._ai_offline)

        self._ai_vision = QCheckBox("界面无控件文字时自动读屏（视觉模型 / 离线 OCR）")
        self._ai_vision.setToolTip(
            "默认自动：能识别到多模态模型就用模型读屏，否则用离线 OCR。\n"
            "勾选表示强制尝试视觉模型（即使模型名识别不出来），失败后自动降级为 OCR。"
        )
        ai_form.addRow("", self._ai_vision)

        self._ai_vision_model = QLineEdit()
        self._ai_vision_model.setPlaceholderText("自动识别，可填 qwen-vl-max / glm-4v-plus / gpt-4o")
        self._ai_vision_model.setToolTip(
            "留空时自动推导：主模型是多模态模型就用主模型，"
            "否则按接口服务商推断默认视觉模型。"
        )
        ai_form.addRow("视觉模型:", self._ai_vision_model)

        self._ai_vision_base = QLineEdit()
        self._ai_vision_base.setPlaceholderText("留空沿用上方接口地址")
        ai_form.addRow("视觉接口:", self._ai_vision_base)

        self._ai_vision_key = QLineEdit()
        self._ai_vision_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_vision_key.setPlaceholderText("留空沿用上方 API Key")
        ai_form.addRow("视觉 Key:", self._ai_vision_key)

        self._ai_vision_hint = QLabel("")
        self._ai_vision_hint.setWordWrap(True)
        self._ai_vision_hint.setStyleSheet("color: #555;")
        ai_form.addRow("", self._ai_vision_hint)

        # 免费视觉模型引导
        free_vision_group = QGroupBox("免费视觉模型（无需配置，注册即用）")
        free_vision_layout = QVBoxLayout(free_vision_group)
        free_vision_layout.setSpacing(6)

        self._free_vision_label = QLabel(
            "以下平台提供新手免费额度，注册后填入 API Key 即可自动玩扫雷/五子棋等游戏："
        )
        self._free_vision_label.setWordWrap(True)
        self._free_vision_label.setStyleSheet("color: #666; font-size: 11px;")
        free_vision_layout.addWidget(self._free_vision_label)

        free_providers = [
            ("阿里云百炼", "https://bailian.console.aliyun.com/", "新用户送 100 万 token，支持 qwen-vl-max", "qwen-vl-max", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            ("智谱 AI", "https://open.bigmodel.cn/", "新用户送 50 万 token，支持 glm-4v-plus", "glm-4v-plus", "https://open.bigmodel.cn/api/paas/v4"),
            ("Moonshot", "https://platform.moonshot.cn/", "新用户送 15 万 token，支持 moonshot-v1-8k-vision-preview", "moonshot-v1-8k-vision-preview", "https://api.moonshot.cn/v1"),
        ]

        self._free_vision_buttons = []
        for name, url, desc, model, base_url in free_providers:
            row = QHBoxLayout()
            btn = QPushButton(f"🔗 {name}")
            btn.setToolTip(f"{desc}\n点击打开注册页面")
            btn.clicked.connect(lambda checked, u=url: self._open_url(u))
            btn.setStyleSheet("QPushButton { text-align: left; padding: 4px 8px; }")
            row.addWidget(btn, 0)
            label = QLabel(desc)
            label.setWordWrap(True)
            label.setStyleSheet("color: #555; font-size: 10px;")
            row.addWidget(label, 1)
            use_btn = QPushButton("使用此模型")
            use_btn.setToolTip(f"自动填入 {model} 和接口地址")
            use_btn.clicked.connect(lambda checked, m=model, b=base_url: self._use_free_vision(m, b))
            row.addWidget(use_btn, 0)
            free_vision_layout.addLayout(row)
            self._free_vision_buttons.append(btn)

        ai_form.addRow("", free_vision_group)

        test_row = QHBoxLayout()
        self._btn_test_ai = QPushButton("🔗 测试连接")
        self._btn_test_ai.clicked.connect(self._on_test_ai)
        test_row.addWidget(self._btn_test_ai)
        self._ai_test_label = QLabel("")
        self._ai_test_label.setStyleSheet("color: #555;")
        test_row.addWidget(self._ai_test_label, 1)
        ai_form.addRow("", test_row)

        # 表单任何变化都重新推导视觉能力提示
        for widget, signal in (
            (self._ai_base_url, "textChanged"),
            (self._ai_api_key, "textChanged"),
            (self._ai_model, "textChanged"),
            (self._ai_vision_model, "textChanged"),
            (self._ai_vision_base, "textChanged"),
            (self._ai_vision_key, "textChanged"),
        ):
            getattr(widget, signal).connect(self._refresh_vision_hint)
        self._ai_vision.toggled.connect(self._refresh_vision_hint)

        tabs.addTab(ai_page, "AI 助手")

        layout.addWidget(tabs)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(
            self._on_save
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).clicked.connect(
            self.reject
        )
        layout.addWidget(buttons)

    def _load_config(self):
        """从配置加载到表单"""
        cfg = self._cm.config
        exec_cfg = cfg.execution
        self._retry_spin.setValue(exec_cfg.retry_count)
        self._retry_delay.setValue(exec_cfg.retry_delay)
        self._step_timeout.setValue(exec_cfg.step_timeout)
        self._batch_interval.setValue(exec_cfg.batch_interval)
        self._stop_on_fail.setChecked(exec_cfg.stop_on_failure)
        self._parallel.setChecked(exec_cfg.parallel_devices)

        log_cfg = cfg.log
        idx = self._log_level.findText(log_cfg.level)
        if idx >= 0:
            self._log_level.setCurrentIndex(idx)
        self._log_file.setChecked(log_cfg.log_to_file)
        self._audit.setChecked(log_cfg.audit_log)

        report_cfg = cfg.report
        self._report_dir.setText(report_cfg.output_dir)
        self._include_shot.setChecked(report_cfg.include_screenshots)
        self._include_logs.setChecked(report_cfg.include_logs)

        self._default_serial.setText(cfg.serial.port)
        self._default_host.setText(cfg.network.host)

        ai_cfg = cfg.ai
        self._ai_enabled.setChecked(ai_cfg.enabled)
        self._ai_base_url.setText(ai_cfg.base_url)
        self._ai_api_key.setText(ai_cfg.api_key)
        self._ai_model.setText(ai_cfg.model)
        self._ai_timeout.setValue(int(ai_cfg.timeout))
        self._ai_auto_diagnose.setChecked(ai_cfg.auto_diagnose)
        self._ai_auto_repair.setChecked(ai_cfg.auto_repair)
        self._ai_max_rounds.setValue(ai_cfg.max_fix_rounds)
        self._ai_offline.setChecked(ai_cfg.offline_fallback)
        self._ai_vision.setChecked(bool(getattr(ai_cfg, "vision", False)))
        self._ai_vision_model.setText(str(getattr(ai_cfg, "vision_model", "") or ""))
        self._ai_vision_base.setText(str(getattr(ai_cfg, "vision_base_url", "") or ""))
        self._ai_vision_key.setText(str(getattr(ai_cfg, "vision_api_key", "") or ""))
        self._refresh_vision_hint()

    def _on_save(self):
        """保存表单到配置"""
        cfg = self._cm.config

        cfg.execution.retry_count = self._retry_spin.value()
        cfg.execution.retry_delay = self._retry_delay.value()
        cfg.execution.step_timeout = self._step_timeout.value()
        cfg.execution.batch_interval = self._batch_interval.value()
        cfg.execution.stop_on_failure = self._stop_on_fail.isChecked()
        cfg.execution.parallel_devices = self._parallel.isChecked()

        cfg.log.level = self._log_level.currentText()
        cfg.log.log_to_file = self._log_file.isChecked()
        cfg.log.audit_log = self._audit.isChecked()

        cfg.report.output_dir = self._report_dir.text().strip() or "reports"
        cfg.report.include_screenshots = self._include_shot.isChecked()
        cfg.report.include_logs = self._include_logs.isChecked()

        cfg.serial.port = self._default_serial.text().strip()
        cfg.network.host = self._default_host.text().strip()

        ai_cfg = cfg.ai
        ai_cfg.enabled = self._ai_enabled.isChecked()
        ai_cfg.base_url = self._ai_base_url.text().strip() or "https://api.deepseek.com/v1"
        ai_cfg.api_key = self._ai_api_key.text().strip()
        ai_cfg.model = self._ai_model.text().strip() or "deepseek-chat"
        ai_cfg.timeout = float(self._ai_timeout.value())
        ai_cfg.auto_repair = self._ai_auto_repair.isChecked()
        # 自动修复依赖自动诊断，否则失败时不会触发任何 AI 流程
        ai_cfg.auto_diagnose = (
            self._ai_auto_diagnose.isChecked() or ai_cfg.auto_repair
        )
        ai_cfg.max_fix_rounds = self._ai_max_rounds.value()
        ai_cfg.offline_fallback = self._ai_offline.isChecked()
        ai_cfg.vision = self._ai_vision.isChecked()
        ai_cfg.vision_model = self._ai_vision_model.text().strip()
        ai_cfg.vision_base_url = self._ai_vision_base.text().strip()
        ai_cfg.vision_api_key = self._ai_vision_key.text().strip()

        self._cm.save()
        logger.info("偏好设置已保存")
        self.accept()

    def _refresh_vision_hint(self):
        """按当前表单内容显示执行时会自动采用哪种读屏能力"""
        from core.ai.client import AIClient
        from config.settings import AIConfig

        cfg = AIConfig(
            enabled=True,
            base_url=self._ai_base_url.text().strip() or "https://api.deepseek.com/v1",
            api_key=self._ai_api_key.text().strip(),
            model=self._ai_model.text().strip() or "deepseek-chat",
            vision=self._ai_vision.isChecked(),
            vision_model=self._ai_vision_model.text().strip(),
            vision_base_url=self._ai_vision_base.text().strip(),
            vision_api_key=self._ai_vision_key.text().strip(),
        )
        client = AIClient(cfg)

        if not client.configured:
            self._ai_vision_hint.setText(
                "当前没有 API Key：无需任何配置，执行/生成时会自动使用离线 OCR 定位画布文字。"
            )
            self._ai_vision_hint.setStyleSheet("color: #2e7d32;")
            return

        model = client.vision_model_name()
        if not model:
            self._ai_vision_hint.setText(
                "未识别到可用的视觉模型：将自动使用离线 OCR；"
                "如需视觉读屏，请在「视觉模型」填写多模态模型（如 qwen-vl-max）。"
            )
            self._ai_vision_hint.setStyleSheet("color: #8a6d3b;")
            return

        forced = "（已强制启用）" if self._ai_vision.isChecked() else "（自动识别）"
        self._ai_vision_hint.setText(
            f"{forced}执行/生成遇到画布界面时会自动调用视觉读屏：{model}；"
            "模型不支持图像时自动降级为离线 OCR。"
        )
        self._ai_vision_hint.setStyleSheet("color: #2e7d32;")

    def _open_url(self, url: str):
        """在浏览器中打开 URL"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    def _use_free_vision(self, model: str, base_url: str):
        """使用免费视觉模型：自动填入模型名和接口地址"""
        self._ai_vision_model.setText(model)
        self._ai_vision_base.setText(base_url)
        self._ai_vision.setChecked(True)
        self._ai_vision_hint.setText(
            f"已选择 {model}，请在上方「视觉 Key」填入注册后获得的 API Key"
        )
        self._ai_vision_hint.setStyleSheet("color: #2e7d32;")

    def _on_test_ai(self):
        """用当前表单内容测试 AI 接口连通性"""
        from core.ai.client import AIClient
        from ui.widgets.ai_worker import AIWorker
        from config.settings import AIConfig

        api_key = self._ai_api_key.text().strip()
        if not api_key:
            self._ai_test_label.setText("请先填写 API Key（留空时使用内置规则引擎）")
            self._ai_test_label.setStyleSheet("color: #8a6d3b;")
            return

        cfg = AIConfig(
            enabled=True,
            base_url=self._ai_base_url.text().strip() or "https://api.deepseek.com/v1",
            api_key=api_key,
            model=self._ai_model.text().strip() or "deepseek-chat",
            timeout=float(self._ai_timeout.value()),
            vision=self._ai_vision.isChecked(),
            vision_model=self._ai_vision_model.text().strip(),
        )
        client = AIClient(cfg)

        self._btn_test_ai.setEnabled(False)
        self._ai_test_label.setText("正在连接 AI 接口...")
        self._ai_test_label.setStyleSheet("color: #8a6d3b;")

        worker = AIWorker(
            lambda: client.chat("你是连接测试助手。", "请只回复 OK", temperature=0, timeout=20)
        )
        worker.succeeded.connect(
            lambda text: self._on_test_ai_done(f"✅ 连接成功：{str(text).strip()[:40]}")
        )
        worker.failed.connect(lambda err: self._on_test_ai_done(f"❌ 连接失败：{err}"))
        worker.start()

    def _on_test_ai_done(self, message: str):
        self._btn_test_ai.setEnabled(True)
        self._ai_test_label.setText(message)
        color = "#2e7d32" if message.startswith("✅") else "#c0392b"
        self._ai_test_label.setStyleSheet(f"color: {color};")
