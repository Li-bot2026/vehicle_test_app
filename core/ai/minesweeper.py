"""
内置扫雷视觉引擎
================
不依赖云模型，直接从 ADB 截图里“看”棋盘并推理下一步。

识别思路（针对经典扫雷牌面）：
- 棋盘外框是「左上暗、右下亮」的凹陷面板，用长直线定位棋盘区域；
- 格子是均匀网格，用边缘梯度自相关估计格距，得到行列数；
- 未翻开格：底色偏亮 + 左上白色高光边（凸起）；已翻开格：底色偏暗、无高光；
- 数字颜色固定（1 蓝 / 2 绿 / 3 红 / 4 深蓝 / 5 暗红 / 6 青），
  按格内彩色像素的占比判断数字；OCR 数字作为补充。

推理规则（标准扫雷规则）：
- 数字 N 表示周围 8 格中雷的数量；
- 若周围已确定的雷数 == N，其余未翻开格必定安全 → 点开；
- 若周围未翻开格数 + 已确定的雷数 == N，其余未翻开格全是雷 → 内部标记；
- 没有必然解时，对所有边界约束枚举合法布雷方案，选踩雷概率最低的格子；
- 绝不重复点击已翻开的格子（旧版反复点同一个格子的根本原因）。

胜负：
- 全部非雷格翻开（剩余未翻开格数 == 剩余雷数）即胜利；
- 界面变暗并出现“你输了”提示即失败，可自动点“再玩一局”重开。
"""

from __future__ import annotations

import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
from PyQt6.QtGui import QImage
from loguru import logger


COVERED = 0
OPEN = 1
FLAGGED = 2      # 已插旗（本作的长按标记，画成平面格子 + 红旗下黑杆）
_UNKNOWN = -1

# 单元格内数字颜色 → 数字（经典扫雷配色）
_DIGIT_MASKS = {
    1: lambda r, g, b: (b > 170) & (r < 90) & (g < 90),
    2: lambda r, g, b: (g > 90) & (g < 190) & (r < 90) & (b < 90),
    3: lambda r, g, b: (r > 170) & (g < 90) & (b < 90),
    4: lambda r, g, b: (b > 70) & (b <= 170) & (r < 70) & (g < 70),
    5: lambda r, g, b: (r > 70) & (r <= 170) & (g < 70) & (b < 70),
    6: lambda r, g, b: (g > 70) & (g <= 170) & (b > 70) & (b <= 170) & (r < 70),
}

_MAX_COMPONENT_CELLS = 20
_MAX_ENUM_SOLUTIONS = 4000
_MAX_ENUM_NODES = 200000


# ============================================================
# 图像加载
# ============================================================

def _load_rgb(png: bytes) -> Optional[np.ndarray]:
    """PNG 字节 → (h, w, 3) int32 数组"""
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


def screenshot_dim(png: bytes) -> Optional[Tuple[int, int]]:
    """返回截图屏幕亮度中位数（用于判断是否弹出变暗对话框）"""
    a = _load_rgb(png)
    if a is None:
        return None
    return (int(a.shape[1]), int(a.shape[0]))


def background_luma(png: bytes) -> Optional[float]:
    """画面变暗检测：取四角区域的中位亮度。弹窗/结算时画面会整体变暗。"""
    a = _load_rgb(png)
    if a is None:
        return None
    h, w = a.shape[:2]
    corners = np.concatenate([
        a[0:40, 0:40].reshape(-1, 3),
        a[0:40, w - 40:w].reshape(-1, 3),
        a[h - 40:h, 0:40].reshape(-1, 3),
        a[h - 40:h, w - 40:w].reshape(-1, 3),
    ])
    return float(np.median(corners.mean(axis=1)))


