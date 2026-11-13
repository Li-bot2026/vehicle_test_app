"""
ADB 实时画面流
==============
优先使用 screenrecord 输出 H.264 裸流并实时解码（延迟约 0.2~0.4s），
解码器不可用时回退到 screencap 轮询（大分辨率下只有约 1 fps）。

线程模型：
- 后台线程持续解码，只把最新一帧通过 publish 回调交给 UI
- UI 定时器取走待渲染帧（见各界面 _render_pending）
"""

import os
import queue
import threading
import time
from collections import deque
from typing import Callable, Optional

from PyQt6.QtGui import QImage
from loguru import logger

try:
    import av
    import numpy as np
    H264_IMPORT_ERROR: Optional[Exception] = None
except Exception as e:      # 缺少解码依赖时静默回退
    av = None
    np = None
    H264_IMPORT_ERROR = e

# screencap 原始像素格式 → QImage 格式 / 每像素字节数
RAW_FORMATS = {
    1: (QImage.Format.Format_RGBA8888, 4),
    2: (QImage.Format.Format_RGBX8888, 4),
    3: (QImage.Format.Format_RGB16, 2),
    4: (QImage.Format.Format_ARGB32, 4),
    5: (QImage.Format.Format_RGB888, 3),
}


def qimage_from_frame(frame) -> Optional[QImage]:
    """把 ADBDevice.screencap_frame() 的原始帧转为 QImage（拷贝，可跨线程用）"""
    if not frame:
        return None
    width, height, fmt, data = frame
    qfmt, bpp = RAW_FORMATS.get(fmt, (None, 0))
    if qfmt is None:
        return None
    stride = width * bpp
    if width <= 0 or height <= 0 or len(data) < stride * height:
        return None
    return QImage(data, width, height, stride, qfmt).copy()


def qimage_from_h264_frame(frame) -> Optional[QImage]:
    """把 PyAV 解码出的视频帧转为 RGB888 QImage"""
    try:
        arr = frame.to_ndarray(format="rgb24")
    except Exception as e:
        logger.debug(f"H.264 帧转换失败: {e}")
        return None
    height, width = arr.shape[:2]
    if width <= 0 or height <= 0:
        return None
    return QImage(arr.data, width, height, arr.strides[0],
                  QImage.Format.Format_RGB888).copy()


