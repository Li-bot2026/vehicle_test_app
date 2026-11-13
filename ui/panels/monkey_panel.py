"""
Monkey 测试面板
==============
参考 Android 官方 Monkey / 字节 Fastbot / Maxim / PerfDog 的成熟界面：
- 顶部：目标应用 + 运行控制
- 双列：运行配置 | 事件权重
- 指标卡片行：总事件 / 成功率 / Crash / ANR / 速度 / 已用时
- 进度条 + 可过滤实时日志

整体置于 QScrollArea 中，避免窗口高度不足时控件被压缩裁切。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QSpinBox, QPushButton, QProgressBar, QPlainTextEdit,
    QSlider, QComboBox, QCheckBox, QFrame, QScrollArea, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from typing import Optional, List

from loguru import logger

from core.testing.monkey import MonkeyTester, MonkeyConfig, ActionType
from ui.styles.theme import Tokens as T


class MonkeyWorker(QThread):
    """Monkey 测试工作线程"""

    action_signal = pyqtSignal(str)
    stats_signal = pyqtSignal(dict)
    finished_signal = pyqtSignal(dict)

    def __init__(self, config: MonkeyConfig, adb_device=None, parent=None):
        super().__init__(parent)
        self.config = config
        self._adb_device = adb_device
        self._tester: Optional[MonkeyTester] = None

    def run(self):
        self._tester = MonkeyTester(self.config)
        if self._adb_device:
            self._tester.set_adb_device(self._adb_device)

        self._tester.on_action = lambda a: self.action_signal.emit(
            f"[{a.action_type.value}] {a.detail}" + ("" if a.success else "（失败）"))
        self._tester.on_stats = lambda s: self.stats_signal.emit(self._to_dict(s))
        stats = self._tester.run()
        self.finished_signal.emit(self._to_dict(stats))

    @staticmethod
    def _to_dict(s) -> dict:
        return {
            "total": s.total_actions, "success": s.successful_actions,
            "failed": s.failed_actions, "crashes": s.crashes, "anrs": s.anrs,
            "success_rate": s.success_rate, "duration": s.duration,
            "aps": s.actions_per_second,
        }

    def stop(self):
        if self._tester:
            self._tester.stop()

    def pause(self):
        if self._tester:
            self._tester.pause()

    def resume(self):
        if self._tester:
            self._tester.resume()


class _PackageLoader(QThread):
    """后台加载设备已安装应用列表"""
    loaded = pyqtSignal(list)

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self._device = device

    def run(self):
        try:
            self.loaded.emit(self._device.list_packages())
        except Exception as e:
            logger.debug(f"加载应用列表失败: {e}")
            self.loaded.emit([])


class MonkeyPanel(QWidget):
    """Monkey 测试面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[MonkeyWorker] = None
        self._loader: Optional[_PackageLoader] = None
        self._config = MonkeyConfig()
        self._adb_device = None
        self._log_entries: List[tuple] = []

        self._init_ui()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_stats)

    # ---------- 设备 ----------

    def set_adb_device(self, device):
        self._adb_device = device
        if device is not None:
            size = None
            try:
                size = device.get_screen_size()
            except Exception as e:
                logger.debug(f"获取屏幕尺寸失败: {e}")
            if size:
                w, h = size
                self._width_spin.setValue(w)
                self._height_spin.setValue(h)
                self._device_label.setText(f"{device.device_id} · {w}×{h}")
            else:
                self._device_label.setText(device.device_id)
            self._device_label.setStyleSheet(
                f"color: {T.SUCCESS}; font-weight: bold; background: transparent;")
            self._refresh_packages()
        else:
            self._device_label.setText("未连接")
            self._device_label.setStyleSheet(
                f"color: {T.TEXT_TERTIARY}; background: transparent;")

    def _refresh_packages(self):
        if not self._adb_device or (self._loader and self._loader.isRunning()):
            return
        self._loader = _PackageLoader(self._adb_device, self)
        self._loader.loaded.connect(self._on_packages_loaded)
        self._loader.start()

    def _on_packages_loaded(self, packages: list):
        current = self._app_combo.currentText()
        self._app_combo.blockSignals(True)
        self._app_combo.clear()
        self._app_combo.addItem("不限制（当前前台应用）", "")
        for pkg in packages:
            self._app_combo.addItem(pkg, pkg)
        idx = self._app_combo.findText(current)
        if idx >= 0:
            self._app_combo.setCurrentIndex(idx)
        self._app_combo.blockSignals(False)

    # ---------- UI ----------

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ---- 顶部控制栏 ----
        top = QHBoxLayout()
        top.setSpacing(8)
        app_label = QLabel("目标应用:")
        top.addWidget(app_label)
        self._app_combo = QComboBox()
        self._app_combo.setEditable(True)
        self._app_combo.setMinimumWidth(240)
        self._app_combo.addItem("不限制（当前前台应用）", "")
        top.addWidget(self._app_combo, 1)
        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self._refresh_packages)
        top.addWidget(self._refresh_btn)
        self._device_label = QLabel("未连接")
        self._device_label.setStyleSheet(f"color: {T.TEXT_TERTIARY};")
        top.addWidget(self._device_label)
        top.addSpacing(12)
        self._start_btn = QPushButton("开始测试")
        self._start_btn.setDefault(True)
        self._start_btn.clicked.connect(self._on_start)
        top.addWidget(self._start_btn)
        self._pause_btn = QPushButton("暂停")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._on_pause)
        top.addWidget(self._pause_btn)
        self._stop_btn = QPushButton("停止")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        top.addWidget(self._stop_btn)
        layout.addLayout(top)

        # ---- 双列：配置 | 事件权重 ----
        columns = QHBoxLayout()
        columns.setSpacing(10)

        config_group = QGroupBox("运行配置")
        cfg = QGridLayout(config_group)
        cfg.setHorizontalSpacing(12)
        cfg.setVerticalSpacing(10)
        cfg.setColumnStretch(1, 1)

        self._duration_spin = QSpinBox()
        self._duration_spin.setRange(10, 86400)
        self._duration_spin.setValue(300)
        self._duration_spin.setSuffix(" 秒")
        self._duration_spin.setFixedWidth(180)
        self._add_cfg_row(cfg, 0, "测试时长:", self._duration_spin)

        self._events_spin = QSpinBox()
        self._events_spin.setRange(0, 10000000)
        self._events_spin.setValue(0)
        self._events_spin.setSpecialValueText("不限")
        self._events_spin.setSuffix(" 个")
        self._events_spin.setFixedWidth(180)
        self._events_spin.setToolTip("总事件数上限，达到后停止；不限 = 仅按时长停止；与时长上限先到先停")
        self._add_cfg_row(cfg, 1, "总事件数:", self._events_spin)

        self._throttle_spin = QSpinBox()
        self._throttle_spin.setRange(0, 5000)
        self._throttle_spin.setValue(200)
        self._throttle_spin.setSuffix(" ms")
        self._throttle_spin.setFixedWidth(180)
        self._add_cfg_row(cfg, 2, "事件间隔:", self._throttle_spin)

        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 999999)
        self._seed_spin.setValue(0)
        self._seed_spin.setSpecialValueText("随机")
        self._seed_spin.setFixedWidth(180)
        self._add_cfg_row(cfg, 3, "随机种子:", self._seed_spin)

        screen = QWidget()
        sh = QHBoxLayout(screen)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(6)
        self._width_spin = QSpinBox()
        self._width_spin.setRange(100, 3840)
        self._width_spin.setValue(1920)
        self._width_spin.setFixedWidth(90)
        sh.addWidget(self._width_spin)
        sh.addWidget(QLabel("×"))
        self._height_spin = QSpinBox()
        self._height_spin.setRange(100, 2160)
        self._height_spin.setValue(1080)
        self._height_spin.setFixedWidth(90)
        sh.addWidget(self._height_spin)
        sh.addStretch()
        self._add_cfg_row(cfg, 4, "屏幕尺寸:", screen)

        self._crash_check = QCheckBox("检测到 Crash 立即停止")
        self._crash_check.setChecked(True)
        cfg.addWidget(self._crash_check, 5, 1)
        self._anr_check = QCheckBox("检测到 ANR 立即停止")
        self._anr_check.setChecked(True)
        cfg.addWidget(self._anr_check, 6, 1)
        columns.addWidget(config_group, 1)

        weight_group = QGroupBox("事件权重")
        wg = QVBoxLayout(weight_group)
        wg.setSpacing(10)
        self._weight_sliders = {}
        for key, text, default in [
            ("tap", "点击", 40), ("swipe", "滑动", 20),
            ("long_press", "长按", 10), ("input", "输入", 10),
            ("key", "系统键", 10), ("app_switch", "应用切换", 10),
        ]:
            row, slider = self._weight_row(text, default)
            self._weight_sliders[key] = slider
            wg.addLayout(row)
        wg.addStretch()
        columns.addWidget(weight_group, 1)
        layout.addLayout(columns)

        # ---- 指标卡片 ----
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self._total_card, self._total_value = self._metric_card("总事件数")
        self._rate_card, self._rate_value = self._metric_card("成功率")
        self._rate_value.setText("0.0%")
        self._crash_card, self._crash_value = self._metric_card("Crash", danger=True)
        self._anr_card, self._anr_value = self._metric_card("ANR", danger=True)
        self._speed_card, self._speed_value = self._metric_card("事件/秒")
        self._time_card, self._time_value = self._metric_card("已用时")
        for card in (self._total_card, self._rate_card, self._crash_card,
                     self._anr_card, self._speed_card, self._time_card):
            cards.addWidget(card, 1)
        layout.addLayout(cards)

        # ---- 进度条 ----
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        layout.addWidget(self._progress_bar)

        # ---- 日志 ----
        log_group = QGroupBox("实时日志")
        log_layout = QVBoxLayout(log_group)
        log_header = QHBoxLayout()
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["全部", "仅事件", "仅 Crash/ANR"])
        self._filter_combo.currentIndexChanged.connect(self._render_log)
        log_header.addWidget(QLabel("过滤:"))
        log_header.addWidget(self._filter_combo)
        log_header.addStretch()
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_log)
        log_header.addWidget(clear_btn)
        log_layout.addLayout(log_header)
        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMinimumHeight(160)
        log_layout.addWidget(self._log_text)
        layout.addWidget(log_group)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _add_cfg_row(self, grid: QGridLayout, row: int, text: str, field: QWidget):
        label = QLabel(text)
        label.setFixedWidth(70)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(label, row, 0)
        grid.addWidget(field, row, 1,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def _weight_row(self, text: str, default: int):
        row = QHBoxLayout()
        row.setSpacing(10)
        label = QLabel(text)
        label.setFixedWidth(70)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(default)
        value = QLabel(f"{default}%")
        value.setFixedWidth(44)
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider.valueChanged.connect(lambda v: value.setText(f"{v}%"))
        row.addWidget(label)
        row.addWidget(slider, 1)
        row.addWidget(value)
        return row, slider

    def _metric_card(self, caption: str, danger: bool = False):
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {T.SURFACE}; border: 1px solid {T.BORDER}; "
            f"border-radius: {T.RADIUS_MD}; }}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        value = QLabel("0")
        color = T.DANGER if danger else T.PRIMARY
        value.setStyleSheet(
            f"font-size: 16pt; font-weight: bold; color: {color}; "
            f"border: none; background: transparent;")
        cap = QLabel(caption)
        cap.setStyleSheet(
            f"font-size: {T.FONT_SIZE_SM}pt; color: {T.TEXT_SECONDARY}; "
            f"border: none; background: transparent;")
        lay.addWidget(value)
        lay.addWidget(cap)
        return card, value

    # ---------- 配置 ----------

    def _get_config(self) -> MonkeyConfig:
        sliders = self._weight_sliders
        total = sum(s.value() for s in sliders.values()) or 1
        pkg = self._app_combo.currentData()
        if pkg is None or self._app_combo.currentIndex() == 0:
            pkg = "" if self._app_combo.currentIndex() == 0 else self._app_combo.currentText().strip()
        return MonkeyConfig(
            duration=float(self._duration_spin.value()),
            max_events=self._events_spin.value(),
            throttle_ms=self._throttle_spin.value(),
            seed=self._seed_spin.value(),
            screen_width=self._width_spin.value(),
            screen_height=self._height_spin.value(),
            package=pkg or "",
            stop_on_crash=self._crash_check.isChecked(),
            stop_on_anr=self._anr_check.isChecked(),
            tap_prob=sliders["tap"].value() / total,
            swipe_prob=sliders["swipe"].value() / total,
            long_press_prob=sliders["long_press"].value() / total,
            input_prob=sliders["input"].value() / total,
            key_prob=sliders["key"].value() / total,
            app_switch_prob=sliders["app_switch"].value() / total,
        )

    # ---------- 控制 ----------

    def _on_start(self):
        if self._worker and self._worker.isRunning():
            return
        self._config = self._get_config()
        self._worker = MonkeyWorker(self._config, adb_device=self._adb_device)
        self._worker.action_signal.connect(self._on_action)
        self._worker.stats_signal.connect(self._on_stats)
        self._worker.finished_signal.connect(self._on_finished)

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText("暂停")
        self._app_combo.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._clear_log()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._poll_timer.start()

        self._append_log("info", "开始 Monkey 测试...")
        if self._adb_device:
            self._append_log("info", f"目标设备: {self._adb_device.device_id}")
        else:
            self._append_log("info", "未连接 ADB 设备，本地模拟模式")
        if self._config.package:
            self._append_log("info", f"目标应用: {self._config.package}")
        if self._config.max_events > 0:
            self._append_log("info", f"事件数上限: {self._config.max_events}（与时长先到先停）")
        self._worker.start()

    def _on_stop(self):
        if self._worker:
            self._worker.stop()
            self._append_log("info", "正在停止...")

    def _on_pause(self):
        if self._worker and self._worker._tester:
            if self._pause_btn.text() == "暂停":
                self._worker.pause()
                self._pause_btn.setText("继续")
                self._append_log("info", "已暂停")
            else:
                self._worker.resume()
                self._pause_btn.setText("暂停")
                self._append_log("info", "已继续")

    # ---------- 回调 ----------

    def _on_action(self, msg: str):
        kind = "crash" if ("[CRASH]" in msg or "[ANR]" in msg) else "action"
        self._append_log(kind, msg)

    def _on_stats(self, stats: dict):
        self._total_value.setText(str(stats["total"]))
        self._rate_value.setText(f"{stats['success_rate']:.1f}%")
        self._crash_value.setText(str(stats["crashes"]))
        self._anr_value.setText(str(stats["anrs"]))
        self._speed_value.setText(f"{stats['aps']:.1f}")
        self._time_value.setText(f"{stats['duration']:.0f}s")

    def _poll_stats(self):
        if not self._worker or not self._worker._tester:
            return
        stats = self._worker._tester.stats
        elapsed = stats.duration
        self._time_value.setText(f"{elapsed:.0f}s")
        pct = elapsed / self._config.duration if self._config.duration > 0 else 0.0
        count_text = f"{stats.total_actions} 次事件"
        if self._config.max_events > 0:
            pct = max(pct, stats.total_actions / self._config.max_events)
            count_text = f"{stats.total_actions}/{self._config.max_events} 次事件"
        pct = min(1.0, pct)
        self._progress_bar.setValue(int(pct * 100))
        self._progress_bar.setFormat(f"{count_text} | {int(pct * 100)}%")

    def _on_finished(self, stats: dict):
        self._poll_timer.stop()
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("暂停")
        self._app_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._on_stats(stats)
        self._progress_bar.setValue(self._progress_bar.maximum())
        count_text = f"{stats['total']} 次事件"
        if self._config.max_events > 0:
            count_text = f"{stats['total']}/{self._config.max_events} 次事件"
        self._progress_bar.setFormat(f"{count_text} | 100%")
        self._append_log("info", "测试完成!")
        QMessageBox.information(
            self, "测试完成",
            f"Monkey 测试已完成\n\n"
            f"总事件数: {stats['total']}\n"
            f"成功率: {stats['success_rate']:.1f}%\n"
            f"Crash: {stats['crashes']}  ANR: {stats['anrs']}\n"
            f"用时: {stats['duration']:.1f} 秒")

    # ---------- 日志 ----------

    def _append_log(self, kind: str, text: str):
        self._log_entries.append((kind, text))
        if len(self._log_entries) > 2000:
            self._log_entries = self._log_entries[-2000:]
        if self._kind_visible(kind):
            self._log_text.appendPlainText(text)

    def _kind_visible(self, kind: str) -> bool:
        idx = self._filter_combo.currentIndex()
        if idx == 1:
            return kind == "action"
        if idx == 2:
            return kind == "crash"
        return True

    def _render_log(self):
        self._log_text.clear()
        for kind, text in self._log_entries:
            if self._kind_visible(kind):
                self._log_text.appendPlainText(text)

    def _clear_log(self):
        self._log_entries.clear()
        self._log_text.clear()