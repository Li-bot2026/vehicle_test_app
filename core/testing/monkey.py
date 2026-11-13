"""
Monkey 随机压力测试
==================
参考 Android 官方 Monkey / 字节 Fastbot / Maxim 的成熟设计：

- 事件权重配置（点击/滑动/长按/输入/系统键/应用切换）
- throttle 事件间隔、seed 可复现
- 目标应用约束：离开目标应用时自动拉回（Fastbot/Maxim 做法）
- Crash / ANR 检测（logcat 错误流）与可选的立即停止策略
- 无 ADB 设备时本地模拟，便于离线演示
"""

import random
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from loguru import logger


class ActionType(Enum):
    TAP = "点击"
    SWIPE = "滑动"
    LONG_PRESS = "长按"
    INPUT = "输入"
    KEY = "系统键"
    APP_SWITCH = "应用切换"


@dataclass
class MonkeyConfig:
    duration: float = 300.0          # 测试时长（秒）
    max_events: int = 0              # 总事件数上限，0 = 不限（与时长先到先停）
    throttle_ms: int = 200           # 事件间隔（毫秒）
    seed: int = 0                    # 0 = 随机
    screen_width: int = 1920
    screen_height: int = 1080
    package: str = ""                # 目标应用包名，空 = 不限制
    stop_on_crash: bool = True
    stop_on_anr: bool = True
    tap_prob: float = 0.4
    swipe_prob: float = 0.2
    long_press_prob: float = 0.1
    input_prob: float = 0.1
    key_prob: float = 0.1
    app_switch_prob: float = 0.1


@dataclass
class MonkeyAction:
    action_type: ActionType
    detail: str
    success: bool


@dataclass
class MonkeyStats:
    total_actions: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    crashes: int = 0
    anrs: int = 0
    duration: float = 0.0
    event_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.total_actions == 0:
            return 0.0
        return self.successful_actions / self.total_actions * 100.0

    @property
    def actions_per_second(self) -> float:
        if self.duration <= 0:
            return 0.0
        return self.total_actions / self.duration


# 系统键池（BACK/HOME/MENU/音量+/音量-，参考官方 Monkey syskeys 类别）
_KEY_POOL = [(4, "BACK"), (3, "HOME"), (82, "MENU"), (24, "音量+"), (25, "音量-")]
_INPUT_POOL = ["test", "123", "hello", "666", "abc"]

_CRASH_RE = re.compile(r"FATAL EXCEPTION|Fatal signal")
_ANR_RE = re.compile(r"ANR in |Application Not Responding")
_TS_RE = re.compile(r"^(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")