class LiveStream:
    """
    ADB 实时画面流（后台线程）
    --------------------------
    publish(kind, payload): kind 为 "img"(QImage) 或 "png"(bytes)
    is_active(): 返回流是否仍然有效（界面隐藏/切换设备后为 False，线程自行退出）
    """

    MODE_H264 = "h264"
    MODE_SCREENCAP = "screencap"

    # 管道空闲多久后把解析器里挂起的最后一帧刷出来（H.264 裸流末帧要等下个起始码）
    FLUSH_IDLE = 0.15

    def __init__(self, device, publish: Callable[[str, object], None],
                 is_active: Callable[[], bool]):
        self._device = device
        self._publish = publish
        self._is_active = is_active

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._proc = None
        self._proc_lock = threading.Lock()
        self._remote_pid: Optional[int] = None
        self._pid_lock = threading.Lock()

        self._pub_times = deque(maxlen=240)
        self._fps = 0.0
        self.mode = ""
        self.max_width = 1280

    # ---------- 生命周期 ----------

    def start(self, fps_getter: Callable[[], int], max_width: Optional[int] = None):
        if max_width:
            self.max_width = max_width
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, args=(fps_getter,),
            daemon=True, name="ADBLiveStream"
        )
        self._thread.start()

    def stop(self, timeout: float = 1.0):
        self._stop_event.set()
        self._kill_proc()
        # 设备端 screenrecord 可能没随管道关闭而退出，按 PID 精确清理
        with self._pid_lock:
            pid, self._remote_pid = self._remote_pid, None
        if pid:
            threading.Thread(
                target=self._device.kill_remote_pid, args=(pid,),
                daemon=True, name="ADBKillRemote"
            ).start()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _alive(self) -> bool:
        return not self._stop_event.is_set() and self._is_active()

    def _kill_proc(self):
        with self._proc_lock:
            proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    # ---------- 运行 ----------

    def _run(self, fps_getter: Callable[[], int]):
        if H264_IMPORT_ERROR is None:
            try:
                if self._h264_loop(fps_getter):
                    return
            except Exception as e:
                logger.warning(f"H.264 投屏异常，回退截图模式: {e}")
        else:
            logger.debug(f"未启用 H.264 投屏: {H264_IMPORT_ERROR}")
        self._screencap_loop(fps_getter)

    def _h264_loop(self, fps_getter) -> bool:
        """返回 True 表示成功跑过 H.264 流；False 表示应回退截图模式"""
        self.mode = self.MODE_H264
        delivered = False
        empty_sessions = 0

        while self._alive():
            proc = self._device.screenrecord_stream(max_width=self.max_width)
            if proc is None:
                break
            with self._proc_lock:
                self._proc = proc
            try:
                codec = av.CodecContext.create("h264", "r")
                codec.thread_type = av.codec.context.ThreadType.NONE
            except Exception as e:
                logger.warning(f"创建 H.264 解码器失败: {e}")
                self._kill_proc()
                break

            chunks: "queue.Queue[Optional[bytes]]" = queue.Queue()
            with self._pid_lock:
                self._remote_pid = None
            threading.Thread(
                target=self._pump, args=(proc, chunks),
                daemon=True, name="ADBH264Read"
            ).start()

            session_frames = 0
            session_start = time.time()
            last_pub = 0.0
            broken = False

            def feed(data: bytes):
                nonlocal session_frames, last_pub, broken
                try:
                    for packet in codec.parse(data):
                        for frame in codec.decode(packet):
                            session_frames += 1
                            last_pub = self._maybe_publish(
                                frame, last_pub, fps_getter
                            )
                except Exception as e:
                    # 解析到被截断的帧：重连拿新的关键帧，避免画面花屏
                    logger.debug(f"H.264 解码异常，重连画面流: {e}")
                    broken = True

            while self._alive() and not broken:
                try:
                    chunk = chunks.get(timeout=self.FLUSH_IDLE)
                except queue.Empty:
                    feed(b"")       # 空闲刷新挂起的最后一帧
                    continue
                if chunk is None:
                    break
                feed(chunk)

            self._kill_proc()
            try:
                proc.wait(timeout=0.5)
            except Exception:
                pass

            if session_frames:
                delivered = True
                empty_sessions = 0
            elif broken:
                empty_sessions = 0
            else:
                empty_sessions += 1
                # 连续两轮都没有画面才判定 screenrecord 不可用（静态画面也会少帧）
                if not delivered and empty_sessions >= 2:
                    logger.warning("screenrecord 无画面输出，回退截图模式")
                    return False

            if not self._alive():
                break
            # time-limit 到期重连；连续失败时退避，避免空转
            if empty_sessions:
                time.sleep(min(2.0, 0.3 * empty_sessions))
            elif time.time() - session_start < 0.5:
                time.sleep(0.2)

        return delivered

    def _pump(self, proc, chunks: "queue.Queue[Optional[bytes]]"):
        fd = proc.stdout.fileno()
        header = b""
        while b"\n" not in header and len(header) <= 32:
            try:
                b = os.read(fd, 1)
            except OSError:
                b = b""
            if not b:
                break
            header += b
        try:
            pid = int(header.strip())
        except ValueError:
            pid = None
            chunks.put(header)      # 非预期输出，原样交给解码器
        if pid:
            with self._pid_lock:
                self._remote_pid = pid
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.put(chunk)
        chunks.put(None)

    def _maybe_publish(self, frame, last_pub: float,
                       fps_getter: Callable[[], int]) -> float:
        now = time.time()
        interval = 1.0 / max(1, fps_getter())
        if now - last_pub < interval * 0.85:
            return last_pub
        img = qimage_from_h264_frame(frame)
        if img is None:
            return last_pub
        try:
            self._publish_frame("img", img)
        except Exception as e:
            logger.warning(f"画面帧投递失败: {e}")
        return now

    def _screencap_loop(self, fps_getter):
        self.mode = self.MODE_SCREENCAP
        while self._alive():
            interval = 1.0 / max(1, fps_getter())
            start = time.time()
            try:
                img = qimage_from_frame(self._device.screencap_frame())
                if img is not None:
                    self._publish_frame("img", img)
                else:
                    data = self._device.screencap_bytes()
                    if data:
                        self._publish_frame("png", data)
            except Exception as e:
                logger.debug(f"抓帧异常: {e}")
            self._stop_event.wait(max(0.0, interval - (time.time() - start)))

    def _publish_frame(self, kind: str, payload):
        now = time.time()
        self._pub_times.append(now)
        while self._pub_times and now - self._pub_times[0] > 1.0:
            self._pub_times.popleft()
        self._fps = float(len(self._pub_times))
        self._publish(kind, payload)
