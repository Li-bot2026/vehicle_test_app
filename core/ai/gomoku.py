"""
内置五子棋决策引擎（离线，不依赖外部大模型）
==============================================
输入棋盘网格（0 空 / 1 黑 / 2 白），输出下一步落子坐标。

结构：
1. 立即成五 / 堵对手成五
2. 连续冲四（VCF）强杀搜索
3. 增量棋型评估 + 迭代加深 α-β 搜索（威胁优先排序）
4. 兜底：棋型打分 + 活三防守

评估按「线」维护：每次落子只更新经过该点的 4 条线，
所以搜索里每走一步的增量代价很小，能搜到 4~6 层。
"""

import time
from typing import List, Optional, Tuple

EMPTY, BLACK, WHITE = 0, 1, 2

_DIRS = ((0, 1), (1, 0), (1, 1), (1, -1))
_WIN = 10_000_000
_INF = 10 ** 9


def opponent(color: int) -> int:
    return BLACK if color == WHITE else WHITE


def _score_level(sc: int) -> int:
    """棋型分值 → 威胁等级：5 成五 / 4 活四 / 3 冲四 / 2 活三 / 1 弱"""
    if sc >= 5_000_000:
        return 5
    if sc >= 300_000:
        return 4
    if sc >= 40_000:
        return 3
    if sc >= 8_000:
        return 2
    if sc >= 300:
        return 1
    return 0


# ---------- 单线棋型评分 ----------

def _pat_score(s: str) -> int:
    """一条线（两端补 o 当边界）上最高的威胁分值"""
    if "xxxxx" in s:
        return 5_000_000            # 成五
    if ".xxxx." in s:
        return 300_000              # 活四
    if (".xxxx" in s or "xxxx." in s or "x.xxx." in s
            or ".xxx.x" in s or "xx.xx." in s or ".xx.xx" in s):
        return 40_000               # 冲四（含跳四）
    if ".xxx." in s or ".x.xx." in s or ".xx.x." in s:
        return 8_000                # 活三（含跳三）
    if ("xxx" in s or "x.xx" in s or "xx.x" in s
            or "x..xx" in s or "xx..x" in s):
        return 900                  # 眠三
    if ".xx." in s or ".x.x." in s or ".x..x." in s:
        return 300                  # 活二
    if "xx" in s or "x.x" in s:
        return 40
    if "x" in s:
        return 2
    return 0


