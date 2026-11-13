"""
修复动作应用器
===============
把诊断器给出的动作（JSON）落到测试用例或设备上，并返回可审计的结果。

安全约束：
- ADB 命令只允许只读诊断与启动/停止应用的命令白名单
- 所有动作应用前由 UI 明确征得用户同意（自动修复模式除外）
"""

from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger

from ..testcase.model import TestCase, TestStep, StepType

# 允许 AI 自动执行的 ADB 命令前缀（只读诊断 + 启停应用）
ADB_ALLOWED_PREFIXES = (
    "getprop", "dumpsys", "ps", "pidof", "logcat", "wm",
    "settings get", "pm list", "pm path",
    "am start", "am force-stop", "input keyevent",
    "screencap", "svc", "cmd ", "ls", "cat",
)


@dataclass
class RepairResult:
    """修复结果"""
    applied: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)

    @property
    def success(self) -> bool:
        return bool(self.applied) and not self.failed

    @property
    def summary(self) -> str:
        parts = []
        if self.applied:
            parts.append("已修复：" + "；".join(self.applied))
        if self.failed:
            parts.append("未完成：" + "；".join(self.failed))
        return " ".join(parts) or "未执行任何修复动作"


def _is_adb_command_allowed(command: str) -> bool:
    cmd = (command or "").strip().lower()
    return bool(cmd) and cmd.startswith(ADB_ALLOWED_PREFIXES)


_STEP_SECTIONS = {
    "setup": "setup_steps",
    "setup_steps": "setup_steps",
    "steps": "steps",
    "teardown": "teardown_steps",
    "teardown_steps": "teardown_steps",
}


def _resolve_step(case: TestCase, target, default_index,
                  default_section: str = "steps"):
    """把动作 target 解析为 (步骤列表, 列表内下标)"""
    attr = _STEP_SECTIONS.get(str(default_section or "").strip(), "steps")
    steps = getattr(case, attr, case.steps)
    raw = target if target not in (None, "") else default_index
    try:
        idx = int(raw)
    except (TypeError, ValueError):
        return None, None
    if 0 <= idx < len(steps):
        return steps, idx
    return None, None


def _find_adb_device(device_manager, device_id: str):
    if device_manager is None:
        return None
    from ..device.adb_device import ADBDevice

    if device_id:
        dev = device_manager.get_device(device_id)
        if isinstance(dev, ADBDevice):
            return dev
    for dev in device_manager.get_all_devices().values():
        if isinstance(dev, ADBDevice):
            return dev
    return None


def apply_actions(case: TestCase, actions: List[dict],
                  device_manager=None,
                  default_step_index: Optional[int] = None,
                  default_step_section: str = "steps") -> RepairResult:
    """把诊断动作应用到用例/设备"""
    result = RepairResult()

    # 插入动作会改变后续下标，放到最后按目标下标从大到小应用
    normal: List[dict] = []
    inserts: List[dict] = []
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        (inserts if action.get("kind") == "insert_wait" else normal).append(action)

    def _resolved_target(action):
        steps, idx = _resolve_step(
            case, action.get("target"), default_step_index,
            default_step_section
        )
        return idx

    inserts.sort(key=lambda a: -1 if _resolved_target(a) is None
                 else _resolved_target(a), reverse=True)

    for action in normal + inserts:
        kind = action.get("kind")
        payload = action.get("payload") or {}
        target = action.get("target")
        desc = action.get("description") or kind

        try:
            if kind == "set_step_param":
                steps, idx = _resolve_step(
                    case, target, default_step_index, default_step_section
                )
                if idx is None:
                    raise ValueError("步骤序号无效")
                key = str(payload.get("key") or "")
                if not key:
                    raise ValueError("缺少参数名")
                steps[idx].params[key] = payload.get("value")
                result.applied.append(desc)

            elif kind == "set_step_timeout":
                steps, idx = _resolve_step(
                    case, target, default_step_index, default_step_section
                )
                if idx is None:
                    raise ValueError("步骤序号无效")
                timeout = float(payload.get("timeout", 0))
                if timeout <= 0:
                    raise ValueError("超时时间必须大于 0")
                steps[idx].timeout = timeout
                result.applied.append(desc)

            elif kind == "set_step_expected":
                steps, idx = _resolve_step(
                    case, target, default_step_index, default_step_section
                )
                if idx is None:
                    raise ValueError("步骤序号无效")
                steps[idx].expected = payload.get("expected")
                result.applied.append(desc)

            elif kind == "set_step_tolerance":
                steps, idx = _resolve_step(
                    case, target, default_step_index, default_step_section
                )
                if idx is None:
                    raise ValueError("步骤序号无效")
                steps[idx].tolerance = float(payload.get("tolerance", 0))
                result.applied.append(desc)

            elif kind == "replace_step":
                steps, idx = _resolve_step(
                    case, target, default_step_index, default_step_section
                )
                if idx is None:
                    raise ValueError("步骤序号无效")
                raw_type = str(payload.get("step_type") or "wait")
                try:
                    step_type = StepType(raw_type)
                except ValueError:
                    raise ValueError(f"不支持的步骤类型: {raw_type}")
                old = steps[idx]
                params = payload.get("params")
                steps[idx] = TestStep(
                    step_type=step_type,
                    description=str(payload.get("description") or old.description),
                    device_id=old.device_id,
                    params=params if isinstance(params, dict) else old.params,
                    expected=old.expected,
                    timeout=old.timeout,
                )
                result.applied.append(desc)

            elif kind == "insert_wait":
                steps, idx = _resolve_step(
                    case, target, default_step_index, default_step_section
                )
                if idx is None:
                    raise ValueError("步骤序号无效")
                duration = float(payload.get("duration", 1.0))
                steps.insert(idx + 1, TestStep(
                    step_type=StepType.WAIT,
                    description=f"AI 补充等待 {duration:g}s",
                    params={"duration": duration},
                ))
                result.applied.append(desc)

            elif kind == "connect_device":
                device_id = str(payload.get("device_id") or "")
                if not device_id:
                    raise ValueError("缺少设备 ID")
                if device_manager is None:
                    raise ValueError("设备管理器不可用")
                device = device_manager.auto_connect(device_id)
                if device is None:
                    raise ValueError(f"无法连接设备 {device_id}")
                result.applied.append(desc)

            elif kind == "run_adb":
                command = str(payload.get("command") or "")
                if not _is_adb_command_allowed(command):
                    raise ValueError(f"ADB 命令不在安全白名单内: {command}")
                device = _find_adb_device(device_manager, str(payload.get("device_id") or ""))
                if device is None:
                    raise ValueError("没有可用的 ADB 设备")
                ok, output = device.shell(command)
                if not ok:
                    raise ValueError(f"命令执行失败: {output}")
                result.commands.append(command)
                result.applied.append(desc)

            elif kind in ("", "none"):
                continue

            else:
                raise ValueError(f"未知动作类型: {kind}")

        except Exception as e:
            logger.warning(f"修复动作执行失败 [{kind}]: {e}")
            result.failed.append(f"{desc}（{e}）")

    return result
