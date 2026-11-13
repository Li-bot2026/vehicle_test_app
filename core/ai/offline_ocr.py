"""
离线 OCR 定位
=============
为画布界面（游戏 / 视频等 SurfaceView）提供离线文字定位能力。

当系统控件树里没有可读文字、且未配置视觉模型时，用 RapidOCR
在截图中识别中文文字并返回目标坐标，让 ui_tap / agent_task 能
在没有 API Key 的情况下点击游戏按钮。

依赖：rapidocr_onnxruntime（可选，缺失时自动降级）
"""

from __future__ import annotations

import io
from typing import List, Optional, Tuple

from loguru import logger

_ocr_engine = None
_ocr_init_failed = False


def _get_engine():
    """懒加载 RapidOCR 引擎（首次调用时初始化）"""
    global _ocr_engine, _ocr_init_failed
    if _ocr_engine is not None:
        return _ocr_engine
    if _ocr_init_failed:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
        return _ocr_engine
    except ImportError:
        _ocr_init_failed = True
        logger.debug(
            "离线 OCR 不可用：未安装 rapidocr_onnxruntime，"
            "可执行 pip install rapidocr_onnxruntime 启用画布文字定位"
        )
        return None
    except Exception as e:
        _ocr_init_failed = True
        logger.debug(f"离线 OCR 初始化失败：{e}")
        return None


def is_available() -> bool:
    """离线 OCR 是否可用（已安装 RapidOCR）"""
    return _get_engine() is not None


def find_text(
    image_bytes: bytes,
    target: str,
    screen_size: Optional[Tuple[int, int]] = None,
) -> Optional[Tuple[int, int]]:
    """
    在截图中查找目标文字，返回其中心坐标 (x, y)；找不到返回 None。

    参数：
        image_bytes: PNG/JPEG 截图原始字节
        target: 要查找的文字（支持部分匹配）
        screen_size: (宽, 高)，用于坐标合法性校验
    """
    engine = _get_engine()
    if engine is None or not target:
        return None

    try:
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        img_array = np.array(img)
        result, _ = engine(img_array)
    except ImportError:
        logger.debug("离线 OCR 需要 Pillow + numpy")
        return None
    except Exception as e:
        logger.debug(f"离线 OCR 识别失败：{e}")
        return None

    if not result:
        return None

    target_lower = target.lower()
    best_match = None
    best_score = 0.0

    for line in result:
        box, text, confidence = line
        if not text:
            continue
        text_stripped = text.strip()
        # 精确包含匹配
        if target in text_stripped or text_stripped in target:
            if confidence > best_score:
                best_score = confidence
                # box 是四个角点 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                cx = int(sum(xs) / len(xs))
                cy = int(sum(ys) / len(ys))
                best_match = (cx, cy)
        else:
            # 模糊匹配：去掉空格和标点后比较
            text_clean = text_stripped.replace(" ", "").replace(" ", "")
            target_clean = target.replace(" ", "").replace(" ", "")
            if target_clean in text_clean or text_clean in target_clean:
                if confidence > best_score:
                    best_score = confidence
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    cx = int(sum(xs) / len(xs))
                    cy = int(sum(ys) / len(ys))
                    best_match = (cx, cy)

    if best_match and screen_size:
        w, h = screen_size
        x, y = best_match
        if not (0 <= x <= w and 0 <= y <= h):
            logger.debug(f"OCR 坐标越界：({x},{y}) 超出 {w}x{h}")
            return None

    return best_match


def find_all_texts(
    image_bytes: bytes,
    screen_size: Optional[Tuple[int, int]] = None,
) -> List[Tuple[str, Tuple[int, int], float]]:
    """
    识别截图中的所有文字，返回 [(文字, 中心坐标, 置信度), ...]
    """
    return find_all_texts_region(image_bytes, None, screen_size)


def find_all_texts_region(
    image_bytes: bytes,
    box: Optional[Tuple[int, int, int, int]] = None,
    screen_size: Optional[Tuple[int, int]] = None,
    scale: float = 1.0,
) -> List[Tuple[str, Tuple[int, int], float]]:
    """
    识别截图指定区域（box=(x0,y0,x1,y1)，None 表示全图）内的文字。
    区域会按 scale 放大后再识别，适合变暗弹窗里的小字；
    返回的坐标已换算回原截图坐标系。
    """
    engine = _get_engine()
    if engine is None:
        return []

    try:
        import numpy as np
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        logger.debug(f"离线 OCR 识别失败：{e}")
        return []

    ox = oy = 0.0
    factor = 1.0
    try:
        if box is not None:
            w, h = img.size
            x0 = max(0, int(box[0]))
            y0 = max(0, int(box[1]))
            x1 = min(w, int(box[2]) + 1)
            y1 = min(h, int(box[3]) + 1)
            if x1 - x0 < 16 or y1 - y0 < 16:
                return []
            img = img.crop((x0, y0, x1, y1))
            ox, oy = float(x0), float(y0)
        if scale and scale != 1.0:
            img = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                Image.LANCZOS)
            factor = float(scale)
        img_array = np.array(img)
        result, _ = engine(img_array)
    except Exception as e:
        logger.debug(f"离线 OCR 识别失败：{e}")
        return []

    if not result:
        return []

    texts = []
    for line in result:
        box_pts, text, confidence = line
        if not text:
            continue
        text = text.strip()
        if not text:
            continue
        xs = [p[0] for p in box_pts]
        ys = [p[1] for p in box_pts]
        cx = int(ox + sum(xs) / len(xs) / factor)
        cy = int(oy + sum(ys) / len(ys) / factor)
        if screen_size:
            w, h = screen_size
            if not (0 <= cx <= w and 0 <= cy <= h):
                continue
        texts.append((text, (cx, cy), confidence))

    return texts
