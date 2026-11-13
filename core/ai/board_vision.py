"""
棋盘视觉识别（内置，不依赖外部 AI 服务）
==========================================
从 ADB 截图中定位棋盘、读取每个交叉点是「空 / 黑 / 白」，
供内置五子棋引擎在离线状态下真正“看见”并落子。

实现要点：
- 棋盘是木色矩形，用颜色阈值找外框（纯 numpy，无需 opencv）
- 网格为等距线，按外框内缩比例推算，再在线附近做暗线峰值校准
- 棋子以交叉点环形区域的平均颜色判定；星位小圆点只占圆心，不会误判成黑子
"""

from typing import List, Optional, Tuple

import numpy as np
from PyQt6.QtGui import QImage

EMPTY, BLACK, WHITE = 0, 1, 2
BOARD_SIZE = 15

# 判定阈值（截图取值为 0-255 的三通道和/最小值）
# 放宽阈值以适配不同游戏的棋子颜色
_BLACK_SUM = 200       # 三通道和低于该值 → 黑子（从 150 放宽到 200）
_WHITE_MIN = 170       # 最小通道高于该值且足够亮 → 白子（从 185 降低到 170）
_WHITE_SUM = 540       # 三通道和高于该值 → 白子（从 585 降低到 540）


class Board:
    """一帧棋盘：网格状态 + 屏幕几何信息"""

    def __init__(self, grid: List[List[int]], x0: float, y0: float,
                 step: float, size: int):
        self.grid = grid
        self.x0 = x0
        self.y0 = y0
        self.step = step
        self.size = size

    # ---------- 坐标 ----------

    def center(self, row: int, col: int) -> Tuple[int, int]:
        return (int(round(self.x0 + col * self.step)),
                int(round(self.y0 + row * self.step)))

    # ---------- 状态 ----------

    def at(self, row: int, col: int) -> int:
        return self.grid[row][col]

    def stones(self, color: int) -> int:
        return sum(row.count(color) for row in self.grid)

    def total(self) -> int:
        return self.stones(BLACK) + self.stones(WHITE)

    def signature(self) -> Tuple[Tuple[int, ...], ...]:
        return tuple(tuple(row) for row in self.grid)

    def has_five(self, color: int) -> bool:
        """是否已有五连（含长连）"""
        n = self.size
        for r in range(n):
            for c in range(n):
                if self.grid[r][c] != color:
                    continue
                for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                    if _five_from(self.grid, n, r, c, dr, dc, color):
                        return True
        return False

    def find_five(self, color: int) -> Optional[List[Tuple[int, int]]]:
        """返回五连的坐标列表（用于日志/截图标注）"""
        n = self.size
        for r in range(n):
            for c in range(n):
                if self.grid[r][c] != color:
                    continue
                for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                    if _five_from(self.grid, n, r, c, dr, dc, color):
                        return [(r + dr * i, c + dc * i) for i in range(5)]
        return None

    def empty_cells(self) -> List[Tuple[int, int]]:
        return [(r, c) for r in range(self.size) for c in range(self.size)
                if self.grid[r][c] == EMPTY]


def _five_from(grid, n, r, c, dr, dc, color) -> bool:
    er, ec = r + dr * 4, c + dc * 4
    if not (0 <= er < n and 0 <= ec < n):
        return False
    return all(grid[r + dr * i][c + dc * i] == color for i in range(5))


# ---------- 截图解析 ----------

def _load_rgb(png: bytes) -> Optional[np.ndarray]:
    if not png:
        return None
    img = QImage()
    if not img.loadFromData(png):
        return None
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = img.constBits().asstring(img.sizeInBytes())
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpl)[:, : w * 3]
    return np.ascontiguousarray(arr.reshape(h, w, 3)).astype(np.int32)


def _load_rgb_from_raw(raw_data: bytes, width: int, height: int, fmt: int) -> Optional[np.ndarray]:
    """从原始帧数据加载 RGB 数组（比 PNG 解码更快）"""
    try:
        bpp = {1: 4, 2: 4, 3: 2, 4: 4, 5: 3}.get(fmt)
        if not bpp:
            return None
        # RGBA8888 (fmt=1) 或 RGBX8888 (fmt=2)
        if bpp == 4:
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width, 4)
            return arr[:, :, :3].astype(np.int32)
        # RGB565 (fmt=3)
        elif bpp == 2:
            arr = np.frombuffer(raw_data, dtype=np.uint16).reshape(height, width)
            r = ((arr >> 11) & 0x1F) << 3
            g = ((arr >> 5) & 0x3F) << 2
            b = (arr & 0x1F) << 3
            return np.stack([r, g, b], axis=-1).astype(np.int32)
        # RGB888 (fmt=5)
        elif bpp == 3:
            arr = np.frombuffer(raw_data, dtype=np.uint8).reshape(height, width, 3)
            return arr.astype(np.int32)
    except Exception:
        pass
    return None


def _longest_run(values: np.ndarray, merge_gap: int = 0
                 ) -> Optional[Tuple[int, int]]:
    """有序索引中最长的连续段（允许 merge_gap 以内的空隙）"""
    if len(values) == 0:
        return None
    best = (int(values[0]), int(values[0]))
    start = prev = int(values[0])
    for v in values[1:]:
        v = int(v)
        if v - prev <= merge_gap + 1:
            prev = v
            continue
        if prev - start > best[1] - best[0]:
            best = (start, prev)
        start = prev = v
    if prev - start > best[1] - best[0]:
        best = (start, prev)
    return best


