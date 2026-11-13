"""
执行画面
=========
执行用例时展示的实时视图：
- 左侧：车机实时画面（ADB 投屏，可点击/滑动）
- 右侧：步骤执行列表 + 实时日志
执行开始自动切换到该页。
"""

import threading
import time
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QTextEdit, QSplitter,
    QGroupBox, QPushButton
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor, QFont

from loguru import logger

from core.device.manager import DeviceManager
from core.device.adb_device import ADBDevice
from core.execution.engine import ExecutionEngine, ExecutionState

from .adb_screen import ADBScreenView
from .live_stream import LiveStream
from ..styles.theme import Tokens as T

STATUS_COLORS = {
    "passed": "#52c41a",
    "failed": "#ff4d4f",
    "error": "#ff7a45",
    "skipped": "#faad14",
    "running": "#1677ff",
}


class ADBLiveScreen(QWidget):
    """内嵌的 ADB 实时画面（自动选择第一个已连接 ADB 设备）"""

    size_ready = pyqtSignal(object, object)   # (device, (w, h))

    def __init__(self, device_manager: DeviceManager, parent=None):
        super().__init__(parent)
        self._dm = device_manager
        self._device: Optional[ADBDevice] = None
        self._streaming = False
        self._stream: Optional[LiveStream] = None
        self._stream_gen = 0
        self._last_status_ts = 0.0
        self._pending_frame = None        # ("img", QImage) | ("png", bytes)
        self._frame_lock = threading.Lock()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 顶部控制栏
        top = QHBoxLayout()
        top.setSpacing(12)

        # 画面设备选择
        device_label = QLabel("画面设备:")
        device_label.setStyleSheet("color: #475569; font-size: 10pt;")
        top.addWidget(device_label)

        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(180)
        self._device_combo.setMaximumWidth(240)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        top.addWidget(self._device_combo)

        top.addSpacing(20)

        # 帧率设置
        fps_label = QLabel("帧率:")
        fps_label.setStyleSheet("color: #475569; font-size: 10pt;")
        top.addWidget(fps_label)

        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(5, 90)
        self._fps_spin.setValue(30)
        self._fps_spin.setSuffix(" fps")
        self._fps_spin.setFixedWidth(90)
        top.addWidget(self._fps_spin)

        top.addStretch()
        layout.addLayout(top)

        self._view = ADBScreenView()
        self._view.set_placeholder("暂无画面\n连接 ADB 设备后执行用例即可实时显示车机画面")
        self._view.tap_requested.connect(self._on_tap)
        self._view.swipe_requested.connect(self._on_swipe)
        layout.addWidget(self._view, 1)

        self._status_label = QLabel("等待执行")
        self._status_label.setStyleSheet(
            f"color: {T.TEXT_TERTIARY}; font-size: {T.FONT_SIZE_SM}pt; padding: 4px 0;"
        )
        layout.addWidget(self._status_label)

        self._render_timer = QTimer(self)
        self._render_timer.setInterval(11)  # 约 90fps 渲染上限
        self._render_timer.timeout.connect(self._render_pending)

        self._dm.signals.device_list_changed.connect(self._refresh_devices)
        self.size_ready.connect(self._apply_screen_size)
        self._refresh_devices()

    # ---------- 设备 ----------

    def _refresh_devices(self):
        current_id = self._device_combo.currentData()
        self._device_combo.blockSignals(True)
        try:
            self._device_combo.clear()
            for device_id, device in self._dm.get_all_devices().items():
                if isinstance(device, ADBDevice):
                    self._device_combo.addItem(device_id, device_id)
            if current_id is not None:
                idx = self._device_combo.findData(current_id)
                if idx >= 0:
                    self._device_combo.setCurrentIndex(idx)
        finally:
            self._device_combo.blockSignals(False)

        self._on_device_changed(self._device_combo.currentIndex())

        # 页面可见时设备就绪即自动开播
        if self.isVisible() and self._device is not None and not self._streaming:
            self._start_stream()

    def _on_device_changed(self, idx: int):
        device_id = self._device_combo.itemData(idx)
        device = self._dm.get_device(device_id) if device_id else None
        new_device = device if isinstance(device, ADBDevice) else None
        changed = new_device is not self._device
        self._device = new_device

        if self._device is None:
            self._stop_stream()
            with self._frame_lock:
                self._pending_frame = None
            self._view.set_placeholder(
                "暂无画面\n请在左侧设备面板连接 ADB 设备"
            )
            self._view.clear_frame()
            self._status_label.setText("暂无已连接的 ADB 设备")
            return

        if not changed:
            return

        with self._frame_lock:
            self._pending_frame = None
        self._stop_stream()
        self._view.set_placeholder("正在获取车机画面...")
        self._fetch_screen_size(self._device)
        if self.isVisible():
            self._start_stream()

    def _fetch_screen_size(self, device: ADBDevice):
        """后台获取分辨率，避免在主线程执行 adb shell 造成卡顿"""

        def worker():
            try:
                size = device.get_screen_size()
            except Exception as e:
                logger.debug(f"获取分辨率失败: {e}")
                return
            if size:
                self.size_ready.emit(device, size)

        threading.Thread(
            target=worker, daemon=True, name="ExecADBSize"
        ).start()

    def _apply_screen_size(self, device: ADBDevice, size):
        if self._device is device:
            self._view._dev_size = size

    # ---------- 投屏 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._stop_stream()
        self._status_label.setText("画面已暂停（切回本页自动恢复）")

    def start(self):
        self._refresh_devices()
        if self._device is None:
            self._view.set_placeholder(
                "暂无画面\n请在左侧设备面板连接 ADB 设备"
            )
            self._view.clear_frame()
            self._status_label.setText("暂无已连接的 ADB 设备（非 ADB 用例仍可正常执行）")
            return
        self._view.set_placeholder("正在获取车机画面...")
        if not self._streaming:
            self._start_stream()

    def stop(self):
        self._stop_stream()

    def _start_stream(self):
        if self._device is None:
            return
        if self._streaming:
            return
        self._stream_gen += 1
        gen = self._stream_gen
        self._streaming = True
        stream = LiveStream(
            self._device,
            publish=self._publish_frame,
            is_active=lambda: self._streaming and self._stream_gen == gen,
        )
        self._stream = stream
        stream.start(lambda: self._fps_spin.value())
        self._render_timer.start()
        self._status_label.setText("正在连接画面流...")

    def _stop_stream(self):
        self._streaming = False
        self._stream_gen += 1
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.stop()
        self._render_timer.stop()

    def _publish_frame(self, kind: str, payload):
        with self._frame_lock:
            self._pending_frame = (kind, payload)

    def _stream_status_text(self) -> str:
        stream = self._stream
        mode = "H.264 实时流" if stream and stream.mode == LiveStream.MODE_H264 \
            else "截图模式"
        fps_actual = stream.fps if stream else 0.0
        device_id = self._device.device_id if self._device else "-"
        text = (f"实时画面 | {device_id} | {mode} | 目标 {self._fps_spin.value()} fps"
                f" / 实测 {fps_actual:.0f} fps")
        if fps_actual < 0.5:
            text += "（画面无变化）"
        return text

    def _render_pending(self):
        now = time.time()
        if self._streaming and now - self._last_status_ts >= 0.5:
            self._last_status_ts = now
            self._status_label.setText(self._stream_status_text())

        with self._frame_lock:
            item = self._pending_frame
            self._pending_frame = None
        if not item:
            return

        kind, payload = item
        if kind == "img":
            pixmap = QPixmap.fromImage(payload)
        else:
            pixmap = QPixmap()
            if not pixmap.loadFromData(payload):
                return

        if pixmap.isNull():
            return
        self._view.set_frame(pixmap, self._view._dev_size)

    # ---------- 交互 ----------

    def _on_tap(self, x: int, y: int):
        if self._device:
            threading.Thread(target=self._device.input_tap, args=(x, y),
                             daemon=True).start()

    def _on_swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int):
        if self._device:
            threading.Thread(target=self._device.input_swipe,
                             args=(x1, y1, x2, y2, duration),
                             daemon=True).start()

    def closeEvent(self, event):
        self._stop_stream()
        event.accept()