class _Board:
    """扁平棋盘 + 按线维护的棋型分"""

    __slots__ = ("n", "n2", "g", "lines", "cell_lines", "line_score",
                 "score", "c1", "c2")

    def __init__(self, grid, n: int):
        self.n = n
        self.n2 = n * n
        self.g = [0] * (n * n)
        for r in range(n):
            row = grid[r]
            base = r * n
            for c in range(n):
                self.g[base + c] = int(row[c])

        self.lines = []
        self.cell_lines = [[] for _ in range(n * n)]
        self._build_lines()

        self.line_score = [(0, 0)] * len(self.lines)
        self.score = {BLACK: 0, WHITE: 0}
        self.c1 = [0] * (n * n)
        self.c2 = [0] * (n * n)

        for lid in range(len(self.lines)):
            self._update_line(lid)
        for idx in range(n * n):
            if self.g[idx]:
                self._bump(idx, +1)

    # ---- 预计算 ----

    def _build_lines(self):
        n = self.n

        def add(cells):
            lid = len(self.lines)
            self.lines.append(tuple(cells))
            for idx in cells:
                self.cell_lines[idx].append(lid)

        for r in range(n):
            add([r * n + c for c in range(n)])
        for c in range(n):
            add([r * n + c for r in range(n)])
        for s in range(2 * n - 1):
            cells = [(r, s - r) for r in range(n) if 0 <= s - r < n]
            if len(cells) >= 5:
                add([r * n + c for r, c in cells])
        for d in range(-(n - 1), n):
            cells = [(r, r - d) for r in range(n) if 0 <= r - d < n]
            if len(cells) >= 5:
                add([r * n + c for r, c in cells])

    def _update_line(self, lid: int):
        g = self.g
        s = "o" + "".join(
            "." if g[i] == 0 else ("b" if g[i] == 1 else "w")
            for i in self.lines[lid]
        ) + "o"
        nb = _pat_score(s.replace("b", "x").replace("w", "o"))
        nw = _pat_score(s.replace("w", "x").replace("b", "o"))
        ob, ow = self.line_score[lid]
        if nb != ob:
            self.score[BLACK] += nb - ob
        if nw != ow:
            self.score[WHITE] += nw - ow
        self.line_score[lid] = (nb, nw)

    # ---- 邻近计数（候选点生成） ----

    def _bump(self, idx: int, delta: int):
        n = self.n
        r, c = divmod(idx, n)
        c1, c2 = self.c1, self.c2
        for rr in range(max(0, r - 2), min(n, r + 3)):
            base = rr * n
            dr = abs(rr - r)
            for cc in range(max(0, c - 2), min(n, c + 3)):
                j = base + cc
                if j == idx:
                    continue
                c2[j] += delta
                if dr <= 1 and abs(cc - c) <= 1:
                    c1[j] += delta

    # ---- 落子 / 撤销 ----

    def place(self, idx: int, color: int):
        self.g[idx] = color
        for lid in self.cell_lines[idx]:
            self._update_line(lid)
        self._bump(idx, +1)

    def undo(self, idx: int):
        self.g[idx] = EMPTY
        self._bump(idx, -1)
        for lid in self.cell_lines[idx]:
            self._update_line(lid)

    # ---- 查询 ----

    def evaluate(self, color: int) -> int:
        opp = opponent(color)
        return self.score[color] - (self.score[opp] * 21) // 20

    def wins_with(self, idx: int, color: int) -> bool:
        n = self.n
        g = self.g
        r, c = divmod(idx, n)
        for dr, dc in _DIRS:
            cnt = 1
            rr, cc = r + dr, c + dc
            while 0 <= rr < n and 0 <= cc < n and g[rr * n + cc] == color:
                cnt += 1
                rr += dr
                cc += dc
            rr, cc = r - dr, c - dc
            while 0 <= rr < n and 0 <= cc < n and g[rr * n + cc] == color:
                cnt += 1
                rr -= dr
                cc -= dc
            if cnt >= 5:
                return True
        return False

    def winning_cells(self, color: int, limit: int = 2) -> List[int]:
        out = []
        g = self.g
        n2 = self.n2
        for idx in range(n2):
            if g[idx] == 0 and self.c1[idx] and self.wins_with(idx, color):
                out.append(idx)
                if len(out) >= limit:
                    break
        return out

    def winning_both(self, color: int, opp: int,
                     limit: int = 3) -> Tuple[List[int], List[int]]:
        """一次扫描同时找出双方成五点"""
        mine, theirs = [], []
        g = self.g
        c1 = self.c1
        for idx in range(self.n2):
            if g[idx] != 0 or not c1[idx]:
                continue
            if len(mine) < limit and self.wins_with(idx, color):
                mine.append(idx)
            if len(theirs) < limit and self.wins_with(idx, opp):
                theirs.append(idx)
        return mine, theirs

    def candidates(self, limit: int, radius: int = 2,
                   center_bias: bool = True) -> List[int]:
        """棋子附近按邻近密度排序的落点"""
        g = self.g
        counter = self.c1 if radius == 1 else self.c2
        n = self.n
        mid = (n - 1) / 2
        scored = []
        for idx in range(self.n2):
            if g[idx] != 0 or counter[idx] == 0:
                continue
            r, c = divmod(idx, n)
            s = counter[idx] * 8
            if center_bias:
                s += int(n - (abs(r - mid) + abs(c - mid)))
            scored.append((s, idx))
        scored.sort(reverse=True)
        return [idx for _, idx in scored[:limit]]

    def threat_level(self, idx: int, color: int) -> int:
        """假设 color 落在 idx，四个方向里最高的威胁等级（不改动棋型分）"""
        if self.g[idx] != 0:
            return 0
        self.g[idx] = color
        best = 0
        n = self.n
        r, c = divmod(idx, n)
        for dr, dc in _DIRS:
            chars = []
            for k in range(-4, 5):
                rr, cc = r + dr * k, c + dc * k
                if 0 <= rr < n and 0 <= cc < n:
                    v = self.g[rr * n + cc]
                    chars.append("." if v == 0 else
                                 ("x" if v == color else "o"))
                else:
                    chars.append("o")
            lvl = _score_level(_pat_score("".join(chars)))
            if lvl > best:
                best = lvl
                if best >= 5:
                    break
        self.g[idx] = 0
        return best