def dialog_panel(png: bytes) -> Optional[Tuple[int, int, int, int]]:
    """
    定位变暗画面里的亮色弹窗面板，返回 (x0, y0, x1, y1)。

    结算弹窗是浅色大矩形，叠在变暗的棋盘上；按 32px 方块统计亮度，
    取最大的连续亮块作为面板。整屏 OCR 对变暗画面识别率很低，
    先裁出这块面板再放大识别，文字才读得出来。
    """
    a = _load_rgb(png)
    if a is None:
        return None
    h, w = a.shape[:2]
    blk = 32
    gh, gw = h // blk, w // blk
    if gh < 4 or gw < 4:
        return None
    grid = (a.mean(axis=2)[:gh * blk, :gw * blk]
            .reshape(gh, blk, gw, blk).mean(axis=(1, 3)) > 120)
    seen = np.zeros((gh, gw), dtype=bool)
    best: Optional[Tuple[int, int, int, int, int]] = None
    for sy in range(gh):
        for sx in range(gw):
            if not grid[sy, sx] or seen[sy, sx]:
                continue
            stack = [(sy, sx)]
            seen[sy, sx] = True
            count = 0
            y0b = y1b = sy
            x0b = x1b = sx
            while stack:
                y, x = stack.pop()
                count += 1
                y0b, y1b = min(y0b, y), max(y1b, y)
                x0b, x1b = min(x0b, x), max(x1b, x)
                for ny, nx in ((y - 1, x), (y + 1, x),
                               (y, x - 1), (y, x + 1)):
                    if (0 <= ny < gh and 0 <= nx < gw
                            and grid[ny, nx] and not seen[ny, nx]):
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if count >= 20 and (best is None or count > best[0]):
                best = (count, y0b, y1b, x0b, x1b)
    if best is None:
        return None
    _c, y0b, y1b, x0b, x1b = best
    x0, y0 = x0b * blk, y0b * blk
    x1 = min(w - 1, (x1b + 1) * blk - 1)
    y1 = min(h - 1, (y1b + 1) * blk - 1)
    # 面板不会太小，避免把零星亮块当弹窗
    if x1 - x0 < w * 0.12 or y1 - y0 < h * 0.06:
        return None
    return (x0, y0, x1, y1)


# ============================================================
# 棋盘定位
# ============================================================

def _groups(values: Sequence[int], gap: int = 3) -> List[Tuple[int, int]]:
    """连续整数分组，返回 [(start, end), ...]"""
    if not values:
        return []
    out: List[Tuple[int, int]] = []
    start = prev = int(values[0])
    for v in values[1:]:
        v = int(v)
        if v - prev <= gap:
            prev = v
            continue
        out.append((start, prev))
        start = prev = v
    out.append((start, prev))
    return out


def _line_groups(score: np.ndarray, thr: float = 0.5) -> List[Tuple[int, int]]:
    """把「长暗线」评分曲线转成一条条线（分组），返回 [(起点, 终点), ...]"""
    idx = [i for i, v in enumerate(score) if v > thr]
    return _groups(idx)


def _periodic_run(groups: Sequence[Tuple[int, int]],
                  min_lines: int = 5) -> Optional[Tuple[float, float, int, float]]:
    """
    在一堆线分组中找出「等间距」的那一段（即棋盘网格）。

    扫雷棋盘由一圈外框 + 内部等距网格线组成，外框和网格线都是暗线；
    棋盘上方可能还有装饰线，另外红旗等彩色元素偶尔也会被误判成线，
    所以不要求严格相邻，只要落在「格距的整数倍」上就收进同一条链，
    离网格的杂线直接跳过。

    返回 (第一条线中心, 最后一条线中心, 格子数, 格距) 或 None。
    """
    if len(groups) < min_lines:
        return None
    centers = [(g[0] + g[1]) / 2.0 for g in groups]
    diffs = [b - a for a, b in zip(centers, centers[1:])]
    cand = [d for d in diffs if 30 <= d <= 220]
    if len(cand) < 3:
        return None
    pitch = float(np.median(cand))
    # 用靠近中位数的差分再收敛一次，避免杂线把格距拉偏
    near = [d for d in cand if abs(d - pitch) <= pitch * 0.15]
    if len(near) >= 3:
        pitch = float(np.median(near))
    if pitch <= 0:
        return None

    # 以每个分组为起点向后串链：落在格距 1~3 倍位置上的分组才收进来
    best: Optional[List[Tuple[int, int]]] = None  # [(分组下标, 跳过的格数), ...]
    for i in range(len(centers)):
        chain: List[Tuple[int, int]] = [(i, 0)]
        last = centers[i]
        for j in range(i + 1, len(centers)):
            gap = centers[j] - last
            k = int(round(gap / pitch))
            if 1 <= k <= 3 and abs(gap - k * pitch) <= pitch * 0.25:
                chain.append((j, k))
                last = centers[j]
        if best is None or len(chain) > len(best):
            best = chain
    if best is None or len(best) < min_lines:
        return None

    first_c = best[0][0]
    last_c = best[-1][0]
    cells = sum(k for _, k in best[1:])
    span = centers[last_c] - centers[first_c]
    if cells <= 0:
        return None
    # 实际跨度应等于「格子数 × 格距」（允许一点偏差）
    if abs(span - cells * pitch) > pitch * 0.5:
        return None
    # 用链内的实际间隔重新算格距（跳线时也能算准）
    steps = [centers[b] - centers[a]
             for (a, _), (b, _) in zip(best, best[1:])]
    ks = [k for _, k in best[1:]]
    if steps:
        pitch = float(np.median([s / k for s, k in zip(steps, ks)]))
    return centers[first_c], centers[last_c], cells, pitch


