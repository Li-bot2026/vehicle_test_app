"""
界面方法记忆
=============
把「目标文字 → 屏幕坐标」的对应关系沉淀到用户配置文件，
让新用例不需要改程序就能适配新应用/新按钮：

- 自动学习：画布界面上 OCR / 视觉模型成功定位后，自动记录坐标
- 手动补充：直接编辑 YAML，为无文字按钮或识别不准的目标指定坐标
- 自定义同义词：在 ui_synonyms.yaml 里扩展 resource-id 语义匹配

文件位置（用户目录，随程序自动创建，也可手动编辑）：
    ~/.vehicle_test_app/ui_methods.yaml
    ~/.vehicle_test_app/ui_synonyms.yaml

ui_methods.yaml 结构：
    apps:
      com.wt.sweepmine:
        "2560x1600":
          开始游戏: {x: 1278, y: 962}

ui_synonyms.yaml 结构：
    synonyms:
      人机: [ai, cpu, bot]
      联机: [online, multi]
    （与内置同义词表合并，同键会追加而不是覆盖）
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from loguru import logger

_LOCK = threading.RLock()
_methods_cache: Optional[dict] = None
_methods_mtime: float = -1.0
_synonyms_cache: Optional[Dict[str, Tuple[str, ...]]] = None
_synonyms_mtime: float = -1.0

_METHODS_HEADER = """\
# 界面方法记忆（程序自动维护，也可手动编辑）
# 画布界面上 OCR/视觉模型定位成功后会自动写入这里，下次直接复用坐标。
#
# 结构：
#   apps:
#     com.example.app:
#       "2560x1600":            # 屏幕分辨率，必须与设备一致
#         开始游戏: {x: 1278, y: 962}
#
# 需要手动补充时，直接加一行「目标文字: {x: ..., y: ...}」即可。
"""

_SYNONYMS_HEADER = """\
# 自定义 resource-id 同义词（可选）
# 用于无文字控件：把中文目标映射到 resource-id 里的英文片段。
#
# 结构：
#   synonyms:
#     人机: [ai, cpu, bot]
#     联机: [online, multi]
#
# 与程序内置同义词表合并（同键追加，不会覆盖）。
"""


def _config_dir() -> Path:
    root = Path.home() / ".vehicle_test_app"
    root.mkdir(parents=True, exist_ok=True)
    return root


def methods_path() -> Path:
    return _config_dir() / "ui_methods.yaml"


def synonyms_path() -> Path:
    return _config_dir() / "ui_synonyms.yaml"


def _read_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        logger.debug("PyYAML 未安装，界面方法记忆不可用")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"界面方法文件解析失败，已忽略：{path.name} - {e}")
        return {}
    return data if isinstance(data, dict) else {}


def _load_methods(force: bool = False) -> dict:
    """加载方法记忆（按文件修改时间自动刷新，支持运行中手动编辑）"""
    global _methods_cache, _methods_mtime
    path = methods_path()
    with _LOCK:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            mtime = -1.0
        if (_methods_cache is not None and not force
                and mtime == _methods_mtime):
            return _methods_cache
        data = _read_yaml(path)
        data.setdefault("apps", {})
        _methods_cache = data
        _methods_mtime = mtime
        return data


def _save_methods() -> None:
    global _methods_mtime
    try:
        import yaml
    except ImportError:
        return
    path = methods_path()
    with _LOCK:
        text = yaml.safe_dump(
            _methods_cache or {"apps": {}},
            allow_unicode=True, sort_keys=False,
        )
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(_METHODS_HEADER + text, encoding="utf-8")
            tmp.replace(path)
            _methods_mtime = path.stat().st_mtime
        except Exception as e:
            logger.warning(f"界面方法记忆保存失败：{e}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _screen_key(size) -> str:
    if not size or len(size) != 2:
        return ""
    return f"{int(size[0])}x{int(size[1])}"


def lookup(package: str, size, text: str) -> Optional[Tuple[int, int]]:
    """查询已学习的坐标；没有记录返回 None"""
    if not package or not text:
        return None
    key = _screen_key(size)
    if not key:
        return None
    data = _load_methods()
    screens = (data.get("apps") or {}).get(package) or {}
    entries = screens.get(key) or screens.get("default") or {}
    if not isinstance(entries, dict):
        return None

    want = _norm(text)
    # 先精确匹配，再包含匹配（OCR 可能带上相邻文字）
    exact = None
    loose = None
    for name, value in entries.items():
        if not isinstance(value, dict):
            continue
        try:
            x, y = int(value["x"]), int(value["y"])
        except (KeyError, TypeError, ValueError):
            continue
        name_norm = _norm(str(name))
        if name_norm == want:
            exact = (x, y)
            break
        if loose is None and (want in name_norm or name_norm in want):
            loose = (x, y)
    result = exact or loose
    if result:
        logger.debug(f"界面方法记忆命中：{package} {key} 「{text}」→ {result}")
    return result


def remember(package: str, size, text: str, x: int, y: int) -> None:
    """记录一次成功定位（画布界面），供下次直接复用"""
    if not package or not text:
        return
    key = _screen_key(size)
    if not key:
        return
    data = _load_methods()
    apps = data.setdefault("apps", {})
    screens = apps.setdefault(package, {})
    entries = screens.setdefault(key, {})
    if not isinstance(entries, dict):
        entries = screens[key] = {}
    entries[str(text).strip()] = {"x": int(x), "y": int(y)}
    # 防止文件无限膨胀：单屏最多保留 200 条
    if len(entries) > 200:
        for name in list(entries)[: len(entries) - 200]:
            entries.pop(name, None)
    _save_methods()
    logger.info(f"已学习界面方法：{package} {key} 「{text}」→ ({x},{y})")


def extra_synonyms() -> Dict[str, Tuple[str, ...]]:
    """读取用户自定义的 resource-id 同义词（按修改时间缓存）"""
    global _synonyms_cache, _synonyms_mtime
    path = synonyms_path()
    with _LOCK:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            mtime = -1.0
        if _synonyms_cache is not None and mtime == _synonyms_mtime:
            return _synonyms_cache
        raw = _read_yaml(path).get("synonyms") or {}
        result: Dict[str, Tuple[str, ...]] = {}
        if isinstance(raw, dict):
            for key, values in raw.items():
                if isinstance(values, str):
                    values = [values]
                if not isinstance(values, (list, tuple)):
                    continue
                items = tuple(
                    str(v).strip().lower() for v in values if str(v).strip()
                )
                if items:
                    result[str(key)] = items
        _synonyms_cache = result
        _synonyms_mtime = mtime
        return result


def ensure_templates() -> None:
    """首次使用时可调用：生成带注释的示例文件，方便用户手动编辑"""
    targets = (
        (methods_path(), _METHODS_HEADER + "apps: {}\n"),
        (synonyms_path(), _SYNONYMS_HEADER + "synonyms: {}\n"),
    )
    for path, content in targets:
        try:
            if not path.exists():
                path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.debug(f"界面方法模板创建失败：{e}")
