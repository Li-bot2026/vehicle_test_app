"""
ADB 设备
=========
通过 Android Debug Bridge (ADB) 与车机的 Android 系统交互。

核心功能：
- ADB 连接与命令执行
- Shell 命令远程执行
- 文件推拉（push / pull）
- 应用安装与卸载
- 截图与录屏
- Logcat 日志抓取
"""

import base64
import os
import re
import shlex
import struct
import subprocess
import sys
import threading
import time
from typing import Optional, List, Tuple, Callable, Dict
from pathlib import Path

# Windows 下隐藏 subprocess 控制台窗口
if sys.platform == "win32":
    _CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    _CREATE_NO_WINDOW = 0

from loguru import logger

from .manager import BaseDevice, DeviceStatus
from .ui_automation import (
    UiNode, parse_dump, find_nodes, dedupe_nodes, best_node, visible_texts
)


# 渲染表面占位文字（游戏/视频等 SurfaceView 画面常见）
_SURFACE_LABELS = {"game view", "surfaceview", "surface", "video view"}


def _is_surface_only(nodes) -> bool:
    """判断控件树是否只是渲染表面（SurfaceView/TextureView），没有可读文字"""
    if not nodes:
        return False
    has_surface = any(
        "surfaceview" in (n.cls or "").lower()
        or "textureview" in (n.cls or "").lower()
        or (n.desc or "").strip().lower() in _SURFACE_LABELS
        for n in nodes
    )
    if not has_surface:
        return False
    # 除渲染表面外，没有其它带文字或可点击标签的节点
    labeled = [
        n for n in nodes
        if (n.text or "").strip()
        or (n.clickable and (n.label or "").strip())
    ]
    return len(labeled) <= 1