def _wood_bbox(a: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """找到木色棋盘外框 (x0, y0, x1, y1)，找不到返回 None"""
    h, w = a.shape[:2]
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    # 放宽木色阈值，适配更多棋盘颜色（金黄/浅棕/深棕）
    wood = ((r > 140) & (g > 90) & (b > 40)
            & (r > g) & (g > b)           # 暖色调：R > G > B
            & ((r - b) > 25)               # 红色分量明显高于蓝色
            & ((r - g) < 110))             # 但不过于偏红
    cols = wood.sum(axis=0)
    rows = wood.sum(axis=1)
    xs = np.where(cols > h * 0.12)[0]
    ys = np.where(rows > w * 0.12)[0]
    # 网格线会把木色列/行割成小段，允许 ≤10px 的空隙；
    # 取最长连续段，避免把左侧金色按钮等木色物体算进外框。
    run_x = _longest_run(xs, merge_gap=10)
    run_y = _longest_run(ys, merge_gap=10)
    if run_x is None or run_y is None:
        return None
    x0, x1 = run_x
    y0, y1 = run_y
    ww, hh = x1 - x0, y1 - y0
    if ww < w * 0.15 or hh < h * 0.15:
        return None
    # 棋盘近似正方形
    if abs(ww - hh) > 0.15 * max(ww, hh):
        return None
    return x0, y0, x1, y1


def _count_grid_lines(a: np.ndarray, x0: int, y0: int, x1: int, y1: int,
                      step_guess: float) -> int:
    """在棋盘上方一条干净横带里数竖线数量，用于确认棋盘规格（15/13/19 路）"""
    band_y0 = y0 + int((y1 - y0) * 0.03)
    band_y1 = y0 + int((y1 - y0) * 0.10)
    band = a[band_y0:band_y1, x0:x1]
    if band.size == 0:
        return 0
    profile = band.sum(axis=2).mean(axis=0)
    # 暗线 = 明显低于局部均值的列
    base = float(np.median(profile))
    thresh = base * 0.985
    peaks = []
    min_gap = max(4, int(step_guess * 0.6))
    i = 1
    while i < len(profile) - 1:
        if profile[i] < thresh and profile[i] <= profile[i - 1] \
                and profile[i] <= profile[i + 1]:
            peaks.append(i)
            i += min_gap
        else:
            i += 1
    return len(peaks)


def detect_board(png: bytes) -> Optional[Board]:
    """从截图识别棋盘并读取棋子分布"""
    a = _load_rgb(png)
    if a is None:
        return None
    return _detect_board_from_array(a)


def detect_board_from_raw(raw_data: bytes, width: int, height: int, fmt: int) -> Optional[Board]:
    """从原始帧数据识别棋盘（比 PNG 解码更快）"""
    a = _load_rgb_from_raw(raw_data, width, height, fmt)
    if a is None:
        return None
    return _detect_board_from_array(a)


def _detect_board_from_array(a: np.ndarray) -> Optional[Board]:
    """从 numpy 数组识别棋盘"""
    bbox = _wood_bbox(a)
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    from loguru import logger
    logger.debug(f"棋盘外框：({x0},{y0})-({x1},{y1}) 尺寸{(x1-x0)}x{(y1-y0)}")

    # 网格线在外框内等距内缩；先用 15 路推算步长，再按检测到的线数校正
    inset = (x1 - x0) * 0.019
    step = (x1 - x0 - 2 * inset) / (BOARD_SIZE - 1)
    size = BOARD_SIZE
    lines = _count_grid_lines(a, x0, y0, x1, y1, step)
    # 只接受标准棋盘尺寸（13/15/19 路），其他都默认 15 路
    if lines in (13, 19) and abs(lines - BOARD_SIZE) >= 2:
        size = lines
        step = (x1 - x0 - 2 * inset) / (size - 1)
    elif lines != BOARD_SIZE:
        # 非标准尺寸，记录日志但使用默认 15 路
        logger.debug(f"棋盘线数检测为{lines}，非标准尺寸，使用默认 15 路")

    grid = [[EMPTY] * size for _ in range(size)]
    for r in range(size):
        cy = y0 + inset + r * step
        for c in range(size):
            cx = x0 + inset + c * step
            grid[r][c] = _classify(a, cx, cy, step)

    return Board(grid, x0 + inset, y0 + inset, step, size)


def _classify(a: np.ndarray, cx: float, cy: float, step: float) -> int:
    """判定单个交叉点：空 / 黑 / 白"""
    rad = max(3, int(step * 0.30))
    inner = max(2, int(step * 0.13))
    y1, y2 = int(cy) - rad, int(cy) + rad
    x1, x2 = int(cx) - rad, int(cx) + rad
    if y1 < 0 or x1 < 0 or y2 >= a.shape[0] or x2 >= a.shape[1]:
        return EMPTY
    patch = a[y1:y2, x1:x2]
    yy, xx = np.ogrid[y1:y2, x1:x2]
    d2 = (yy - cy) ** 2 + (xx - cx) ** 2
    mask = (d2 <= rad * rad) & (d2 >= inner * inner)
    if not mask.any():
        return EMPTY
    px = patch[mask]
    mean = px.mean(axis=0)
    total = float(mean.sum())
    min_chan = float(mean.min())
    # 黑子：整体很暗
    if total < _BLACK_SUM:
        return BLACK
    # 白子：三个通道都很亮
    if min_chan > _WHITE_MIN and total > _WHITE_SUM:
        return WHITE
    return EMPTY