# ---------- VCF：连续冲四强杀 ----------

def _vcf(b: _Board, color: int, depth: int, deadline: float) -> Optional[int]:
    """我方连续冲四能否必胜；返回第一手（idx）或 None"""
    if time.time() > deadline:
        return None
    wins = b.winning_cells(color, limit=1)
    if wins:
        return wins[0]
    if depth <= 0:
        return None
    for idx in _four_moves(b, color, deadline):
        b.place(idx, color)
        ok = _vcf_reply(b, color, depth - 1, deadline)
        b.undo(idx)
        if ok:
            return idx
        if time.time() > deadline:
            return None
    return None


def _vcf_reply(b: _Board, color: int, depth: int, deadline: float) -> bool:
    """我方刚做出冲四，轮到对手；判断是否抵挡不住"""
    opp = opponent(color)
    if b.winning_cells(opp, limit=1):
        return False                       # 对手也有成五点，先手在对手
    mine = b.winning_cells(color)
    if len(mine) >= 2:
        return True                        # 双四，对手堵不完
    if not mine:
        return False
    block = mine[0]
    b.place(block, opp)                    # 对手唯一的应手
    res = _vcf(b, color, depth - 1, deadline) is not None
    b.undo(block)
    return res


def _four_moves(b: _Board, color: int, deadline: float) -> List[int]:
    """所有「落子后形成冲四」的点，按邻近密度排序"""
    out = []
    g = b.g
    for idx in range(b.n2):
        if g[idx] != 0 or not b.c1[idx]:
            continue
        if time.time() > deadline:
            break
        b.place(idx, color)
        four = bool(b.winning_cells(color, limit=1))
        b.undo(idx)
        if four:
            out.append(idx)
    return out


# ---------- 搜索 ----------

class _Timeout(Exception):
    pass


def _negamax(b: _Board, to_move: int, depth: int, alpha: int, beta: int,
             deadline: float) -> int:
    if time.time() > deadline:
        raise _Timeout
    opp = opponent(to_move)

    mine, theirs = b.winning_both(to_move, opp)
    if mine:
        # 该走的一方已有成五点（对手没堵住）→ 直接赢
        return _WIN + depth

    if depth <= 0:
        return b.evaluate(to_move)

    if theirs:
        moves = theirs                     # 必须堵，别的都是输
    else:
        limit = 8 if depth >= 3 else 6
        moves = b.candidates(limit, radius=2 if depth >= 3 else 1)

    best = -_INF
    for idx in moves:
        b.place(idx, to_move)
        if b.wins_with(idx, to_move):
            score = _WIN + depth
        else:
            score = -_negamax(b, opp, depth - 1, -beta, -alpha, deadline)
        b.undo(idx)
        if score > best:
            best = score
        if best > alpha:
            alpha = best
        if alpha >= beta:
            break
    return best


def _search_root(b: _Board, color: int, depth: int,
                 deadline: float) -> Tuple[Optional[int], int]:
    """根节点：候选点里带上所有强威胁点，按局面分排序后做 α-β"""
    opp = opponent(color)
    cands = _root_candidates(b, color)
    if not cands:
        return None, -_INF

    # 先按「走后局面分」排序，好让 α-β 尽早剪枝
    ranked = []
    for idx in cands:
        b.place(idx, color)
        v = b.evaluate(color)
        b.undo(idx)
        ranked.append((v, idx))
    ranked.sort(reverse=True)

    best_move, best_score = ranked[0][1], -_INF
    alpha, beta = -_INF, _INF
    for _, idx in ranked:
        b.place(idx, color)
        if b.wins_with(idx, color):
            b.undo(idx)
            return idx, _WIN + depth
        score = -_negamax(b, opponent(color), depth - 1, -beta, -alpha,
                          deadline)
        b.undo(idx)
        if score > best_score:
            best_score, best_move = score, idx
        if score > alpha:
            alpha = score
    return best_move, best_score


