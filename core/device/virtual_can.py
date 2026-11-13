"""
虚拟 CAN 总线通信模块
=====================
纯软件实现的 CAN/CAN FD 通信，用于开发和测试。

功能：
- CAN 2.0A/B 消息收发
- CAN FD 消息收发（最大 64 字节）
- 多通道支持
- 消息过滤
- 总线负载统计
- 消息录制与回放
"""

import time
import threading
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import List, Optional, Dict, Callable, Tuple
from collections import deque


class CANProtocol(IntEnum):
    """CAN 协议类型"""
    CAN_20A = 0      # CAN 2.0A (11-bit ID)
    CAN_20B = 1      # CAN 2.0B (29-bit ID)
    CAN_FD = 2       # CAN FD


@dataclass
class CANMessage:
    """CAN 消息"""
    id: int                          # CAN ID
    data: bytes                      # 数据（CAN 2.0: 0-8 字节, CAN FD: 0-64 字节）
    protocol: CANProtocol = CANProtocol.CAN_20B
    is_extended: bool = False        # 扩展帧
    is_remote: bool = False          # 远程帧
    bitrate_switch: bool = False     # CAN FD BRS
    timestamp: float = 0.0           # 时间戳
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        
        # 验证数据长度
        max_len = 64 if self.protocol == CANProtocol.CAN_FD else 8
        if len(self.data) > max_len:
            raise ValueError(f"数据长度 {len(self.data)} 超过最大限制 {max_len}")
    
    @property
    def dlc(self) -> int:
        """数据长度代码"""
        return len(self.data)
    
    def __repr__(self) -> str:
        proto = "FD" if self.protocol == CANProtocol.CAN_FD else "2.0"
        data_hex = ' '.join(f'{b:02X}' for b in self.data)
        return f"CAN{proto}(ID=0x{self.id:03X}, Data=[{data_hex}], DLC={self.dlc})"


@dataclass
class CANFilter:
    """CAN 过滤器"""
    id: int = 0                      # 过滤 ID
    mask: int = 0x1FFFFFFF           # 掩码
    is_extended: bool = False        # 扩展帧
    
    def match(self, msg: CANMessage) -> bool:
        """检查消息是否匹配过滤器"""
        if msg.is_extended != self.is_extended:
            return False
        return (msg.id & self.mask) == (self.id & self.mask)


@dataclass
class BusStatistics:
    """总线统计"""
    tx_count: int = 0                # 发送计数
    rx_count: int = 0                # 接收计数
    error_count: int = 0             # 错误计数
    start_time: float = 0.0          # 开始时间
    
    @property
    def total_count(self) -> int:
        return self.tx_count + self.rx_count
    
    @property
    def duration(self) -> float:
        if self.start_time == 0.0:
            return 0.0
        return time.time() - self.start_time
    
    @property
    def messages_per_second(self) -> float:
        dur = self.duration
        if dur == 0.0:
            return 0.0
        return self.total_count / dur
    
    def reset(self):
        self.tx_count = 0
        self.rx_count = 0
        self.error_count = 0
        self.start_time = time.time()


