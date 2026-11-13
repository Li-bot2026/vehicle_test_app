"""
设备连接管理 - 基类与设备管理器
================================
定义所有设备类型的统一接口，以及设备管理器负责设备的发现、连接与生命周期管理。

设计说明：
- BaseDevice 是所有通信设备的抽象基类，统一 connect / disconnect / send / recv 接口
- DeviceManager 维护已连接设备的注册表，支持自动扫描、断线重连、状态广播
- 所有设备操作均为异步安全（通过信号槽与 Qt 事件循环集成）
"""

import time
import threading
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable
from datetime import datetime

from loguru import logger
from PyQt6.QtCore import QObject, pyqtSignal, QThread


# ============================================================
# 枚举与数据类
# ============================================================

class DeviceType(Enum):
    """设备类型枚举"""
    SERIAL = "Serial"
    NETWORK_TCP = "TCP"
    NETWORK_UDP = "UDP"
    ADB = "ADB"


class DeviceStatus(Enum):
    """设备状态枚举"""
    DISCONNECTED = "未连接"
    CONNECTING = "连接中"
    CONNECTED = "已连接"
    ERROR = "异常"
    RECONNECTING = "重连中"


@dataclass
class DeviceInfo:
    """设备描述信息"""
    device_id: str                          # 唯一标识
    device_type: DeviceType                 # 设备类型
    name: str = ""                          # 显示名称
    port: str = ""                          # 端口/通道
    status: DeviceStatus = DeviceStatus.DISCONNECTED
    last_seen: Optional[datetime] = None    # 最后活跃时间
    extra: Dict = field(default_factory=dict)  # 扩展属性


