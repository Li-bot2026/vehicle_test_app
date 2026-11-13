"""
测试运行器
===========
为执行引擎提供各步骤类型的实际处理逻辑。

设计说明：
- TestRunner 将步骤处理器注册到 ExecutionEngine
- 每种步骤类型有独立的处理方法
- 支持扩展：通过 register_handler 添加自定义步骤类型
"""

import re
import time
import shlex
import base64
import importlib
from typing import Dict, Callable, Optional, Any, List, Tuple

from loguru import logger

from ..testcase.model import TestStep, StepType, TestStatus
from ..device.manager import DeviceManager
from ..device.serial_device import SerialDevice
from ..device.network_device import NetworkDevice
from ..device.adb_device import ADBDevice
from ..device.ui_automation import (
    find_nodes, visible_texts, dedupe_nodes, best_node,
)
from ..assertion.engine import AssertionEngine
from .engine import ExecutionEngine


def _swipe_coords(size, region: str, direction: str, swipe_ratio: float = 0.33):
    """根据区域与方向计算滑动起止坐标（基于屏幕分辨率）
    
    参数:
        swipe_ratio: 滑动幅度比例，默认 0.33（每次滑动屏幕的 1/3）
    """
    w, h = size
    # 各区域的可滑动矩形框 (x0, y0, x1, y1)
    boxes = {
        "left": (int(w * 0.03), int(h * 0.2), int(w / 3), int(h * 0.8)),
        "right": (int(w * 2 / 3), int(h * 0.2), int(w * 0.97), int(h * 0.8)),
        "top": (int(w * 0.2), int(h * 0.03), int(w * 0.8), int(h / 3)),
        "bottom": (int(w * 0.2), int(h * 2 / 3), int(w * 0.8), int(h * 0.97)),
        "center": (int(w * 0.2), int(h * 0.25), int(w * 0.8), int(h * 0.75)),
    }
    x0, y0, x1, y1 = boxes.get(region, boxes["center"])
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    # 计算滑动距离（区域高度的 swipe_ratio）
    swipe_distance = int((y1 - y0) * swipe_ratio)
    if direction == "up":        # 手指从下往上，内容向下翻
        return (cx, cy + swipe_distance // 2), (cx, cy - swipe_distance // 2)
    if direction == "down":
        return (cx, cy - swipe_distance // 2), (cx, cy + swipe_distance // 2)
    if direction == "left":
        swipe_distance = int((x1 - x0) * swipe_ratio)
        return (cx + swipe_distance // 2, cy), (cx - swipe_distance // 2, cy)
    swipe_distance = int((x1 - x0) * swipe_ratio)
    return (cx - swipe_distance // 2, cy), (cx + swipe_distance // 2, cy)    # right


# 中文语义 → 常见英文 resource-id 片段。
# 游戏/启动页的按钮常常是「无文字无描述的 ImageButton」，
# 只剩 resource-id 可读，用这张表做离线语义匹配。
_RID_SYNONYMS = {
    "人机": ("single", "pve", "computer", "robot"),
    "单人": ("single", "solo", "pve"),
    "电脑": ("computer", "pve", "single"),
    "双人": ("double", "pvp", "multi", "two", "local"),
    "多人": ("multi", "pvp", "online"),
    "联机": ("online", "multi", "pvp"),
    "对战": ("game", "fight", "battle", "match", "pk"),
    "对弈": ("game", "chess", "battle", "play"),
    "下棋": ("game", "chess", "play"),
    "开始": ("start", "begin"),
    "继续": ("continue", "resume", "next"),
    "重新": ("restart", "again", "replay", "retry"),
    "重来": ("restart", "again", "replay", "retry"),
    "悔棋": ("undo", "regret"),
    "认输": ("surrender", "resign", "giveup"),
    "返回": ("back", "return"),
    "退出": ("exit", "quit", "close"),
    "关闭": ("close", "cancel", "exit"),
    "设置": ("setting", "config", "option"),
    "确认": ("confirm", "ok", "sure", "yes", "submit"),
    "确定": ("confirm", "ok", "sure", "yes", "submit"),
    "取消": ("cancel", "no"),
    "首页": ("home", "main", "menu"),
    "菜单": ("menu", "home"),
    "抽奖": ("lottery", "draw", "prize", "luck"),
    "摇一摇": ("shake",),
}

# resource-id 中的通用词：单独命中没有区分度，不能作为定位依据
_GENERIC_RID_TOKENS = {
    "game", "play", "battle", "match", "fight", "pk",
    "list", "btn", "button", "view", "layout", "item",
    "text", "icon", "image", "img", "main", "menu", "next",
    "sure", "yes", "ok", "no",
}
# 长度不足 4 但有辨识度的 token
_STRONG_SHORT_TOKENS = {"pve", "pvp"}

# 目标尾部常见的修饰词，匹配前去掉
_TARGET_TAIL_RE = re.compile(
    r"(按钮|按键|选项|图标|模式|界面|页面|选项卡|入口|这一项|这个)$"
)
# 棋类目标 → 交给内置视觉引擎（识别棋盘并落子）
_GOMOKU_HINT_RE = re.compile(r"(五子棋|下棋|棋盘|落子|对弈|棋局)")
# 扫雷目标 → 交给内置视觉引擎（识别雷区、按规则推理）
_MINESWEEPER_HINT_RE = re.compile(r"(扫雷|雷区|mine|sweep)", re.IGNORECASE)

# 渲染表面占位文字（游戏/视频 SurfaceView 画面常见），不算“界面可读文字”
_SURFACE_LABELS = {"game view", "surfaceview", "surface", "video view"}


def _readable_texts(nodes, limit: int = 60) -> List[str]:
    """界面可读文字（过滤掉 SurfaceView 占位标签）"""
    texts = [
        t for t in visible_texts(nodes, limit)
        if t.strip() and t.strip().lower() not in _SURFACE_LABELS
    ]
    return texts


def _is_canvas_screen(nodes) -> bool:
    """是否是读不到文字的渲染画面（游戏/视频等画布界面）"""
    return not _readable_texts(nodes, 8)


class TestRunner:
    """
    测试运行器
    -----------
    将各步骤类型的处理逻辑绑定到执行引擎。
    """

    def __init__(self, engine: ExecutionEngine,
                 device_manager: DeviceManager):
        self._engine = engine
        self._device_manager = device_manager
        self._assertion_engine = AssertionEngine()
        # AI 助手（自主任务需要）；由主窗口注入，可为 None
        self._assistant = None

        # 注册所有默认处理器
        self._register_handlers()

    def set_assistant(self, assistant) -> None:
        """注入 AI 助手，供自主任务（agent_task）决策使用"""
        self._assistant = assistant

    def _register_handlers(self):
        """注册所有默认步骤处理器"""
        handlers = {
            StepType.SEND_SERIAL: self._handle_send_serial,
            StepType.SEND_NETWORK: self._handle_send_network,
            StepType.SEND_ADB: self._handle_send_adb,
            StepType.SEND_CAN: self._handle_send_can,
            StepType.WAIT: self._handle_wait,
            StepType.WAIT_SIGNAL: self._handle_wait_signal,
            StepType.WAIT_CAN: self._handle_wait_can,
            StepType.ASSERT_VALUE: self._handle_assert_value,
            StepType.ASSERT_RANGE: self._handle_assert_range,
            StepType.ASSERT_STATE: self._handle_assert_state,
            StepType.ASSERT_TIMING: self._handle_assert_timing,
            StepType.ASSERT_CUSTOM: self._handle_assert_custom,
            StepType.SCREENSHOT: self._handle_screenshot,
            StepType.LOG_MESSAGE: self._handle_log_message,
            StepType.APP_START: self._handle_app_start,
            StepType.UI_TAP: self._handle_ui_tap,
            StepType.UI_INPUT: self._handle_ui_input,
            StepType.UI_WAIT_TEXT: self._handle_ui_wait_text,
            StepType.UI_SWIPE: self._handle_ui_swipe,
            StepType.AGENT_TASK: self._handle_agent_task,
        }
        for step_type, handler in handlers.items():
            self._engine.register_step_handler(step_type, handler)

    def register_handler(self, step_type: StepType,
                         handler: Callable[[TestStep], bool]):
        """注册自定义步骤处理器"""
        self._engine.register_step_handler(step_type, handler)

    # ---------- 发送类处理器 ----------

    def _handle_send_serial(self, step: TestStep) -> bool:
        """发送串口数据"""
        device = self._get_device(step.device_id)
        if not isinstance(device, SerialDevice):
            step.error_message = f"设备不是串口类型: {step.device_id}"
            return False

        data = step.params.get("data", "")
        mode = step.params.get("mode", "text")  # text / hex / bytes

        if mode == "hex":
            try:
                raw = bytes.fromhex(data.replace(" ", ""))
            except ValueError:
                step.error_message = f"十六进制数据格式错误: {data}"
                return False
        else:
            raw = data.encode("utf-8")

        success = device.send(raw)
        if not success:
            step.error_message = "串口发送失败"
        return success

    def _handle_send_network(self, step: TestStep) -> bool:
        """发送网络数据"""
        device = self._get_device(step.device_id)
        if not isinstance(device, NetworkDevice):
            step.error_message = f"设备不是网络类型: {step.device_id}"
            return False

        data = step.params.get("data", "")
        encoding = step.params.get("encoding", "utf-8")
        raw = data.encode(encoding) if isinstance(data, str) else data

        success = device.send(raw)
        if not success:
            step.error_message = "网络发送失败"
        return success

    def _handle_send_adb(self, step: TestStep) -> bool:
        """执行 ADB 命令"""
        device = self._get_device(step.device_id)
        if not isinstance(device, ADBDevice):
            step.error_message = f"设备不是 ADB 类型: {step.device_id}"
            return False

        command = step.params.get("command", "")
        if not command:
            step.error_message = "ADB 命令为空"
            return False

        success, output = device.shell(
            command, timeout=max(1.0, float(step.timeout or 30.0))
        )
        step.actual = output
        if not success:
            step.error_message = f"ADB 命令失败: {output}"
        return success


    def _handle_send_can(self, step: TestStep) -> bool:
        """发送 CAN 消息"""
        try:
            from ..device.virtual_can import VirtualCANBus, CANMessage
        except ImportError as e:
            step.error_message = f"CAN 模块导入失败：{e}"
            return False

        can = self._get_can_device(step.device_id)
        if not can or not can.is_connected:
            step.error_message = f"CAN 设备未连接：{step.device_id}"
            return False

        msg_id = int(step.params.get("id", "0"), 0)
        data_hex = step.params.get("data", "")
        data = bytes.fromhex(data_hex.replace(" ", "").replace("0x", ""))
        is_extended = step.params.get("extended", False)
        is_fd = step.params.get("fd", False)

        msg = CANMessage(id=msg_id, data=data, is_extended=is_extended,
                         bitrate_switch=is_fd)

        try:
            success = can.send(msg)
            if success:
                step.actual = f"发送 CAN 0x{msg_id:03X}: {data_hex}"
            else:
                step.error_message = "CAN 发送失败"
            return success
        except Exception as e:
            step.error_message = f"CAN 发送异常：{e}"
            return False

    def _handle_wait_can(self, step: TestStep) -> bool:
        """等待 CAN 消息"""
        try:
            from ..device.virtual_can import VirtualCANBus
        except ImportError as e:
            step.error_message = f"CAN 模块导入失败：{e}"
            return False

        can = self._get_can_device(step.device_id)
        if not can or not can.is_connected:
            step.error_message = f"CAN 设备未连接：{step.device_id}"
            return False

        timeout = float(step.timeout or 5.0)
        expected_id = step.params.get("id")
        if expected_id:
            expected_id = int(expected_id, 0)

        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._engine.checkpoint():
                step.error_message = "执行已被用户停止"
                return False

            messages = can.receive(timeout=0.1, max_count=10)
            for msg in messages:
                if expected_id is None or msg.id == expected_id:
                    step.actual = f"收到 CAN 0x{msg.id:03X}: {' '.join(f'{b:02X}' for b in msg.data)}"
                    return True
            time.sleep(0.1)

        step.error_message = f"等待 CAN 消息超时 ({timeout}秒)"
        return False

    def _get_can_device(self, device_id: str):
        """获取 CAN 设备连接"""
        device = self._get_device(device_id)
        if hasattr(device, 'is_connected'):
            return device
        return None

    # ---------- 等待类处理器 ----------

    def _handle_wait(self, step: TestStep) -> bool:
        """等待指定时间（分片等待，便于暂停/停止及时生效）"""
        duration = float(step.params.get("duration", 1.0))
        waited = 0.0
        while waited < duration:
            if self._engine.checkpoint():
                step.error_message = "执行已被用户停止"
                return False
            chunk = min(0.2, duration - waited)
            time.sleep(chunk)
            waited += chunk
        return True

    def _handle_wait_signal(self, step: TestStep) -> bool:
        """等待特定信号/报文"""
        timeout = step.timeout
        expected_data = step.params.get("data_pattern")

        device = self._get_device(step.device_id)
        if not device:
            step.error_message = "设备未连接"
            return False

        start = time.time()
        while time.time() - start < timeout:
            if self._engine.checkpoint():
                step.error_message = "执行已被用户停止"
                return False
            data = device.recv(timeout=0.1)
            if data:
                if expected_data:
                    if not self._match_pattern(data, expected_data):
                        continue
                step.actual = data.hex()
                return True

        step.error_message = f"等待超时 ({timeout}s)"
        return False

    # ---------- 断言类处理器 ----------

    def _handle_assert_value(self, step: TestStep) -> bool:
        """数值断言"""
        return self._assertion_engine.assert_value(
            actual=step.actual,
            expected=step.expected,
            tolerance=step.tolerance
        )

    def _handle_assert_range(self, step: TestStep) -> bool:
        """范围断言"""
        min_val = step.params.get("min")
        max_val = step.params.get("max")
        return self._assertion_engine.assert_range(
            actual=step.actual,
            min_val=min_val,
            max_val=max_val
        )

    def _handle_assert_state(self, step: TestStep) -> bool:
        """状态断言"""
        return self._assertion_engine.assert_state(
            actual=step.actual,
            expected=step.expected
        )

    def _handle_assert_timing(self, step: TestStep) -> bool:
        """时序断言"""
        return self._assertion_engine.assert_timing(
            actual_time=step.actual,
            max_time=step.params.get("max_time"),
            min_time=step.params.get("min_time")
        )

    def _handle_assert_custom(self, step: TestStep) -> bool:
        """自定义 Python 脚本断言"""
        script = step.params.get("script", "")
        if not script:
            step.error_message = "自定义断言脚本为空"
            return False

        try:
            # 在受限环境中执行脚本
            context = {
                "actual": step.actual,
                "expected": step.expected,
                "params": step.params,
                "result": False,
            }
            exec(script, {"__builtins__": {}}, context)
            return bool(context.get("result", False))
        except Exception as e:
            step.error_message = f"自定义断言异常: {e}"
            return False

    # ---------- 工具类处理器 ----------

    def _handle_screenshot(self, step: TestStep) -> bool:
        """截图"""
        device = self._get_device(step.device_id)
        if isinstance(device, ADBDevice):
            save_path = step.params.get("save_path", "screenshot.png")
            success = device.take_screenshot(save_path)
            if not success:
                step.error_message = "截图失败"
            return success
        else:
            step.error_message = "截图仅支持 ADB 设备"
            return False

    def _handle_log_message(self, step: TestStep) -> bool:
        """记录日志"""
        message = step.params.get("message", "")
        level = step.params.get("level", "INFO")
        logger.log(level, f"[测试日志] {message}")
        return True

    # ---------- 界面操作处理器 ----------

    def _handle_app_start(self, step: TestStep) -> bool:
        """启动应用（activity 留空时自动解析启动页）"""
        device = self._get_device(step.device_id)
        if not isinstance(device, ADBDevice):
            step.error_message = f"设备不是 ADB 类型: {step.device_id}"
            return False

        package = str(
            step.params.get("package") or step.params.get("pkg") or ""
        ).strip()
        if not package:
            step.error_message = "app_start 缺少 package 参数"
            return False

        # 先强制停止应用，确保每次测试都重新启动
        device.shell(f"am force-stop {shlex.quote(package)}", timeout=5.0)
        import time
        time.sleep(0.3)

        component = str(step.params.get("activity") or "").strip()
        if not component:
            component = device.resolve_launch_activity(package)

        timeout = max(5.0, float(step.timeout or 30.0))
        if component:
            if "/" not in component:
                component = f"{package}/{component}"
            success, output = device.shell(
                f"am start -W -n {component}", timeout=timeout
            )
            failed = (not success or "Error" in output
                      or "Exception" in output
                      or "does not exist" in output)
        else:
            # 无法解析启动页：按包名拉起 LAUNCHER 入口
            success, output = device.shell(
                f"monkey -p {shlex.quote(package)} "
                f"-c android.intent.category.LAUNCHER 1",
                timeout=timeout,
            )
            failed = (not success or "No activities found" in output
                      or "** Error:" in output)

        step.actual = output
        if failed:
            step.error_message = f"启动失败: {output or '未知错误'}"
            return False
        return True

    def _should_redirect_to_game_engine(self, text: str, device) -> bool:
        """
        检查文本是否应该重定向到游戏引擎。

        只有当文本描述的是「进行一局游戏」而非简单按钮点击时才触发。
        需要同时满足：包含游戏关键词 + 包含动作/角色词 + 文本足够长。
        """
        # 五子棋：必须同时包含棋盘类词 AND 动作/角色类词，且文本足够长
        has_gomoku_board = bool(_GOMOKU_HINT_RE.search(text))
        has_gomoku_action = any(
            k in text for k in [
                "执黑", "执白", "你执", "我执", "对弈", "下棋",
                "棋局", "获胜", "胜利", "赢", "进行一局", "来一局",
                "五子棋游戏",
            ]
        )
        if has_gomoku_board and has_gomoku_action and len(text) > 15:
            return True

        # 扫雷：同上
        has_mine = bool(_MINESWEEPER_HINT_RE.search(text))
        has_mine_action = any(
            k in text for k in [
                "扫雷", "雷区", "插旗", "翻格", "通关", "完成一局",
                "进行一局", "来一局",
            ]
        )
        if has_mine and has_mine_action and len(text) > 15:
            return True

        return False

    def _redirect_to_game_engine(self, step: TestStep, device, text: str) -> bool:
        """
        重定向到游戏引擎执行
        
        将 ui_tap 步骤转换为游戏引擎任务
        """
        self._agent_log("INFO", f"🎮 检测到游戏引擎任务，重定向执行：{text[:50]}...")
        
        # 检查五子棋
        if _GOMOKU_HINT_RE.search(text):
            self._agent_log("INFO", "→ 五子棋引擎接管")
            outcome = self._agent_gomoku(step, device, text)
            if outcome is not None:
                return outcome
            self._agent_log("WARN", "五子棋引擎未识别到棋盘，尝试常规点击")
        
        # 检查扫雷
        if _MINESWEEPER_HINT_RE.search(text):
            self._agent_log("INFO", "→ 扫雷引擎接管")
            outcome = self._agent_minesweeper(step, device, text)
            if outcome is not None:
                return outcome
            self._agent_log("WARN", "扫雷引擎未识别到棋盘，尝试常规点击")
        
        # 游戏引擎未接管，回退到常规点击
        return False

    def _handle_ui_tap(self, step: TestStep) -> bool:
        """按界面文字（或坐标）点击；文字不可见时用坐标/视觉兜底"""
        device = self._get_device(step.device_id)
        if not isinstance(device, ADBDevice):
            step.error_message = f"设备不是 ADB 类型: {step.device_id}"
            return False

        text = str(step.params.get("text") or "").strip()
        x, y = step.params.get("x"), step.params.get("y")
        
        # 检查是否应该重定向到游戏引擎
        if text and self._should_redirect_to_game_engine(text, device):
            return self._redirect_to_game_engine(step, device, text)
        
        if text:
            # 画布界面（游戏/视频渲染）读不到文字：
            # 直接走「已学习坐标 → 视觉模型 → 离线 OCR」定位
            nodes = []
            canvas_tried = False
            try:
                nodes = device.dump_ui()
                canvas = _is_canvas_screen(nodes)
            except Exception as e:
                logger.debug(f"画布界面检测失败: {e}")
                canvas = False
            if canvas:
                canvas_tried = True
                ok2, canvas_msg = self._tap_canvas_target(device, text)
                if ok2:
                    step.actual = canvas_msg
                    return True
                if not nodes:
                    step.error_message = canvas_msg
                    step.actual = canvas_msg
                    return False
                # 可能正处于界面切换，继续按文字等待
                logger.debug(f"画布定位未成功，继续文字匹配：{canvas_msg}")

            ok, message = device.find_and_tap(
                text,
                timeout=min(max(3.0, float(step.timeout or 10.0)), 20.0),
                index=int(step.params.get("index", 0) or 0),
                long_press=bool(step.params.get("long_press", False)),
                exact=str(step.params.get("match", "contains")) == "exact",
                cancel=self._engine.checkpoint,
            )
            if ok:
                step.actual = message
                return True

            # 用户已请求停止：不再尝试任何兜底动作
            if self._engine._stop_event.is_set():
                step.error_message = message or "执行已被用户停止"
                step.actual = step.error_message
                return False

            # 文字在系统控件树中不可见（游戏/视频等 SurfaceView 画面）→ 兜底
            if x is not None and y is not None:
                # 兜底 1：步骤已给出坐标
                ok2 = device.input_tap(int(x), int(y))
                step.actual = f"未找到「{text}」，已回退点击坐标 ({x},{y})"
                if not ok2:
                    step.error_message = "点击注入失败"
                return ok2

            # 兜底 2：画布定位（复用已学习坐标 / 视觉模型 / 离线 OCR）
            if canvas_tried:
                step.error_message = message
                step.actual = message
                return False
            ok2, canvas_msg = self._tap_canvas_target(device, text)
            step.actual = canvas_msg
            if not ok2:
                step.error_message = f"{message}；{canvas_msg}"
            return ok2

        if x is None or y is None:
            step.error_message = "ui_tap 需要 text 或 x/y 参数"
            return False
        ok = device.input_tap(int(x), int(y))
        step.actual = f"已点击坐标 ({x},{y})"
        if not ok:
            step.error_message = "点击注入失败"
        return ok

    def _vision_available(self) -> bool:
        """视觉模型是否可用（已配置 API Key 且支持图像输入）"""
        client = getattr(self._assistant, "client", None)
        return bool(
            client and client.configured
            and getattr(client, "supports_vision", lambda: False)()
        )

    def _ocr_locate(self, device, text: str):
        """用离线 OCR 在截图中定位文字，返回中心坐标 (x, y) 或 None"""
        try:
            from ..ai.offline_ocr import is_available, find_text
        except ImportError:
            return None
        if not is_available():
            return None
        try:
            size = device.get_screen_size(use_cache=True) or (1080, 1920)
            raw = device.screencap_bytes()
            if not raw:
                return None
            return find_text(raw, text, screen_size=size)
        except Exception as e:
            logger.debug(f"离线 OCR 定位失败: {e}")
            return None

    def _vision_locate(self, device, text: str):
        """用视觉模型在截图中定位文字，返回中心坐标 (x, y) 或 None"""
        client = getattr(self._assistant, "client", None)
        if client is None or not client.configured:
            logger.debug(f"视觉定位不可用：未配置 AI 模型（目标「{text}」）")
            return None
        if not getattr(client, "supports_vision", lambda: False)():
            logger.debug(
                f"视觉定位不可用：当前模型 {getattr(client.config, 'model', '')} "
                "不是多模态模型，已自动改用离线 OCR"
            )
            return None
        try:
            from ..ai.prompts import LOCATE_SYSTEM
            size = device.get_screen_size(use_cache=True) or (1080, 1920)
            raw = device.screencap_bytes()
            if not raw:
                return None
            image_b64 = base64.b64encode(raw).decode("ascii")
            prompt = (
                f"设备截图为 {size[0]}x{size[1]} 像素。"
                f"请定位界面中的文字「{text}」，返回其中心点像素坐标。"
            )
            data = client.chat_json(
                LOCATE_SYSTEM, prompt, image_b64=image_b64, timeout=30.0
            )
            if not data.get("found"):
                return None
            vx, vy = int(data.get("x")), int(data.get("y"))
            if 0 <= vx <= size[0] and 0 <= vy <= size[1]:
                return (vx, vy)
        except Exception as e:
            logger.debug(f"视觉定位失败: {e}")
        return None

    def _handle_ui_input(self, step: TestStep) -> bool:
        """向输入框输入文字"""
        device = self._get_device(step.device_id)
        if not isinstance(device, ADBDevice):
            step.error_message = f"设备不是 ADB 类型: {step.device_id}"
            return False

        text = str(step.params.get("text") or "")
        if not text:
            step.error_message = "ui_input 缺少 text 参数"
            return False

        ok, message = device.ui_input_text(
            text, clear=bool(step.params.get("clear", True))
        )
        if ok and step.params.get("submit"):
            device.keyevent(66)
            message += "，并已提交"

        # 输入失败时，用视觉/OCR 兜底验证文字是否实际已出现在界面上
        if not ok and not self._engine._stop_event.is_set():
            located = self._vision_locate(device, text)
            if located:
                vx, vy = located
                step.actual = (
                    f"输入接口报失败，但视觉模型在截图 ({vx},{vy}) 处确认「{text}」已出现"
                )
                return True
            located = self._ocr_locate(device, text)
            if located:
                vx, vy = located
                step.actual = (
                    f"输入接口报失败，但离线 OCR 在截图 ({vx},{vy}) 处确认「{text}」已出现"
                )
                return True

        step.actual = message
        if not ok:
            step.error_message = message
        return ok

    def _handle_ui_wait_text(self, step: TestStep) -> bool:
        """等待界面出现指定文字（页面加载 / 结果校验）"""
        device = self._get_device(step.device_id)
        if not isinstance(device, ADBDevice):
            step.error_message = f"设备不是 ADB 类型: {step.device_id}"
            return False

        raw = str(step.params.get("text") or step.expected or "")
        # 纯空格文本不做 strip（空格本身就是要等待的内容）
        text = raw if raw.strip() == "" else raw.strip()
        if not text:
            step.error_message = "ui_wait_text 缺少 text 参数"
            return False

        ok, message = device.wait_for_text(
            text,
            timeout=max(1.0, float(step.timeout or 10.0)),
            exact=str(step.params.get("match", "contains")) == "exact",
            interval=float(step.params.get("interval", 0.8) or 0.8),
            cancel=self._engine.checkpoint,
        )
        # 文字在控件树里读不到（画布/游戏渲染界面）时，让视觉模型看截图确认
        if not ok and not self._engine._stop_event.is_set():
            located = self._vision_locate(device, text)
            if located:
                vx, vy = located
                step.actual = (
                    f"系统控件无「{text}」，视觉模型在截图 ({vx},{vy}) 处确认已出现"
                )
                return True
            # 视觉模型不可用时，尝试离线 OCR 确认
            located = self._ocr_locate(device, text)
            if located:
                vx, vy = located
                step.actual = (
                    f"系统控件无「{text}」，离线 OCR 在截图 ({vx},{vy}) 处确认已出现"
                )
                return True
        step.actual = message
        if not ok:
            step.error_message = message
        return ok

    def _handle_ui_swipe(self, step: TestStep) -> bool:
        """滑动/滚动界面；带 until_text 时反复滑动直到目标文字出现"""
        device = self._get_device(step.device_id)
        if not isinstance(device, ADBDevice):
            step.error_message = f"设备不是 ADB 类型: {step.device_id}"
            return False

        direction = str(step.params.get("direction") or "up").strip().lower()
        if direction not in ("up", "down", "left", "right"):
            direction = "up"
        region = str(step.params.get("region") or "full").strip().lower()
        duration = int(step.params.get("duration", 400) or 400)
        until_text = str(step.params.get("until_text") or "").strip()
        max_swipes = int(step.params.get("max_swipes", 10) or 10)

        size = device.get_screen_size(use_cache=True)
        if not size:
            step.error_message = "无法获取屏幕分辨率"
            return False
        # 使用较小的滑动幅度（每次滑动屏幕的 1/3），避免划过头
        start, end = _swipe_coords(size, region, direction, swipe_ratio=0.33)

        if not until_text:
            ok = device.input_swipe(*start, *end, duration)
            step.actual = f"滑动 {direction}/{region} ({start}->{end})"
            if not ok:
                step.error_message = "滑动指令执行失败"
            return ok

        deadline = time.time() + max(3.0, float(step.timeout or 20.0))
        count = 0
        while True:
            if self._engine.checkpoint():
                step.error_message = "执行已被用户停止"
                return False
            nodes = device.dump_ui()
            if find_nodes(nodes, until_text):
                step.actual = f"滑动 {count} 次后找到「{until_text}」"
                return True
            # 控件树找不到时，滑动几次后再用视觉/OCR 检测（避免每次都调用慢接口）
            if count >= 2 and not self._engine._stop_event.is_set():
                located = self._vision_locate(device, until_text)
                if located:
                    step.actual = f"滑动 {count} 次后，视觉模型确认「{until_text}」已出现"
                    return True
                located = self._ocr_locate(device, until_text)
                if located:
                    step.actual = f"滑动 {count} 次后，OCR 确认「{until_text}」已出现"
                    return True
            if count >= max_swipes or time.time() >= deadline:
                step.error_message = (
                    f"滑动 {count} 次仍未找到「{until_text}」"
                )
                return False
            device.input_swipe(*start, *end, duration)
            count += 1
            time.sleep(0.8)  # 减少等待时间，提升滑动响应速度

    # ---------- 自主任务（观察-决策-行动） ----------

    def _handle_agent_task(self, step: TestStep) -> bool:
        """
        自主决策执行：把开放式目标（如“自己下一局五子棋并获胜”）交给 AI 智能体，
        循环 观察界面 → 让模型决策下一步 → 执行动作，直到 done/fail 或达到步数上限。
        """
        device = self._get_device(step.device_id)
        if not isinstance(device, ADBDevice):
            step.error_message = f"自主任务需要 ADB 设备: {step.device_id or '（未连接）'}"
            return False

        goal = str(step.params.get("goal") or step.description or "").strip()
        if not goal:
            step.error_message = "agent_task 缺少 goal 目标"
            return False

        client = getattr(self._assistant, "client", None)
        if client is None or not client.configured:
            # 没有 API Key 时退化为离线规则引擎：把目标里能确定的点击动作做完，
            # 开放式目标（如“自己下完并获胜”）交由用户配置视觉模型后再执行。
            return self._agent_offline(step, device, goal)

        max_steps = max(1, int(step.params.get("max_steps", 40) or 40))
        deadline = time.time() + max(30.0, float(step.timeout or 300.0))
        from ..ai.prompts import AGENT_SYSTEM

        def log(level: str, message: str):
            try:
                self._engine.signals.log_output.emit(level, message)
            except Exception:
                logger.info(message)

        log("INFO", f"🤖 自主任务开始：{goal}")
        # 加载应用知识（如果有）
        app_knowledge = self._load_app_knowledge(device, client)
        if app_knowledge:
            log("INFO", f"📚 已加载应用知识: {app_knowledge.get('name', '未知')} "
                        f"(v{app_knowledge.get('version', 0)}, "
                        f"{len(app_knowledge.get('rules', []))} 条规则)")
        recent_actions = []       # 最近的 (action, target) 指纹，用于死循环检测
        state = {"done": False, "fail": False, "reason": ""}
        used = 0

        while used < max_steps and time.time() < deadline:
            if self._engine.checkpoint():
                step.error_message = "自主任务已被用户停止"
                step.actual = step.error_message
                return False
            used += 1
            nodes, taps, lines, ocr_items = self._agent_observe(device)
            if not nodes:
                log("WARN", f"第 {used} 步：界面结构抓取失败，等待后重试")
                time.sleep(1.0)
                continue

            image_b64 = self._agent_screenshot(device, client)
            prompt = self._agent_prompt(goal, used, max_steps, lines, state, app_knowledge)

            try:
                decision = client.chat_json(
                    AGENT_SYSTEM, prompt, image_b64=image_b64,
                    timeout=min(60.0, max(5.0, deadline - time.time())),
                )
            except Exception as e:
                log("ERROR", f"第 {used} 步决策失败：{e}")
                step.error_message = f"AI 决策失败：{e}"
                return False

            action = str(decision.get("action") or "").strip().lower()
            thought = str(decision.get("thought") or "").strip()
            reason = str(decision.get("reason") or "").strip()
            log("INFO", f"第 {used}/{max_steps} 步 · 决策={action or '?'}"
                        f"{'｜' + thought if thought else ''}")

            if action == "done":
                state["done"] = True
                state["reason"] = reason or "模型判断目标已达成"
                log("INFO", f"✅ 自主任务完成：{state['reason']}")
                step.actual = f"自主任务完成（{used} 步）：{state['reason']}"
                return True
            if action == "fail":
                step.error_message = (
                    f"自主任务在第 {used} 步判定失败：{reason or '模型未能达成目标'}"
                )
                step.actual = step.error_message
                return False

            ok, desc, fingerprint = self._agent_act(device, taps, decision, ocr_items)
            log("INFO", f"    执行：{desc}")

            # 死循环检测：连续重复同一动作则提示模型换思路
            if fingerprint:
                recent_actions.append(fingerprint)
                if len(recent_actions) > 4:
                    recent_actions.pop(0)
                if (len(recent_actions) >= 3
                        and len(set(recent_actions[-3:])) == 1):
                    log("WARN", "    检测到重复动作，请在下一步更换策略（如滑动/返回）")
                    state["reason"] = "上一步动作重复，界面没有变化，请换一个动作"
            time.sleep(0.5)

        step.error_message = (
            f"自主任务达到上限（{used}/{max_steps} 步）仍未完成目标"
        )
        step.actual = step.error_message
        log("ERROR", step.error_message)
        return False

    def _agent_observe(self, device):
        """
        抓取当前界面，返回 (原始节点, 可操作节点列表, 供模型阅读的文本行, OCR 文字)。

        画布渲染界面（游戏/视频）控件树读不到文字时，自动调用离线 OCR，
        把识别到的文字和坐标一并交给模型，可用 tap_text 直接按文字点击。
        """
        nodes = device.dump_ui()
        taps = []
        seen = set()
        for node in nodes:
            if not (node.clickable or node.label or node.is_edit_text):
                continue
            x, y = node.center
            if (x, y) in seen:
                continue
            seen.add((x, y))
            taps.append(node)
            if len(taps) >= 60:
                break

        lines = []
        for i, node in enumerate(taps):
            x, y = node.center
            label = node.label or node.cls.split(".")[-1]
            flags = "可点" if node.clickable else ("输入框" if node.is_edit_text else "文本")
            lines.append(f"[{i}] {label} @({x},{y}) {flags}")

        visible_list = _readable_texts(nodes, 20)
        ocr_items = []
        if not visible_list or _is_canvas_screen(nodes):
            ocr_items = self._agent_ocr(device)
            if ocr_items:
                visible_list = [t for t, _x, _y in ocr_items[:20]]

        visible = "、".join(visible_list) or "（无可见文字，可能为渲染画面）"
        header = [f"当前界面可见文字：{visible}"]
        if ocr_items:
            header.append("画布界面 OCR 文字（重要：可直接用 tap_text 按文字点击）：")
            for i, (text, x, y) in enumerate(ocr_items[:30]):
                header.append(f"[T{i}] {text} @({x},{y})")
        return nodes, taps, header + lines, ocr_items

    def _agent_ocr(self, device, limit: int = 30):
        """读取画布界面文字，返回 [(文字, x, y)]（OCR 不可用时为空）"""
        try:
            from ..ai.offline_ocr import is_available, find_all_texts
        except ImportError:
            return []
        if not is_available():
            return []
        try:
            size = device.get_screen_size(use_cache=True) or (1080, 1920)
            raw = device.screencap_bytes()
            if not raw:
                return []
            found = []
            seen = set()
            for text, (x, y), _conf in find_all_texts(raw, size):
                text = str(text).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                found.append((text, int(x), int(y)))
                if len(found) >= limit:
                    break
            return found
        except Exception as e:
            logger.debug(f"自主任务 OCR 读取失败: {e}")
            return []

    def _agent_screenshot(self, device, client):
        """视觉模型可用时返回截图 base64，否则返回 None"""
        if not getattr(client, "supports_vision", lambda: False)():
            return None
        try:
            raw = device.screencap_bytes()
        except Exception:
            raw = None
        return base64.b64encode(raw).decode("ascii") if raw else None

    @staticmethod
    def _agent_prompt(goal, used, max_steps, lines, state, app_knowledge=None) -> str:
        parts = [
            f"目标：{goal}",
            f"进度：第 {used}/{max_steps} 步。",
        ]
        if state.get("reason"):
            parts.append(f"提示：{state['reason']}")
        # 注入应用知识（如果有）
        if app_knowledge:
            parts.append("\n【应用知识】")
            if app_knowledge.get("rules"):
                parts.append("规则：" + "；".join(app_knowledge["rules"][:5]))
            if app_knowledge.get("controls"):
                parts.append("操作：" + "；".join(app_knowledge["controls"][:5]))
            if app_knowledge.get("win_condition"):
                parts.append(f"胜利条件：{app_knowledge['win_condition']}")
            if app_knowledge.get("lose_condition"):
                parts.append(f"失败条件：{app_knowledge['lose_condition']}")
        parts.append("\n当前界面可操作控件：")
        parts.extend(lines)
        parts.append("请决定下一步动作（只输出一个 JSON 对象）。")
        return "\n".join(parts)

    def _load_app_knowledge(self, device, client) -> Optional[Dict[str, Any]]:
        """
        加载当前前台应用的知识：已有则直接返回，否则触发网络学习。
        返回知识字典或 None。
        """
        try:
            from ..ai.knowledge import get_knowledge_base
            from ..ai.web_learner import get_or_learn
        except ImportError as e:
            logger.debug(f"知识系统导入失败: {e}")
            return None

        package = self._foreground_package(device)
        if not package:
            return None

        kb = get_knowledge_base()
        # 尝试从知识库加载
        existing = kb.load(package)
        if existing:
            logger.debug(f"使用已有知识: {package}")
            return existing

        # 没有知识，尝试从网络学习（需要 AI 客户端）
        if client is None or not client.configured:
            logger.debug(f"无 AI 客户端，跳过网络学习: {package}")
            return None

        # 从包名推断应用名（简单取最后一段）
        app_name = package.split(".")[-1] if "." in package else package
        # 常见应用名映射
        name_map = {
            "sweepmine": "扫雷",
            "gomoku": "五子棋",
            "minesweeper": "扫雷",
        }
        for key, val in name_map.items():
            if key in package.lower():
                app_name = val
                break

        try:
            learned = get_or_learn(app_name, package, client, kb)
            return learned
        except Exception as e:
            logger.warning(f"网络学习失败 {package}: {e}")
            return None

    def _agent_act(self, device, taps, decision, ocr_items=None):
        """执行模型给出的动作，返回 (是否成功, 描述, 用于死循环检测的指纹)"""
        action = str(decision.get("action") or "").strip().lower()

        if action == "tap_text":
            target = str(decision.get("text") or "").strip()
            if not target:
                return False, "tap_text 缺少 text", None
            # 与用例里的 ui_tap 同一条链路：学习坐标 → 视觉 → 离线 OCR
            ok, message = self._tap_canvas_target(device, target)
            return ok, message, (("tap", target) if ok else None)

        if action == "tap_index":
            try:
                idx = int(decision.get("index", -1))
            except (TypeError, ValueError):
                idx = -1
            if idx < 0 or idx >= len(taps):
                return False, f"点击 index={idx} 越界", None
            node = taps[idx]
            x, y = node.center
            ok = device.input_tap(x, y)
            label = node.label or node.cls.split(".")[-1]
            return ok, f"点击「{label}」({x},{y})", ("tap", x, y)

        if action == "tap_xy":
            try:
                x = int(decision.get("x"))
                y = int(decision.get("y"))
            except (TypeError, ValueError):
                return False, "tap_xy 缺少有效坐标", None
            ok = device.input_tap(x, y)
            return ok, f"点击坐标 ({x},{y})", ("tap", x, y)

        if action == "swipe":
            direction = str(decision.get("direction") or "up").strip().lower()
            if direction not in ("up", "down", "left", "right"):
                direction = "up"
            size = device.get_screen_size(use_cache=True)
            if not size:
                return False, "无法获取屏幕分辨率", None
            start, end = _swipe_coords(size, "center", direction)
            ok = device.input_swipe(*start, *end, 400)
            return ok, f"向{direction}滑动", ("swipe", direction)

        if action == "input":
            text = str(decision.get("text") or "")
            if not text:
                return False, "input 缺少 text", None
            ok, message = device.ui_input_text(text)
            return ok, message, ("input", text)

        if action == "back":
            ok = device.keyevent(4)
            return ok, "返回上一级", ("back",)

        if action == "wait":
            time.sleep(1.2)
            return True, "等待界面变化", None

        return False, f"未知动作：{action or '（空）'}", None

    # ---------- 离线规则引擎（未配置 AI 时的自主任务兜底） ----------

    def _agent_log(self, level: str, message: str):
        try:
            self._engine.signals.log_output.emit(level, message)
        except Exception:
            logger.info(message)

    @staticmethod
    def _match_node_by_target(nodes, target: str):
        """
        按 resource-id 的语义片段匹配控件。
        例如目标「人机对弈」→ single/pve/computer/robot/game/chess，
        可命中 rid=.../single_game_ib 这种无文字的 ImageButton。
        返回 (最匹配的节点 或 None, 用到的 token 列表)。
        """
        # 内置同义词 + 用户自定义同义词（~/.vehicle_test_app/ui_synonyms.yaml）
        synonyms = dict(_RID_SYNONYMS)
        try:
            from ..ai.ui_memory import extra_synonyms
            for key, extra in extra_synonyms().items():
                merged = tuple(dict.fromkeys(synonyms.get(key, ()) + extra))
                synonyms[key] = merged
        except Exception as e:
            logger.debug(f"自定义同义词加载失败: {e}")

        tokens: List[str] = []
        for key, syns in synonyms.items():
            if key in target:
                tokens.extend(syns)
        tokens.extend(w.lower() for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", target))
        tokens = list(dict.fromkeys(tokens))
        # 通用词（game/play/btn/list…）几乎没有区分度，
        # 单独命中它们会点错按钮，因此必须至少命中一个「强 token」。
        strong = [
            tok for tok in tokens
            if tok in _STRONG_SHORT_TOKENS
            or (len(tok) >= 4 and tok not in _GENERIC_RID_TOKENS)
        ]
        if not strong:
            return None, tokens

        scored = []
        for node in nodes:
            # 只取 resource-id 的名字段，避免包名（com.xxx.main 里的 ai）误匹配
            rid = (node.rid or "").rsplit("/", 1)[-1].lower()
            if not rid:
                continue
            score = sum(len(tok) for tok in strong if tok in rid)
            if score <= 0:
                continue
            scored.append((score, node))

        if not scored:
            return None, strong
        scored.sort(key=lambda item: (
            -item[0],
            0 if item[1].clickable else 1,
            0 if item[1].enabled else 1,
            item[1].area,
        ))
        best = scored[0][1]
        best_rid = (best.rid or "").rsplit("/", 1)[-1].lower()
        return best, [tok for tok in strong if tok in best_rid]

    def _foreground_package(self, device) -> str:
        """当前前台应用包名（用于界面方法记忆的分组键）"""
        try:
            package, _activity = device.get_foreground_app()
            return package or ""
        except Exception:
            return ""

    def _tap_canvas_target(self, device, target: str):
        """
        在「读不到文字的画布界面」上定位并点击一个目标。

        顺序：已学习坐标 → 视觉模型 → 离线 OCR；
        定位成功后会记住坐标（同一应用/分辨率下次直接复用）。
        返回 (是否成功, 描述)。
        """
        size = device.get_screen_size(use_cache=True)
        package = self._foreground_package(device)

        # 1) 复用已学习坐标（程序自动沉淀，也可以由用户手动配置）
        cached = None
        try:
            from ..ai.ui_memory import lookup
            cached = lookup(package, size, target)
        except Exception as e:
            logger.debug(f"界面方法记忆查询失败: {e}")
        if cached:
            cx, cy = cached
            if device.input_tap(cx, cy):
                return True, f"复用已学习坐标「{target}」({cx},{cy})"
            return False, f"复用坐标「{target}」({cx},{cy}) 后点击注入失败"

        # 2) 视觉模型
        located = self._vision_locate(device, target)
        how = "视觉定位"
        # 3) 离线 OCR（无需 API Key）
        if not located:
            located = self._ocr_locate(device, target)
            how = "离线 OCR 定位"
        if located:
            vx, vy = located
            if not device.input_tap(vx, vy):
                return False, f"{how}「{target}」({vx},{vy}) 后点击注入失败"
            try:
                from ..ai.ui_memory import remember
                remember(package, size, target, vx, vy)
            except Exception as e:
                logger.debug(f"界面方法记忆写入失败: {e}")
            return True, f"界面文字不可见，{how}「{target}」({vx},{vy})"

        return False, (
            f"画布界面未能定位「{target}」：系统控件无文字、"
            "OCR 未识别到目标。可在 ~/.vehicle_test_app/ui_methods.yaml "
            "里为该目标补充坐标，下次执行直接复用"
        )

    def _tap_target(self, device, target: str):
        """按 文字/描述 → resource-id 语义 → 画布兜底 的顺序点击一个目标"""
        target = _TARGET_TAIL_RE.sub("", target.strip())
        if not target:
            return False, "点击目标为空"

        nodes = device.dump_ui()
        canvas = _is_canvas_screen(nodes)

        if nodes:
            node = None
            how = ""
            matches = dedupe_nodes(find_nodes(nodes, target))
            if matches:
                node, how = best_node(matches), "文字"
            if node is None:
                node, tokens = self._match_node_by_target(nodes, target)
                if node is not None:
                    how = "resource-id(" + "/".join(tokens[:3]) + ")"
            if node is not None:
                x, y = node.center
                if not device.input_tap(x, y):
                    return False, f"点击「{target}」({x},{y}) 注入失败"
                label = (node.label or node.rid.rsplit("/", 1)[-1]
                         or node.cls.split(".")[-1])
                return True, f"已点击「{label}」({x},{y}，{how}匹配)"

        # 画布/游戏界面里系统读不到文字：学习坐标 + 视觉/OCR 定位
        if canvas:
            ok, message = self._tap_canvas_target(device, target)
            if ok:
                return True, message
            if not nodes:
                return False, f"界面结构抓取失败（uiautomator dump 无返回）；{message}"

        # 文字兜底：页面可能还在加载
        ok, msg = device.find_and_tap(
            target, timeout=3.0, cancel=self._engine.checkpoint,
        )
        if ok:
            return True, msg

        # 最后兜底：画布定位（复用已学习坐标 / 视觉模型 / 离线 OCR）
        ok2, canvas_msg = self._tap_canvas_target(device, target)
        if ok2:
            return True, canvas_msg
        return False, canvas_msg or msg

    def _screen_texts(self, device, limit: int = 40) -> List[str]:
        """读取当前界面文字：控件树优先，画布界面回退 OCR"""
        try:
            texts = _readable_texts(device.dump_ui(), limit)
        except Exception:
            texts = []
        if not texts:
            try:
                from ..ai.offline_ocr import find_all_texts
                raw = device.screencap_bytes()
                if raw:
                    texts = [
                        t for t, _pos, _conf in find_all_texts(raw)
                        if t.strip().lower() not in _SURFACE_LABELS
                    ][:limit]
            except Exception as e:
                logger.debug(f"OCR 读取界面文字失败: {e}")
        return texts

    def _wait_text_smart(self, device, text: str, timeout: float = 10.0):
        """等待界面文字出现；控件树读不到时用 OCR 轮询（画布界面）"""
        ok, _msg = device.wait_for_text(
            text, timeout=timeout, cancel=self._engine.checkpoint,
        )
        if ok:
            return True, f"界面已出现「{text}」"
        if self._engine._stop_event.is_set():
            return False, "执行已被用户停止"
        deadline = time.time() + min(5.0, max(1.0, timeout))
        while time.time() < deadline:
            if self._engine.checkpoint():
                return False, "执行已被用户停止"
            if self._ocr_locate(device, text):
                return True, f"画布界面 OCR 确认「{text}」已出现"
            time.sleep(0.8)
        return False, f"等待「{text}」超时"

    def _tap_target_retry(self, device, target: str, timeout: float = 8.0):
        """反复尝试点击目标（界面加载/弹窗延迟时自动重试）"""
        deadline = time.time() + max(2.0, float(timeout))
        last = ""
        while True:
            if self._engine.checkpoint():
                return False, "执行已被用户停止"
            ok, message = self._tap_target(device, target)
            if ok:
                return True, message
            last = message
            if time.time() >= deadline:
                return False, last
            time.sleep(0.5)

    def _agent_exec_action(self, device, verb: str, target: str, extra: dict):
        """执行一条解析出的确定性动作，返回 (是否成功, 描述)"""
        from ..ai.generator import (
            _SWIPE_VERBS, _LAUNCH_VERBS, _INPUT_VERBS, _TAP_VERBS,
        )

        if verb in ("截图", "截屏"):
            ok = device.take_screenshot("screenshots/agent_task.png")
            return ok, "截取当前屏幕"

        if verb in _SWIPE_VERBS:
            direction = str(extra.get("direction") or "up")
            region = str(extra.get("region") or "full")
            size = device.get_screen_size(use_cache=True)
            if not size:
                return False, "无法获取屏幕分辨率"
            start, end = _swipe_coords(size, region, direction)
            until_text = str(extra.get("until_text") or "").strip()
            if until_text:
                deadline = time.time() + 15.0
                count = 0
                while count < 10 and time.time() < deadline:
                    if self._engine.checkpoint():
                        return False, "执行已被用户停止"
                    nodes = device.dump_ui()
                    if find_nodes(nodes, until_text) or self._ocr_locate(
                            device, until_text):
                        return True, f"滑动 {count} 次后出现「{until_text}」"
                    device.input_swipe(*start, *end, 400)
                    count += 1
                    time.sleep(0.4)
                return False, f"滑动 {count} 次仍未出现「{until_text}」"
            ok = device.input_swipe(*start, *end, 400)
            return ok, f"向{direction}滑动（{region} 区域）"

        if verb in _INPUT_VERBS:
            if not target:
                return False, "输入内容为空"
            ok, message = device.ui_input_text(target, clear=True)
            return ok, message

        if verb == "返回":
            ok = device.keyevent(4)
            return ok, "返回上一级"

        if verb in ("退出", "关闭"):
            # 先尝试按文字点击，失败再按返回键
            ok, message = self._tap_target(device, target or verb)
            if ok:
                return True, message
            ok = device.keyevent(4)
            return ok, f"未找到「{target or verb}」，已按返回键"

        if verb == "找到":
            if not target:
                return False, "未指明要查找的文字"
            return self._wait_text_smart(device, target, timeout=8.0)

        if verb in _TAP_VERBS or verb in _LAUNCH_VERBS:
            if not target:
                return False, f"{verb}缺少点击目标"
            if target in ("返回", "退出"):
                ok = device.keyevent(4)
                return ok, f"按返回键（{target}）"
            if extra.get("sequenced"):
                time.sleep(1.5)
                return True, f"等待界面切换（{verb}{target}）"
            return self._tap_target_retry(device, target)

        if target:
            return self._tap_target_retry(device, target)
        return False, f"暂不支持的动作：{verb}"

    def _agent_check_goal(self, device, goal_clause: str,
                          wait_seconds: float = 5.0):
        """
        轮询界面（控件文字 + 画布 OCR）判断开放式目标是否达成。
        返回 (True 成功 / False 失败 / None 未判定, 依据说明)。
        """
        win_words = ("胜利", "获胜", "赢了", "你赢了", "您赢了", "通关", "过关")
        fail_words = ("失败", "输了", "告负", "你输了", "您输了", "未通过")
        done_words = ("成功", "完成", "已保存", "已提交")
        wants_win = bool(re.search(r"(胜利|获胜|赢|通关|过关)", goal_clause))
        wants_done = bool(re.search(r"(完成|成功|保存|提交)", goal_clause))

        deadline = time.time() + max(1.0, wait_seconds)
        last_texts: List[str] = []
        while True:
            if self._engine.checkpoint():
                return None, "执行已被用户停止"
            last_texts = self._screen_texts(device)
            joined = " ".join(last_texts)
            hit = next((w for w in win_words if w in joined), "")
            if hit:
                return True, f"检测到「{hit}」，目标已达成"
            if wants_done:
                hit = next((w for w in done_words if w in joined), "")
                if hit:
                    return True, f"检测到「{hit}」，目标已达成"
            if wants_win:
                hit = next((w for w in fail_words if w in joined), "")
                if hit:
                    return False, f"检测到「{hit}」，目标未达成"
            if time.time() >= deadline:
                visible = "、".join(last_texts[:8]) or "（无可见文字）"
                return None, f"界面暂无明确结果（可见：{visible}）"
            time.sleep(1.0)

    def _agent_open_goal(self, step: TestStep, device, goal_clause: str,
                         full_goal: str = ""):
        """
        处理开放式目标（赢一局/完成流程）。
        先执行子句里能确定的动作，再尝试内置引擎与结果判定。
        返回 (最终结果 True/False/None, 是否已接管, 描述)。
        """
        from ..ai.generator import (
            _parse_ui_actions, _split_clauses, _normalize_clause,
        )

        executed: List[str] = []

        # 1) 子句内仍可确定的动作（如“进入游戏后获得胜利”里的点击开始游戏）
        #    逐个子句单独解析，避免整句被再次折叠成「自主」而漏掉点击
        for clause in _split_clauses(_normalize_clause(goal_clause)):
            for verb, target, extra in _parse_ui_actions(clause):
                if verb == "自主":
                    continue
                if verb == "等待":
                    if target and not extra.get("timed"):
                        ok, msg = self._wait_text_smart(
                            device, target, timeout=6.0)
                        if ok:
                            executed.append(msg)
                    else:
                        time.sleep(min(3.0, float(
                            extra.get("duration") or 1.5)))
                    continue
                if self._engine.checkpoint():
                    return None, True, "执行已被用户停止"
                ok, msg = self._agent_exec_action(
                    device, verb, target, extra)
                self._agent_log("INFO" if ok else "WARN", f"    {msg}")
                if ok:
                    executed.append(target or verb)
                    time.sleep(1.0)
                elif verb in ("点击", "点一下", "选择", "进入", "打开"):
                    # 开放式目标不因单个点击失败而中断
                    self._agent_log("WARN", f"    ⚠ {msg}")

        prefix = f"已执行 {'、'.join(executed)}；" if executed else ""

        # 2) 五子棋类目标：内置视觉引擎真正下完并争胜
        hint_text = f"{full_goal} {goal_clause}"
        if _GOMOKU_HINT_RE.search(hint_text):
            outcome = self._agent_gomoku(step, device, hint_text)
            if outcome is not None:
                return outcome, True, step.actual or step.error_message
            executed.append("五子棋引擎未接管（未识别到棋盘）")

        # 2.5) 扫雷类目标：内置视觉引擎（识别雷区 + 规则推理，不逐格 OCR）
        outcome = self._agent_minesweeper(step, device, hint_text)
        if outcome is not None:
            return outcome, True, step.actual or step.error_message

        # 3) 结果判定：轮询界面文字（含画布 OCR）
        verdict, evidence = self._agent_check_goal(device, goal_clause)
        if verdict is True:
            return True, True, f"{prefix}{evidence}"
        if verdict is False:
            return False, True, f"{prefix}{evidence}"

        # 4) 开放式目标（如"获得胜利"）：尝试主动推进
        #    对于游戏类目标，点击屏幕中央等安全位置，看能否触发进展
        if any(k in hint_text for k in ("胜利", "赢", "获胜", "完成", "通关")):
            size = device.get_screen_size(use_cache=True)
            if size:
                cx, cy = size[0] // 2, size[1] // 2
                max_attempts = 3
                for attempt in range(max_attempts):
                    if self._engine.checkpoint():
                        return None, True, "执行已被用户停止"
                    self._agent_log("INFO", f"    尝试点击屏幕中央 ({cx},{cy}) 第{attempt+1}次")
                    device.input_tap(cx, cy)
                    time.sleep(1.5)
                    verdict, evidence = self._agent_check_goal(device, goal_clause)
                    if verdict is True:
                        return True, True, f"{prefix}点击推进后{evidence}"
                    if verdict is False:
                        return False, True, f"{prefix}点击推进后{evidence}"
                executed.append(f"尝试点击中央{max_attempts}次未达目标")

        return None, False, (
            f"{prefix}{evidence}；该开放式目标需要视觉模型（设置里配置 API Key）"
            "或该游戏的可执行策略才能自动完成"
        )

    def _agent_offline(self, step: TestStep, device, goal: str) -> bool:
        """
        未配置 AI 模型时的自主任务：
        用与用例生成相同的通用解析器拆解目标，逐条执行确定性动作，
        开放式目标（赢一局等）交给内置引擎/结果判定。
        因此换一个应用或游戏不需要改程序，只改自然语言描述即可。
        """
        from ..ai.generator import _parse_ui_actions

        self._agent_log("INFO", f"🤖 自主任务（离线规则模式）：{goal}")
        actions = _parse_ui_actions(goal) or [("自主", goal, {"goal": goal})]

        executed: List[str] = []
        notes: List[str] = []
        for verb, target, extra in actions:
            if self._engine.checkpoint():
                step.error_message = "自主任务已被用户停止"
                step.actual = step.error_message
                return False

            if verb == "自主":
                goal_clause = extra.get("goal") or target or goal
                result, handled, desc = self._agent_open_goal(
                    step, device, goal_clause, full_goal=goal,
                )
                if handled:
                    step.actual = desc
                    if result:
                        self._agent_log("INFO", f"✅ {desc}")
                        return True
                    step.error_message = desc
                    self._agent_log("WARN", f"⚠ {desc}")
                    return False
                if desc:
                    notes.append(desc)
                continue

            if verb == "等待":
                if target and not extra.get("timed"):
                    ok, msg = self._wait_text_smart(device, target, timeout=10.0)
                else:
                    duration = min(30.0, max(0.2, float(
                        extra.get("duration") or 2.0)))
                    time.sleep(duration)
                    ok, msg = True, f"等待 {duration:g} 秒"
                self._agent_log("INFO" if ok else "WARN", f"    {msg}")
                if not ok:
                    step.error_message = f"自主任务：{msg}"
                    step.actual = step.error_message
                    return False
                continue

            ok, msg = self._agent_exec_action(device, verb, target, extra)
            self._agent_log("INFO" if ok else "WARN", f"    {msg}")
            if not ok:
                step.error_message = f"自主任务：{msg}"
                step.actual = step.error_message
                return False
            executed.append(target or verb)
            time.sleep(1.0)

        parts: List[str] = []
        if executed:
            parts.append("已执行 " + "、".join(executed))
        parts.extend(notes)
        if parts:
            step.actual = "自主任务完成（离线规则）：" + "；".join(parts)
        else:
            step.actual = "自主任务完成（离线规则）"
        self._agent_log("INFO", f"✅ {step.actual}")
        return True

    # ---------- 内置五子棋视觉引擎 ----------

    def _read_board(self, device, timeout: float = 8.0):
        """反复截图直到识别出棋盘；识别不到返回 None"""
        from ..ai.board_vision import detect_board, detect_board_from_raw

        deadline = time.time() + timeout
        while True:
            if self._engine.checkpoint():
                return None

            # 优先尝试原始帧（更快），失败再用 PNG 截图
            board = None
            frame = device.screencap_frame(timeout=5.0)
            if frame:
                width, height, fmt, raw_data = frame
                board = detect_board_from_raw(raw_data, width, height, fmt)

            if board is None:
                raw = device.screencap_bytes()
                board = detect_board(raw) if raw else None

            if board is not None:
                return board
            if time.time() >= deadline:
                return None
            time.sleep(0.5)  # 减少等待时间，从 0.8 降到 0.5

    @staticmethod
    def _gomoku_result_text(device):
        """从界面文字判断是否出现了「您赢了 / 您输了」结算弹窗"""
        try:
            nodes = device.dump_ui(timeout=8)
        except Exception:
            return None
        text = " ".join(visible_texts(nodes, 40))
        if any(k in text for k in ("您赢了", "你赢了", "获胜", "胜利")):
            return "win"
        if any(k in text for k in ("您输了", "你输了", "失败", "告负")):
            return "lose"
        return None

    @staticmethod
    def _gomoku_scores(device):
        """读取「自己 / 电脑」比分；画布弹窗取不到文字时用它兜底判断胜负"""
        try:
            nodes = device.dump_ui(timeout=8)
        except Exception:
            return None
        human = computer = None
        for n in nodes:
            rid = (n.rid or "").rsplit("/", 1)[-1]
            text = (n.text or "").strip()
            if not text.isdigit():
                continue
            if rid == "human_score_tv":
                human = int(text)
            elif rid == "computer_score_tv":
                computer = int(text)
        if human is None or computer is None:
            return None
        return human, computer

    def _agent_minesweeper(self, step: TestStep, device, goal: str):
        """
        内置扫雷引擎：识别雷区 → 按规则推理 → 翻格/插旗 → 循环直到胜负。

        识别不依赖 OCR 读每一格：用网格线定位棋盘、按格子填充色判断
        覆盖/翻开/插旗，数字按颜色分类读取。规则：数字=周围 8 格雷数；
        已翻开格不再点；能确定的雷长按插旗；能确定的安全格直接翻开；
        没有必然解时按约束枚举算雷率最低的格子。
        胜负按右上角雷数计数器判定：计数器是「还没插旗的雷数」，
        归零 = 全部雷已标出 = 通关（只翻完安全格不会结束，这是本游戏的关键）。
        结算弹窗靠画面整体变暗识别，踩雷后自动「再玩一局」。

        返回 True/False 表示已接管并给出结果；返回 None 表示不是扫雷界面。
        """
        from ..ai import minesweeper as ms

        hint = goal or ""
        package = self._foreground_package(device)
        if not (_MINESWEEPER_HINT_RE.search(hint)
                or _MINESWEEPER_HINT_RE.search(package or "")):
            return None

        ocr_items: List[Tuple[str, Tuple[int, int], float]] = []
        ocr_ok = True
        try:
            from ..ai.offline_ocr import (
                find_all_texts,
                find_all_texts_region,
                is_available,
            )
            ocr_ok = is_available()
        except Exception as e:
            logger.debug(f"扫雷引擎：离线 OCR 不可用 {e}")
            ocr_ok = False

        size = device.get_screen_size(use_cache=True) or (1080, 1920)

        def shot() -> Optional[bytes]:
            try:
                return device.screencap_bytes()
            except Exception as e:
                logger.debug(f"扫雷引擎截图失败：{e}")
                return None

        def ocr_texts(raw: Optional[bytes]) -> List[Tuple[str, Tuple[int, int], float]]:
            if not raw or not ocr_ok:
                return []
            try:
                return [(str(t).strip(), (int(x), int(y)), float(c))
                        for t, (x, y), c in find_all_texts(raw, size)
                        if str(t).strip()]
            except Exception as e:
                logger.debug(f"扫雷引擎 OCR 失败：{e}")
                return []

        def dim_texts(raw: Optional[bytes]) -> List[Tuple[str, Tuple[int, int], float]]:
            """
            变暗弹窗里的文字识别：整屏 OCR 基本读不出小字，
            先定位浅色面板、裁出来放大 2 倍再识别；失败再退回整屏。
            """
            items: List[Tuple[str, Tuple[int, int], float]] = []
            if not raw or not ocr_ok:
                return items
            try:
                panel = ms.dialog_panel(raw)
                if panel:
                    items = [(str(t).strip(), (int(x), int(y)), float(c))
                             for t, (x, y), c in
                             find_all_texts_region(raw, panel, size, scale=2.0)
                             if str(t).strip()]
            except Exception as e:
                logger.debug(f"扫雷引擎弹窗 OCR 失败：{e}")
            return items or ocr_texts(raw)

        def low_luma(raw: Optional[bytes]) -> bool:
            if not raw:
                return False
            luma = ms.background_luma(raw)
            return luma is not None and luma < 150

        def wait_bright(timeout: float = 5.0) -> Optional[bytes]:
            """等待画面从变暗弹窗恢复（重开/进入牌局有过渡动画）"""
            t_end = time.time() + timeout
            raw = None
            while True:
                raw = shot()
                if raw and not low_luma(raw):
                    return raw
                if time.time() >= t_end:
                    return raw
                time.sleep(0.8)

        def wait_dim(timeout: float = 2.5) -> Optional[bytes]:
            """等结算弹窗把画面压暗（通关/踩雷后有一小段动画延迟）"""
            t_end = time.time() + timeout
            while time.time() < t_end:
                raw = shot()
                if raw and low_luma(raw):
                    return raw
                time.sleep(0.25)
            return None

        restart_words = ("再玩一局", "再来一局", "重新开始", "重新一局",
                         "再试一次", "再玩", "再来")
        win_words = ("胜利", "恭喜", "你赢了", "成功", "过关")
        loss_words = ("输了", "失败", "游戏结束", "踩雷", "你输")

        # ---------- 首帧：确认是扫雷界面 ----------
        raw = shot()
        if not raw:
            return None
        board = None if low_luma(raw) else ms.detect_board(raw)

        mines_total = None
        if board is None:
            # 可能是开始页/失败结算页：找「开始游戏 / 再玩一局」之类的入口
            items = dim_texts(raw) if low_luma(raw) else ocr_texts(raw)
            self._agent_log(
                "INFO",
                f"    扫雷：首帧未识别棋盘，OCR 文字：{[t for t, _p, _c in items[:12]]}")
            button = next(
                ((t, p) for t, p, _c in items
                 if any(k in t for k in ("开始游戏", "开始", "继续", "下一局")
                        + restart_words)),
                None)
            if button:
                if any(k in button[0] for k in restart_words):
                    self._agent_log(
                        "WARN", f"    结算弹窗，点击「{button[0]}」重开（第 1 次）")
                else:
                    self._agent_log("INFO", f"    点击「{button[0]}」进入牌局")
                device.input_tap(*button[1])
                time.sleep(1.2)
                # 点完按钮有过渡动画，反复等画面稳定并重试识别
                t_end = time.time() + 10.0
                while time.time() < t_end:
                    raw = wait_bright(3.0)
                    if raw:
                        board = ms.detect_board(raw)
                    if board is not None:
                        break
                    time.sleep(0.8)
            if board is None:
                self._agent_log("WARN", "    未识别到扫雷棋盘，跳过内置扫雷引擎")
                return None
        else:
            ocr_items = ocr_texts(raw)

        if board is not None:
            mines_left = ms.read_mine_counter(raw)
            if mines_left is not None:
                # 计数器是「还没插旗的雷数」，加上已插旗数就是总雷数
                mines_total = mines_left + board.flagged_count()
        if board is not None:
            self._agent_log(
                "INFO",
                f"🧠 扫雷引擎接管：{board.rows}×{board.cols} 格，"
                f"未翻开 {board.covered_count()} 格，总雷数 {mines_total or '未知'}")

        # ---------- 循环 ----------
        deadline = time.time() + max(90.0, float(step.timeout or 300.0))
        moves = 0
        max_moves = 400
        restarts = 0
        max_restarts = 3
        avoid: set = set()
        miss: dict = {}      # 格子 → 点击后画面无变化的次数
        flag_miss: dict = {}  # 格子 → 长按插旗没生效的次数
        no_flag: set = set()  # 插旗一直无效的格子（不再重试，避免死循环）
        pending: set = set()  # 上一轮刚长按过的格子，等下一帧确认是否变成旗
        blind = 0            # 连续识别不到棋盘的次数
        stall = 0            # 连续「没有可操作格子」的次数

        while time.time() < deadline and moves < max_moves:
            if self._engine.checkpoint():
                step.error_message = "扫雷任务已被用户停止"
                step.actual = step.error_message
                return False

            raw = shot()
            if not raw:
                break

            # 画面整体变暗 → 结算/提示弹窗
            if low_luma(raw):
                ocr_items = dim_texts(raw)
                texts = "".join(t for t, _p, _c in ocr_items)
                if any(k in texts for k in win_words):
                    step.actual = f"扫雷胜利（{moves} 步）：识别到胜利提示"
                    self._agent_log("INFO", f"✅ {step.actual}")
                    return True
                button = next(
                    ((t, p) for t, p, _c in ocr_items
                     if any(k in t for k in restart_words)),
                    None)
                if button and restarts < max_restarts:
                    restarts += 1
                    self._agent_log(
                        "WARN",
                        f"    踩雷了，点击「{button[0]}」重开（第 {restarts} 次）")
                    device.input_tap(*button[1])
                    avoid.clear()
                    miss.clear()
                    flag_miss.clear()
                    no_flag.clear()
                    pending.clear()
                    blind = 0
                    stall = 0
                    mines_total = None
                    raw = wait_bright()
                    continue
                if any(k in texts for k in loss_words):
                    step.error_message = f"扫雷失败（{moves} 步）：识别到失败提示"
                    step.actual = step.error_message
                    self._agent_log("WARN", f"⚠ {step.actual}")
                    return False
                if not ocr_ok:
                    self._agent_log("WARN", "    出现弹窗但 OCR 不可用，无法判断胜负")
                    return None
                # 其它弹窗：等界面恢复
                time.sleep(1.0)
                continue

            board = ms.detect_board(raw)
            if board is None:
                blind += 1
                if blind > 10:
                    break
                time.sleep(0.5)
                continue
            blind = 0

            # 计数器 = 还没插旗的雷数；加上已插旗数才是本局总雷数。
            # 总雷数不会变，取历次推算的最大值，避免偶发误读把总数越算越小。
            counter = ms.read_mine_counter(raw)
            if counter is not None and counter > board.rows * board.cols * 0.4:
                counter = None
            if counter is not None:
                est = counter + board.flagged_count()
                if mines_total is None or est > mines_total:
                    mines_total = est

            # 计数器归零 = 雷全部插旗 = 应该通关了。
            # 但七段码偶有误读，必须以「弹出结算弹窗」为准：
            # 弹窗出现就交给下一轮的文字识别判胜负，没弹窗就当读数不可信。
            if counter == 0:
                if wait_dim(4.0) is not None:
                    time.sleep(0.6)
                    continue
                self._agent_log("WARN", "    计数器读到 0 但没有结算弹窗，按误读处理")
                counter = None

            # 上一轮长按的格子有没有真的变成旗（没有就说明插旗没生效）
            if pending:
                flagged_now = set(board.flagged_cells())
                for cell in pending - flagged_now:
                    flag_miss[cell] = flag_miss.get(cell, 0) + 1
                    if flag_miss[cell] >= 2:
                        no_flag.add(cell)
                        self._agent_log(
                            "WARN",
                            f"    插旗连续 {flag_miss[cell]} 次无效，暂不插旗 "
                            f"({cell[0]},{cell[1]})")
                pending.clear()

            # 1) 已确定的雷先插旗（长按 = 插旗，再长按一次取消）
            known = ms.deduce_mines(board)
            to_flag = sorted(known - set(board.flagged_cells()) - no_flag)
            if to_flag:
                for cell in to_flag[:6]:
                    x, y = board.center(*cell)
                    moves += 1
                    self._agent_log(
                        "INFO",
                        f"    第{moves}步：插旗 ({cell[0]},{cell[1]}) "
                        f"@({x},{y}) 逻辑推理：该格必定是雷")
                    device.input_swipe(x, y, x, y, 700)
                    pending.add(cell)
                    time.sleep(0.5)
                stall = 0
                continue

            # 2) 翻一个安全格
            move = ms.choose_move(board, mines_total=mines_total, avoid=avoid)
            if move is None:
                if avoid:
                    # 还有未开格但都被判定为“点了没反应”→ 不能算赢
                    step.actual = (
                        f"扫雷引擎结束（{moves} 步）："
                        f"{len(avoid)} 个格子点击无响应，无法继续")
                    self._agent_log("WARN", f"⚠ {step.actual}")
                    return False
                flagged_now = set(board.flagged_cells())
                # 安全格全翻完了 → 剩下的未开格只能是雷（可能有雷不挨任何数字，
                # 逻辑推理推不出来，只能靠「总数 - 已插旗」反推）
                left = (mines_total - board.flagged_count()
                        if mines_total is not None else None)
                if left is not None and board.covered_count() == left:
                    rest_mines = list(board.covered_cells())
                else:
                    rest_mines = [c for c in board.covered_cells()
                                  if c in known and c not in flagged_now]
                if rest_mines and stall < 2:
                    # 之前判定「插旗无效」的雷格补插一次（可能被误判或又被取消）
                    no_flag.clear()
                    flag_miss.clear()
                    for cell in rest_mines:
                        x, y = board.center(*cell)
                        moves += 1
                        self._agent_log(
                            "INFO",
                            f"    第{moves}步：补插旗 ({cell[0]},{cell[1]}) "
                            f"@({x},{y}) 只剩雷格，重新确认")
                        device.input_swipe(x, y, x, y, 700)
                        pending.add(cell)
                        time.sleep(0.5)
                    stall += 1
                    continue
                # 雷都插旗了、安全格也翻完了：等结算画面
                stall += 1
                if stall > 6:
                    step.actual = f"扫雷引擎结束（{moves} 步）：没有可操作的格子"
                    self._agent_log("WARN", f"⚠ {step.actual}")
                    return False
                time.sleep(1.0)
                continue

            sig = board.signature()
            cell = (move["row"], move["col"])
            moves += 1
            stall = 0
            self._agent_log(
                "INFO",
                f"    第{moves}步：点 ({cell[0]},{cell[1]}) "
                f"@({move['x']},{move['y']}) {move['reason']}")
            device.input_tap(move["x"], move["y"])

            # 校验点击是否生效：开格有动画，轮询一小段时间等画面变化
            changed = False
            t_end = time.time() + 1.2
            while time.time() < t_end:
                time.sleep(0.3)
                raw2 = shot()
                if not raw2:
                    break
                if low_luma(raw2):
                    changed = True        # 结算弹窗出现也算有响应
                    break
                board2 = ms.detect_board(raw2)
                if board2 is not None and board2.signature() != sig:
                    changed = True
                    break
            if not changed:
                miss[cell] = miss.get(cell, 0) + 1
                if miss[cell] >= 2:
                    avoid.add(cell)
                    self._agent_log(
                        "WARN",
                        f"    该格连续 {miss[cell]} 次点击无变化，跳过 "
                        f"({cell[0]},{cell[1]})")

        step.actual = f"扫雷引擎结束（{moves} 步）"
        self._agent_log("WARN", f"⚠ {step.actual}")
        return False

    def _agent_gomoku(self, step: TestStep, device, goal: str):
        """
        内置视觉引擎：看棋盘 → 算落子 → 点击 → 等对手应手，直到分出胜负。

        返回 True/False 表示已接管并给出结果；返回 None 表示当前界面不是棋盘，
        交由调用方按「离线规则」处理。
        """
        from ..ai.board_vision import BLACK, WHITE, EMPTY
        from ..ai.gomoku import choose_move

        # 解析目标文本，确定 AI 的颜色
        # 关键："你执X"表示 AI 的颜色，"我执X"表示用户的颜色
        my_color = None
        goal_lower = goal.lower()
        self._agent_log("INFO", f"📋 目标文本：{goal[:80]}...")
        
        # 优先匹配"你执X"（AI 的颜色）
        if "你执白" in goal or "你执白棋" in goal:
            my_color = WHITE
            self._agent_log("INFO", "📋 目标指定 AI 执白棋（匹配'你执白'）")
        elif "你执黑" in goal or "你执黑棋" in goal:
            my_color = BLACK
            self._agent_log("INFO", "📋 目标指定 AI 执黑棋（匹配'你执黑'）")
        # 其次匹配"AI 执X"
        elif "ai 执白" in goal_lower or "ai.*白" in goal_lower:
            my_color = WHITE
            self._agent_log("INFO", "📋 目标指定 AI 执白棋（匹配'AI 执白'）")
        elif "ai 执黑" in goal_lower or "ai.*黑" in goal_lower:
            my_color = BLACK
            self._agent_log("INFO", "📋 目标指定 AI 执黑棋（匹配'AI 执黑'）")
        else:
            self._agent_log("INFO", "📋 目标未指定颜色，将根据棋盘推断")

        board = self._read_board(device, timeout=12.0)
        if board is None:
            self._agent_log("WARN", "    当前界面未识别到棋盘，跳过内置对弈引擎")
            return None

        self._agent_log(
            "INFO",
            f"🧠 内置视觉引擎接管：{board.size}×{board.size} 棋盘，"
            f"已落子 {board.total()} 手（黑{board.stones(BLACK)} 白{board.stones(WHITE)}）",
        )

        # 游戏引擎需要更长时间（一局五子棋通常 5-10 分钟）
        # 至少 5 分钟，如果步骤设置了更长的 timeout 则用步骤的
        game_timeout = max(300.0, float(step.timeout or 300.0))
        deadline = time.time() + game_timeout
        self._agent_log("INFO", f"⏱ 游戏引擎超时设置：{int(game_timeout)}秒")
        move_no = 0
        stuck = 0
        score_before = self._gomoku_scores(device)

        def finish(winner_color) -> bool:
            if winner_color is None:
                return False
            if winner_color == my_color:
                step.actual = (
                    f"内置五子棋引擎获胜：共 {move_no} 手，"
                    f"{'黑' if my_color == BLACK else '白'}棋五连"
                )
                self._agent_log("INFO", f"✅ {step.actual}")
                return True
            step.error_message = "内置五子棋引擎已尽力，对手先成五（本局告负）"
            step.actual = step.error_message
            self._agent_log("WARN", f"⚠ {step.actual}")
            return False

        def score_verdict(mine):
            """弹窗遮住棋盘时，用 自己/电脑 比分变化判断胜负"""
            if not score_before:
                return None
            for _ in range(4):
                now = self._gomoku_scores(device)
                if now and now != score_before:
                    if now[0] > score_before[0]:
                        return mine
                    if now[1] > score_before[1]:
                        return WHITE if mine == BLACK else BLACK
                    return None
                time.sleep(0.7)
            return None

        while move_no < 120 and time.time() < deadline:
            if self._engine.checkpoint():
                step.error_message = "自主任务已被用户停止"
                step.actual = step.error_message
                return False

            # 日志：当前局面
            self._agent_log("DEBUG", f"  局面：黑{board.stones(BLACK)} 白{board.stones(WHITE)} 共{board.total()}子")

            # 胜负已在棋盘上体现（同色五连）
            for color in (BLACK, WHITE):
                if board.has_five(color):
                    return finish(color)

            # 未知己方颜色时按「该谁走」推断：轮到的就是我方
            color = my_color
            if color is None:
                color = (WHITE if board.stones(BLACK) > board.stones(WHITE)
                         else BLACK)
                # 记住推断的颜色
                my_color = color

            move = choose_move(board.grid, color, board.size)
            if move is None:
                step.error_message = "棋盘已满，无法继续落子"
                step.actual = step.error_message
                return False

            row, col = move
            x, y = board.center(row, col)
            self._agent_log("INFO", f"  AI 决策：{'黑' if color == BLACK else '白'}({row},{col}) @({x},{y})")

            # 落子前先判断这一手是否直接成五（结算弹窗会遮挡棋盘）
            board.grid[row][col] = color
            winning = board.has_five(color)
            board.grid[row][col] = EMPTY

            device.input_tap(x, y)
            time.sleep(0.6)

            if winning:
                my_color = color
                move_no += 1
                self._agent_log(
                    "INFO",
                    f"    第 {move_no} 手：{'黑' if color == BLACK else '白'}"
                    f"({row},{col}) @({x},{y})，成五",
                )
                time.sleep(0.8)
                return finish(color)

            after = self._read_board(device, timeout=4.0)
            if after is None:
                my_color = my_color or color
                # 棋盘被结算弹窗遮挡时，用界面文字判断胜负
                result = self._gomoku_result_text(device)
                if result is not None:
                    return finish(my_color if result == "win" else
                                  (WHITE if my_color == BLACK else BLACK))
                # 结算弹窗是画布绘制、取不到文字：用比分变化兜底
                verdict = score_verdict(my_color)
                if verdict is not None:
                    self._agent_log("INFO", "    依据比分变化判断本局结果")
                    return finish(verdict)
                step.error_message = "对局中截图识别失败"
                step.actual = step.error_message
                return False

            # 日志：记录落子后棋盘状态
            self._agent_log("DEBUG", f"    落子后棋盘：黑{after.stones(BLACK)} 白{after.stones(WHITE)} 共{after.total()}子")

            placed = after.at(row, col)
            if placed == EMPTY:
                # 还没轮到我方（或点偏），稍等后重试
                stuck += 1
                self._agent_log("WARN", f"    落子未生效（第{stuck}次）：({row},{col}) 仍为空")
                if stuck >= 8:
                    step.error_message = "连续无法落子：可能不是我方回合或点击未生效"
                    step.actual = step.error_message
                    self._agent_log("WARN", step.error_message)
                    return False
                time.sleep(1.2)
                board = self._read_board(device, timeout=4.0) or after
                continue

            stuck = 0
            # 只有在颜色未从目标文本确定时，才根据实际落子更新颜色
            if my_color is None:
                my_color = color
            opp = WHITE if color == BLACK else BLACK
            opp_before = board.stones(opp)   # 落子前的对手子数
            move_no += 1
            self._agent_log(
                "INFO",
                f"    第 {move_no} 手：{'黑' if color == BLACK else '白'}"
                f"({row},{col}) @({x},{y})"
                f"{' [检测:' + ('黑' if placed == BLACK else '白') + ']' if placed != color else ''}",
            )
            board = after

            # 等对手应手（对手手速很快，应手可能已经出现在 after 里）
            wait_deadline = time.time() + 12.0
            total_before = after.total()  # 记录当前总子数，用于检测悔棋
            while after.stones(opp) <= opp_before:
                if self._engine.checkpoint():
                    step.error_message = "自主任务已被用户停止"
                    step.actual = step.error_message
                    return False
                for c in (BLACK, WHITE):
                    if after.has_five(c):
                        return finish(c)
                if time.time() >= wait_deadline:
                    board = self._read_board(device, timeout=3.0) or after
                    for c in (BLACK, WHITE):
                        if board.has_five(c):
                            return finish(c)
                    step.error_message = "对手长时间未应手，内置引擎无法继续"
                    step.actual = step.error_message
                    self._agent_log("WARN", step.error_message)
                    return False
                time.sleep(0.9)
                newer = self._read_board(device, timeout=3.0)
                if newer is not None:
                    # 检测悔棋：总子数减少
                    if newer.total() < total_before:
                        self._agent_log("INFO", "    检测到悔棋，重新评估局面")
                        board = newer
                        after = newer
                        # 重新计算对手子数基准
                        opp_before = board.stones(opp)
                        total_before = board.total()
                        # 跳出等待循环，回到主循环重新决策
                        break
                    after = newer
                    board = newer
                    if newer.stones(opp) > opp_before:
                        self._agent_log(
                            "INFO", f"    对手应手（第 {newer.stones(opp)} 子）")

        # 区分超时和手数上限
        if time.time() >= deadline:
            step.error_message = f"内置五子棋引擎超时（{move_no} 手，{int(time.time() - (deadline - max(60, float(step.timeout or 300))))}秒）仍未分出胜负"
        else:
            step.error_message = f"内置五子棋引擎达到手数上限（{move_no} 手）仍未分出胜负"
        step.actual = step.error_message
        self._agent_log("WARN", step.error_message)
        return False

    # ---------- 辅助方法 ----------

    def _get_device(self, device_id: str):
        """获取设备实例（未连接时按扫描信息自动连接）"""
        if not device_id:
            # 返回第一个可用设备
            devices = self._device_manager.get_all_devices()
            return next(iter(devices.values()), None) if devices else None

        device = self._device_manager.get_device(device_id)
        if device is None:
            device = self._device_manager.auto_connect(device_id)
            if device:
                logger.info(f"执行引擎自动连接设备: {device_id}")
        return device

    def _match_pattern(self, data: bytes, pattern: str) -> bool:
        """
        匹配数据模式。
        支持: 精确匹配、通配符 (*)、前缀匹配
        """
        data_hex = data.hex().upper()
        pattern = pattern.replace(" ", "").upper()

        if "*" in pattern:
            # 通配符匹配
            import re
            regex = pattern.replace("*", ".*")
            return bool(re.match(regex, data_hex))
        else:
            return data_hex.startswith(pattern)