def _root_candidates(b: _Board, color: int, limit: int = 16) -> List[int]:
    """根候选：邻近密度前 10 名 + 双方活三级以上的威胁点"""
    opp = opponent(color)
    base = b.candidates(10, radius=2)
    chosen = list(base)
    seen = set(base)
    for idx in b.candidates(60, radius=2):
        if len(chosen) >= limit:
            break
        if idx in seen:
            continue
        if (b.threat_level(idx, color) >= 2
                or b.threat_level(idx, opp) >= 2):
            chosen.append(idx)
            seen.add(idx)
    return chosen


# ---------- 对外接口 ----------

def choose_move(grid: List[List[int]], color: int,
                n: Optional[int] = None,
                time_limit: float = 2.5,
                use_search: bool = True) -> Optional[Tuple[int, int]]:
    """计算下一步落子（行, 列）；无空位时返回 None"""
    n = n or len(grid)
    empties = [(r, c) for r in range(n) for c in range(n)
               if grid[r][c] == EMPTY]
    if not empties:
        return None

    b = _Board(grid, n)
    opp = opponent(color)

    def to_rc(idx):
        return divmod(idx, n)

    # 1) 自己能立刻成五
    wins = b.winning_cells(color, limit=1)
    if wins:
        return to_rc(wins[0])

    # 2) 对手能成五 → 堵
    blocks = b.winning_cells(opp)
    if blocks:
        return to_rc(blocks[0])

    # 3) 空盘 / 无邻近棋子：占天元
    if not any(b.g):
        mid = n // 2
        return mid, mid

    if not use_search:
        return to_rc(_heuristic_move(b, color))

    hard_deadline = time.time() + max(0.5, time_limit)

    # 4) 威胁扫描：谁能做成活四 / 冲四
    pool = b.candidates(60, radius=2)
    own_open4 = None
    opp_open4 = None
    for idx in pool:
        if own_open4 is None and b.threat_level(idx, color) >= 4:
            own_open4 = idx
        if opp_open4 is None and b.threat_level(idx, opp) >= 4:
            opp_open4 = idx
        if own_open4 is not None and opp_open4 is not None:
            break

    # 自己成活四 = 双杀，对手堵不完
    if own_open4 is not None:
        return to_rc(own_open4)

    # 5) VCF：连续冲四强杀
    vcf_budget = min(1.2, max(0.3, time_limit * 0.4))
    combo = _vcf(b, color, depth=10, deadline=time.time() + vcf_budget)
    if combo is not None:
        return to_rc(combo)

    # 6) 对手能成活四 → 必须占住
    if opp_open4 is not None:
        return to_rc(opp_open4)

    # 7) 对手能形成双四 → 必须占住（否则堵不完）
    for idx in pool:
        b.place(idx, opp)
        double = len(b.winning_cells(opp)) >= 2
        b.undo(idx)
        if double:
            return to_rc(idx)

    # 8) 迭代加深搜索
    best_move, best_score = None, -_INF
    try:
        for depth in (2, 4, 6):
            move, score = _search_root(b, color, depth, hard_deadline)
            if move is not None:
                best_move, best_score = move, score
            if score >= _WIN // 2 or time.time() > hard_deadline:
                break
    except _Timeout:
        pass

    if best_move is not None:
        return to_rc(best_move)

    return to_rc(_heuristic_move(b, color))


def _heuristic_move(b: _Board, color: int) -> int:
    """兜底：按邻近密度 + 攻防棋型选点，并避开让对手立即成五的点"""
    opp = opponent(color)
    cands = b.candidates(20, radius=2)
    if not cands:
        full = [i for i in range(b.n2) if b.g[i] == 0]
        return full[0] if full else 0

    best, best_score = cands[0], -_INF
    for idx in cands:
        b.place(idx, color)
        danger = bool(b.winning_cells(opp, limit=1))
        sc = b.score[color] - b.score[opp]
        b.undo(idx)
        if danger:
            sc -= 4_000_000
        if sc > best_score:
            best, best_score = idx, sc
    return best
