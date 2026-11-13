"""
网络设备（TCP / UDP）
=====================
基于 Python socket 实现 TCP/UDP 通信，用于与车机的以太网接口交互。

核心功能：
- TCP 客户端连接（可靠传输）
- UDP 客户端通信（低延迟）
- 自动重连（TCP）
- 心跳检测
"""

import socket
import time
import threading
from typing import Optional, Callable, Tuple

from loguru import logger

from .manager import BaseDevice, DeviceStatus


class NetworkDevice(BaseDevice):
    """
    网络通信设备
    -------------
    支持 TCP 和 UDP 两种协议。
    """

    def __init__(self, device_id: str, config: dict):
        """
        参数:
            config:
                - protocol: "tcp" 或 "udp"
                - host: 目标 IP 地址
                - port: 目标端口
                - local_port: 本地端口（UDP 用，0 表示自动分配）
                - timeout: 连接/读取超时（秒）
                - heartbeat: 是否启用心跳
                - heartbeat_interval: 心跳间隔（秒）
                - buffer_size: 接收缓冲区大小
        """
        super().__init__(device_id, config)
        self._socket: Optional[socket.socket] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._running = False
        self._on_data: Optional[Callable[[bytes, Tuple], None]] = None
        self._buffer_size = config.get("buffer_size", 4096)
        self._rx_buffer = bytearray()

    # ---------- 设备接口 ----------

    def connect(self) -> bool:
        """建立网络连接"""
        protocol = self.config.get("protocol", "tcp").lower()
        host = self.config.get("host", "127.0.0.1")
        port = self.config.get("port", 8080)
        timeout = self.config.get("timeout", 5.0)

        try:
            if protocol == "tcp":
                return self._connect_tcp(host, port, timeout)
            elif protocol == "udp":
                return self._connect_udp(host, port)
            else:
                logger.error(f"不支持的协议: {protocol}")
                return False
        except Exception as e:
            logger.error(f"网络连接失败: {e}")
            return False

    def _connect_tcp(self, host: str, port: int, timeout: float) -> bool:
        """建立 TCP 连接"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(timeout)
        self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        try:
            self._socket.connect((host, port))
            self._running = True

            # 启动接收线程
            self._recv_thread = threading.Thread(
                target=self._tcp_recv_loop, daemon=True,
                name=f"TCP_RX_{self.device_id}"
            )
            self._recv_thread.start()

            # 启动心跳（如果启用）
            if self.config.get("heartbeat", False):
                self._heartbeat_thread = threading.Thread(
                    target=self._heartbeat_loop, daemon=True,
                    name=f"TCP_HB_{self.device_id}"
                )
                self._heartbeat_thread.start()

            logger.info(f"TCP 已连接: {host}:{port}")
            return True

        except Exception as e:
            logger.error(f"TCP 连接失败: {host}:{port} - {e}")
            self._socket = None
            return False

    def _connect_udp(self, host: str, port: int) -> bool:
        """建立 UDP 连接"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        local_port = self.config.get("local_port", 0)
        if local_port > 0:
            self._socket.bind(("", local_port))

        self._socket.settimeout(self.config.get("timeout", 5.0))
        self._running = True
        self._target = (host, port)

        # 启动接收线程
        self._recv_thread = threading.Thread(
            target=self._udp_recv_loop, daemon=True,
            name=f"UDP_RX_{self.device_id}"
        )
        self._recv_thread.start()

        logger.info(f"UDP 已绑定: 本地端口 {local_port or '自动'}, 目标 {host}:{port}")
        return True

    def disconnect(self) -> None:
        """关闭网络连接"""
        self._running = False

        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=2.0)
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)

        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        logger.info(f"网络已断开: {self.device_id}")

    def send(self, data: bytes) -> bool:
        """发送数据"""
        if not self._socket:
            return False

        protocol = self.config.get("protocol", "tcp").lower()
        try:
            if protocol == "tcp":
                self._socket.sendall(data)
            else:
                target = self._target or (
                    self.config.get("host", "127.0.0.1"),
                    self.config.get("port", 8080)
                )
                self._socket.sendto(data, target)
            return True
        except Exception as e:
            logger.error(f"网络发送失败: {e}")
            return False

    def recv(self, timeout: float = 1.0) -> Optional[bytes]:
        """接收数据（从后台接收线程的缓冲区消费，阻塞到超时）"""
        if not self._running:
            return None
        deadline = time.time() + max(0.0, timeout)
        while True:
            if self._rx_buffer:
                data = bytes(self._rx_buffer)
                self._rx_buffer.clear()
                return data
            if time.time() >= deadline:
                return None
            time.sleep(0.01)

    def is_connected(self) -> bool:
        """检查连接状态"""
        if not self._socket or not self._running:
            return False

        # TCP 额外检查
        if self.config.get("protocol", "tcp").lower() == "tcp":
            try:
                self._socket.settimeout(0.1)
                self._socket.recv(1)
                # 如果收到空数据说明对端关闭
                return True
            except socket.timeout:
                return True  # 超时说明连接正常
            except Exception:
                return False
        return True

    # ---------- 扩展功能 ----------

    def set_data_callback(self, callback: Callable[[bytes, Tuple], None]):
        """设置数据接收回调 (data, sender_addr)"""
        self._on_data = callback

    def get_socket_info(self) -> dict:
        """获取连接信息"""
        if self._socket:
            try:
                local = self._socket.getsockname()
                remote = self._socket.getpeername() if self.config.get("protocol") == "tcp" else None
                return {
                    "local": f"{local[0]}:{local[1]}",
                    "remote": f"{remote[0]}:{remote[1]}" if remote else None,
                    "protocol": self.config.get("protocol", "tcp"),
                }
            except Exception:
                pass
        return {}

    # ---------- 内部方法 ----------

    def _tcp_recv_loop(self):
        """TCP 接收循环"""
        while self._running and self._socket:
            try:
                data = self._socket.recv(self._buffer_size)
                if not data:
                    logger.warning("TCP 连接已关闭（对端）")
                    self.status = DeviceStatus.ERROR
                    break
                self._rx_buffer.extend(data)
                if len(self._rx_buffer) > 1_000_000:
                    del self._rx_buffer[:-100_000]
                if self._on_data:
                    self._on_data(data, ("", 0))
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"TCP 接收异常: {e}")
                    self.status = DeviceStatus.ERROR
                break

    def _udp_recv_loop(self):
        """UDP 接收循环"""
        while self._running and self._socket:
            try:
                data, addr = self._socket.recvfrom(self._buffer_size)
                if data:
                    self._rx_buffer.extend(data)
                    if len(self._rx_buffer) > 1_000_000:
                        del self._rx_buffer[:-100_000]
                    if self._on_data:
                        self._on_data(data, addr)
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"UDP 接收异常: {e}")
                break

    def _heartbeat_loop(self):
        """TCP 心跳循环"""
        interval = self.config.get("heartbeat_interval", 10.0)
        heartbeat_data = self.config.get("heartbeat_data", b"\x00")
        while self._running and self._socket:
            try:
                time.sleep(interval)
                if self._running:
                    self._socket.sendall(heartbeat_data)
            except Exception as e:
                if self._running:
                    logger.warning(f"心跳发送失败: {e}")
                break
