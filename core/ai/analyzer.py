"""
失败诊断器
===========
在用例执行失败时分析原因，并给出可执行的修复动作。

两种模式：
- LLM 模式：调用配置的 AI 模型（结构化 JSON 输出）
- 规则模式：内置常见故障知识库（无需 API Key，离线可用）
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from loguru import logger

from .client import AIClient, AIError
from .prompts import DIAGNOSE_SYSTEM, diagnose_prompt

VALID_ACTION_KINDS = {
    "set_step_param",
    "set_step_timeout",
    "set_step_expected",
    "set_step_tolerance",
    "replace_step",
    "insert_wait",
    "connect_device",
    "run_adb",
    "none",
}


@dataclass
class Diagnosis:
    """诊断结果"""
    reason: str
    solution: str
    confidence: float = 0.6
    auto_fixable: bool = False
    actions: List[dict] = field(default_factory=list)
    source: str = "rule"          # rule / llm
    raw: str = ""

    @property
    def source_label(self) -> str:
        return "AI 模型" if self.source == "llm" else "内置规则引擎"


def analyze_failure(client: AIClient, case, step, step_index,
                    error: str, devices: list,
                    offline_fallback: bool = True) -> Diagnosis:
    """
    诊断用例失败。

    参数:
        client: AI 客户端
        case: TestCase 对象（快照）
        step: 失败步骤（TestStep，可能为 None）
        step_index: 失败步骤在 case.steps 中的下标（可能为 None）
        error: 错误信息
        devices: 已连接/可用设备摘要列表
    """
    error_text = (error or (step.error_message if step else "") or "").strip()

    if client.configured:
        try:
            return _diagnose_with_llm(
                client, case, step, step_index, error_text, devices
            )
        except Exception as e:
            logger.warning(f"AI 诊断失败，回退内置规则引擎: {e}")
            if not offline_fallback:
                raise

    diagnosis = _diagnose_with_rules(step, step_index, error_text)
    if not offline_fallback and diagnosis.source == "rule" and not client.configured:
        raise AIError("未配置 AI 模型，且已关闭内置规则引擎")
    return diagnosis


# ============================================================
# LLM 诊断
# ============================================================

def _diagnose_with_llm(client: AIClient, case, step, step_index,
                       error: str, devices: list) -> Diagnosis:
    case_data = _compact_case(case)
    step_desc = step.description if step else ""
    step_type = step.step_type.value if step else ""
    user = diagnose_prompt(case_data, step_index, step_desc, step_type, error, devices)
    data = client.chat_json(DIAGNOSE_SYSTEM, user, temperature=0.1, timeout=min(
        float(getattr(client.config, "timeout", 60.0) or 60.0), 30.0
    ))

    reason = str(data.get("reason") or "").strip()
    solution = str(data.get("solution") or "").strip()
    if not reason:
        raise AIError("AI 未返回失败原因")

    actions = _sanitize_actions(data.get("actions"), step_index, step)
    try:
        confidence = float(data.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7

    return Diagnosis(
        reason=reason,
        solution=solution or "请根据原因人工调整用例后重试。",
        confidence=max(0.0, min(1.0, confidence)),
        auto_fixable=bool(data.get("auto_fixable")) and bool(actions),
        actions=actions,
        source="llm",
    )


def _compact_case(case) -> dict:
    """压缩用例数据，避免提示词过长"""
    return {
        "name": case.name,
        "category": case.category,
        "priority": case.priority.value if case.priority else "",
        "setup_steps": [_compact_step(s) for s in case.setup_steps],
        "steps": [_compact_step(s) for s in case.steps],
        "teardown_steps": [_compact_step(s) for s in case.teardown_steps],
    }


def _compact_step(step) -> dict:
    return {
        "description": step.description,
        "step_type": step.step_type.value,
        "device_id": step.device_id,
        "params": step.params,
        "expected": step.expected,
        "timeout": step.timeout,
    }


def _sanitize_actions(raw_actions, step_index, step) -> List[dict]:
    """过滤模型返回的非法动作"""
    if not isinstance(raw_actions, list):
        return []
    result = []
    for raw in raw_actions:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip()
        if kind not in VALID_ACTION_KINDS or kind == "none":
            continue
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        target = raw.get("target")
        if target in (None, "") and step_index is not None:
            target = str(step_index)
        result.append({
            "kind": kind,
            "target": str(target) if target not in (None, "") else "",
            "payload": payload,
            "description": str(raw.get("description") or ""),
        })
        if len(result) >= 5:
            break
    return result


# ============================================================
# 内置规则诊断
# ============================================================

def _diagnose_with_rules(step, step_index, error: str) -> Diagnosis:
    text = error or ""
    lowered = text.lower()
    idx = str(step_index) if step_index is not None else ""
    timeout = float(getattr(step, "timeout", 30.0) or 30.0) if step else 30.0
    device_id = getattr(step, "device_id", "") if step else ""

    # 1. 等待超时
    if "超时" in text or "timeout" in lowered or "timed out" in lowered:
        new_timeout = min(max(timeout * 2, 10.0), 300.0)
        actions = []
        if idx:
            actions.append({
                "kind": "set_step_timeout",
                "target": idx,
                "payload": {"timeout": new_timeout},
                "description": f"将步骤超时从 {timeout:g}s 调整为 {new_timeout:g}s",
            })
            # 失败步骤本身不会执行完，等待必须插在它之前
            try:
                pos = int(idx)
            except ValueError:
                pos = -1
            if pos > 0:
                actions.append({
                    "kind": "insert_wait",
                    "target": str(pos - 1),
                    "payload": {"duration": 1.0},
                    "description": "在失败步骤前补充 1 秒等待，避开设备响应抖动",
                })
        return Diagnosis(
            reason="等待设备响应超时：在设定时间内没有收到预期的数据或结果。",
            solution=(
                "1. 确认设备处于工作状态且指令格式正确；\n"
                "2. 适当增大该步骤的超时时间（如翻倍）；\n"
                "3. 若设备响应本身较慢，可在发送指令后增加等待步骤；\n"
                "4. 检查设备连接是否稳定（串口/USB/网络）。"
            ),
            confidence=0.75,
            auto_fixable=bool(actions),
            actions=actions,
        )

    # 2. 设备未连接 / 类型不匹配
    if any(k in text for k in ("未连接", "不是", "设备未就绪", "请先在左侧连接")):
        actions = []
        if device_id:
            actions.append({
                "kind": "connect_device",
                "target": "",
                "payload": {"device_id": device_id},
                "description": f"自动连接设备 {device_id} 后重试",
            })
        return Diagnosis(
            reason=f"步骤引用的设备不可用（device_id={device_id or '未指定'}），可能是未连接或设备类型不匹配。",
            solution=(
                "1. 在左侧设备面板连接该设备；\n"
                "2. 核实用例步骤的 device_id 与实际连接的设备 ID 一致；\n"
                "3. 检查设备驱动/线缆/网络是否正常。"
            ),
            confidence=0.85,
            auto_fixable=bool(actions),
            actions=actions,
        )

    # 3. ADB 相关失败
    if "adb" in lowered or "device offline" in lowered or "no devices" in lowered:
        actions = [{
            "kind": "run_adb",
            "target": "",
            "payload": {"device_id": device_id, "command": "getprop ro.product.model"},
            "description": "执行只读诊断命令，确认 ADB 通道是否正常",
        }]
        if device_id:
            actions.insert(0, {
                "kind": "connect_device",
                "target": "",
                "payload": {"device_id": device_id},
                "description": f"重新连接 ADB 设备 {device_id}",
            })
        return Diagnosis(
            reason="ADB 命令执行失败：设备可能离线、未授权或 adb 服务异常。",
            solution=(
                "1. 在设备上确认已允许 USB 调试授权；\n"
                "2. 执行 adb devices 确认设备状态为 device；\n"
                "3. 若显示 offline，重新插拔 USB 或执行 adb reconnect；\n"
                "4. 确认目标包名/Activity 拼写正确。"
            ),
            confidence=0.8,
            auto_fixable=True,
            actions=actions,
        )

    # 4. 串口异常
    if "串口" in text or "serial" in lowered or "com" in lowered:
        return Diagnosis(
            reason="串口通信失败：端口被占用、参数不匹配或设备未就绪。",
            solution=(
                "1. 确认串口号正确（设备管理器查看 COM 端口）；\n"
                "2. 核对波特率/数据位/停止位/校验位与设备一致；\n"
                "3. 关闭占用该端口的其他程序；\n"
                "4. 重新插拔串口线后重试。"
            ),
            confidence=0.7,
            auto_fixable=False,
            actions=[],
        )

    # 5. 网络异常
    if any(k in text for k in ("连接被拒绝", "Connection refused", "网络", "socket")) or \
            "connection reset" in lowered:
        return Diagnosis(
            reason="网络通信失败：目标服务未启动、IP/端口错误或被防火墙拦截。",
            solution=(
                "1. 确认目标 IP 与端口正确；\n"
                "2. 在车机上确认被测服务已启动；\n"
                "3. 检查两端网络是否互通（ping / telnet）；\n"
                "4. 排查防火墙或端口占用。"
            ),
            confidence=0.7,
            auto_fixable=False,
            actions=[],
        )

    # 6. 断言失败
    if any(k in text for k in ("断言", "不匹配", "期望", "assert")):
        return Diagnosis(
            reason="断言未通过：实际结果与预期不一致，可能为产品缺陷或期望值设置不当。",
            solution=(
                "1. 核对实际返回值与期望值是否语义一致（大小写、单位、格式）；\n"
                "2. 确认测试步骤顺序正确，状态已就绪后再断言；\n"
                "3. 若为数值，考虑传感器精度并设置合理容差；\n"
                "4. 若确认产品行为异常，请提交缺陷而不是修改期望值。"
            ),
            confidence=0.65,
            auto_fixable=False,
            actions=[],
        )

    # 7. 未注册步骤类型
    if "未注册的步骤类型" in text or "invalid steptype" in lowered:
        return Diagnosis(
            reason="用例包含当前版本不支持的步骤类型（可能是旧版本用例或配置写错）。",
            solution="打开用例编辑器，将该步骤改为受支持的类型，或升级软件版本。",
            confidence=0.9,
            auto_fixable=False,
            actions=[],
        )

    # 8. 文件/路径
    if any(k in text for k in ("文件不存在", "No such file", "路径")):
        return Diagnosis(
            reason="引用的文件或路径不存在。",
            solution="检查截图保存目录、导入文件路径是否存在，并确保有写入权限。",
            confidence=0.8,
            auto_fixable=False,
            actions=[],
        )

    # 兜底
    return Diagnosis(
        reason="执行过程中出现未分类异常：" + (text[:200] or "无详细错误信息"),
        solution=(
            "1. 查看日志文件获取完整堆栈；\n"
            "2. 确认设备连接与步骤参数；\n"
            "3. 单步执行该用例定位具体失败环节。"
        ),
        confidence=0.4,
        auto_fixable=False,
        actions=[],
    )