class BaseDevice(ABC):
    """
    设备抽象基类
    ------------
    所有通信设备（串口 / 网络 / ADB）必须实现此接口。
    """

    def __init__(self, device_id: str, config: dict):
        self.device_id = device_id
        self.config = config
        self._status = DeviceStatus.DISCONNECTED
        self._callbacks: List[Callable] = []
        self._lock = threading.Lock()

    @property
    def status(self) -> DeviceStatus:
        return self._status

    @status.setter
    def status(self, value: DeviceStatus):
        old = self._status
        self._status = value
        if old != value:
            self._notify_status_change(old, value)

    @abstractmethod
    def connect(self) -> bool:
        """连接设备，返回是否成功"""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接"""
        ...

    @abstractmethod
    def send(self, data: bytes) -> bool:
        """发送数据，返回是否成功"""
        ...

    @abstractmethod
    def recv(self, timeout: float = 1.0) -> Optional[bytes]:
        """接收数据，超时返回 None"""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """检查连接状态"""
        ...

    def on_status_change(self, callback: Callable):
        """注册状态变化回调"""
        self._callbacks.append(callback)

    def _notify_status_change(self, old: DeviceStatus, new: DeviceStatus):
        """通知所有监听者"""
        for cb in self._callbacks:
            try:
                cb(self.device_id, old, new)
            except Exception as e:
                logger.error(f"状态回调异常: {e}")


# ============================================================
# 设备管理器
# ============================================================

class DeviceManagerSignals(QObject):
    """设备管理器的 Qt 信号（用于跨线程通知 UI）"""
    # 设备列表变化
    device_list_changed = pyqtSignal()
    # 设备状态变化 (device_id, old_status, new_status)
    device_status_changed = pyqtSignal(str, str, str)
    # 收到数据 (device_id, data)
    data_received = pyqtSignal(str, bytes)
    # 错误信息 (device_id, error_msg)
    error_occurred = pyqtSignal(str, str)
    # 扫描完成 (found_count, device_infos)
    scan_completed = pyqtSignal(int, list)


class DeviceManager:
    """
    设备管理器
    ----------
    职责：
    1. 自动扫描可用设备（串口 / 网络 / ADB）
    2. 管理设备的连接、断开、重连
    3. 维护设备注册表，提供查询接口
    4. 广播设备状态变化事件
    """

    def __init__(self):
        self.signals = DeviceManagerSignals()
        self._devices: Dict[str, BaseDevice] = {}
        self._scan_thread: Optional[threading.Thread] = None
        self._reconnect_timers: Dict[str, float] = {}
        self._running = True

        # 最近一次扫描结果（供 UI 查询）
        self.last_scan_results: List[DeviceInfo] = []

        # 自动重连配置
        self._auto_reconnect = True
        self._reconnect_interval = 5.0  # 秒

    # ---------- 设备扫描 ----------

    def scan_devices(self) -> List[DeviceInfo]:
        """
        扫描所有类型的可用设备。
        在后台线程中执行，完成后通过信号通知。
        """
        found_devices = []

        # 扫描串口
        found_devices.extend(self._scan_serial_ports())

        # 扫描 ADB 设备
        found_devices.extend(self._scan_adb_devices())

        # 始终提供虚拟设备（无硬件环境下的调试/演示）
        found_devices.extend(self._get_virtual_devices())

        self.last_scan_results = found_devices
        self.signals.scan_completed.emit(len(found_devices), found_devices)
        logger.info(f"设备扫描完成，发现 {len(found_devices)} 个设备")
        return found_devices

    def _get_virtual_devices(self) -> List[DeviceInfo]:
        """
        虚拟设备列表——无需真实硬件即可使用的调试设备：
        - 虚拟串口: pyserial loop:// 回环
        - 本地回环 TCP/UDP
        """
        devices = [
            DeviceInfo(
                device_id="serial_loop",
                device_type=DeviceType.SERIAL,
                name="虚拟串口 (loop://)",
                port="loop://",
                extra={"is_virtual": True, "baudrate": 115200}
            ),
            DeviceInfo(
                device_id="udp_loopback",
                device_type=DeviceType.NETWORK_UDP,
                name="本地回环 UDP (127.0.0.1:5555)",
                port="127.0.0.1:5555",
                extra={
                    "is_virtual": True, "protocol": "udp",
                    "host": "127.0.0.1", "port": 5555
                }
            ),
            DeviceInfo(
                device_id="tcp_loopback",
                device_type=DeviceType.NETWORK_TCP,
                name="本地回环 TCP (127.0.0.1:5556)",
                port="127.0.0.1:5556",
                extra={
                    "is_virtual": True, "protocol": "tcp",
                    "host": "127.0.0.1", "port": 5556
                }
            ),
        ]
        return devices

    def scan_devices_async(self):
        """异步扫描（在后台线程中执行）"""
        if self._scan_thread and self._scan_thread.is_alive():
            logger.warning("扫描正在进行中")
            return
        self._scan_thread = threading.Thread(
            target=self.scan_devices, daemon=True, name="DeviceScanner"
        )
        self._scan_thread.start()

    def _scan_serial_ports(self) -> List[DeviceInfo]:
        """扫描可用串口"""
        devices = []
        try:
            import serial.tools.list_ports
            for port_info in serial.tools.list_ports.comports():
                devices.append(DeviceInfo(
                    device_id=f"serial_{port_info.device}",
                    device_type=DeviceType.SERIAL,
                    name=f"{port_info.description} ({port_info.device})",
                    port=port_info.device,
                    extra={"vid": port_info.vid, "pid": port_info.pid}
                ))
        except Exception as e:
            logger.error(f"串口扫描失败: {e}")
        return devices

    def _scan_adb_devices(self) -> List[DeviceInfo]:
        """扫描 ADB 连接的设备"""
        devices = []
        try:
            import subprocess
            import sys
            _no_window = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(
                ["adb", "devices"],
                capture_output=True, text=True, timeout=5,
                creationflags=_no_window,
            )
            for line in result.stdout.strip().split("\n")[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    serial = parts[0]
                    devices.append(DeviceInfo(
                        device_id=f"adb_{serial}",
                        device_type=DeviceType.ADB,
                        name=f"ADB Device ({serial})",
                        port=serial,
                        extra={"serial": serial}
                    ))
        except Exception as e:
            logger.debug(f"ADB 扫描失败（可能未安装 ADB）: {e}")
        return devices

    # ---------- 设备连接管理 ----------

    def connect_device(self, device: BaseDevice) -> bool:
        """
        连接一个设备并注册到管理器。
        """
        device_id = device.device_id

        # 已连接则直接返回成功
        if device_id in self._devices:
            logger.debug(f"设备已连接，跳过: {device_id}")
            return True

        # 注册状态回调
        device.on_status_change(self._on_device_status_change)

        # 尝试连接
        device.status = DeviceStatus.CONNECTING
        try:
            success = device.connect()
            if success:
                device.status = DeviceStatus.CONNECTED
                self._devices[device_id] = device
                self.signals.device_status_changed.emit(
                    device_id, DeviceStatus.CONNECTING.value,
                    DeviceStatus.CONNECTED.value
                )
                self.signals.device_list_changed.emit()
                logger.info(f"设备已连接: {device_id}")
            else:
                device.status = DeviceStatus.ERROR
                logger.warning(f"设备连接失败: {device_id}")
            return success
        except Exception as e:
            device.status = DeviceStatus.ERROR
            self.signals.error_occurred.emit(device_id, str(e))
            logger.error(f"设备连接异常: {device_id} - {e}")
            return False

    def create_device_from_info(self, info: DeviceInfo) -> Optional[BaseDevice]:
        """
        根据扫描信息创建设备实例（未连接）。
        供 UI 连接面板与执行引擎复用。
        """
        from .serial_device import SerialDevice
        from .network_device import NetworkDevice
        from .adb_device import ADBDevice

        extra = dict(info.extra)
        try:
            if info.device_type == DeviceType.SERIAL:
                config = {
                    "port": info.port or extra.get("port", "loop://"),
                    "baudrate": extra.get("baudrate", 115200),
                }
                return SerialDevice(info.device_id, config)
            elif info.device_type in (DeviceType.NETWORK_TCP, DeviceType.NETWORK_UDP):
                config = {
                    "protocol": "tcp" if info.device_type == DeviceType.NETWORK_TCP else "udp",
                    "host": extra.get("host", "127.0.0.1"),
                    "port": extra.get("port", 8080),
                    "local_port": extra.get("local_port", 0),
                }
                return NetworkDevice(info.device_id, config)
            elif info.device_type == DeviceType.ADB:
                config = {"device_serial": extra.get("serial", "")}
                return ADBDevice(info.device_id, config)
        except Exception as e:
            logger.error(f"创建设备失败: {e}")
        return None

    def auto_connect(self, device_id: str) -> Optional[BaseDevice]:
        """
        按需自动连接设备：从扫描结果中查找设备并连接。
        用于执行引擎在用例引用设备时自动建立连接。
        """
        if not device_id:
            return None

        # 已连接直接返回
        device = self._devices.get(device_id)
        if device:
            return device

        # 查找扫描结果（未扫描过则先扫描）
        if not self.last_scan_results:
            self.scan_devices()
        info = next(
            (d for d in self.last_scan_results if d.device_id == device_id),
            None
        )
        if info is None:
            logger.warning(f"未在扫描结果中找到设备: {device_id}")
            return None

        device = self.create_device_from_info(info)
        if device and self.connect_device(device):
            return device
        return None

    def disconnect_device(self, device_id: str) -> None:
        """断开指定设备"""
        device = self._devices.get(device_id)
        if device:
            try:
                device.disconnect()
                device.status = DeviceStatus.DISCONNECTED
                logger.info(f"设备已断开: {device_id}")
            except Exception as e:
                logger.error(f"设备断开异常: {device_id} - {e}")
            finally:
                del self._devices[device_id]
                self.signals.device_list_changed.emit()

    def disconnect_all(self) -> None:
        """断开所有设备"""
        for device_id in list(self._devices.keys()):
            self.disconnect_device(device_id)

    # ---------- 查询接口 ----------

    def get_device(self, device_id: str) -> Optional[BaseDevice]:
        """获取指定设备"""
        return self._devices.get(device_id)

    def get_all_devices(self) -> Dict[str, BaseDevice]:
        """获取所有已连接设备"""
        return dict(self._devices)

    def get_connected_count(self) -> int:
        """获取已连接设备数量"""
        return len(self._devices)

    # ---------- 自动重连 ----------

    def _on_device_status_change(self, device_id: str,
                                  old: DeviceStatus, new: DeviceStatus):
        """处理设备状态变化"""
        self.signals.device_status_changed.emit(
            device_id, old.value, new.value
        )

        # 如果断开且开启了自动重连，则安排重连
        if (new == DeviceStatus.ERROR and self._auto_reconnect
                and device_id in self._devices):
            self._reconnect_timers[device_id] = time.time()

    def _check_reconnect(self):
        """检查需要重连的设备（由主循环定时调用）"""
        now = time.time()
        for device_id, last_time in list(self._reconnect_timers.items()):
            if now - last_time >= self._reconnect_interval:
                device = self._devices.get(device_id)
                if device:
                    logger.info(f"尝试重连: {device_id}")
                    device.status = DeviceStatus.RECONNECTING
                    try:
                        if device.connect():
                            device.status = DeviceStatus.CONNECTED
                            self._reconnect_timers.pop(device_id, None)
                            logger.info(f"重连成功: {device_id}")
                        else:
                            device.status = DeviceStatus.ERROR
                            self._reconnect_timers[device_id] = now
                    except Exception as e:
                        logger.error(f"重连失败: {device_id} - {e}")
                        device.status = DeviceStatus.ERROR
                        self._reconnect_timers[device_id] = now

    # ---------- 清理 ----------

    def shutdown(self):
        """关闭管理器，断开所有设备"""
        self._running = False
        self.disconnect_all()
        logger.info("设备管理器已关闭")