class ADBDevice(BaseDevice):
    """
    ADB 通信设备
    -------------
    通过 adb 命令行工具与车机 Android 系统交互。
    """

    def __init__(self, device_id: str, config: dict):
        """
        参数:
            config:
                - device_serial: 设备序列号（留空自动选择）
                - adb_path: adb 可执行文件路径（默认 "adb"）
                - host: ADB server 地址
                - port: ADB server 端口
        """
        super().__init__(device_id, config)
        self._adb_path = config.get("adb_path", "adb")
        self._serial = config.get("device_serial", "")
        self._logcat_thread: Optional[threading.Thread] = None
        self._logcat_running = False
        self._on_logcat: Optional[Callable[[str], None]] = None
        self._screen_size: Optional[Tuple[int, int]] = None
        self._ui_lock = threading.Lock()
        self._ui_cache: Tuple[float, List[UiNode]] = (0.0, [])

    def _build_cmd(self, *args) -> List[str]:
        """构建 adb 命令，自动添加 -s 序列号参数"""
        cmd = [self._adb_path]
        if self._serial:
            cmd.extend(["-s", self._serial])
        cmd.extend(args)
        return cmd

    # ---------- 设备接口 ----------

    def connect(self) -> bool:
        """连接 ADB 设备"""
        try:
            # 检查 adb 是否可用
            result = self._run_cmd("version")
            if result is None:
                logger.error("ADB 工具不可用")
                return False

            # 如果有网络地址，先执行 adb connect
            host = self.config.get("host", "")
            port = self.config.get("port", 5555)
            if host and host not in ("127.0.0.1", "localhost"):
                connect_result = self._run_cmd("connect", f"{host}:{port}")
                if connect_result and "connected" in connect_result.lower():
                    logger.info(f"ADB 网络连接成功: {host}:{port}")
                else:
                    logger.warning(f"ADB 网络连接可能失败: {connect_result}")

            # 验证设备在线（仅 state 为 device 才算可用）
            devices_output = self._run_cmd("devices")
            online = self._parse_devices(devices_output or "")
            if self._serial:
                serials = [s for s, st in online if st == "device"]
                if self._serial in serials:
                    logger.info(f"ADB 设备已连接: {self._serial}")
                    return True
                logger.warning(f"ADB 设备不可用: {self._serial} ({online})")
                return False
            if any(st == "device" for _, st in online):
                logger.info("ADB 设备已连接（默认设备）")
                return True

            logger.warning("ADB 未发现在线设备")
            return False

        except Exception as e:
            logger.error(f"ADB 连接失败: {e}")
            return False

    @staticmethod
    def _parse_devices(output: str):
        """解析 adb devices 输出，返回 [(serial, state), ...]"""
        devices = []
        for line in output.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                devices.append((parts[0], parts[1]))
        return devices

    def disconnect(self) -> None:
        """断开 ADB 设备"""
        self._stop_logcat()

        host = self.config.get("host", "")
        port = self.config.get("port", 5555)
        if host and host not in ("127.0.0.1", "localhost"):
            self._run_cmd("disconnect", f"{host}:{port}")

        logger.info(f"ADB 已断开: {self.device_id}")

    def send(self, data: bytes) -> bool:
        """通过 ADB 发送数据（写入设备 stdin）"""
        try:
            cmd = self._build_cmd("shell", "cat > /data/local/tmp/recv.bin")
            proc = subprocess.run(
                cmd, input=data, capture_output=True, timeout=10,
                creationflags=_CREATE_NO_WINDOW,
            )
            return proc.returncode == 0
        except Exception as e:
            logger.error(f"ADB 发送失败: {e}")
            return False

    def recv(self, timeout: float = 1.0) -> Optional[bytes]:
        """从 ADB 接收数据（读取设备文件）"""
        try:
            cmd = self._build_cmd("shell", "cat /data/local/tmp/recv.bin")
            result = subprocess.run(
                cmd, capture_output=True, timeout=timeout,
                creationflags=_CREATE_NO_WINDOW,
            )
            return result.stdout if result.returncode == 0 else None
        except Exception as e:
            logger.error(f"ADB 接收失败: {e}")
            return None

    def is_connected(self) -> bool:
        """检查 ADB 设备是否在线"""
        try:
            output = self._run_cmd("devices")
            if output:
                lines = output.strip().split("\n")
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 2:
                        serial = parts[0]
                        state = parts[1]
                        if (not self._serial or serial == self._serial) \
                                and state == "device":
                            return True
            return False
        except Exception:
            return False

    # ---------- Shell 命令执行 ----------

    def shell(self, command: str, timeout: float = 30.0) -> Tuple[bool, str]:
        """
        执行 shell 命令。

        返回:
            (success: bool, output: str)
        """
        try:
            cmd = self._build_cmd("shell", command)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                creationflags=_CREATE_NO_WINDOW,
            )
            success = result.returncode == 0
            output = result.stdout if success else result.stderr
            return success, output.strip()
        except subprocess.TimeoutExpired:
            return False, "命令执行超时"
        except Exception as e:
            return False, str(e)

    # ---------- 文件操作 ----------

    def push(self, local_path: str, remote_path: str) -> bool:
        """推送文件到设备"""
        try:
            cmd = self._build_cmd("push", local_path, remote_path)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                    creationflags=_CREATE_NO_WINDOW)
            if result.returncode == 0:
                logger.info(f"文件已推送: {local_path} -> {remote_path}")
                return True
            else:
                logger.error(f"推送失败: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"推送异常: {e}")
            return False

    def pull(self, remote_path: str, local_path: str) -> bool:
        """从设备拉取文件（本地目录不存在时自动创建）"""
        try:
            parent = os.path.dirname(os.path.abspath(local_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            cmd = self._build_cmd("pull", remote_path, local_path)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                    creationflags=_CREATE_NO_WINDOW)
            if result.returncode == 0:
                logger.info(f"文件已拉取: {remote_path} -> {local_path}")
                return True
            else:
                logger.error(f"拉取失败: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"拉取异常: {e}")
            return False

    # ---------- 应用管理 ----------

    def install_app(self, apk_path: str) -> bool:
        """安装 APK"""
        try:
            cmd = self._build_cmd("install", "-r", apk_path)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                                    creationflags=_CREATE_NO_WINDOW)
            success = "success" in result.stdout.lower()
            if success:
                logger.info(f"APK 安装成功: {apk_path}")
            else:
                logger.error(f"APK 安装失败: {result.stdout}")
            return success
        except Exception as e:
            logger.error(f"APK 安装异常: {e}")
            return False

    def uninstall_app(self, package: str) -> bool:
        """卸载应用"""
        try:
            cmd = self._build_cmd("uninstall", package)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                    creationflags=_CREATE_NO_WINDOW)
            return "success" in result.stdout.lower()
        except Exception as e:
            logger.error(f"卸载异常: {e}")
            return False

    def start_activity(self, activity: str) -> bool:
        """启动 Activity"""
        success, output = self.shell(f"am start -n {activity}")
        return success

    def stop_app(self, package: str) -> bool:
        """强制停止应用"""
        success, _ = self.shell(f"am force-stop {package}")
        return success

    def get_foreground_app(self) -> Tuple[Optional[str], Optional[str]]:
        """
        获取当前前台应用的包名和 Activity。
        
        返回:
            (package_name, activity_name) 或 (None, None)
        """
        try:
            success, output = self.shell("dumpsys activity activities | grep mResumedActivity")
            if success and output:
                # 输出格式: mResumedActivity: ActivityRecord{...} com.example.app/.MainActivity ...
                import re
                match = re.search(r'(\S+)/(\S+)', output)
                if match:
                    package = match.group(1)
                    activity = match.group(2)
                    return package, activity
            return None, None
        except Exception as e:
            logger.debug(f"获取前台应用失败: {e}")
            return None, None

    def resolve_launch_activity(self, package: str) -> str:
        """解析应用启动页（形如 com.x/.MainActivity），失败返回空串"""
        package = (package or "").strip()
        if not package:
            return ""
        success, output = self.shell(
            f"cmd package resolve-activity --brief {shlex.quote(package)}",
            timeout=10.0,
        )
        if not success:
            return ""
        for line in reversed(output.splitlines()):
            line = line.strip()
            if "/" in line and " " not in line:
                return line
        return ""

    def list_packages(self, third_party_only: bool = False) -> List[str]:
        """列出已安装应用包名"""
        cmd = "pm list packages -3" if third_party_only else "pm list packages"
        success, output = self.shell(cmd, timeout=20.0)
        if not success:
            return []
        packages = {
            line.strip().split(":", 1)[1]
            for line in output.splitlines()
            if line.strip().startswith("package:")
        }
        return sorted(packages)

    def get_app_name_map(self) -> Dict[str, str]:
        """
        获取应用名称到包名的映射。

        返回:
            dict: {应用名称: 包名}，如 {"媒体中心": "com.xxx.media"}
        """
        name_map: Dict[str, str] = {}
        try:
            # 使用 dumpsys package 获取所有应用的标签信息
            success, output = self.shell("dumpsys package", timeout=30.0)
            if not success:
                return name_map

            current_package = ""
            for line in output.splitlines():
                line = line.strip()
                # 匹配包名行：Package [com.xxx.xxx] (...)
                pkg_match = re.match(r"^Package \[([^\]]+)\]", line)
                if pkg_match:
                    current_package = pkg_match.group(1)
                    continue

                # 匹配应用标签：nonDataActions=... 或 label=xxx
                # 格式通常是：label=媒体中心 或 在 Activity 信息附近有应用名
                if current_package:
                    # 匹配 "label=xxx" 或 "nonDataActions=... label=xxx"
                    label_match = re.search(r"label=([^\s;]+)", line)
                    if label_match:
                        label = label_match.group(1)
                        if label and label not in name_map:
                            name_map[label] = current_package
        except Exception as e:
            logger.debug(f"获取应用名称映射失败：{e}")

        return name_map

    def find_package_by_name(self, app_name: str) -> Optional[str]:
        """
        根据应用名称查找包名。

        参数:
            app_name: 应用名称，如 "媒体中心"

        返回:
            包名（如 "com.xxx.media"），未找到返回 None
        """
        if not app_name:
            return None

        name_map = self.get_app_name_map()

        # 精确匹配
        if app_name in name_map:
            return name_map[app_name]

        # 模糊匹配：应用名包含在包名中，或包名包含应用名的拼音/英文
        app_name_lower = app_name.lower()
        for name, package in name_map.items():
            if app_name_lower in name.lower() or name.lower() in app_name_lower:
                return package
            # 检查包名是否包含应用名的关键部分
            package_lower = package.lower()
            if app_name_lower in package_lower:
                return package

        return None

    # ---------- 截图与录屏 ----------

    def take_screenshot(self, local_path: str) -> bool:
        """截取屏幕"""
        try:
            remote = "/sdcard/screenshot.png"
            self.shell(f"screencap -p {remote}")
            return self.pull(remote, local_path)
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return False

    def screencap_bytes(self) -> Optional[bytes]:
        """
        直接获取屏幕截图的 PNG 字节流（exec-out 单次往返，适合保存截图）。
        """
        try:
            cmd = self._build_cmd("exec-out", "screencap", "-p")
            result = subprocess.run(cmd, capture_output=True, timeout=15,
                                    creationflags=_CREATE_NO_WINDOW)
            if result.returncode == 0 and result.stdout:
                return result.stdout
            if result.stderr:
                logger.debug(f"屏幕截取警告: {result.stderr[:200]}")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("屏幕截取超时")
            return None
        except Exception as e:
            logger.error(f"屏幕截取失败: {e}")
            return None

    # 原始帧像素格式 → 每像素字节数
    _RAW_BPP = {1: 4, 2: 4, 3: 2, 4: 4, 5: 3}

    def screencap_frame(self, timeout: float = 10.0):
        """
        抓取原始帧（exec-out screencap 不带 -p，省去设备端 PNG 编码，延迟更低）。

        返回 (width, height, pixel_format, data)；失败返回 None。
        data 紧跟 12 字节头部（宽/高/格式，小端 uint32）。
        """
        try:
            cmd = self._build_cmd("exec-out", "screencap")
            result = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                    creationflags=_CREATE_NO_WINDOW)
            raw = result.stdout or b""
            if result.returncode != 0 or len(raw) < 16:
                return None
            width, height, fmt = struct.unpack("<III", raw[:12])
            bpp = self._RAW_BPP.get(fmt)
            if not bpp or not (0 < width <= 8192) or not (0 < height <= 8192):
                return None
            if len(raw) - 12 < width * bpp * height:
                return None
            return width, height, fmt, raw[12:]
        except subprocess.TimeoutExpired:
            logger.debug("原始帧抓取超时")
            return None
        except Exception as e:
            logger.debug(f"原始帧抓取失败: {e}")
            return None

    def get_screen_size(self, use_cache: bool = False) -> Optional[Tuple[int, int]]:
        """获取设备屏幕分辨率 (宽, 高)"""
        if use_cache and self._screen_size:
            return self._screen_size
        success, output = self.shell("wm size")
        if not success:
            return None
        m = re.search(r"(\d+)x(\d+)", output)
        if m:
            self._screen_size = (int(m.group(1)), int(m.group(2)))
            return self._screen_size
        return None

    # ---------- 实时画面流 ----------

    def screenrecord_stream(self, max_width: int = 1280,
                            bit_rate: str = "8M",
                            time_limit: int = 180) -> Optional[subprocess.Popen]:
        """
        启动 screenrecord 把 H.264 裸流写到管道（实时投屏用）。

        返回的进程 stdout 可直接喂给解码器；time_limit 到期后流会结束，
        调用方重新启动即可。失败返回 None。
        """
        args = [
            "screenrecord",
            "--output-format=h264",
            "--bit-rate", bit_rate,
            "--time-limit", str(time_limit),
        ]
        size = self.get_screen_size(use_cache=True)
        if size:
            width, height = size
            if width > max_width > 0:
                scale = max_width / width
                height = max(2, int(round(height * scale / 2)) * 2)
                width = max_width
            if width > 0 and height > 0:
                args += ["--size", f"{width}x{height}"]
        args.append("-")

        # 用 sh 包一层先打印自身 PID，停止时才能精确杀掉设备端 screenrecord
        remote_cmd = "sh -c 'echo $$; exec " + " ".join(args) + "'"
        try:
            return subprocess.Popen(
                self._build_cmd("exec-out", remote_cmd),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            logger.error(f"启动 screenrecord 失败: {e}")
            return None

    def kill_remote_pid(self, pid: int):
        """结束设备端进程（实时流停止时的清理）"""
        try:
            self.shell(f"kill -9 {int(pid)}", timeout=5.0)
        except Exception as e:
            logger.debug(f"清理设备端进程失败: {e}")

    def input_tap(self, x: int, y: int) -> bool:
        """模拟点击屏幕坐标 (设备分辨率坐标系)"""
        success, output = self.shell(f"input tap {int(x)} {int(y)}")
        if not success:
            logger.warning(f"点击注入失败: {output}")
        return success

    def input_swipe(self, x1: int, y1: int, x2: int, y2: int,
                    duration_ms: int = 300) -> bool:
        """模拟滑动（也可用于长按：起止坐标相同 + 较长时长）"""
        success, output = self.shell(
            f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}"
        )
        if not success:
            logger.warning(f"滑动注入失败: {output}")
        return success

    def keyevent(self, code: int) -> bool:
        """发送按键事件（如 3=HOME, 4=BACK, 26=POWER, 187=APP_SWITCH）"""
        success, output = self.shell(f"input keyevent {int(code)}")
        if not success:
            logger.warning(f"按键注入失败: {output}")
        return success

    # ---------- 界面元素操作 ----------

    def dump_ui(self, timeout: float = 20.0,
                use_cache: bool = False) -> List[UiNode]:
        """抓取当前界面控件树（uiautomator dump）"""
        with self._ui_lock:
            ts, cached = self._ui_cache
            if use_cache and cached and time.time() - ts < 0.8:
                return list(cached)

        remote = "/sdcard/window_dump.xml"
        self.shell(f"uiautomator dump {remote}", timeout=timeout)
        try:
            result = subprocess.run(
                self._build_cmd("exec-out", f"cat {remote}"),
                capture_output=True, timeout=timeout,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            logger.debug(f"读取界面结构失败: {e}")
            return []

        nodes = parse_dump(result.stdout or b"")
        if nodes:
            with self._ui_lock:
                self._ui_cache = (time.time(), nodes)
        return nodes

    def visible_texts(self, limit: int = 60) -> List[str]:
        """当前界面可见文字"""
        return visible_texts(self.dump_ui(use_cache=True), limit)

    def wait_for_text(self, text: str, timeout: float = 10.0,
                      exact: bool = False,
                      interval: float = 0.8,
                      cancel: Optional[Callable[[], bool]] = None
                      ) -> Tuple[bool, str]:
        """等待界面出现指定文字（也可作为页面加载/结果校验）"""
        text = (text or "").strip()
        if not text:
            return False, "等待文字为空"

        deadline = time.time() + max(0.0, timeout)
        while True:
            if cancel is not None and cancel():
                return False, "执行已被用户停止"
            nodes = self.dump_ui()
            matches = find_nodes(nodes, text, exact=exact)
            if matches:
                node = best_node(matches)
                return True, f"界面已出现「{node.label}」"
            if time.time() >= deadline:
                visible = "、".join(visible_texts(nodes, 15)) or "（无可见文字）"
                return False, (
                    f"等待「{text}」超时（{timeout:.0f}s），"
                    f"当前可见文字：{visible}"
                )
            time.sleep(interval)

    # 搜索框同义词：当找不到精确匹配时，尝试这些同义词
    _SEARCH_SYNONYMS = {
        "搜索": ["搜索歌曲", "搜索音乐", "搜索框", "查找", "放大镜"],
        "搜索框": ["搜索", "搜索歌曲", "搜索音乐", "查找", "放大镜"],
    }

    # 常见搜索/输入框 resource-id 映射（按优先级排序）
    _SEARCH_RESOURCE_IDS = [
        "com.tinnove.mediacenter:id/edit_text",  # 媒体中心搜索框
        "com.tinnove.mediacenter:id/sv_search",  # 媒体中心搜索容器
    ]

    def find_and_tap(self, text: str, timeout: float = 10.0,
                     index: int = 0, long_press: bool = False,
                     exact: bool = False,
                     cancel: Optional[Callable[[], bool]] = None
                     ) -> Tuple[bool, str]:
        """按界面文字查找控件并点击（不依赖写死坐标）"""
        text = (text or "").strip()
        if not text:
            return False, "点击文字为空"

        deadline = time.time() + max(0.0, timeout)
        visible = []
        surface_hits = 0
        tried_synonyms = set()
        
        while True:
            if cancel is not None and cancel():
                return False, "执行已被用户停止"
            nodes = self.dump_ui()
            visible = visible_texts(nodes, 15)
            matches = dedupe_nodes(find_nodes(nodes, text, exact=exact))
            if matches:
                target = matches[max(0, min(int(index), len(matches) - 1))]
                x, y = target.center
                if long_press:
                    ok = self.input_swipe(x, y, x, y, 800)
                else:
                    ok = self.input_tap(x, y)
                if ok:
                    return True, f"已点击「{target.label}」({x},{y})"
                return False, f"点击「{target.label}」注入失败"

            # 尝试同义词（仅对搜索相关文字）
            if text not in tried_synonyms:
                tried_synonyms.add(text)
                synonyms = self._SEARCH_SYNONYMS.get(text, [])
                for synonym in synonyms:
                    syn_matches = dedupe_nodes(find_nodes(nodes, synonym, exact=False))
                    if syn_matches:
                        target = syn_matches[0]
                        x, y = target.center
                        ok = self.input_tap(x, y)
                        if ok:
                            return True, f"已点击「{target.label}」（同义词「{synonym}」）({x},{y})"

            # 尝试通过 resource-id 定位（搜索框等常见控件）
            if not hasattr(self, '_tried_resource_ids'):
                self._tried_resource_ids = set()
            for rid in self._SEARCH_RESOURCE_IDS:
                if rid not in self._tried_resource_ids:
                    self._tried_resource_ids.add(rid)
                    # 查找匹配 resource-id 的节点
                    rid_matches = [n for n in nodes if n.resource_id == rid]
                    if rid_matches:
                        target = rid_matches[0]
                        x, y = target.center
                        ok = self.input_tap(x, y)
                        if ok:
                            return True, f"已点击「{target.label}」（resource-id: {rid}）({x},{y})"

            # 游戏/视频等 SurfaceView 画面：控件树里只有渲染表面，没有可读文字。
            # 连续两次都是这种情况就快速失败，避免无谓地等满超时。
            if _is_surface_only(nodes):
                surface_hits += 1
                if surface_hits >= 2:
                    return False, (
                        f"界面为渲染画面（SurfaceView），无法读取「{text}」"
                    )
            else:
                surface_hits = 0

            if time.time() >= deadline:
                shown = "、".join(visible) or "（无可见文字）"
                return False, (
                    f"界面中未找到「{text}」（等待 {timeout:.0f}s），"
                    f"当前可见文字：{shown}"
                )
            time.sleep(0.8)

    def ui_input_text(self, text: str, clear: bool = False) -> Tuple[bool, str]:
        """
        向当前输入框输入文字。

        设备自带的 `input text` 不支持中文（会抛 NullPointerException），
        因此中文按以下顺序降级：
        1. 聚焦输入框 + ADBKeyboard 广播
        2. 聚焦输入框 + 点击界面中的同名词条
        3. 直接点击界面中的同名词条
        """
        text = text or ""
        if not text:
            return False, "输入内容为空"

        # 特殊处理：纯空格输入
        if text.strip() == "":
            edit, _ = self._focus_edit_text()
            if edit is None:
                return False, "未找到输入框，无法输入空格"
            # 清除已有内容（使用 ADBKeyboard 清除广播，更可靠）
            if clear:
                self.shell("am broadcast -a ADB_CLEAR_TEXT", timeout=10.0)
                time.sleep(0.3)
            # 空格通过 ADB keyevent 输入（62 = 空格键）
            space_count = len(text)
            if space_count > 50:
                return False, f"空格数量过多（{space_count}），超过限制"
            keyevents = " ".join(["62"] * space_count)
            success, output = self.shell(f"input keyevent {keyevents}", timeout=10.0)
            if not success or "Exception" in output or "Error" in output:
                return False, f"空格输入失败：{output or '未知错误'}"
            return True, f"已输入 {space_count} 个空格"

        # 超长字符串截断（ADB 命令长度限制约 4096 字符）
        if len(text) > 200:
            logger.warning(f"输入文字过长（{len(text)} 字符），截断到 200 字符")
            text = text[:200]

        if any(ord(ch) > 127 for ch in text):
            # 中文输入：先尝试聚焦输入框
            self._focus_edit_text()

            if self._adb_keyboard_available():
                ok, message = self._adb_keyboard_input(text, clear)
                if ok:
                    return True, message

            node = self._find_tappable_text(text)
            if node is not None:
                x, y = node.center
                if self.input_tap(x, y):
                    return True, (
                        f"设备输入法无法注入「{text}」，"
                        f"已点击界面中的同名词条「{node.label}」"
                    )

            return False, (
                f"设备输入法无法注入中文「{text}」：请安装 ADBKeyboard，"
                "或改用界面中已出现的词条/英文关键词"
            )

        edit, _ = self._focus_edit_text()
        if clear and edit is not None and edit.text:
            count = min(len(edit.text), 128)
            self.shell(
                "input keyevent 123 " + " ".join(["67"] * count),
                timeout=10.0,
            )

        escaped = text.replace(" ", "%s").replace("'", "'\\''")
        success, output = self.shell(f"input text '{escaped}'", timeout=15.0)
        if not success or "Exception" in output or "Error" in output:
            return False, f"文字输入失败: {output or '未知错误'}"
        return True, f"已输入文字「{text}」"

    def _focus_edit_text(self, max_retries: int = 2) -> Tuple[Optional[UiNode], List[UiNode]]:
        """
        确保某个输入框获得焦点，返回 (输入框, 当前控件列表)
        
        增加重试机制：首次点击后重新检测焦点状态，确保输入框真正获得焦点。
        支持通过 resource-id 定位搜索框。
        """
        nodes = self.dump_ui()
        edits = [n for n in nodes if n.is_edit_text]
        
        # 如果没有找到 EditText，尝试通过 resource-id 查找搜索框
        if not edits:
            for rid in self._SEARCH_RESOURCE_IDS:
                rid_matches = [n for n in nodes if n.resource_id == rid]
                if rid_matches:
                    edits = rid_matches
                    break
        
        if not edits:
            return None, nodes
        
        # 检查是否已有输入框获得焦点
        focused = [n for n in edits if n.focused]
        if focused:
            return focused[0], nodes
        
        # 选择面积最大的输入框（通常是主输入框）
        target = max(edits, key=lambda n: n.area)
        
        # 点击输入框并等待焦点
        for attempt in range(max_retries + 1):
            self.input_tap(*target.center)
            time.sleep(0.5)
            
            # 重新检测焦点状态
            nodes = self.dump_ui()
            edits = [n for n in nodes if n.is_edit_text]
            # 同样尝试 resource-id 回退
            if not edits:
                for rid in self._SEARCH_RESOURCE_IDS:
                    rid_matches = [n for n in nodes if n.resource_id == rid]
                    if rid_matches:
                        edits = rid_matches
                        break
            focused = [n for n in edits if n.focused]
            if focused:
                return focused[0], nodes
            
            if attempt < max_retries:
                logger.debug(f"输入框焦点获取失败，重试 {attempt + 1}/{max_retries}")
                time.sleep(0.3)
        
        # 重试后仍未获得焦点，返回目标节点（至少点击过了）
        return target, nodes

    def _find_tappable_text(self, text: str) -> Optional[UiNode]:
        """在界面中寻找与 text 同名的可点击词条"""
        nodes = self.dump_ui()
        exact = dedupe_nodes(find_nodes(nodes, text, exact=True))
        if exact:
            return best_node(exact)
        loose = dedupe_nodes(find_nodes(nodes, text))
        clickable = [n for n in loose if n.clickable]
        return best_node(clickable or loose)

    _ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"

    def _adb_keyboard_available(self) -> bool:
        success, output = self.shell(
            "pm list packages com.android.adbkeyboard", timeout=10.0
        )
        return bool(success and "com.android.adbkeyboard" in output)

    def _adb_keyboard_input(self, text: str, clear: bool) -> Tuple[bool, str]:
        ime = self._ADB_KEYBOARD_IME
        success, output = self.shell(
            f"ime enable {ime} && ime set {ime}", timeout=15.0
        )
        if not success:
            return False, f"启用 ADBKeyboard 失败: {output}"

        if clear:
            self.shell("am broadcast -a ADB_CLEAR_TEXT", timeout=10.0)
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        success, output = self.shell(
            f"am broadcast -a ADB_INPUT_B64 --es msg {payload}", timeout=10.0
        )
        if not success or "Broadcast completed" not in output:
            return False, f"ADBKeyboard 输入失败: {output}"
        return True, f"已通过 ADBKeyboard 输入「{text}」"

    def start_screen_record(self, remote_path: str = "/sdcard/recording.mp4",
                             time_limit: int = 180) -> bool:
        """开始录屏"""
        success, _ = self.shell(
            f"screenrecord --time-limit {time_limit} {remote_path}"
        )
        return success

    def stop_screen_record(self, local_path: str) -> bool:
        """停止录屏并拉取到本地"""
        # 发送 Ctrl+C 停止（通过 kill 进程）
        self.shell("pkill -INT screenrecord")
        time.sleep(1)
        return self.pull("/sdcard/recording.mp4", local_path)

    # ---------- Logcat ----------

    def start_logcat(self, callback: Callable[[str], None],
                     filter_tag: str = "", level: str = ""):
        """
        启动 logcat 日志流。

        参数:
            callback: 每行日志的回调
            filter_tag: 过滤标签
            level: 日志级别 (V/D/I/W/E)
        """
        self._on_logcat = callback
        self._logcat_running = True
        self._logcat_thread = threading.Thread(
            target=self._logcat_loop,
            args=(filter_tag, level),
            daemon=True,
            name=f"Logcat_{self.device_id}"
        )
        self._logcat_thread.start()

    def _stop_logcat(self):
        """停止 logcat"""
        self._logcat_running = False
        if self._logcat_thread and self._logcat_thread.is_alive():
            self._logcat_thread.join(timeout=3.0)

    def _logcat_loop(self, filter_tag: str, level: str):
        """Logcat 读取循环"""
        try:
            cmd = self._build_cmd("logcat")
            if level:
                cmd.append(f"*:{level}")
            if filter_tag:
                cmd.extend(["-s", filter_tag])

            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
                creationflags=_CREATE_NO_WINDOW,
            )

            while self._logcat_running:
                line = proc.stdout.readline()
                if not line:
                    break
                if self._on_logcat:
                    self._on_logcat(line.rstrip())

            proc.terminate()
        except Exception as e:
            if self._logcat_running:
                logger.error(f"Logcat 异常: {e}")

    # ---------- 内部方法 ----------

    def _run_cmd(self, *args, timeout: float = 10.0) -> Optional[str]:
        """执行 adb 命令并返回输出"""
        try:
            cmd = self._build_cmd(*args)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                creationflags=_CREATE_NO_WINDOW,
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            logger.warning(f"ADB 命令超时: {' '.join(args)}")
            return None
        except FileNotFoundError:
            logger.error(f"ADB 工具未找到: {self._adb_path}")
            return None
        except Exception as e:
            logger.error(f"ADB 命令异常: {e}")
            return None

    @staticmethod
    def list_devices(adb_path: str = "adb") -> List[dict]:
        """列出所有 ADB 设备"""
        devices = []
        try:
            result = subprocess.run(
                [adb_path, "devices"],
                capture_output=True, text=True, timeout=5,
                creationflags=_CREATE_NO_WINDOW,
            )
            for line in result.stdout.strip().split("\n")[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    devices.append({
                        "serial": parts[0],
                        "state": parts[1],
                        "model": parts[2] if len(parts) > 2 else "",
                    })
        except Exception as e:
            logger.error(f"ADB 设备列表失败: {e}")
        return devices