def _line_extent(profile: np.ndarray, thr: float = 0.5,
                 max_gap: int = 4) -> Optional[Tuple[int, int]]:
    """沿一条线的走向找最长的连续暗段，返回 (起点, 终点)"""
    idx = np.where(profile > thr)[0]
    if idx.size == 0:
        return None
    best = (int(idx[0]), int(idx[0]))
    start = prev = int(idx[0])
    for v in idx[1:]:
        v = int(v)
        if v - prev <= max_gap:
            prev = v
            continue
        if prev - start > best[1] - best[0]:
            best = (start, prev)
        start = prev = v
    if prev - start > best[1] - best[0]:
        best = (start, prev)
    return best


def _detect_field(a: np.ndarray
                  ) -> Optional[Tuple[float, float, float, float, int, int]]:
    """
    定位棋盘区域与行列数。

    先找纵向网格线（它们只在棋盘高度范围内连续存在，顶部按钮/面板的分隔线
    太短，不会入选），再用纵线的实际长度范围卡住横线统计，得到棋盘上下边界
    和行数——界面顶部的装饰面板恰好与网格对齐，只看间距是分不开的。

    （棋盘上的红旗是纯红高饱和色，灰度恰好落在「暗线」区间，
    所以这里只认接近灰/黑的像素，避免红旗被当成网格线。）
    返回 (x0, y0, x1, y1, cols, rows)，坐标为棋盘内容区（网格线内侧）。
    """
    h, w = a.shape[:2]
    gray = a.mean(axis=2)
    spread = a.max(axis=2).astype(np.int16) - a.min(axis=2).astype(np.int16)
    dark = (((gray > 85) & (gray < 170)) & (spread < 45)).astype(np.float32)

    # ---- 纵向：全屏统计，按钮分隔线太短不达标 ----
    score_c = dark.mean(axis=0)
    groups_c = _line_groups(score_c)
    run_c = _periodic_run(groups_c)
    if run_c is None:
        return None
    left_c, right_c, cols, pitch_c = run_c
    if not (5 <= cols <= 40):
        return None

    # 用纵线的纵向范围确定棋盘上下边界：
    # 把所有纵线所在的窄带叠成一条剖面（线上=1、线外=0），
    # 格子边框的高光会在横线处把剖面打断几像素，所以容忍小缺口。
    xs: List[int] = []
    for g in groups_c:
        cx = int((g[0] + g[1]) / 2)
        if left_c - 2 <= cx <= right_c + 2:
            xs.extend(range(max(0, cx - 1), min(w, cx + 2)))
    if len(xs) < 6:
        return None
    ext = _line_extent(dark[:, xs].mean(axis=1), thr=0.5, max_gap=12)
    if ext is None or ext[1] - ext[0] < 200:
        return None
    y_top, y_bot = ext

    # ---- 横向：只统计纵线覆盖的那一段，避开顶部面板 ----
    x_lo, x_hi = int(left_c) + 2, int(right_c) - 1
    if x_hi - x_lo < 100:
        return None
    score_r = dark[y_top + 2:y_bot - 1, x_lo:x_hi].mean(axis=1)
    groups_r = [(g[0] + y_top + 2, g[1] + y_top + 2) for g in _line_groups(score_r)]
    run_r = _periodic_run(groups_r)
    if run_r is None:
        return None
    top_c, bottom_c, rows, pitch_r = run_r
    if not (5 <= rows <= 40):
        return None
    # 格子应接近正方形（车机 UI 可能略宽/略高）
    if not (0.6 <= pitch_c / pitch_r <= 1.6):
        return None

    # 取网格线「内侧」作为内容区，避免把外框厚度算进格子
    left_g = [g for g in groups_c if abs((g[0] + g[1]) / 2 - left_c) < 1]
    right_g = [g for g in groups_c if abs((g[0] + g[1]) / 2 - right_c) < 1]
    top_g = [g for g in groups_r if abs((g[0] + g[1]) / 2 - top_c) < 1]
    bottom_g = [g for g in groups_r if abs((g[0] + g[1]) / 2 - bottom_c) < 1]
    x0 = (left_g[0][1] + 1) if left_g else left_c + pitch_c / 2
    x1 = (right_g[0][0] - 1) if right_g else right_c - pitch_c / 2
    y0 = (top_g[0][1] + 1) if top_g else top_c + pitch_r / 2
    y1 = (bottom_g[0][0] - 1) if bottom_g else bottom_c - pitch_r / 2
    if x1 - x0 < 80 or y1 - y0 < 80:
        return None
    return x0, y0, x1, y1, cols, rows


# ============================================================
# 棋盘模型
# ============================================================