class MonkeyTester:
    """Monkey 测试执行器（在工作线程中调用 run()）"""

    def __init__(self, config: MonkeyConfig):
        self.config = config
        self.stats = MonkeyStats()
        self.on_action: Optional[Callable[[MonkeyAction], None]] = None
        self.on_stats: Optional[Callable[[MonkeyStats], None]] = None
        self._adb = None
        self._stop_flag = threading.Event()
        self._pause_flag = threading.Event()  # set = 暂停
        self._rng = random.Random(config.seed or None)
        self._packages: List[str] = []
        self._launch_activity: str = ""
        self._last_log_ts: str = ""

    def set_adb_device(self, device):
        self._adb = device

    # ---------- 控制 ----------

    def stop(self):
        self._stop_flag.set()

    def pause(self):
        self._pause_flag.set()

    def resume(self):
        self._pause_flag.clear()

    @property
    def is_paused(self) -> bool:
        return self._pause_flag.is_set()

    # ---------- 主循环 ----------

    def run(self) -> MonkeyStats:
        cfg = self.config
        weights = [
            (ActionType.TAP, cfg.tap_prob),
            (ActionType.SWIPE, cfg.swipe_prob),
            (ActionType.LONG_PRESS, cfg.long_press_prob),
            (ActionType.INPUT, cfg.input_prob),
            (ActionType.KEY, cfg.key_prob),
            (ActionType.APP_SWITCH, cfg.app_switch_prob),
        ]
        weights = [(t, w) for t, w in weights if w > 0]
        if not weights:
            weights = [(ActionType.TAP, 1.0)]

        if self._adb:
            self._prepare_target()
            self._baseline_logcat()

        start = time.monotonic()
        active = 0.0
        last_tick = start
        last_stats_emit = 0.0
        last_log_poll = 0.0
        event_no = 0

        while not self._stop_flag.is_set():
            now = time.monotonic()
            if self._pause_flag.is_set():
                last_tick = now
                time.sleep(0.1)
                continue
            active += now - last_tick
            last_tick = now
            self.stats.duration = active
            if active >= cfg.duration:
                break

            # 暂停/停止响应间隔
            throttle = max(0.0, cfg.throttle_ms / 1000.0)
            if self._stop_flag.wait(throttle):
                break

            action = self._dispatch(self._pick(weights))
            event_no += 1
            self.stats.total_actions += 1
            if action.success:
                self.stats.successful_actions += 1
            else:
                self.stats.failed_actions += 1
            key = action.action_type.value
            self.stats.event_counts[key] = self.stats.event_counts.get(key, 0) + 1
            if self.on_action:
                self.on_action(action)

            # 事件数上限（官方 Monkey 的 COUNT 语义）
            if cfg.max_events > 0 and self.stats.total_actions >= cfg.max_events:
                break

            # 目标应用约束：每 10 个事件检查一次是否离开目标应用
            if self._adb and cfg.package and event_no % 10 == 0:
                self._ensure_foreground()

            # Crash/ANR 检测：每 2 秒轮询一次 logcat 错误流
            if self._adb and now - last_log_poll >= 2.0:
                last_log_poll = now
                if self._poll_crashes():
                    break

            if now - last_stats_emit >= 0.5:
                last_stats_emit = now
                if self.on_stats:
                    self.on_stats(self.stats)

        self.stats.duration = active
        if self.on_stats:
            self.on_stats(self.stats)
        return self.stats

    # ---------- 事件分发 ----------

    def _pick(self, weights):
        total = sum(w for _, w in weights)
        r = self._rng.random() * total
        acc = 0.0
        for action_type, w in weights:
            acc += w
            if r <= acc:
                return action_type
        return weights[-1][0]

    def _dispatch(self, action_type: ActionType) -> MonkeyAction:
        w, h = self.config.screen_width, self.config.screen_height
        if not self._adb:
            return self._simulate(action_type)

        if action_type == ActionType.TAP:
            x, y = self._rand_xy()
            ok = self._adb.input_tap(x, y)
            return MonkeyAction(action_type, f"tap ({x}, {y})", ok)

        if action_type == ActionType.SWIPE:
            x1, y1 = self._rand_xy()
            x2, y2 = self._rand_xy()
            ms = self._rng.randint(200, 600)
            ok = self._adb.input_swipe(x1, y1, x2, y2, ms)
            return MonkeyAction(action_type, f"swipe ({x1},{y1})->({x2},{y2}) {ms}ms", ok)

        if action_type == ActionType.LONG_PRESS:
            x, y = self._rand_xy()
            ok = self._adb.input_swipe(x, y, x, y, self._rng.randint(700, 1200))
            return MonkeyAction(action_type, f"long_press ({x}, {y})", ok)

        if action_type == ActionType.INPUT:
            text = self._rng.choice(_INPUT_POOL)
            ok, _ = self._adb.shell(f"input text '{text}'", timeout=10.0)
            return MonkeyAction(action_type, f"input '{text}'", ok)

        if action_type == ActionType.KEY:
            code, name = self._rng.choice(_KEY_POOL)
            ok = self._adb.keyevent(code)
            return MonkeyAction(action_type, f"key {name}({code})", ok)

        # APP_SWITCH：随机拉起一个已安装应用（官方 Monkey appswitch 类别）
        if not self._packages:
            self._packages = self._adb.list_packages(third_party_only=True)
        if self._packages:
            pkg = self._rng.choice(self._packages)
            activity = self._adb.resolve_launch_activity(pkg)
            if activity:
                ok = self._adb.start_activity(activity)
                return MonkeyAction(action_type, f"switch -> {pkg}", ok)
        ok = self._adb.keyevent(187)
        return MonkeyAction(action_type, "key APP_SWITCH(187)", ok)

    def _simulate(self, action_type: ActionType) -> MonkeyAction:
        w, h = self.config.screen_width, self.config.screen_height
        x, y = self._rand_xy()
        ok = self._rng.random() < 0.95
        detail = {
            ActionType.TAP: f"模拟 tap ({x}, {y})",
            ActionType.SWIPE: f"模拟 swipe ({x},{y})->({self._rand_xy()})",
            ActionType.LONG_PRESS: f"模拟 long_press ({x}, {y})",
            ActionType.INPUT: f"模拟 input '{self._rng.choice(_INPUT_POOL)}'",
            ActionType.KEY: f"模拟 key {self._rng.choice(_KEY_POOL)[1]}",
            ActionType.APP_SWITCH: "模拟 app_switch",
        }[action_type]
        return MonkeyAction(action_type, detail, ok)

    def _rand_xy(self):
        margin_x = max(1, int(self.config.screen_width * 0.05))
        margin_y = max(1, int(self.config.screen_height * 0.05))
        return (
            self._rng.randint(margin_x, self.config.screen_width - margin_x),
            self._rng.randint(margin_y, self.config.screen_height - margin_y),
        )

    # ---------- 目标应用约束 ----------

    def _prepare_target(self):
        pkg = self.config.package.strip()
        if not pkg:
            return
        self._launch_activity = self._adb.resolve_launch_activity(pkg)
        if self._launch_activity:
            self._adb.start_activity(self._launch_activity)
        self._packages = self._adb.list_packages(third_party_only=True)

    def _ensure_foreground(self):
        pkg = self.config.package.strip()
        if not pkg or not self._launch_activity:
            return
        current, _ = self._adb.get_foreground_app()
        if current != pkg:
            self._adb.start_activity(self._launch_activity)
            if self.on_action:
                self.on_action(MonkeyAction(
                    ActionType.APP_SWITCH, f"离开目标应用，拉回 {pkg}", True))

    # ---------- Crash / ANR 检测 ----------

    def _baseline_logcat(self):
        ok, output = self._adb.shell("logcat -d -v time *:E", timeout=10.0)
        if ok:
            for line in output.splitlines():
                m = _TS_RE.match(line)
                if m and m.group(1) > self._last_log_ts:
                    self._last_log_ts = m.group(1)

    def _poll_crashes(self) -> bool:
        """轮询新增错误日志，返回是否应停止测试"""
        ok, output = self._adb.shell("logcat -d -v time *:E", timeout=10.0)
        if not ok:
            return False
        stop = False
        for line in output.splitlines():
            m = _TS_RE.match(line)
            if not m or m.group(1) <= self._last_log_ts:
                continue
            self._last_log_ts = m.group(1)
            if _CRASH_RE.search(line):
                self.stats.crashes += 1
                logger.warning(f"Monkey 检测到 Crash: {line[:120]}")
                if self.on_action:
                    self.on_action(MonkeyAction(ActionType.KEY, f"[CRASH] {line[:100]}", False))
                if self.config.stop_on_crash:
                    stop = True
            elif _ANR_RE.search(line):
                self.stats.anrs += 1
                logger.warning(f"Monkey 检测到 ANR: {line[:120]}")
                if self.on_action:
                    self.on_action(MonkeyAction(ActionType.KEY, f"[ANR] {line[:100]}", False))
                if self.config.stop_on_anr:
                    stop = True
        return stop