class VirtualCANChannel:
    """虚拟 CAN 通道"""
    
    def __init__(self, channel_id: int, baudrate: int = 500000, is_fd: bool = True):
        self.channel_id = channel_id
        self.baudrate = baudrate
        self.is_fd = is_fd
        self.connected = False
        
        # 消息队列
        self._tx_queue: deque = deque()
        self._rx_queue: deque = deque()
        self._lock = threading.Lock()
        
        # 过滤器
        self._filters: List[CANFilter] = []
        self._filter_enabled = False
        
        # 统计
        self.stats = BusStatistics()
        
        # 消息日志
        self._message_log: List[CANMessage] = []
        self._max_log_size = 10000
    
    def connect(self) -> bool:
        """连接通道"""
        self.connected = True
        self.stats.start_time = time.time()
        return True
    
    def disconnect(self):
        """断开通道"""
        self.connected = False
    
    def send(self, msg: CANMessage) -> bool:
        """发送消息"""
        if not self.connected:
            return False
        
        # 验证协议
        if msg.protocol == CANProtocol.CAN_FD and not self.is_fd:
            return False
        
        with self._lock:
            self._tx_queue.append(msg)
            self.stats.tx_count += 1
            
            # 回环模式：发送到接收队列
            self._rx_queue.append(msg)
            
            # 记录日志
            if len(self._message_log) < self._max_log_size:
                self._message_log.append(msg)
        
        return True
    
    def receive(self, timeout: float = 1.0, max_count: int = 100) -> List[CANMessage]:
        """接收消息"""
        if not self.connected:
            return []
        
        messages = []
        end_time = time.time() + timeout
        
        while time.time() < end_time and len(messages) < max_count:
            with self._lock:
                if self._rx_queue:
                    msg = self._rx_queue.popleft()
                    
                    # 应用过滤器
                    if self._filter_enabled and self._filters:
                        if not any(f.match(msg) for f in self._filters):
                            continue
                    
                    messages.append(msg)
                    self.stats.rx_count += 1
                else:
                    time.sleep(0.001)
        
        return messages
    
    def inject_message(self, msg: CANMessage):
        """注入消息（模拟外部 ECU）"""
        with self._lock:
            self._rx_queue.append(msg)
    
    def set_filter(self, filter_id: int, filter_mask: int, is_extended: bool = False):
        """设置过滤器"""
        with self._lock:
            self._filters.append(CANFilter(
                id=filter_id,
                mask=filter_mask,
                is_extended=is_extended
            ))
            self._filter_enabled = True
    
    def clear_filter(self):
        """清除过滤器"""
        with self._lock:
            self._filters.clear()
            self._filter_enabled = False
    
    def clear_buffer(self):
        """清空缓冲区"""
        with self._lock:
            self._rx_queue.clear()
    
    def get_message_log(self) -> List[CANMessage]:
        """获取消息日志"""
        with self._lock:
            return self._message_log.copy()
    
    def clear_message_log(self):
        """清空消息日志"""
        with self._lock:
            self._message_log.clear()


class VirtualCANBus:
    """
    虚拟 CAN 总线
    
    支持多通道、消息路由、总线负载统计。
    
    示例:
        bus = VirtualCANBus()
        ch0 = bus.add_channel(0, baudrate=500000, is_fd=True)
        ch0.connect()
        
        # 发送消息
        msg = CANMessage(id=0x123, data=b'\\x01\\x02\\x03\\x04')
        ch0.send(msg)
        
        # 接收消息
        msgs = ch0.receive(timeout=1.0)
        
        ch0.disconnect()
    """
    
    def __init__(self):
        self._channels: Dict[int, VirtualCANChannel] = {}
        self._lock = threading.Lock()
    
    def add_channel(self, channel_id: int, baudrate: int = 500000, 
                    is_fd: bool = True) -> VirtualCANChannel:
        """添加通道"""
        with self._lock:
            if channel_id in self._channels:
                raise ValueError(f"通道 {channel_id} 已存在")
            
            channel = VirtualCANChannel(channel_id, baudrate, is_fd)
            self._channels[channel_id] = channel
            return channel
    
    def remove_channel(self, channel_id: int):
        """移除通道"""
        with self._lock:
            if channel_id in self._channels:
                ch = self._channels[channel_id]
                if ch.connected:
                    ch.disconnect()
                del self._channels[channel_id]
    
    def get_channel(self, channel_id: int) -> Optional[VirtualCANChannel]:
        """获取通道"""
        return self._channels.get(channel_id)
    
    @property
    def channels(self) -> Dict[int, VirtualCANChannel]:
        """所有通道"""
        return self._channels.copy()
    
    def route_message(self, from_channel: int, to_channel: int, msg: CANMessage):
        """路由消息从一个通道到另一个通道"""
        src = self._channels.get(from_channel)
        dst = self._channels.get(to_channel)
        
        if src and dst and dst.connected:
            dst.inject_message(msg)
    
    def broadcast(self, msg: CANMessage, exclude_channel: Optional[int] = None):
        """广播消息到所有通道"""
        for ch_id, ch in self._channels.items():
            if ch_id != exclude_channel and ch.connected:
                ch.inject_message(msg)
    
    def get_statistics(self) -> Dict[int, BusStatistics]:
        """获取所有通道统计"""
        return {ch_id: ch.stats for ch_id, ch in self._channels.items()}
    
    def disconnect_all(self):
        """断开所有通道"""
        for ch in self._channels.values():
            ch.disconnect()