class MinesweeperBoard:
    """一帧扫雷棋盘：格子状态 + 数字 + 屏幕坐标"""

    def __init__(self, rows: int, cols: int, state: List[List[int]],
                 numbers: Dict[Tuple[int, int], int],
                 x0: float, y0: float, cw: float, ch: float,
                 mines_total: Optional[int] = None):
        self.rows = rows
        self.cols = cols
        self.state = state          # COVERED / OPEN / FLAGGED
        self.numbers = numbers      # (r,c) -> 1..8
        self.x0, self.y0 = x0, y0
        self.cw, self.ch = cw, ch
        self.mines_total = mines_total

    # ---------- 几何 ----------

    def center(self, r: int, c: int) -> Tuple[int, int]:
        return (int(round(self.x0 + (c + 0.5) * self.cw)),
                int(round(self.y0 + (r + 0.5) * self.ch)))

    def cell_at(self, x: int, y: int) -> Optional[Tuple[int, int]]:
        c = int((x - self.x0) // self.cw)
        r = int((y - self.y0) // self.ch)
        if 0 <= r < self.rows and 0 <= c < self.cols:
            return r, c
        return None

    # ---------- 状态 ----------

    def is_covered(self, r: int, c: int) -> bool:
        return self.state[r][c] == COVERED

    def is_flagged(self, r: int, c: int) -> bool:
        return self.state[r][c] == FLAGGED

    def covered_cells(self) -> List[Tuple[int, int]]:
        return [(r, c) for r in range(self.rows) for c in range(self.cols)
                if self.state[r][c] == COVERED]

    def flagged_cells(self) -> List[Tuple[int, int]]:
        return [(r, c) for r in range(self.rows) for c in range(self.cols)
                if self.state[r][c] == FLAGGED]

    def covered_count(self) -> int:
        return sum(row.count(COVERED) for row in self.state)

    def flagged_count(self) -> int:
        return sum(row.count(FLAGGED) for row in self.state)

    def open_count(self) -> int:
        return sum(row.count(OPEN) for row in self.state)

    def neighbors(self, r: int, c: int) -> List[Tuple[int, int]]:
        out = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                rr, cc = r + dr, c + dc
                if 0 <= rr < self.rows and 0 <= cc < self.cols:
                    out.append((rr, cc))
        return out

    def signature(self) -> Tuple[Tuple[int, ...], ...]:
        return tuple(tuple(row) for row in self.state)

    def ascii(self) -> str:
        rows = []
        for r in range(self.rows):
            line = ""
            for c in range(self.cols):
                s = self.state[r][c]
                if s == COVERED:
                    line += "#"
                elif s == FLAGGED:
                    line += "F"
                else:
                    n = self.numbers.get((r, c), 0)
                    line += str(n) if n else "."
            rows.append(line)
        return "\n".join(rows)


# ============================================================
# 识别
# ============================================================

def _cell_state(a: np.ndarray, xa: float, ya: float,
                cw: float, ch: float) -> int:
    """
    单格状态判定（COVERED / OPEN / FLAGGED）。

    本作画风：
    - 未翻开格：底色更亮（约 216）、左上白色高光边；
    - 已翻开格：底色偏暗（约 196），无高光；
    - 已插旗格：画成平面格子 + 红色旗面 + 黑色旗杆，
      用「红色占比高 + 有黑色」和数字 3/5（只有红色）区分开。
    """
    xs = slice(max(0, int(xa + cw * 0.28)), max(0, int(xa + cw * 0.72)))
    ys = slice(max(0, int(ya + ch * 0.28)), max(0, int(ya + ch * 0.72)))
    inner = a[ys, xs]
    if inner.size == 0:
        return OPEN
    r, g, b = inner[:, :, 0], inner[:, :, 1], inner[:, :, 2]
    red = float(((r > 150) & (g < 90) & (b < 90)).mean())
    dark = float(((r < 70) & (g < 70) & (b < 70)).mean())
    if red >= 0.15 and dark >= 0.08:
        return FLAGGED
    fill = float(np.median(inner))
    if fill >= 206:
        return COVERED
    if fill <= 200:
        return OPEN
    # 中间地带：左上白色高光 + 右下暗边
    top = a[int(ya + 2):int(ya + 7), xs]
    left = a[ys, int(xa + 2):int(xa + 7)]
    bottom = a[int(ya + ch - 7):int(ya + ch - 2), xs]
    right = a[ys, int(xa + cw - 7):int(xa + cw - 2)]
    tl_hi = max(float(np.percentile(top, 90)), float(np.percentile(left, 90)))
    br_lo = min(float(np.percentile(bottom, 10)), float(np.percentile(right, 10)))
    return COVERED if (tl_hi >= 240 and br_lo < 200) else OPEN


def _read_number(a: np.ndarray, xa: float, ya: float,
                 cw: float, ch: float) -> int:
    """读取已翻开格子里的数字（0 表示空白格）"""
    inner = a[int(ya + ch * 0.20):int(ya + ch * 0.80),
              int(xa + cw * 0.20):int(xa + cw * 0.80)]
    if inner.size == 0:
        return 0
    r, g, b = inner[:, :, 0], inner[:, :, 1], inner[:, :, 2]
    total = inner.shape[0] * inner.shape[1]
    hits: Dict[int, int] = {}
    for num, mask_fn in _DIGIT_MASKS.items():
        hits[num] = int(mask_fn(r, g, b).sum())
    best_num, best_hits = max(hits.items(), key=lambda kv: kv[1])
    if best_hits >= max(18, int(total * 0.02)):
        return best_num
    # 7 是黑色，没有色彩；8 是灰色，极少见，交给 OCR 兜底
    dark = ((r < 70) & (g < 70) & (b < 70)).sum()
    if dark >= max(25, int(total * 0.03)):
        return 7
    gray = ((np.abs(r - g) < 10) & (np.abs(g - b) < 10)
            & (r > 100) & (r < 160)).sum()
    if gray >= max(30, int(total * 0.06)):
        return 8
    return 0


def detect_board(png: bytes,
                 ocr_items: Optional[Iterable[Tuple[str, Tuple[int, int], float]]] = None,
                 mines_total: Optional[int] = None) -> Optional[MinesweeperBoard]:
    """
    从截图中识别扫雷棋盘。

    ocr_items: [(文字, (x, y), 置信度), ...]，用于补充 7/8 等颜色不明显的数字。
    """
    a = _load_rgb(png)
    if a is None:
        return None
    field = _detect_field(a)
    if field is None:
        return None
    x0, y0, x1, y1, cols, rows = field
    fw, fh = x1 - x0, y1 - y0
    if fw < 80 or fh < 80:
        return None

    cw = fw / cols
    ch = fh / rows

    state = [[COVERED] * cols for _ in range(rows)]
    numbers: Dict[Tuple[int, int], int] = {}
    for r in range(rows):
        ya = y0 + r * ch
        for c in range(cols):
            xa = x0 + c * cw
            s = _cell_state(a, xa, ya, cw, ch)
            state[r][c] = s
            if s != OPEN:
                continue
            n = _read_number(a, xa, ya, cw, ch)
            if n:
                numbers[(r, c)] = n

    # OCR 补充数字（尤其 7/8），只填“已翻开但没读到数字”的格子
    for text, pos, _conf in (ocr_items or []):
        t = str(text).strip()
        if len(t) != 1 or t not in "12345678":
            continue
        cx, cy = int(pos[0]), int(pos[1])
        rc = None
        cc = int((cx - x0) // cw)
        rr = int((cy - y0) // ch)
        if 0 <= rr < rows and 0 <= cc < cols:
            rc = (rr, cc)
        if rc and state[rc[0]][rc[1]] == OPEN and rc not in numbers:
            numbers[rc] = int(t)

    board = MinesweeperBoard(rows, cols, state, numbers, x0, y0, cw, ch,
                             mines_total)
    # 至少要有一定规模的棋盘，避免把普通表格误认成扫雷
    unknown_cells = board.covered_count() + board.flagged_count()
    if unknown_cells < max(8, board.rows * board.cols * 0.02):
        return None
    if board.open_count() + unknown_cells < 60:
        return None
    return board


def parse_mine_count(ocr_items: Iterable[Tuple[str, Tuple[int, int], float]],
                     screen_size: Tuple[int, int]) -> Optional[int]:
    """从右上角雷数计数器（如“031”）读取总雷数（OCR 备用方案）"""
    sw, sh = screen_size
    best: Optional[int] = None
    best_y = 10 ** 9
    for text, pos, _conf in ocr_items or []:
        x, y = int(pos[0]), int(pos[1])
        if y > sh * 0.25 or x < sw * 0.5:
            continue
        for token in re.findall(r"\d{1,3}", str(text)):
            try:
                value = int(token)
            except ValueError:
                continue
            if 1 <= value <= 999 and y < best_y:
                best, best_y = value, y
    return best


# 七段数码管：a上 b右上 c右下 d下 e左下 f左上 g中
_SEG_PATTERNS: Dict[int, Tuple[int, int, int, int, int, int, int]] = {
    0: (1, 1, 1, 1, 1, 1, 0),
    1: (0, 1, 1, 0, 0, 0, 0),
    2: (1, 1, 0, 1, 1, 0, 1),
    3: (1, 1, 1, 1, 0, 0, 1),
    4: (0, 1, 1, 0, 0, 1, 1),
    5: (1, 0, 1, 1, 0, 1, 1),
    6: (1, 0, 1, 1, 1, 1, 1),
    7: (1, 1, 1, 0, 0, 0, 0),
    8: (1, 1, 1, 1, 1, 1, 1),
    9: (1, 1, 1, 1, 0, 1, 1),
}


def _read_seven_seg(cell: np.ndarray) -> Optional[int]:
    """单个七段数码管的亮灭模式 → 数字"""
    h, w = cell.shape
    if h < 4 or w < 3:
        return None

    def lit(y0: float, y1: float, x0: float, x1: float) -> int:
        sub = cell[int(y0 * h):max(int(y0 * h) + 1, int(y1 * h)),
                   int(x0 * w):max(int(x0 * w) + 1, int(x1 * w))]
        return 1 if sub.size and float(sub.mean()) > 0.25 else 0

    bits = (
        lit(0.00, 0.16, 0.18, 0.82),   # a
        lit(0.10, 0.40, 0.78, 1.00),   # b
        lit(0.60, 0.90, 0.78, 1.00),   # c
        lit(0.84, 1.00, 0.18, 0.82),   # d
        lit(0.60, 0.90, 0.00, 0.22),   # e
        lit(0.10, 0.40, 0.00, 0.22),   # f
        lit(0.42, 0.58, 0.18, 0.82),   # g
    )
    best: Optional[Tuple[int, int]] = None
    for digit, pat in _SEG_PATTERNS.items():
        diff = sum(1 for x, y in zip(bits, pat) if x != y)
        if best is None or diff < best[0]:
            best = (diff, digit)
    if best is None or best[0] > 1:
        return None
    return best[1]


def read_mine_counter(png: bytes) -> Optional[int]:
    """
    读取右上角七段码显示的雷数（如 031 → 31，全部插完旗时是 0）。

    直接对红色发光段做分段判定，比 OCR 更稳（OCR 会把 031 读成 60）。
    识别失败返回 None。
    """
    a = _load_rgb(png)
    if a is None:
        return None
    h, w = a.shape[:2]
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    red = (r > 120) & (g < 90) & (b < 90)
    # 跳过最顶部（Unity 的性能条也是红色，横贯整屏会污染列分组）
    y_off = int(h * 0.04)
    top = red[y_off: int(h * 0.25)]

    cols = [int(x) for x in np.where(top.any(axis=0))[0] if int(x) > w * 0.5]
    if not cols:
        return None

    digits: List[Tuple[int, int, int, int]] = []
    for x0, x1 in _groups(cols, gap=10):
        if x1 - x0 < 5:
            continue
        rows = np.where(top[:, x0:x1 + 1].any(axis=1))[0]
        if rows.size == 0:
            continue
        y0, y1 = int(rows[0]) + y_off, int(rows[-1]) + y_off
        # 过矮的区域是装饰线/进度条（或棋盘上的红数字），不是数码管
        if y1 - y0 < int(h * 0.025):
            continue
        digits.append((x0, x1, y0, y1))
    if not 1 <= len(digits) <= 4:
        return None

    # 相邻数字可能连成一块（如「018」里的 1 和 8 只隔几像素），
    # 所以不按连通块切分，而是按整体宽度均分成固定字位，
    # 再把每个字位单独送给七段解码（字位里的 1 靠右，也能认出来）。
    x_min = min(d[0] for d in digits)
    x_max = max(d[1] for d in digits)
    # 用最高的一位数作为字位高度（「1」通常比「0/8」矮一点）
    y0 = min(d[2] for d in digits)
    y1 = max(d[3] for d in digits)
    dh = max(1, y1 - y0 + 1)
    span = max(1, x_max - x_min + 1)
    n = int(round(span / (dh * 0.55)))
    n = max(1, min(4, n))
    if span / n < dh * 0.2:
        return None
    value = 0
    for i in range(n):
        sx0 = x_min + int(round(i * span / n))
        sx1 = x_min + int(round((i + 1) * span / n))
        if sx1 - sx0 < 3:
            return None
        digit = _read_seven_seg(red[max(0, y0 - 2):y1 + 3, sx0:sx1 + 1])
        if digit is None:
            return None
        value = value * 10 + digit
    return value if 0 <= value <= 999 else None


# ============================================================
# 推理
# ============================================================

def _deduce(board: MinesweeperBoard
            ) -> Tuple[Optional[Tuple[int, int]], Set[Tuple[int, int]]]:
    """
    确定性推理：
    - 返回 (必定安全的格子, 已确定的雷集合)
    - 已插旗的格子直接视为已确定的雷
    """
    known_mines: Set[Tuple[int, int]] = set(board.flagged_cells())
    for _ in range(8):
        progress = False
        for (r, c), n in board.numbers.items():
            # 只用真正显示出来的数字（已翻开格）
            if board.state[r][c] != OPEN:
                continue
            unknown = [cell for cell in board.neighbors(r, c)
                       if board.state[cell[0]][cell[1]] == COVERED
                       and cell not in known_mines]
            if not unknown:
                continue
            mines = sum(1 for cell in board.neighbors(r, c) if cell in known_mines)
            remaining = n - mines
            # 只在等式严格成立时推理；矛盾（<0 或 >未开数）说明有误读，跳过
            if remaining == 0:
                return unknown[0], known_mines
            if remaining == len(unknown):
                new = set(unknown) - known_mines
                if new:
                    known_mines |= new
                    progress = True
        if not progress:
            break
    return None, known_mines


def deduce_mines(board: MinesweeperBoard) -> Set[Tuple[int, int]]:
    """已确定的雷：已插旗的 + 逻辑能严格推出的（用于自动插旗）"""
    _safe, mines = _deduce(board)
    return mines


def _component_probabilities(
        cells: List[Tuple[int, int]],
        constraints: List[Tuple[Set[Tuple[int, int]], int]],
) -> Optional[Tuple[Dict[Tuple[int, int], float], float, int, int]]:
    """
    枚举一个连通分量的所有合法布雷方案。
    返回 ({格: 雷概率}, 平均雷数, 最少雷数, 最多雷数)；
    组合过大或被截断时返回 None（此时概率不可信，交给调用方近似）。
    """
    cells = list(cells)
    if len(cells) > _MAX_COMPONENT_CELLS:
        return None
    index = {cell: i for i, cell in enumerate(cells)}
    cell_pos = {cell: i for i, cell in enumerate(cells)}
    cons = [([cell_pos[c] for c in cs], rem) for cs, rem in constraints]

    # 先约束多的格子
    degree = [0] * len(cells)
    for ids, _rem in cons:
        for i in ids:
            degree[i] += 1
    order = sorted(range(len(cells)), key=lambda i: -degree[i])
    pos_of = {i: p for p, i in enumerate(order)}
    cons_mapped = [([pos_of[i] for i in ids], rem) for ids, rem in cons]

    n = len(cells)
    assign = [-1] * n
    counts = [0] * n
    solutions = 0
    mine_sum = 0
    nodes = 0
    min_mines: List[Optional[int]] = [None]
    max_mines: List[Optional[int]] = [None]
    truncated = [False]

    def feasible(step: int) -> bool:
        for ids, rem in cons_mapped:
            assigned = 0
            unassigned = 0
            for i in ids:
                if i < step:
                    if assign[i] == 1:
                        assigned += 1
                else:
                    unassigned += 1
            if assigned > rem or assigned + unassigned < rem:
                return False
        return True

    def backtrack(step: int, mines_used: int) -> None:
        nonlocal solutions, mine_sum, nodes
        if solutions >= _MAX_ENUM_SOLUTIONS or nodes >= _MAX_ENUM_NODES:
            truncated[0] = True
            return
        nodes += 1
        if step == n:
            solutions += 1
            mine_sum += mines_used
            if min_mines[0] is None or mines_used < min_mines[0]:
                min_mines[0] = mines_used
            if max_mines[0] is None or mines_used > max_mines[0]:
                max_mines[0] = mines_used
            for i, value in enumerate(assign):
                if value == 1:
                    counts[order[i]] += 1
            return
        for value in (1, 0):
            assign[step] = value
            if feasible(step + 1):
                backtrack(step + 1, mines_used + value)
        assign[step] = -1

    backtrack(0, 0)
    if solutions == 0 or truncated[0]:
        return None
    probs = {cells[i]: counts[i] / solutions for i in range(n)}
    return probs, mine_sum / solutions, int(min_mines[0]), int(max_mines[0])


def choose_move(board: MinesweeperBoard,
                mines_total: Optional[int] = None,
                avoid: Optional[set] = None,
                rng: Optional[object] = None) -> Optional[dict]:
    """
    计算下一步：返回 {"row", "col", "x", "y", "prob", "reason"}。
    优先确定性安全格；没有时枚举边界约束，选风险最低的格子。
    """
    avoid = set(avoid or ())
    safe, known_mines = _deduce(board)

    def _result(cell, prob, reason):
        r, c = cell
        x, y = board.center(r, c)
        return {"row": r, "col": c, "x": x, "y": y,
                "prob": float(prob), "reason": reason}

    if safe is not None and safe not in avoid:
        return _result(safe, 0.0, "逻辑推理：该格必定安全")

    # 候选格：未翻开、不在排除列表、且不是已确认的雷
    covered = [cell for cell in board.covered_cells()
               if cell not in avoid and cell not in known_mines]
    if not covered:
        return None

    # 无任何已翻开数字 → 开局第一手，点棋盘中部
    if not board.numbers and board.open_count() == 0:
        cr, cc = board.rows / 2, board.cols / 2
        cell = min(covered, key=lambda rc: abs(rc[0] - cr) + abs(rc[1] - cc))
        p = (mines_total / max(1, len(covered))) if mines_total else 0.1
        return _result(cell, p, "开局：翻开棋盘中部")

    # 边界约束（只使用真正显示出来的数字）
    constraints: List[Tuple[Set[Tuple[int, int]], int]] = []
    for (r, c), n in board.numbers.items():
        if board.state[r][c] != OPEN:
            continue
        unknown = [cell for cell in board.neighbors(r, c)
                   if board.state[cell[0]][cell[1]] == COVERED
                   and cell not in known_mines]
        if not unknown:
            continue
        mines = sum(1 for cell in board.neighbors(r, c) if cell in known_mines)
        remaining = n - mines
        # 矛盾约束（雷数超过未开格数或为负）视为误读，直接跳过
        if 0 <= remaining <= len(unknown):
            constraints.append((set(unknown), remaining))

    probs: Dict[Tuple[int, int], float] = {}
    expected_in_components = 0.0
    # 各分量能容纳的雷数区间（用于判断“剩余格子是否必然安全”）
    comp_min = 0
    comp_max = 0
    component_cells: Set[Tuple[int, int]] = set()
    if constraints:
        # 按共享格子分组
        parent: Dict[Tuple[int, int], Tuple[int, int]] = {}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for cs, _rem in constraints:
            for cell in cs:
                parent.setdefault(cell, cell)
            first = next(iter(cs))
            for cell in cs:
                union(first, cell)

        comps: Dict[Tuple[int, int], dict] = {}
        for cs, rem in constraints:
            root = find(next(iter(cs)))
            comp = comps.setdefault(root, {"cells": set(), "cons": []})
            comp["cells"] |= cs
            comp["cons"].append((cs, rem))

        for comp in comps.values():
            result = _component_probabilities(list(comp["cells"]), comp["cons"])
            if result is None:
                # 组合太大/被截断：用局部比例近似，并用区间做保守估计
                for cs, rem in comp["cons"]:
                    ratio = rem / max(1, len(cs))
                    for cell in cs:
                        probs[cell] = max(probs.get(cell, 0.0), ratio)
                expected_in_components += sum(
                    rem for _cs, rem in comp["cons"]) / max(1, len(comp["cons"]))
                comp_min += max((rem for _cs, rem in comp["cons"]), default=0)
                comp_max += min(len(comp["cells"]),
                                sum(rem for _cs, rem in comp["cons"]))
            else:
                cp, mean_mines, lo, hi = result
                probs.update(cp)
                expected_in_components += mean_mines
                comp_min += lo
                comp_max += hi
            component_cells |= comp["cells"]

    # 未进入任何约束的格子按全局剩余雷密度估计
    total = mines_total
    if total is None:
        total = max(1, int(round(board.rows * board.cols * 0.1)))
    unconstrained = [cell for cell in covered if cell not in component_cells]
    if unconstrained:
        left = total - len(known_mines)
        unc_n = len(unconstrained)
        if left - comp_min == 0:
            # 全部剩余雷都在约束分量内 → 其余格子必然安全
            global_p = 0.0
        elif left - comp_max >= unc_n:
            # 其余格子必然全是雷（不会去点）
            global_p = 1.0
        elif left - comp_max < 0 or left - comp_min > unc_n:
            # 与已知信息矛盾（可能有数字/雷数误读）：保守处理，不宣称安全
            global_p = 0.02
        else:
            estimate = left - expected_in_components
            global_p = min(0.98, max(0.02, estimate / unc_n))
        for cell in unconstrained:
            probs[cell] = global_p
    else:
        global_p = 1.0

    cell = min(covered, key=lambda rc: (probs.get(rc, global_p),
                                        rc[0], rc[1]))
    p = probs.get(cell, global_p)
    if p <= 1e-9:
        reason = "逻辑推理：该格必定安全"
    elif p >= 1.0 - 1e-9:
        reason = "剩余格子全是雷，无安全选择"
    elif cell in component_cells:
        reason = f"无必然解，按约束枚举选风险最低格（雷率 {p * 100:.1f}%）"
    else:
        reason = f"无数字线索，随机翻开（估计雷率 {p * 100:.1f}%）"
    return _result(cell, p, reason)


# ============================================================
# 兼容旧接口（Runner 旧版本调用时仍可用）
# ============================================================

def is_minesweeper_screen(ocr_items: Iterable[Tuple[str, int, int]]) -> bool:
    """旧接口：仅凭 OCR 是否出现 1-8 判断（保留兼容）"""
    for text, _x, _y in ocr_items or []:
        if re.search(r"[1-8]", str(text)):
            return True
    return False
