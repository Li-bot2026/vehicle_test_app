"""
界面元素解析与定位
==================
解析 `uiautomator dump` 生成的 XML，按文本查找控件并给出点击坐标。

用途：
- 测试步骤按界面文字操作（不依赖写死坐标）
- 生成用例前把当前界面可见文字交给 AI，避免编造控件
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Tuple

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass
class UiNode:
    """界面中的一个控件节点"""

    text: str
    desc: str
    cls: str
    rid: str
    bounds: Tuple[int, int, int, int]
    clickable: bool = False
    focused: bool = False
    scrollable: bool = False
    enabled: bool = True

    @property
    def label(self) -> str:
        return (self.text or self.desc).strip()

    @property
    def center(self) -> Tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bounds
        return max(0, x2 - x1) * max(0, y2 - y1)

    @property
    def is_edit_text(self) -> bool:
        return self.cls.endswith("EditText")


def _parse_bounds(raw: str) -> Optional[Tuple[int, int, int, int]]:
    match = _BOUNDS_RE.match(raw or "")
    if not match:
        return None
    return tuple(int(v) for v in match.groups())  # type: ignore[return-value]


def _to_bool(raw: Optional[str]) -> bool:
    return (raw or "").strip().lower() == "true"


def parse_dump(xml_data) -> List[UiNode]:
    """把 uiautomator dump 的 XML 解析为控件列表（传入 bytes 以保留编码声明）"""
    if isinstance(xml_data, str):
        xml_data = xml_data.encode("utf-8", "replace")
    if len(xml_data) > 8_000_000:
        return []
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return []

    nodes: List[UiNode] = []
    for el in root.iter("node"):
        bounds = _parse_bounds(el.get("bounds", ""))
        if bounds is None:
            continue
        nodes.append(UiNode(
            text=(el.get("text") or "").strip(),
            desc=(el.get("content-desc") or "").strip(),
            cls=el.get("class") or "",
            rid=el.get("resource-id") or "",
            bounds=bounds,
            clickable=_to_bool(el.get("clickable")),
            focused=_to_bool(el.get("focused")),
            scrollable=_to_bool(el.get("scrollable")),
            enabled=_to_bool(el.get("enabled")) if el.get("enabled") else True,
        ))
    return nodes


def find_nodes(nodes: List[UiNode], query: str,
               exact: bool = False) -> List[UiNode]:
    """按文本 / 描述查找控件，保持文档顺序"""
    query = (query or "").strip().lower()
    if not query:
        return []

    matched = []
    for node in nodes:
        for label in (node.text, node.desc):
            label = (label or "").strip().lower()
            if not label:
                continue
            if (label == query) if exact else (query in label):
                matched.append(node)
                break
    return matched


def dedupe_nodes(nodes: List[UiNode]) -> List[UiNode]:
    """去掉同一位置重复上报的节点（多窗口时会出现）"""
    seen = set()
    result = []
    for node in nodes:
        key = (node.label, node.bounds)
        if key in seen:
            continue
        seen.add(key)
        result.append(node)
    return result


def best_node(nodes: List[UiNode]) -> Optional[UiNode]:
    """从候选节点中选出最可能的点击目标：可点击优先，其次面积最小"""
    if not nodes:
        return None
    clickable = [n for n in nodes if n.clickable]
    pool = clickable or nodes
    return min(pool, key=lambda n: (0 if n.clickable else 1, n.area))


def visible_texts(nodes: List[UiNode], limit: int = 60) -> List[str]:
    """提取界面可见文字（去重、过滤纯数字/占位符）"""
    texts: List[str] = []
    seen = set()
    for node in nodes:
        label = node.label
        if not label or label in seen:
            continue
        if len(label) > 40 or label in ("- -", "-", "0"):
            continue
        seen.add(label)
        texts.append(label)
        if len(texts) >= limit:
            break
    return texts


def describe_nodes(nodes: List[UiNode], limit: int = 40) -> List[dict]:
    """给 AI 的精简控件清单"""
    items = []
    for node in nodes:
        label = node.label
        if not label:
            continue
        items.append({
            "text": node.text[:30],
            "desc": node.desc[:30],
            "class": node.cls.split(".")[-1],
            "bounds": list(node.bounds),
            "clickable": node.clickable,
        })
        if len(items) >= limit:
            break
    return items