# 便捷函数
def create_can_bus(channels: List[Dict] = None) -> VirtualCANBus:
    """
    创建 CAN 总线
    
    参数:
        channels: 通道配置列表，例如：
            [
                {"id": 0, "baudrate": 500000, "is_fd": True},
                {"id": 1, "baudrate": 250000, "is_fd": False}
            ]
    """
    bus = VirtualCANBus()
    
    if channels:
        for ch_cfg in channels:
            bus.add_channel(
                channel_id=ch_cfg.get("id", 0),
                baudrate=ch_cfg.get("baudrate", 500000),
                is_fd=ch_cfg.get("is_fd", True)
            )
    
    return bus


# 测试代码
if __name__ == "__main__":
    print("虚拟 CAN 总线测试")
    print("=" * 60)
    
    # 创建总线
    bus = create_can_bus([
        {"id": 0, "baudrate": 500000, "is_fd": True},
        {"id": 1, "baudrate": 250000, "is_fd": False}
    ])
    
    ch0 = bus.get_channel(0)
    ch1 = bus.get_channel(1)
    
    ch0.connect()
    ch1.connect()
    
    # 测试 1: 发送和接收（回环）
    print("\n1. 回环测试")
    msg = CANMessage(id=0x123, data=b'\x01\x02\x03\x04\x05\x06\x07\x08')
    ch0.send(msg)
    print(f"   发送: {msg}")
    
    rx_msgs = ch0.receive(timeout=0.1)
    for rx in rx_msgs:
        print(f"   接收: {rx}")
    
    # 测试 2: CAN FD 消息
    print("\n2. CAN FD 消息（64 字节）")
    fd_msg = CANMessage(
        id=0x200,
        data=b'\xAA\xBB\xCC\xDD' * 16,
        protocol=CANProtocol.CAN_FD,
        bitrate_switch=True
    )
    ch0.send(fd_msg)
    print(f"   发送: {fd_msg}")
    
    rx_msgs = ch0.receive(timeout=0.1)
    for rx in rx_msgs:
        print(f"   接收: {rx}")
    
    # 测试 3: 通道间路由
    print("\n3. 通道间路由")
    route_msg = CANMessage(id=0x300, data=b'\x10\x20\x30\x40')
    ch0.send(route_msg)
    bus.route_message(0, 1, route_msg)
    print(f"   通道 0 发送: {route_msg}")
    
    rx_msgs = ch1.receive(timeout=0.1)
    for rx in rx_msgs:
        print(f"   通道 1 接收: {rx}")
    
    # 测试 4: 过滤器
    print("\n4. 过滤器测试")
    ch0.set_filter(0x100, 0x700)  # 只接收 ID 0x100-0x1FF
    ch0.clear_buffer()
    
    ch0.send(CANMessage(id=0x123, data=b'\x01'))
    ch0.send(CANMessage(id=0x200, data=b'\x02'))  # 应该被过滤
    
    rx_msgs = ch0.receive(timeout=0.1)
    print(f"   收到 {len(rx_msgs)} 帧（预期 1 帧）")
    for rx in rx_msgs:
        print(f"   接收: {rx}")
    
    ch0.clear_filter()
    
    # 测试 5: 统计
    print("\n5. 总线统计")
    stats = bus.get_statistics()
    for ch_id, stat in stats.items():
        print(f"   通道 {ch_id}: TX={stat.tx_count}, RX={stat.rx_count}, Total={stat.total_count}")
    
    # 清理
    bus.disconnect_all()
    print("\n测试完成")
