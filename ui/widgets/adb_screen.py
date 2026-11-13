"""
ADB 屏幕投屏
============
通过 ADB 实时显示车机屏幕画面，并支持鼠标点击/滑动操作。

设计说明：
- ADBScreenView 负责画面渲染与坐标映射（保持纵横比居中显示）
- ADBScreenDialog 负责实时抓帧（后台线程持续抓取最新帧，UI 高频取走渲染）
- 点击/滑动坐标按显示区域映射回设备真实分辨率
"""

import threading
import time
from typing import Optional, Tuple

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QSpinBox, QFileDialog, QMessageBox,
    QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QRectF, QPointF, QPoint
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont

from loguru import logger

from core.device.manager import DeviceManager
from core.device.adb_device import ADBDevice
from .live_stream import LiveStream
from .dialog_utils import allow_resize
from ..styles.theme import Tokens as T


class ADBScreenView(QWidget):
    """
    屏幕显示视图
    -------------
    按纵横比居中显示设备画面，并将鼠标事件映射为设备坐标。
    """

    tap_requested = pyqtSignal(int, int)                   # 设备坐标点击
    swipe_requested = pyqtSignal(int, int, int, int, int)  # 起x,起y,终x,终y,时长ms

    CLICK_THRESHOLD = 15  # 判定为点击的最大移动距离（像素）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._dev_size: Optional[Tuple[int, int]] = None
        self._press_pos: Optional[QPoint] = None
        self._placeholder = "无画面\n连接 ADB 设备后点击「开始刷新」"
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)

    def set_placeholder(self, text: str):
        """无画面时显示的提示文字"""
        self._placeholder = text
        self.update()

    def set_frame(self, pixmap: QPixmap, dev_size: Optional[Tuple[int, int]]):
        """更新画面帧"""
        self._pixmap = pixmap
        if dev_size:
            self._dev_size = dev_size
        elif not self._dev_size and pixmap and not pixmap.isNull():
            self._dev_size = (pixmap.width(), pixmap.height())
        self.update()

    def clear_frame(self):
        self._pixmap = None
        self.update()

    def _image_rect(self) -> QRectF:
        """计算保持纵横比的实际显示区域"""
        if not self._pixmap or self._pixmap.isNull():
            return QRectF()
        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(w / pw, h / ph)
        dw, dh = pw * scale, ph * scale
        return QRectF((w - dw) / 2, (h - dh) / 2, dw, dh)

    def _map_to_device(self, pos: QPoint) -> Optional[Tuple[int, int]]:
        """将控件坐标映射到设备分辨率坐标"""
        rect = self._image_rect()
        if rect.isEmpty():
            return None
        if not rect.contains(QPointF(pos)):
            return None
        if not self._dev_size:
            return None
        dev_w, dev_h = self._dev_size
        x = int((pos.x() - rect.x()) / rect.width() * dev_w)
        y = int((pos.y() - rect.y()) / rect.height() * dev_h)
        return max(0, min(dev_w - 1, x)), max(0, min(dev_h - 1, y))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#151515"))

        if self._pixmap and not self._pixmap.isNull():
            rect = self._image_rect()
            painter.drawPixmap(rect, self._pixmap,
                               QRectF(self._pixmap.rect()))
        else:
            painter.setPen(QColor("#666666"))
            painter.setFont(QFont("Microsoft YaHei", 11))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                self._placeholder
            )
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._press_pos is None:
            return

        release_pos = event.position().toPoint()
        delta = release_pos - self._press_pos
        start_dev = self._map_to_device(self._press_pos)
        end_dev = self._map_to_device(release_pos)

        is_click = abs(delta.x()) < self.CLICK_THRESHOLD
        is_click = is_click and abs(delta.y()) < self.CLICK_THRESHOLD

        if is_click:
            # 点击
            if end_dev:
                self.tap_requested.emit(*end_dev)
        else:
            # 滑动（长按视为起止坐标相同的滑动）
            if start_dev and end_dev:
                self.swipe_requested.emit(
                    start_dev[0], start_dev[1],
                    end_dev[0], end_dev[1], 300
                )
        self._press_pos = None