class ExecutionView(QWidget):
    """执行画面：车机实时画面 + 步骤执行进度 + 实时日志"""

    def __init__(self, engine: ExecutionEngine,
                 device_manager: DeviceManager, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._dm = device_manager
        self._current_case_id = ""
        self._elapsed_start = 0.0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 顶部状态
        header = QHBoxLayout()
        self._case_label = QLabel("当前用例: -")
        self._case_label.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        header.addWidget(self._case_label, 1)

        self._result_label = QLabel("")
        header.addWidget(self._result_label)

        self._elapsed_label = QLabel("已用时: 00:00")
        self._elapsed_label.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        header.addWidget(self._elapsed_label)

        # 执行控制：暂停/继续、结束（仅在执行期间可用）
        self._pause_btn = QPushButton("⏸ 暂停")
        self._pause_btn.setToolTip("暂停 / 继续当前执行")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause_clicked)
        header.addWidget(self._pause_btn)

        self._stop_btn = QPushButton("⏹ 结束")
        self._stop_btn.setToolTip("结束当前执行（已完成步骤的结果会保留）")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            f"QPushButton:enabled {{ color: {T.DANGER}; font-weight: bold; }}"
        )
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        header.addWidget(self._stop_btn)

        layout.addLayout(header)

        self._progress_bar = QProgressBar()
        self._progress_bar.setFormat("%v/%m - %p%")
        layout.addWidget(self._progress_bar)

        self._stats_label = QLabel("通过 0 | 失败 0 | 错误 0 | 跳过 0")
        self._stats_label.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        layout.addWidget(self._stats_label)

        # 主体：左画面 + 右步骤/日志
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)

        screen_group = QGroupBox("车机实时画面")
        screen_layout = QVBoxLayout(screen_group)
        self._live_screen = ADBLiveScreen(self._dm)
        screen_layout.addWidget(self._live_screen)
        splitter.addWidget(screen_group)

        right = QSplitter(Qt.Orientation.Vertical)
        right.setHandleWidth(6)

        self._step_table = QTableWidget()
        self._step_table.setColumnCount(5)
        self._step_table.setHorizontalHeaderLabels(
            ["序号", "步骤描述", "类型", "状态", "耗时"]
        )
        header_view = self._step_table.horizontalHeader()
        # 允许拖动调整列宽，描述列默认占满剩余空间
        for col in range(5):
            header_view.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        header_view.setStretchLastSection(True)
        for col, width in enumerate((60, 320, 110, 80, 80)):
            self._step_table.setColumnWidth(col, width)
        self._step_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._step_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        right.addWidget(self._step_table)

        self._log_output = QTextEdit()
        self._log_output.setReadOnly(True)
        self._log_output.setFont(QFont("Consolas", 9))
        right.addWidget(self._log_output)

        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)

        layout.addWidget(splitter, 1)

    def _connect_signals(self):
        signals = self._engine.signals
        signals.execution_started.connect(self._on_execution_started)
        signals.execution_finished.connect(self._on_execution_finished)
        signals.case_started.connect(self._on_case_started)
        signals.case_finished.connect(self._on_case_finished)
        signals.step_started.connect(self._on_step_started)
        signals.step_finished.connect(self._on_step_finished)
        signals.progress_updated.connect(self._on_progress)
        signals.log_output.connect(self._on_log)
        signals.state_changed.connect(self._on_state_changed)

    # ---------- 执行事件 ----------

    def _on_execution_started(self, suite_name: str):
        self._step_table.setRowCount(0)
        self._log_output.clear()
        self._case_label.setText("当前用例: -")
        self._result_label.setText("")
        self._progress_bar.setValue(0)
        self._stats_label.setText("通过 0 | 失败 0 | 错误 0 | 跳过 0")
        self._elapsed_start = time.time()
        self._elapsed_timer.start()
        self._elapsed_label.setText("已用时: 00:00")
        self._live_screen.start()
        self._append_log("INFO", f"开始执行: {suite_name}")
        # 执行期间开放控制按钮
        self._pause_btn.setText("⏸ 暂停")
        self._pause_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)

    def _on_execution_finished(self, suite_name: str, duration: float):
        self._elapsed_timer.stop()
        # 画面保持实时刷新，便于执行结束后回看车机状态
        self._elapsed_label.setText(f"总耗时: {duration:.1f}s")
        self._update_stats(self._engine.progress)
        self._append_log("INFO", f"执行结束: {suite_name}（{duration:.1f}s）")
        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._pause_btn.setText("⏸ 暂停")

    # ---------- 执行控制 ----------

    def _on_pause_clicked(self):
        """暂停 / 继续 切换"""
        if self._engine.state == ExecutionState.PAUSED:
            self._engine.resume()
        else:
            self._engine.pause()

    def _on_stop_clicked(self):
        """结束当前执行"""
        self._engine.stop()
        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._append_log("WARN", "已请求结束执行，等待当前步骤完成后停止…")

    def _on_state_changed(self, state: str):
        if state == ExecutionState.PAUSED.value:
            self._pause_btn.setText("▶ 继续")
            self._pause_btn.setEnabled(True)
            self._append_log("WARN", "执行已暂停，点击「继续」恢复")
        elif state == ExecutionState.RUNNING.value:
            self._pause_btn.setText("⏸ 暂停")
            self._pause_btn.setEnabled(True)
        elif state == ExecutionState.STOPPING.value:
            self._pause_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)

    def _on_case_started(self, case_id: str, name: str):
        # 每次用例开始都清空表格（重试不会重复触发 case_started）
        self._step_table.setRowCount(0)
        self._current_case_id = case_id
        self._case_label.setText(f"当前用例: {name}")
        self._result_label.setText("")
        self._append_log("INFO", f"▶ 开始执行: {name}")

    def _on_case_finished(self, case_id: str, name: str, status: str,
                          duration: float):
        color = STATUS_COLORS.get(status, "#333")
        self._result_label.setText(f"结果: {status} ({duration:.2f}s)")
        self._result_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        self._append_log(status.upper(), f"{name} - {status} ({duration:.2f}s)")

    def _on_step_started(self, case_id: str, description: str, step_idx: int):
        # 失败重试时复用已有行，避免同一用例步骤重复堆叠
        for row in range(self._step_table.rowCount()):
            desc_item = self._step_table.item(row, 1)
            status_item = self._step_table.item(row, 3)
            if (desc_item is not None and desc_item.text() == description
                    and status_item is not None
                    and status_item.text() != "执行中"):
                status_item.setText("执行中")
                status_item.setForeground(QColor(STATUS_COLORS["running"]))
                self._step_table.setItem(row, 4, QTableWidgetItem("-"))
                self._step_table.scrollToItem(desc_item)
                return

        row = self._step_table.rowCount()
        self._step_table.insertRow(row)
        idx_item = QTableWidgetItem(str(step_idx + 1))
        idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._step_table.setItem(row, 0, idx_item)
        self._step_table.setItem(row, 1, QTableWidgetItem(description))
        self._step_table.setItem(row, 2, QTableWidgetItem(self._step_type(description)))

        status_item = QTableWidgetItem("执行中")
        status_item.setForeground(QColor(STATUS_COLORS["running"]))
        self._step_table.setItem(row, 3, status_item)
        self._step_table.setItem(row, 4, QTableWidgetItem("-"))
        self._step_table.scrollToBottom()

    def _on_step_finished(self, case_id: str, description: str, status: str,
                          duration: float):
        for row in range(self._step_table.rowCount() - 1, -1, -1):
            status_item = self._step_table.item(row, 3)
            desc_item = self._step_table.item(row, 1)
            if status_item is None or desc_item is None:
                continue
            if desc_item.text() != description:
                continue
            if status_item.text() != "执行中":
                continue
            status_item.setText(status)
            status_item.setForeground(QColor(STATUS_COLORS.get(status, "#333")))
            self._step_table.setItem(row, 4, QTableWidgetItem(f"{duration:.2f}s"))
            break

    def _on_progress(self, progress):
        self._progress_bar.setMaximum(max(1, progress.total))
        self._progress_bar.setValue(progress.current)
        self._update_stats(progress)

    def _update_stats(self, progress):
        text = (
            f"通过 {progress.passed} | 失败 {progress.failed} | "
            f"错误 {progress.error} | 跳过 {progress.skipped}"
        )
        aborted = getattr(progress, "aborted", 0)
        if aborted:
            text += f" | 中止 {aborted}"
        self._stats_label.setText(text)

    def _on_log(self, level: str, message: str):
        self._append_log(level, message)

    # ---------- 工具 ----------

    def _append_log(self, level: str, message: str):
        self._log_output.append(f"[{level}] {message}")
        scrollbar = self._log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _step_type(self, description: str) -> str:
        case = self._engine._current_case
        if case is None:
            return "-"
        for steps in (case.setup_steps, case.steps, case.teardown_steps):
            for step in steps:
                if step.description == description:
                    return step.step_type.value
        return "-"

    def _tick_elapsed(self):
        elapsed = int(time.time() - self._elapsed_start)
        self._elapsed_label.setText(
            f"已用时: {elapsed // 60:02d}:{elapsed % 60:02d}"
        )

    def closeEvent(self, event):
        self._live_screen.close()
        event.accept()
