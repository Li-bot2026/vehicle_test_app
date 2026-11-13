"""
主窗口
=======
应用程序的主界面，集成所有功能面板。
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QStatusBar, QMenuBar, QMenu,
    QToolBar, QLabel, QSplitter, QMessageBox,
    QFileDialog, QApplication
)
from PyQt6.QtCore import Qt, QTimer, QSize, QSettings
from PyQt6.QtGui import QAction, QIcon, QFont, QColor

from loguru import logger

from config.settings import ConfigManager
from core.device.manager import DeviceManager
from core.device.adb_device import ADBDevice
from core.testcase.database import TestDatabase
from core.execution.engine import ExecutionEngine, ExecutionState
from core.execution.runner import TestRunner
from core.reporting.generator import ReportGenerator
from core.ai.assistant import AIAssistant

from .panels.connection_panel import ConnectionPanel
from .panels.test_panel import TestPanel
from .panels.report_panel import ReportPanel
from .panels.knowledge_panel import KnowledgePanel
from .panels.can_monitor_panel import CANMonitorPanel
from .panels.monkey_panel import MonkeyPanel
from .widgets.execution_view import ExecutionView


class MainWindow(QMainWindow):
    """
    主窗口
    -------
    布局：
    - 顶部：菜单栏 + 工具栏
    - 左侧：设备连接面板
    - 中间：标签页（测试用例 / 执行画面 / 报告）
    - 底部：状态栏
    """

    APP_TITLE = "车机测试应用程序 v1.0"
    APP_SIZE = (1400, 900)

    def __init__(self):
        super().__init__()

        # 初始化核心组件
        self._config_mgr = ConfigManager()
        self._config_mgr.load()
        self._device_mgr = DeviceManager()
        self._db = TestDatabase(str(Path.home() / ".vehicle_test_app" / "test_results.db"))
        self._exec_engine = ExecutionEngine(self._device_mgr)
        self._runner = TestRunner(self._exec_engine, self._device_mgr)
        self._report_gen = ReportGenerator()
        self._adb_screen_dlg = None

        # AI 助手状态
        self._ai = AIAssistant(self._config_mgr.config.ai)
        self._runner.set_assistant(self._ai)
        self._ai_busy = False
        self._ai_paused = False
        self._fix_rounds = {}
        self._rerun_queue = []

        # 初始化 UI
        self._init_ui()
        self._init_menu()
        self._init_toolbar()
        self._init_statusbar()
        self._connect_signals()

        # 恢复窗口几何状态
        self._restore_geometry()

        # 定时刷新
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh)
        self._refresh_timer.start(1000)

        logger.info("主窗口初始化完成")

    def _restore_geometry(self):
        """恢复窗口大小和位置"""
        settings = QSettings("VehicleTestApp", "MainWindow")
        geo = settings.value("geometry")
        if geo and isinstance(geo, bytes):
            self.restoreGeometry(geo)
        else:
            self.resize(1400, 900)
        # 分割器状态在 _init_ui 后恢复
        splitter_state = settings.value("splitter_state")
        if splitter_state and isinstance(splitter_state, bytes):
            # 延迟恢复，等 _init_ui 完成
            QTimer.singleShot(0, lambda: self._restore_splitter(splitter_state))

    def _restore_splitter(self, state):
        """恢复分割器状态"""
        if hasattr(self, '_main_splitter'):
            self._main_splitter.restoreState(state)

    def _save_geometry(self):
        """保存窗口大小和位置"""
        settings = QSettings("VehicleTestApp", "MainWindow")
        settings.setValue("geometry", self.saveGeometry())
        if hasattr(self, '_main_splitter'):
            settings.setValue("splitter_state", self._main_splitter.saveState())

    def closeEvent(self, event):
        """关闭窗口时保存状态并清理资源"""
        self._save_geometry()
        self._exec_engine.stop()
        self._device_mgr.shutdown()
        self._db.close()
        self._config_mgr.save()
        event.accept()

    def _init_ui(self):
        """初始化界面布局"""
        self.setWindowTitle(self.APP_TITLE)
        self.setMinimumSize(1024, 700)  # 增大最小尺寸

        # 中心部件
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        # 分割器：左侧设备面板 + 右侧标签页
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setChildrenCollapsible(False)

        # 左侧：设备连接面板（宽度可拖动调整）
        self._connection_panel = ConnectionPanel(self._device_mgr)
        self._connection_panel.setMinimumWidth(220)
        self._connection_panel.setMaximumWidth(400)
        splitter.addWidget(self._connection_panel)

        # 右侧：标签页
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._test_panel = TestPanel(
            self._exec_engine, self._db, self._device_mgr
        )
        self._execution_view = ExecutionView(
            self._exec_engine, self._device_mgr
        )
        self._report_panel = ReportPanel(self._report_gen, self._db)

        self._tabs.addTab(self._test_panel, "📋 测试用例")
        self._tabs.addTab(self._execution_view, "🎬 执行画面")
        self._tabs.addTab(self._report_panel, "📄 测试报告")
        self._knowledge_panel = KnowledgePanel(ai_client=self._ai.client)
        self._tabs.addTab(self._knowledge_panel, "📚 知识库")
        self._can_monitor_panel = CANMonitorPanel()
        self._tabs.addTab(self._can_monitor_panel, "🔌 CAN 监控")
        self._monkey_panel = MonkeyPanel()
        self._tabs.addTab(self._monkey_panel, "🐒 Monkey 测试")

        splitter.addWidget(self._tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 1000])  # 默认左侧 260px，右侧填充

        main_layout.addWidget(splitter)
        self._main_splitter = splitter  # 保存引用以便恢复状态

    def _init_menu(self):
        """初始化菜单栏"""
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction("导入用例(&I)...", self._on_import_cases)
        file_menu.addAction("导出报告(&E)...", self._on_export_report)
        file_menu.addAction("打开 Allure 报告(&A)...", self._on_open_allure)
        file_menu.addSeparator()
        file_menu.addAction("退出(&Q)", self.close)

        # 设备菜单
        device_menu = menubar.addMenu("设备(&D)")
        device_menu.addAction("扫描设备(&S)", self._on_scan_devices)
        device_menu.addAction("断开全部(&D)", self._on_disconnect_all)

        # 执行菜单
        exec_menu = menubar.addMenu("执行(&R)")
        exec_menu.addAction("执行全部(&A)", self._on_run_all)
        exec_menu.addAction("执行选中(&S)", self._on_run_selected)
        exec_menu.addSeparator()
        exec_menu.addAction("暂停(&P)", self._on_pause)
        exec_menu.addAction("继续(&C)", self._on_resume)
        exec_menu.addAction("停止(&T)", self._on_stop)
        exec_menu.addSeparator()
        exec_menu.addAction(
            "执行画面(&V)",
            lambda: self._tabs.setCurrentWidget(self._execution_view)
        )

        # 工具菜单
        tool_menu = menubar.addMenu("工具(&T)")
        tool_menu.addAction("ADB 屏幕投屏...", self._on_adb_screen)
        tool_menu.addSeparator()
        tool_menu.addAction("用例编辑器...", self._on_case_editor)

        # AI 菜单
        ai_menu = menubar.addMenu("AI(&I)")
        ai_menu.addAction("🤖 AI 测试助手...", self._on_ai_assistant)
        ai_menu.addSeparator()
        self._act_ai_diagnose = QAction("失败时自动诊断", self)
        self._act_ai_diagnose.setCheckable(True)
        self._act_ai_diagnose.toggled.connect(self._on_toggle_ai_diagnose)
        ai_menu.addAction(self._act_ai_diagnose)

        self._act_ai_repair = QAction("自动修复并重跑（不再询问）", self)
        self._act_ai_repair.setCheckable(True)
        self._act_ai_repair.toggled.connect(self._on_toggle_ai_repair)
        ai_menu.addAction(self._act_ai_repair)

        ai_menu.addSeparator()
        ai_menu.addAction("AI 设置...", self._on_ai_settings)
        self._sync_ai_actions()

        # 设置菜单
        settings_menu = menubar.addMenu("设置(&S)")
        settings_menu.addAction("环境配置...", self._on_env_config)
        settings_menu.addAction("偏好设置...", self._on_preferences)
        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction("关于...", self._on_about)

    def _init_toolbar(self):
        """初始化工具栏"""
        toolbar = QToolBar("主工具栏")
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        toolbar.addAction("🔍 扫描", self._on_scan_devices)
        toolbar.addSeparator()
        toolbar.addAction("▶ 执行", self._on_run_all)
        toolbar.addAction("⏸ 暂停", self._on_pause)
        toolbar.addAction("⏹ 停止", self._on_stop)
        toolbar.addSeparator()
        toolbar.addAction("📥 导入", self._on_import_cases)
        toolbar.addAction("📤 导出", self._on_export_report)
        toolbar.addAction("🧪 Allure", self._on_open_allure)
        toolbar.addSeparator()
        toolbar.addAction("🤖 AI 助手", self._on_ai_assistant)
        toolbar.addAction(
            "🎬 执行画面",
            lambda: self._tabs.setCurrentWidget(self._execution_view)
        )

    def _init_statusbar(self):
        """初始化状态栏"""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_label = QLabel("就绪")
        self._device_label = QLabel("设备: 0")
        self._ai_label = QLabel("AI: -")
        self._progress_label = QLabel("")

        self._statusbar.addWidget(self._status_label, 1)
        self._statusbar.addPermanentWidget(self._progress_label)
        self._statusbar.addPermanentWidget(self._ai_label)
        self._statusbar.addPermanentWidget(self._device_label)

        self._sync_ai_actions()

    def _connect_signals(self):
        """连接信号与槽"""
        # 执行引擎信号
        self._exec_engine.signals.execution_started.connect(
            lambda name: self._status_label.setText(f"执行中: {name}")
        )
        # 执行开始时自动切换到执行画面
        self._exec_engine.signals.execution_started.connect(
            lambda _name: self._tabs.setCurrentWidget(self._execution_view)
        )
        self._exec_engine.signals.execution_finished.connect(
            self._on_execution_finished
        )
        self._exec_engine.signals.progress_updated.connect(
            self._on_progress_updated
        )
        self._exec_engine.signals.log_output.connect(
            self._on_log_output
        )
        self._exec_engine.signals.case_failed.connect(
            self._on_case_failed
        )
        self._exec_engine.signals.case_finished.connect(
            self._on_case_status
        )

        # AI 助手入口
        self._test_panel.ai_assistant_requested.connect(self._on_ai_assistant)

        # 设备管理器信号
        self._device_mgr.signals.device_list_changed.connect(
            self._on_device_list_changed
        )
        self._device_mgr.signals.device_status_changed.connect(
            self._on_device_status_changed
        )

        # 连接面板：ADB 设备连接成功后自动打开投屏
        self._connection_panel.device_connected.connect(
            self._on_panel_device_connected
        )
        self._connection_panel.device_disconnected.connect(
            self._on_panel_device_disconnected
        )

    # ---------- 槽函数 ----------

    def _on_refresh(self):
        """定时刷新"""
        self._device_mgr._check_reconnect()

    def _on_scan_devices(self):
        self._status_label.setText("正在扫描设备...")
        self._device_mgr.scan_devices_async()

    def _on_disconnect_all(self):
        self._device_mgr.disconnect_all()

    def _on_import_cases(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入测试用例", "",
            "所有支持格式 (*.yaml *.yml *.json *.csv *.xlsx);;YAML (*.yaml);;JSON (*.json);;CSV (*.csv);;Excel (*.xlsx)"
        )
        if path:
            from core.testcase.parser import TestCaseParser
            result = TestCaseParser.parse_file(path)
            if result.success or result.test_cases:
                for case in result.test_cases:
                    self._db.save_case(case)
                self._test_panel.refresh()
                self._status_label.setText(f"导入成功: {len(result.test_cases)} 个用例")
            if result.errors:
                QMessageBox.warning(self, "导入警告", "\n".join(result.errors))

    def _on_export_report(self):
        suite = self._test_panel.get_current_suite()
        if not suite:
            QMessageBox.information(self, "提示", "没有可导出的测试结果")
            return

        path, selected = QFileDialog.getSaveFileName(
            self, "导出报告", "test_report.html",
            "HTML (*.html);;PDF (*.pdf);;Excel (*.xlsx);;JUnit XML (*.xml)"
        )
        if not path:
            return

        # 用户可能没改扩展名：按选择的格式补齐
        suffix = Path(path).suffix.lower()
        if suffix not in (".html", ".pdf", ".xlsx", ".xml"):
            default_ext = (
                ".pdf" if "PDF" in selected else
                ".xlsx" if "Excel" in selected else
                ".xml" if "XML" in selected else
                ".html"
            )
            path += default_ext
            suffix = default_ext

        result = ""
        try:
            if suffix == ".html":
                result = self._report_gen.generate_html(suite, path)
            elif suffix == ".pdf":
                result = self._report_gen.generate_pdf(suite, path)
            elif suffix == ".xlsx":
                result = self._report_gen.generate_excel(suite, path)
            elif suffix == ".xml":
                result = self._report_gen.generate_junit_xml(suite, path)
        except Exception as e:
            logger.exception(f"导出报告失败: {e}")
            QMessageBox.warning(self, "导出失败", f"报告导出失败：{e}")
            return

        if result:
            self._status_label.setText(f"报告已导出: {result}")
            return

        detail = "报告导出失败。"
        if suffix == ".pdf":
            detail += (
                "\n\n本程序优先使用 WeasyPrint，其次调用系统 Edge/Chrome 无头打印。"
                "\n请确认已安装 Microsoft Edge 或 Chrome，也可先导出 HTML 再用浏览器打印为 PDF。"
            )
        self._status_label.setText("报告导出失败")
        QMessageBox.warning(self, "导出失败", detail)

    def _on_open_allure(self):
        """生成标准 Allure 报告并在浏览器打开本机链接"""
        self._tabs.setCurrentWidget(self._report_panel)
        self._report_panel.open_allure_report()

    def _on_run_all(self):
        suite = self._test_panel.get_current_suite()
        if suite and suite.test_cases:
            self._exec_engine.execute_batch(suite)

    def _on_run_selected(self):
        cases = self._test_panel.get_selected_cases()
        if cases:
            from core.testcase.model import TestSuite
            suite = TestSuite(name="选中执行")
            for c in cases:
                suite.add_case(c)
            self._exec_engine.execute_batch(suite)

    def _on_pause(self):
        if self._exec_engine.state == ExecutionState.RUNNING:
            self._exec_engine.pause()
        elif self._exec_engine.state == ExecutionState.PAUSED:
            self._exec_engine.resume()

    def _on_resume(self):
        self._exec_engine.resume()

    def _on_stop(self):
        self._exec_engine.stop()

    def _on_execution_finished(self, name: str, duration: float):
        self._status_label.setText(f"执行完成: {name} ({duration:.1f}s)")
        self._progress_label.setText("")
        self._test_panel.refresh()
        self._report_panel.refresh()

        # 保存执行记录到数据库
        suite = self._exec_engine._current_suite
        if suite and suite.test_cases:
            record_id = self._db.save_execution(
                suite,
                environment=self._config_mgr.config.environment.name,
                operator="",
            )
            if record_id:
                logger.info(f"执行记录已保存: #{record_id}")
                self._report_panel.refresh()

        # 本轮结束后处理 AI 修复后的重跑队列
        QTimer.singleShot(600, self._drain_rerun_queue)

    def _on_progress_updated(self, progress):
        self._progress_label.setText(
            f"进度: {progress.current}/{progress.total} | "
            f"通过: {progress.passed} 失败: {progress.failed} | "
            f"{progress.current_case_name}"
        )

    def _on_log_output(self, level: str, message: str):
        self._statusbar.showMessage(message, 3000)

    # ---------- AI 助手 ----------

    def _sync_ai_actions(self):
        """同步 AI 菜单勾选状态与状态栏标识"""
        cfg = self._config_mgr.config.ai
        for action in (getattr(self, "_act_ai_diagnose", None),
                       getattr(self, "_act_ai_repair", None)):
            if action is not None:
                action.setEnabled(bool(cfg.enabled))
        if hasattr(self, "_act_ai_diagnose"):
            self._act_ai_diagnose.blockSignals(True)
            self._act_ai_diagnose.setChecked(bool(cfg.auto_diagnose))
            self._act_ai_diagnose.blockSignals(False)
        if hasattr(self, "_act_ai_repair"):
            self._act_ai_repair.blockSignals(True)
            self._act_ai_repair.setChecked(bool(cfg.auto_repair))
            self._act_ai_repair.blockSignals(False)
        if hasattr(self, "_ai_label"):
            if not cfg.enabled:
                text = "AI: 已关闭"
            elif cfg.api_key:
                text = f"AI: {cfg.model}"
            else:
                text = "AI: 内置规则"
            self._ai_label.setText(text)

    def _on_toggle_ai_diagnose(self, checked: bool):
        self._config_mgr.config.ai.auto_diagnose = checked
        self._config_mgr.save()

    def _on_toggle_ai_repair(self, checked: bool):
        cfg = self._config_mgr.config.ai
        cfg.auto_repair = checked
        if checked and not cfg.auto_diagnose:
            cfg.auto_diagnose = True
        self._config_mgr.save()
        self._sync_ai_actions()

    def _on_ai_assistant(self):
        if not self._ai.enabled:
            QMessageBox.information(
                self, "提示",
                "AI 助手已在偏好设置中关闭，可在 设置 → 偏好设置 → AI 助手 中开启。"
            )
            return

        from .widgets.ai_assistant_dialog import AIAssistantDialog
        dlg = AIAssistantDialog(
            self._ai, self._db, self._device_mgr, parent=self
        )
        dlg.cases_saved.connect(lambda _n: self._test_panel.refresh())
        dlg.run_cases_requested.connect(self._on_run_cases_ordered)
        dlg.exec()

    def _on_run_cases_ordered(self, cases):
        if not cases:
            return
        if self._exec_engine.is_running:
            QMessageBox.information(self, "提示", "当前有测试在执行，请稍后再试")
            return

        from core.testcase.model import TestSuite
        suite = TestSuite(name="AI 自动执行")
        for case in cases:
            suite.add_case(case)
        self._tabs.setCurrentWidget(self._test_panel)
        self._exec_engine.execute_batch(suite)

    def _on_case_status(self, case_id, name, status, duration):
        """用例通过后清除自动修复计数"""
        if status == "passed":
            self._fix_rounds.pop(case_id, None)

    def _on_case_failed(self, case_id, case_name, info):
        """用例失败：按配置弹出诊断或直接自动修复"""
        cfg = self._config_mgr.config.ai
        if not cfg.enabled or not cfg.auto_diagnose:
            return
        if not isinstance(info, dict) or info.get("case") is None:
            return
        if self._ai_busy:
            return

        if cfg.auto_repair:
            self._start_auto_diagnosis(case_id, info, cfg)
        else:
            self._show_diagnosis_dialog(case_id, info)

    def _ai_pause_engine(self):
        if self._exec_engine.state == ExecutionState.RUNNING:
            self._exec_engine.pause()
            self._ai_paused = True

    def _ai_resume_engine(self):
        if self._ai_paused:
            self._ai_paused = False
            if self._exec_engine.state == ExecutionState.PAUSED:
                self._exec_engine.resume()
        QTimer.singleShot(400, self._drain_rerun_queue)

    def _show_diagnosis_dialog(self, case_id, info):
        from .widgets.ai_error_dialog import AIErrorDialog

        self._ai_busy = True
        self._ai_pause_engine()
        actions = []
        try:
            dlg = AIErrorDialog(self._ai, info, self._device_mgr, parent=self)
            dlg.exec()
            actions = dlg.repair_actions
        finally:
            dlg.deleteLater()
            self._ai_busy = False
            self._ai_resume_engine()

        if actions:
            cfg = self._config_mgr.config.ai
            rounds = int(self._fix_rounds.get(case_id, 0))
            if rounds >= max(0, int(getattr(cfg, "max_fix_rounds", 2))):
                QMessageBox.information(
                    self, "AI 修复次数已达上限",
                    f"该用例已尝试修复 {rounds} 次仍未通过，请人工处理。"
                )
                return
            result = self._apply_repair(info, actions)
            if result is not None and result.changed:
                self._fix_rounds[case_id] = rounds + 1
                QMessageBox.information(
                    self, "AI 修复完成",
                    f"{result.summary}\n\n该用例将在本轮执行结束后自动重跑。"
                )

    def _start_auto_diagnosis(self, case_id, info, cfg):
        from .widgets.ai_worker import AIWorker

        rounds = int(self._fix_rounds.get(case_id, 0))
        if rounds >= max(0, int(getattr(cfg, "max_fix_rounds", 2))):
            self._exec_engine.signals.log_output.emit(
                "WARN",
                f"AI 自动修复已达上限（{rounds} 次），请人工处理: "
                f"{info.get('step_description')}"
            )
            return

        self._ai_busy = True
        self._ai_pause_engine()

        case = info["case"]
        step = info.get("step")
        step_index = info.get("step_index", -1)
        error = info.get("error", "")

        worker = AIWorker(
            lambda: self._ai.diagnose(
                case, step, step_index, error, self._device_mgr
            )
        )
        worker.succeeded.connect(
            lambda diagnosis: self._on_auto_diagnosed(case_id, info, diagnosis)
        )
        worker.failed.connect(self._on_auto_diagnose_failed)
        worker.start()

    def _on_auto_diagnosed(self, case_id, info, diagnosis):
        self._ai_busy = False
        self._fix_rounds[case_id] = self._fix_rounds.get(case_id, 0) + 1

        self._exec_engine.signals.log_output.emit(
            "WARN", f"🤖 AI 失败原因: {diagnosis.reason}"
        )
        self._exec_engine.signals.log_output.emit(
            "INFO",
            "💡 解决方法: " + (diagnosis.solution or "-").replace("\n", " ")
        )

        if diagnosis.auto_fixable and diagnosis.actions:
            result = self._apply_repair(info, diagnosis.actions)
            if result is not None and result.changed:
                self._statusbar.showMessage(
                    f"AI 已修复并排队重跑: {result.summary}", 8000
                )
        else:
            self._statusbar.showMessage(
                "AI 判定该问题不适合自动修复，请参考日志与解决方法人工处理", 8000
            )

        self._ai_resume_engine()

    def _on_auto_diagnose_failed(self, message):
        self._ai_busy = False
        self._exec_engine.signals.log_output.emit(
            "ERROR", f"AI 诊断失败: {message}"
        )
        self._ai_resume_engine()

    def _apply_repair(self, info, actions):
        case = info.get("case")
        if case is None:
            return None

        step_index = info.get("step_index", -1)
        default_index = (
            step_index if isinstance(step_index, int) and step_index >= 0 else None
        )
        result = self._ai.repair(
            case, actions, self._device_mgr,
            default_step_index=default_index,
            default_step_section=str(info.get("step_section") or "steps"),
        )
        self._exec_engine.signals.log_output.emit(
            "INFO", f"🤖 修复结果: {result.summary}"
        )

        if result.changed:
            if not self._db.save_case(case):
                self._exec_engine.signals.log_output.emit(
                    "ERROR", f"AI 修复未入库，放弃重跑: {case.name}"
                )
                QMessageBox.warning(
                    self, "AI 修复未保存",
                    "修复后的用例保存失败，已取消自动重跑，请检查数据库后重试。"
                )
                return None
            self._test_panel.refresh()
            self._queue_rerun(case.case_id)
        return result

    def _queue_rerun(self, case_id: str):
        if case_id and case_id not in self._rerun_queue:
            self._rerun_queue.append(case_id)
            self._status_label.setText("AI 已修复用例，将在本轮执行结束后自动重跑")

    def _drain_rerun_queue(self):
        if not self._rerun_queue:
            return
        if self._exec_engine.state not in (
            ExecutionState.IDLE, ExecutionState.FINISHED
        ):
            return

        while self._rerun_queue:
            case_id = self._rerun_queue.pop(0)
            case = self._db.load_case(case_id)
            if case is None:
                continue
            self._status_label.setText(f"🤖 AI 修复后重跑: {case.name}")
            self._exec_engine.execute_single(case)
            return

    def _on_device_list_changed(self):
        count = self._device_mgr.get_connected_count()
        self._device_label.setText(f"设备: {count}")
        self._connection_panel.refresh()

    def _on_device_status_changed(self, device_id, old, new):
        self._connection_panel.update_device_status(device_id, new)

    def _on_adb_screen(self):
        device = self._find_adb_device()
        if device is None:
            QMessageBox.information(
                self, "提示", "没有已连接的 ADB 设备，请先在左侧连接"
            )
            return
        self._open_adb_screen(device)

    def _on_panel_device_connected(self, device_id: str):
        """设备连接成功后：注入 Monkey 面板，不自动打开投屏"""
        device = self._device_mgr.get_device(device_id)
        if isinstance(device, ADBDevice):
            self._monkey_panel.set_adb_device(device)

    def _on_panel_device_disconnected(self, device_id: str):
        """设备断开后清理"""
        current = getattr(self._monkey_panel, "_adb_device", None)
        if current is not None and current.device_id == device_id:
            self._monkey_panel.set_adb_device(None)

    def _find_adb_device(self):
        """查找第一个已连接的 ADB 设备"""
        for device in self._device_mgr.get_all_devices().values():
            if isinstance(device, ADBDevice):
                return device
        return None

    def _open_adb_screen(self, device: ADBDevice):
        """打开（或复用）ADB 投屏窗口"""
        if self._adb_screen_dlg is not None:
            self._adb_screen_dlg._refresh_device_combo()
            idx = self._adb_screen_dlg._device_combo.findData(device.device_id)
            if idx >= 0:
                self._adb_screen_dlg._device_combo.setCurrentIndex(idx)
            self._adb_screen_dlg.show()
            self._adb_screen_dlg.raise_()
            self._adb_screen_dlg.activateWindow()
            return

        from ui.widgets.adb_screen import ADBScreenDialog
        dlg = ADBScreenDialog(
            self._device_mgr, device, parent=self
        )
        self._adb_screen_dlg = dlg

        def _cleanup():
            if self._adb_screen_dlg is dlg:
                self._adb_screen_dlg = None
            dlg.deleteLater()

        dlg.finished.connect(lambda _: _cleanup())
        dlg.show()

    def _on_case_editor(self):
        from ui.widgets.test_case_editor import TestCaseEditorDialog
        dlg = TestCaseEditorDialog(self._db, parent=self, device_mgr=self._device_mgr)
        dlg.data_changed.connect(self._test_panel.refresh)
        dlg.exec()

    def _on_env_config(self):
        from ui.widgets.environment_dialog import EnvironmentDialog
        dlg = EnvironmentDialog(self._config_mgr, parent=self)
        dlg.exec()

    def _on_preferences(self):
        self._open_preferences()

    def _on_ai_settings(self):
        self._open_preferences(initial_tab=4)

    def _open_preferences(self, initial_tab=None):
        from ui.widgets.preferences_dialog import PreferencesDialog
        dlg = PreferencesDialog(
            self._config_mgr, parent=self, initial_tab=initial_tab
        )
        if dlg.exec():
            # 配置已保存，同步执行引擎参数
            cfg = self._config_mgr.config
            self._exec_engine.set_config(
                retry_count=cfg.execution.retry_count,
                retry_delay=cfg.execution.retry_delay,
                batch_interval=cfg.execution.batch_interval,
                stop_on_failure=cfg.execution.stop_on_failure,
            )
            self._ai.refresh_config(cfg.ai)
            self._sync_ai_actions()
            self._status_label.setText("偏好设置已生效")

    def _on_about(self):
        QMessageBox.about(
            self, "关于",
            f"{self.APP_TITLE}\n\n"
            "车载信息娱乐系统自动化测试工具\n"
            "支持串口 / 网络 / ADB 通信\n\n"
            "技术栈: Python + PyQt6"
        )

