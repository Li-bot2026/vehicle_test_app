"""
被测应用与界面上下文
====================
生成用例前采集真实环境信息（前台应用、启动页、界面可见文字、已安装应用），
让 AI 直接使用真实包名与控件文字，而不是凭空编造。
"""

from typing import Optional

from loguru import logger

from ..device.adb_device import ADBDevice

# SurfaceView/游戏渲染层的占位文字，不代表可读界面内容
_SURFACE_LABELS = {"game view", "surfaceview", "surface", "video view"}


def _readable(texts) -> list:
    out = []
    for t in texts or []:
        t = str(t).strip()
        if t and t.lower() not in _SURFACE_LABELS and t not in out:
            out.append(t)
    return out


def pick_adb_device(device_manager) -> Optional[ADBDevice]:
    """取第一个已连接的 ADB 设备"""
    if device_manager is None:
        return None
    try:
        for device in device_manager.get_all_devices().values():
            if isinstance(device, ADBDevice):
                return device
    except Exception:
        pass
    return None


def collect_app_context(device_manager, app_package: str = "",
                        app_activity: str = "") -> dict:
    """
    采集生成用例所需的设备上下文。

    参数:
        app_package: 用户在界面上选定的目标应用包名（可为空）
        app_activity: 已知启动页（可为空，为空时自动解析）

    返回空字典表示当前没有可用的 ADB 设备。
    """
    device = pick_adb_device(device_manager)
    if device is None:
        # 没有设备时也要保留用户选定的目标应用，生成用例仍可用包名启动
        target = (app_package or "").strip()
        if not target:
            return {}
        context = {"package": target}
        if (app_activity or "").strip():
            context["launch_activity"] = app_activity.strip()
        return context

    context = {"device_id": device.device_id}
    try:
        package, activity = device.get_foreground_app()
        context["foreground"] = {
            "package": package or "",
            "activity": activity or "",
        }

        screen = device.get_screen_size(use_cache=True)
        if screen:
            context["screen_size"] = [screen[0], screen[1]]

        packages = device.list_packages()
        if packages:
            context["installed_packages"] = packages[:200]

        # 获取应用名称到包名的映射，用于解析中文应用名
        try:
            app_name_map = device.get_app_name_map()
            if app_name_map:
                context["app_name_map"] = app_name_map
        except Exception as e:
            logger.debug(f"获取应用名称映射失败：{e}")

        texts = _readable(device.visible_texts(limit=60))
        if texts:
            context["visible_texts"] = texts
        else:
            # 画布渲染界面：控件树读不到文字，自动用离线 OCR 补全
            context["canvas"] = True
            ocr_texts = _ocr_texts(device, context.get("screen_size"))
            if ocr_texts:
                context["visible_texts"] = ocr_texts
                context["visible_texts_source"] = "ocr"

        target = (app_package or "").strip()
        if not target:
            foreground_pkg = context["foreground"]["package"]
            # 启动器只能说明当前停留位置，不代表被测应用
            if foreground_pkg and "launcher" not in foreground_pkg.lower():
                target = foreground_pkg

        if target:
            context["package"] = target
            launch = (app_activity or "").strip()
            if not launch:
                launch = device.resolve_launch_activity(target)
            if launch:
                context["launch_activity"] = launch
    except Exception as e:
        logger.debug(f"采集应用上下文失败: {e}")

    return context


def _ocr_texts(device, screen_size=None, limit: int = 40) -> list:
    """用离线 OCR 读取画布界面上的文字（不可用时返回空列表）"""
    try:
        from .offline_ocr import is_available, find_all_texts
        if not is_available():
            return []
        image = device.screencap_bytes()
        if not image:
            return []
        size = tuple(screen_size) if screen_size else (
            device.get_screen_size(use_cache=True) or (0, 0))
        found = find_all_texts(image, size)
        texts = []
        for item in found:
            text = str(item[0]).strip()
            if text and text not in texts:
                texts.append(text)
            if len(texts) >= limit:
                break
        return texts
    except Exception as e:
        logger.debug(f"OCR 读取界面文字失败: {e}")
        return []


def is_canvas_context(context: dict) -> bool:
    """上下文是否来自画布渲染界面（控件树读不到文字）"""
    return bool(context.get("canvas")) and not context.get("visible_texts")


def capture_screenshot_b64(device_manager) -> str:
    """采集当前屏幕截图（base64，供视觉模型读屏）；不可用时返回空串"""
    import base64

    device = pick_adb_device(device_manager)
    if device is None:
        return ""
    try:
        image = device.screencap_bytes()
        if not image:
            return ""
        return base64.b64encode(image).decode("ascii")
    except Exception as e:
        logger.debug(f"采集生成用截图失败: {e}")
        return ""