class ADBScreenDialog(QDialog):
    """
    ADB 屏幕投屏对话框
    -------------------
    实时刷新设备画面，支持点击/滑动、常用按键、截图保存。
    """

    # 工作线程抓帧完成后跨线程投递
    frame_ready = pyqtSignal(bytes)
    size_ready = pyqtSignal(object, object)              # (device, (w, h))
    app_info_ready = pyqtSignal(object, object, object)  # (device, package, activity)

    def __init__(self, device_manager: DeviceManager,
                 device: Optional[ADBDevice] = None, parent=None):
        super().__init__(parent)
        self._dm = device_manager
        self._device: Optional[ADBDevice] = None
        self._last_frame_bytes: Optional[bytes] = None
        self._last_pixmap: Optional[QPixmap] = None
        self._fetching = False

        self.setWindowTitle("ADB 屏幕投屏")
        self.resize(560, 820)
        allow_resize(self)
        self._init_ui()

        # 实时投屏：后台线程解码最新帧，UI 定时器取走渲染
        self._pending_frame = None          # ("img", QImage) | ("png", bytes)
        self._frame_lock = threading.Lock()
        self._streaming = False
        self._stream: Optional[LiveStream] = None
        self._stream_gen = 0
        self._last_status_ts = 0.0
        self._app_info_ts = 0.0
        self._app_info_busy = False

        self._render_timer = QTimer(self)
        self._render_timer.setInterval(11)  # 约 90 fps 渲染上限
        self._render_timer.timeout.connect(self._render_pending)

        # 单帧抓取线程 → 主线程处理
        self.frame_ready.connect(self._handle_frame)
        self.size_ready.connect(self._apply_screen_size)
        self.app_info_ready.connect(self._apply_foreground_app)

        # 默认选中指定设备或第一个已连接 ADB 设备
        self._refresh_device_combo()
        if device:
            idx = self._device_combo.findData(device.device_id)
            if idx >= 0:
                self._device_combo.setCurrentIndex(idx)
        self._on_device_changed(self._device_combo.currentIndex())

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 设备选择行
        top = QHBoxLayout()
        top.setSpacing(10)

        device_label = QLabel("设备:")
        device_label.setStyleSheet(f"color: {T.TEXT_SECONDARY}; font-size: {T.FONT_SIZE}pt;")
        top.addWidget(device_label)

        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(220)
        self._device_combo.currentIndexChanged.connect(
            self._on_device_changed
        )
        top.addWidget(self._device_combo, 1)

        self._btn_refresh_devs = QPushButton("刷新")
        self._btn_refresh_devs.clicked.connect(self._on_refresh_devices)
        top.addWidget(self._btn_refresh_devs)

        layout.addLayout(top)

        # 屏幕视图
        self._view = ADBScreenView()
        self._view.tap_requested.connect(self._on_tap)
        self._view.swipe_requested.connect(self._on_swipe)
        layout.addWidget(self._view, 1)

        # 状态栏
        self._status_label = QLabel("未开始")
        self._status_label.setStyleSheet(f"color: {T.TEXT_TERTIARY}; font-size: {T.FONT_SIZE_SM}pt;")
        layout.addWidget(self._status_label)

        # 前台应用信息
        self._app_info_label = QLabel("")
        self._app_info_label.setStyleSheet(f"color: {T.TEXT_SECONDARY}; font-size: {T.FONT_SIZE_SM}pt;")
        self._app_info_label.setWordWrap(True)
        self._app_info_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        app_row = QHBoxLayout()
        app_row.addWidget(self._app_info_label, 1)
        self._btn_copy_app = QPushButton("📋 复制包名")
        self._btn_copy_app.setEnabled(False)
        self._btn_copy_app.clicked.connect(self._copy_foreground_package)
        app_row.addWidget(self._btn_copy_app)
        layout.addLayout(app_row)
        self._foreground_package: Optional[str] = None

        # 控制按钮行 1
        row1 = QHBoxLayout()
        self._btn_start = QPushButton("开始投屏")
        self._btn_start.clicked.connect(self._on_toggle_stream)
        row1.addWidget(self._btn_start)

        self._btn_frame = QPushButton("单帧")
        self._btn_frame.clicked.connect(self._fetch_frame)
        row1.addWidget(self._btn_frame)

        self._btn_save = QPushButton("保存截图")
        self._btn_save.clicked.connect(self._on_save_screenshot)
        row1.addWidget(self._btn_save)

        row1.addWidget(QLabel("帧率上限:"))
        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 90)
        self._fps_spin.setValue(30)
        self._fps_spin.setSuffix(" 帧/秒")
        row1.addWidget(self._fps_spin)
        layout.addLayout(row1)

        # 控制按钮行 2（常用按键）
        row2 = QHBoxLayout()
        self._btn_back = QPushButton("返回")
        self._btn_back.clicked.connect(lambda: self._send_key(4))
        row2.addWidget(self._btn_back)
        self._btn_home = QPushButton("Home")
        self._btn_home.clicked.connect(lambda: self._send_key(3))
        row2.addWidget(self._btn_home)
        self._btn_apps = QPushButton("任务")
        self._btn_apps.clicked.connect(lambda: self._send_key(187))
        row2.addWidget(self._btn_apps)
        self._btn_power = QPushButton("电源")
        self._btn_power.clicked.connect(lambda: self._send_key(26))
        row2.addWidget(self._btn_power)
        layout.addLayout(row2)

    # ---------- 设备选择 ----------

    def _refresh_device_combo(self):
        """刷新已连接的 ADB 设备下拉框"""
        current_id = self._device_combo.currentData()
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        for device_id, device in self._dm.get_all_devices().items():
            if isinstance(device, ADBDevice):
                serial = device.config.get("device_serial") or device.device_id
                self._device_combo.addItem(serial, device_id)
        self._device_combo.blockSignals(False)

        if current_id is not None:
            idx = self._device_combo.findData(current_id)
            if idx >= 0:
                self._device_combo.setCurrentIndex(idx)

    def _on_refresh_devices(self):
        self._refresh_device_combo()
        self._on_device_changed(self._device_combo.currentIndex())

    def _on_device_changed(self, idx: int):
        device_id = self._device_combo.itemData(idx)
        device = self._dm.get_device(device_id) if device_id else None
        self._device = device if isinstance(device, ADBDevice) else None

        if self._device is None:
            self._view.clear_frame()
            self._status_label.setText("未选择 ADB 设备（请先连接）")
            self._app_info_label.setText("")
            self._foreground_package = None
            self._btn_copy_app.setEnabled(False)
            return

        # 丢弃上一设备的待渲染帧
        with self._frame_lock:
            self._pending_frame = None

        # 切换设备后重启画面流（抓帧线程绑定具体设备）
        if self._streaming:
            self._stop_stream()
            self._start_stream()

        self._fetch_screen_size(self._device)
        self._status_label.setText(f"设备: {self._device.device_id}")

        # 更新前台应用信息
        self._app_info_ts = 0.0
        self._update_foreground_app()

        # 切换设备后立即取一帧
        self._fetch_frame()

    def _fetch_screen_size(self, device: ADBDevice):
        """后台获取分辨率，避免阻塞 UI"""

        def worker():
            try:
                size = device.get_screen_size()
            except Exception as e:
                logger.debug(f"获取分辨率失败: {e}")
                return
            if size:
                try:
                    self.size_ready.emit(device, size)
                except RuntimeError:
                    pass   # 对话框已关闭

        threading.Thread(
            target=worker, daemon=True, name="ADBSize"
        ).start()

    def _apply_screen_size(self, device: ADBDevice, size):
        if self._device is device:
            self._view._dev_size = size
            if not self._streaming:
                self._status_label.setText(
                    f"设备: {device.device_id} | 分辨率: {size[0]}x{size[1]}"
                )

    def _update_foreground_app(self):
        """更新前台应用信息（后台执行，避免阻塞 UI 线程）"""
        if self._device is None:
            self._app_info_label.setText("")
            return
        now = time.time()
        if self._app_info_busy or now - self._app_info_ts < 2.0:
            return
        self._app_info_ts = now
        self._app_info_busy = True
        device = self._device

        def worker():
            try:
                package, activity = device.get_foreground_app()
            except Exception as e:
                logger.debug(f"更新前台应用信息失败: {e}")
                package, activity = None, None
            try:
                self.app_info_ready.emit(device, package, activity)
            except RuntimeError:
                pass   # 对话框已关闭

        threading.Thread(
            target=worker, daemon=True, name="ADBForeground"
        ).start()

    def _apply_foreground_app(self, device: ADBDevice,
                              package: Optional[str], activity: Optional[str]):
        self._app_info_busy = False
        if self._device is not device:
            return
        self._foreground_package = package
        if package and activity:
            self._app_info_label.setText(f"前台应用: {package}/{activity}")
        elif package:
            self._app_info_label.setText(f"前台应用: {package}")
        else:
            self._app_info_label.setText("前台应用: 未知")
        self._btn_copy_app.setEnabled(bool(package))

    def _copy_foreground_package(self):
        package = self._foreground_package
        if not package:
            return
        QApplication.clipboard().setText(package)
        self._status_label.setText(f"已复制包名: {package}")

    # ---------- 实时投屏 ----------

    def _on_toggle_stream(self):
        """开始 / 停止实时投屏"""
        if self._streaming:
            self._stop_stream()
            self._status_label.setText("投屏已停止")
        else:
            if self._device is None:
                QMessageBox.information(self, "提示", "请先连接并选择 ADB 设备")
                return
            self._start_stream()

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
        self._btn_start.setText("停止投屏")
        self._status_label.setText("正在连接画面流...")

    def _stop_stream(self):
        self._streaming = False
        self._stream_gen += 1
        stream = self._stream
        self._stream = None
        if stream is not None:
            stream.stop()
        self._render_timer.stop()
        if self._btn_start.text() == "停止投屏":
            self._btn_start.setText("开始投屏")

    def _publish_frame(self, kind: str, payload):
        """后台线程调用：只保留最新一帧"""
        with self._frame_lock:
            self._pending_frame = (kind, payload)

    def _stream_status_text(self) -> str:
        stream = self._stream
        mode = "H.264 实时流" if stream and stream.mode == LiveStream.MODE_H264 \
            else "截图模式"
        fps_actual = stream.fps if stream else 0.0
        text = (f"实时投屏中 | {mode} | 目标 {self._fps_spin.value()} fps | "
                f"实测 {fps_actual:.0f} fps")
        if fps_actual < 0.5:
            text += "（画面无变化）"
        return text

    def _render_pending(self):
        """渲染定时器：取走最新一帧并绘制（最新帧优先，不积压）"""
        now = time.time()
        if self._streaming and now - self._last_status_ts >= 0.5:
            self._last_status_ts = now
            self._status_label.setText(self._stream_status_text())
            self._update_foreground_app()

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
                logger.warning("无法解析屏幕截图数据")
                return
            self._last_frame_bytes = payload

        if pixmap.isNull():
            return
        self._last_pixmap = pixmap
        self._view.set_frame(pixmap, self._view._dev_size)

    # ---------- 单帧抓取 ----------

    def _fetch_frame(self):
        """在工作线程中抓取一帧画面（防止阻塞 UI）"""
        if self._device is None or self._fetching or self._streaming:
            return
        self._fetching = True
        device = self._device

        def worker():
            try:
                data = device.screencap_bytes()
                if data:
                    self.frame_ready.emit(data)
            except RuntimeError:
                # 对话框已关闭，控件在后台线程结束时已被销毁
                pass
            except Exception as e:
                logger.error(f"取帧异常: {e}")
            finally:
                self._fetching = False

        threading.Thread(target=worker, daemon=True,
                         name="ADBScreencap").start()

    def _handle_frame(self, data: bytes):
        """主线程处理单帧抓取结果"""
        if not data or self._streaming:
            # 实时流已接管画面时丢弃迟到的单帧结果
            return
        self._last_frame_bytes = data
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self._last_pixmap = pixmap
            self._view.set_frame(pixmap, self._view._dev_size)
            if not self._streaming:
                self._status_label.setText("已抓取单帧")
        else:
            logger.warning("无法解析屏幕截图数据")

    # ---------- 输入注入 ----------

    def _on_tap(self, x: int, y: int):
        if self._device:
            logger.info(f"点击注入: ({x}, {y})")
            threading.Thread(target=self._device.input_tap, args=(x, y),
                             daemon=True).start()

    def _on_swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int):
        if self._device:
            logger.info(f"滑动注入: ({x1},{y1}) -> ({x2},{y2}) {duration}ms")
            threading.Thread(target=self._device.input_swipe,
                             args=(x1, y1, x2, y2, duration),
                             daemon=True).start()

    def _send_key(self, code: int):
        if self._device:
            threading.Thread(target=self._device.keyevent, args=(code,),
                             daemon=True).start()

    # ---------- 截图保存 ----------

    def _on_save_screenshot(self):
        if not self._last_frame_bytes and self._last_pixmap is None:
            QMessageBox.information(self, "提示", "还没有画面帧，请先刷新")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存截图", "screenshot.png", "PNG (*.png)"
        )
        if not path:
            return
        if self._last_pixmap is not None:
            ok = self._last_pixmap.save(path, "PNG")
        else:
            with open(path, "wb") as f:
                f.write(self._last_frame_bytes)
            ok = True
        if ok:
            self._status_label.setText(f"截图已保存: {path}")
        else:
            QMessageBox.warning(self, "保存失败", f"无法写入截图文件: {path}")

    # ---------- 清理 ----------

    def showEvent(self, event):
        super().showEvent(event)
        # 打开投屏窗口即开始实时画面
        if self._device is not None and not self._streaming:
            self._start_stream()

    def closeEvent(self, event):
        self._stop_stream()
        event.accept()
        # 非模态 show() 关闭时不自动发 finished，主动触发以便主窗口清理引用
        self.done(QDialog.DialogCode.Rejected)

