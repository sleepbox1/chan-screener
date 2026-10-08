# -*- coding: utf-8 -*-
"""
缠论技术分析引擎
=================

实现链路（自下而上）：

    K线  ->  包含处理  ->  分型  ->  笔  ->  中枢  ->  买点(1/2/3类)
                                        \\->  MACD 背驰判断

作者的工程取舍（重要，请先读）：

1. 本引擎不做"线段"划分。笔 -> 中枢 直接衔接，这是多数量化落地实现的做法。
   原因：线段划分的递归特征序列极其依赖实现细节，不同人结果不同，且对
   2/3 类买点的判定影响很小（2/3 买只依赖"笔的离开 + 笔的回抽"）。

2. 买点为"已确认"信号，不是预测。每个买点都锚定在某一笔的终点分型上，
   该分型需要右侧至少一根 K 线才能成立，因此天然滞后 1 根 K 线。
   这对盘后选股是合适的 —— 我们要的是"今天收盘时刚确认 / 仍在有效期内"的买点。

3. 输出统一为 `ChanSignal`，带新鲜度（距离最新 K 线多少根）和破位检查，
   便于上层做时效过滤。

单位约定：所有价格均为前复权价，与 AkShare 的 adjust="qfq" 一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

__all__ = [
    "RawBar",
    "MergedBar",
    "Fractal",
    "Bi",
    "Zhongshu",
    "BuyPoint",
    "ChanResult",
    "analyze",
    "macd",
    "validate_buy",
    "pick_valid_buy",
]


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class RawBar:
    """一根原始 K 线。idx 为其在原始序列中的下标。"""

    idx: int
    dt: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class MergedBar:
    """包含处理后的合并 K 线。"""

    high: float
    low: float
    start_idx: int          # 覆盖的原始 K 线区间起点
    end_idx: int            # 覆盖的原始 K 线区间终点
    high_idx: int           # 最高价所在的原始 K 线下标
    low_idx: int            # 最低价所在的原始 K 线下标
    dt: str = ""
    direction: int = 0      # 相对前一根合并 K 线的走向：1 向上 / -1 向下


@dataclass
class Fractal:
    """分型。idx 为合并 K 线序列中的下标。"""

    idx: int
    kind: str               # 'top' 顶分型 / 'bottom' 底分型
    price: float
    dt: str
    raw_idx: int            # 对应原始 K 线下标（顶分型取 high_idx，底分型取 low_idx）

    @property
    def is_top(self) -> bool:
        return self.kind == "top"


@dataclass
class Bi:
    """笔。由一顶一底两个分型构成。"""

    seq: int                # 笔在序列中的序号
    start_idx: int          # 起始分型在合并序列中的下标
    end_idx: int            # 终止分型在合并序列中的下标
    start_raw: int          # 起始分型对应原始 K 线下标
    end_raw: int            # 终止分型对应原始 K 线下标
    start_dt: str
    end_dt: str
    start_price: float
    end_price: float
    direction: int          # 1 向上笔 / -1 向下笔

    @property
    def high(self) -> float:
        return max(self.start_price, self.end_price)

    @property
    def low(self) -> float:
        return min(self.start_price, self.end_price)

    @property
    def bar_count(self) -> int:
        return self.end_idx - self.start_idx


@dataclass
class Zhongshu:
    """中枢。zg 上沿 / zd 下沿，由连续三笔的价格重叠区间决定。"""

    zg: float
    zd: float
    start_bi: int
    end_bi: int
    start_dt: str
    end_dt: str
    bi_count: int

    @property
    def mid(self) -> float:
        return (self.zg + self.zd) / 2.0


@dataclass
class BuyPoint:
    """买点。

    kind     : '1' / '2' / '3'
    price    : 买点价位（该笔终点的价格）
    raw_idx  : 买点对应的原始 K 线下标
    dt       : 买点确认时间（该原始 K 线的时间）
    bars_ago : 距离最后一根 K 线的根数
    """

    kind: str
    price: float
    raw_idx: int
    dt: str
    bars_ago: int
    bi_seq: int
    zhongshu: Optional[Zhongshu] = None
    detail: str = ""

    @property
    def label(self) -> str:
        return f"{self.kind}买"


@dataclass
class ChanResult:
    """单个标的、单个级别的完整分析结果。"""

    level: str                              # '30' / '5'
    bar_count: int
    merged_count: int
    bi_count: int
    zhongshu_count: int
    zhongshu: Optional[Zhongshu] = None     # 最新中枢
    buy: Optional[BuyPoint] = None          # 最新有效买点
    all_buys: List[BuyPoint] = field(default_factory=list)
    last_close: float = 0.0
    last_dt: str = ""
    ok: bool = True
    reason: str = ""

    def to_dict(self) -> dict:
        d = {
            "level": self.level,
            "ok": self.ok,
            "reason": self.reason,
            "bars": self.bar_count,
            "bi_count": self.bi_count,
            "zs_count": self.zhongshu_count,
            "last_close": round(self.last_close, 3),
            "last_dt": self.last_dt,
        }
        if self.zhongshu:
            d["zs"] = {
                "zg": round(self.zhongshu.zg, 3),
                "zd": round(self.zhongshu.zd, 3),
                "start": self.zhongshu.start_dt,
                "end": self.zhongshu.end_dt,
                "bi_count": self.zhongshu.bi_count,
            }
        if self.buy:
            d["buy"] = {
                "kind": self.buy.kind,
                "label": self.buy.label,
                "price": round(self.buy.price, 3),
                "dt": self.buy.dt,
                "bars_ago": self.buy.bars_ago,
                "detail": self.buy.detail,
            }
        return d


# --------------------------------------------------------------------------- #
# 工具：MACD
# --------------------------------------------------------------------------- #
def _ema(values: Sequence[float], period: int) -> List[float]:
    """指数移动平均。首值用第一个数据点初始化，保证长度与输入一致。"""
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(out[-1] + k * (float(v) - out[-1]))
    return out


def macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[List[float], List[float], List[float]]:
    """返回 (DIF, DEA, MACD柱)。MACD 柱按国内习惯乘 2。"""
    if len(closes) < 2:
        n = len(closes)
        z = [0.0] * n
        return z, z, z
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = _ema(dif, signal)
    hist = [2.0 * (a - b) for a, b in zip(dif, dea)]
    return dif, dea, hist


def _hist_area(hist: Sequence[float], start: int, end: int) -> float:
    """区间内同向动能面积（取绝对值求和）。用于比较背驰。"""
    if start > end:
        start, end = end, start
    lo = max(0, start)
    hi = min(len(hist) - 1, end)
    if hi < lo:
        return 0.0
    return float(sum(abs(x) for x in hist[lo : hi + 1]))


# --------------------------------------------------------------------------- #
# 第一步：K 线包含处理
# --------------------------------------------------------------------------- #
def merge_bars(bars: Sequence[RawBar]) -> List[MergedBar]:
    """标准包含处理：向上处理取高高，向下处理取低低。"""
    merged: List[MergedBar] = []
    for b in bars:
        if not merged:
            merged.append(
                MergedBar(
                    high=b.high,
                    low=b.low,
                    start_idx=b.idx,
                    end_idx=b.idx,
                    high_idx=b.idx,
                    low_idx=b.idx,
                    dt=b.dt,
                )
            )
            continue

        last = merged[-1]
        contains = (last.high >= b.high and last.low <= b.low) or (
            last.high <= b.high and last.low >= b.low
        )

        if not contains:
            direction = 1 if b.high > last.high else -1
            merged.append(
                MergedBar(
                    high=b.high,
                    low=b.low,
                    start_idx=b.idx,
                    end_idx=b.idx,
                    high_idx=b.idx,
                    low_idx=b.idx,
                    dt=b.dt,
                    direction=direction,
                )
            )
            continue

        # 处理包含：方向由前两根已合并 K 线决定；只有一根时退化为与当前 K 线比大小
        if len(merged) >= 2:
            direction = 1 if merged[-1].high > merged[-2].high else -1
        else:
            direction = 1 if b.high > last.high else -1

        if direction == 1:
            new_high = max(last.high, b.high)
            new_low = max(last.low, b.low)
        else:
            new_high = min(last.high, b.high)
            new_low = min(last.low, b.low)

        if b.high >= new_high:
            last.high_idx = b.idx
        if b.low <= new_low:
            last.low_idx = b.idx

        last.high = new_high
        last.low = new_low
        last.end_idx = b.idx
        last.dt = b.dt
        last.direction = direction

    # 补齐方向字段
    for i in range(1, len(merged)):
        if merged[i].direction == 0:
            merged[i].direction = 1 if merged[i].high > merged[i - 1].high else -1
    return merged


# --------------------------------------------------------------------------- #
# 第二步：分型
# --------------------------------------------------------------------------- #
def find_fractals(merged: Sequence[MergedBar]) -> List[Fractal]:
    """三根相邻合并 K 线：中间最高为顶分型，中间最低为底分型。"""
    out: List[Fractal] = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b.high > a.high and b.high > c.high:
            out.append(Fractal(i, "top", b.high, b.dt, b.high_idx))
        elif b.low < a.low and b.low < c.low:
            out.append(Fractal(i, "bottom", b.low, b.dt, b.low_idx))
    return out


# --------------------------------------------------------------------------- #
# 第三步：笔
# --------------------------------------------------------------------------- #
def build_bis(
    merged: Sequence[MergedBar], fracs: Sequence[Fractal], min_gap: int = 4
) -> Tuple[List[Bi], List[Fractal]]:
    """构建笔。

    min_gap：相邻两个分型在合并序列中的最小下标差。
    min_gap=4 表示两个分型之间至少夹着一根独立 K 线（"新笔"标准，也兼容老笔）。
    """
    seq: List[Fractal] = []
    for f in fracs:
        if not seq:
            seq.append(f)
            continue
        last = seq[-1]
        if f.kind == last.kind:
            # 同向分型：保留更极端的那个
            better = (f.kind == "top" and f.price > last.price) or (
                f.kind == "bottom" and f.price < last.price
            )
            if better:
                seq[-1] = f
        else:
            if f.idx - last.idx >= min_gap:
                seq.append(f)
            # 间隔不足则丢弃该分型（不构成独立的一笔）

    bis: List[Bi] = []
    for k in range(len(seq) - 1):
        s, e = seq[k], seq[k + 1]
        if s.kind == e.kind:
            continue
        bis.append(
            Bi(
                seq=len(bis),
                start_idx=s.idx,
                end_idx=e.idx,
                start_raw=s.raw_idx,
                end_raw=e.raw_idx,
                start_dt=s.dt,
                end_dt=e.dt,
                start_price=s.price,
                end_price=e.price,
                direction=1 if e.kind == "top" else -1,
            )
        )
    return bis, seq


# --------------------------------------------------------------------------- #
# 第四步：中枢
# --------------------------------------------------------------------------- #
def find_zhongshus(bis: Sequence[Bi], min_bi: int = 3) -> List[Zhongshu]:
    """连续 min_bi 笔的重叠区间构成中枢；中枢的 ZG / ZD 一经确定便不再改变。

    中枢延伸到哪一笔结束？这里有两处容易踩坑的地方，说明如下：

    1. 不能用"笔的价格区间与中枢区间有重叠"来判断延伸。
       否则一根从中枢下沿直接拉到中枢上方的突破笔（低点仍在中枢内）会被
       当成延伸吃掉，中枢的"离开笔"就会错位到下一根反向笔上，
       导致三类买点永远识别不出来。

    2. 正确判据是看**笔的终点**：
         * 终点仍落在 [ZD, ZG] 内      -> 中枢延伸；
         * 终点跑到 [ZD, ZG] 之外      -> 候选离开笔。此时再看下一笔，
           若下一笔的终点又回到 [ZD, ZG] 内，说明只是假突破（被拉回），
           中枢继续延伸；否则本笔即为真正的离开笔。

    中枢区间起点为第 min_bi 笔处，end_bi 指向最后一笔仍有重叠的笔，
    因此 end_bi + 1 恒为中轴的离开笔、end_bi + 2 恒为其后的回抽笔。
    """
    zs_list: List[Zhongshu] = []
    i = 0
    n = len(bis)
    while i + min_bi <= n:
        group = bis[i : i + min_bi]
        zg = min(b.high for b in group)
        zd = max(b.low for b in group)
        if zg <= zd:
            i += 1
            continue

        j = i + min_bi
        while j < n:
            bj = bis[j]
            if zd <= bj.end_price <= zg:
                j += 1                      # 终点仍在中枢内 -> 延伸
                continue
            # 终点跑出中枢：看下一笔是否被拉回
            if j + 1 < n and zd < bis[j + 1].end_price < zg:
                j += 2                      # 假突破，被拉回，继续延伸
                continue
            break                           # 真离开，j 即离开笔

        zs_list.append(
            Zhongshu(
                zg=zg,
                zd=zd,
                start_bi=i,
                end_bi=j - 1,
                start_dt=bis[i].start_dt,
                end_dt=bis[j - 1].end_dt,
                bi_count=j - i,
            )
        )
        i = max(j, i + 1)  # 下一段从中枢的离开笔重新起算
    return zs_list


# --------------------------------------------------------------------------- #
# 第五步：买点
# --------------------------------------------------------------------------- #
def find_buys(
    bis: Sequence[Bi],
    zs_list: Sequence[Zhongshu],
    hist: Sequence[float],
    bars_count: int,
    beichi_ratio: float = 0.85,
    require_beichi: bool = False,
) -> List[BuyPoint]:
    """识别一 / 二 / 三类买点。

    ---- 三类买点 ----
    中枢形成后，一笔向上离开（终点 > ZG），紧接的回调笔不跌回中枢（终点 > ZG）。
    该回调笔的终点即三买。

    ---- 一类买点 ----
    一笔向下离开中枢（终点 < ZD），且相对"前一个同向向下笔"出现 MACD 背驰
    （绿柱面积明显缩小）。该向下笔的终点即一买。

    ---- 二类买点 ----
    一买之后的反弹笔完成，随后的回调笔终点不破一买低点，该回调笔终点即二买。

    require_beichi 控制二买的宽严：
      * False（默认）—— 宽松。若某笔向下离开中枢创出阶段新低（终点 < 中枢 ZD），
        之后反弹 + 回调不破前低，同样认可为二买。命中更多，适合盘后铺开观察。
      * True —— 严格。必须由 MACD 背驰确认的一买所引出，才认二买。信号更少更精。
    """
    out: List[BuyPoint] = []
    n = len(bis)

    def mk(kind: str, bi: Bi, zs: Optional[Zhongshu], detail: str) -> BuyPoint:
        return BuyPoint(
            kind=kind,
            price=bi.end_price,
            raw_idx=bi.end_raw,
            dt=bi.end_dt,
            bars_ago=max(0, bars_count - 1 - bi.end_raw),
            bi_seq=bi.seq,
            zhongshu=zs,
            detail=detail,
        )

    # ---------------- 三类买点 ----------------
    for zs in zs_list:
        leave_i = zs.end_bi + 1          # 离开中枢的笔
        pull_i = leave_i + 1             # 回调笔
        if pull_i >= n:
            continue
        leave, pull = bis[leave_i], bis[pull_i]
        if leave.direction != 1 or pull.direction != -1:
            continue
        if leave.end_price > zs.zg and pull.end_price > zs.zg:
            out.append(
                mk(
                    "3",
                    pull,
                    zs,
                    f"向上离开中枢上沿 {zs.zg:.2f}，回调至 {pull.end_price:.2f} 未回中枢",
                )
            )

    # ---------------- 一类 / 二类买点 ----------------
    for zs in zs_list:
        leave_i = zs.end_bi + 1
        if leave_i >= n:
            continue
        leave = bis[leave_i]
        if leave.direction != -1 or leave.end_price >= zs.zd:
            continue  # 不是向下离开中枢

        # 背驰：与之前最近的同向（向下）笔比较动能面积
        prev_down = None
        for k in range(leave_i - 1, -1, -1):
            if bis[k].direction == -1:
                prev_down = bis[k]
                break

        beichi = False
        ratio_txt = ""
        if prev_down is not None:
            a_prev = _hist_area(hist, prev_down.start_raw, prev_down.end_raw)
            a_now = _hist_area(hist, leave.start_raw, leave.end_raw)
            if a_prev > 0:
                ratio = a_now / a_prev
                beichi = ratio < beichi_ratio
                if beichi:
                    ratio_txt = f"，动能比 {ratio:.2f}（背驰）"

        # 严格模式下，没有背驰确认就不认这个二买
        if require_beichi and not beichi:
            continue

        # 二买：寻找"上涨一笔 + 回调一笔"，回调不破前低
        for k in range(leave_i + 1, n - 1):
            up, pb = bis[k], bis[k + 1]
            if up.direction != 1 or pb.direction != -1:
                continue
            if pb.end_price > leave.end_price:
                tag = "背驰一买" if beichi else "阶段低点"
                out.append(
                    mk(
                        "2",
                        pb,
                        zs,
                        f"{tag} {leave.end_price:.2f} 后回升，回调至 {pb.end_price:.2f} 未破前低{ratio_txt}",
                    )
                )
                break  # 每个中枢只取最近一次成立

        # 一买本身也记录，供上层参考
        if beichi and leave.end_price < zs.zd:
            out.append(
                mk("1", leave, zs, f"向下离开中枢下沿 {zs.zd:.2f} 且出现背驰{ratio_txt}")
            )

    # 按确认时间排序，去掉同一原始 K 线上的重复信号（保留等级更高者）
    out.sort(key=lambda b: (b.raw_idx, b.kind))
    dedup: List[BuyPoint] = []
    for b in out:
        if dedup and dedup[-1].raw_idx == b.raw_idx:
            if b.kind < dedup[-1].kind:
                dedup[-1] = b
            continue
        dedup.append(b)
    return dedup


# --------------------------------------------------------------------------- #
# 对外主入口
# --------------------------------------------------------------------------- #
def analyze(
    bars: Sequence[RawBar],
    level: str,
    min_gap: int = 4,
    beichi_ratio: float = 0.85,
    require_beichi: bool = False,
) -> ChanResult:
    """对一段 K 线做完整缠论分析，返回最新有效买点 + 最新中枢。"""
    res = ChanResult(
        level=level,
        bar_count=len(bars),
        merged_count=0,
        bi_count=0,
        zhongshu_count=0,
        last_close=bars[-1].close if bars else 0.0,
        last_dt=bars[-1].dt if bars else "",
    )

    if len(bars) < 30:
        res.ok = False
        res.reason = f"K线不足({len(bars)})"
        return res

    merged = merge_bars(bars)
    fracs = find_fractals(merged)
    bis, _ = build_bis(merged, fracs, min_gap=min_gap)
    res.merged_count = len(merged)

    if len(bis) < 5:
        res.ok = False
        res.reason = f"笔数不足({len(bis)})"
        return res

    zs_list = find_zhongshus(bis, min_bi=3)
    res.bi_count = len(bis)
    res.zhongshu_count = len(zs_list)
    res.zhongshu = zs_list[-1] if zs_list else None

    closes = [b.close for b in bars]
    _, _, hist = macd(closes)

    buys = find_buys(
        bis, zs_list, hist, len(bars), beichi_ratio=beichi_ratio, require_beichi=require_beichi
    )
    res.all_buys = buys
    res.buy = buys[-1] if buys else None
    if res.buy is None:
        res.reason = "无有效买点"
    return res


def validate_buy(
    res: ChanResult,
    last_close: float,
    max_bars_ago: int,
    break_tolerance: float = 0.005,
) -> Tuple[bool, str]:
    """买点时效性校验（针对最新买点）。

    条件：
      1. 存在买点，且其等级在允许集合内；
      2. 买点确认时间距离最新 K 线不超过 max_bars_ago 根；
      3. 最新价未有效跌破买点价位（允许 break_tolerance 的容差）。
    """
    if not res.ok or res.buy is None:
        return False, res.reason or "无买点"

    buy = res.buy
    if buy.bars_ago > max_bars_ago:
        return False, f"买点过期({buy.bars_ago}根前)"
    if last_close < buy.price * (1.0 - break_tolerance):
        return False, f"已跌破买点({last_close:.2f}<{buy.price:.2f})"
    return True, ""


def pick_valid_buy(
    res: ChanResult,
    kinds: Sequence[str],
    last_close: float,
    max_bars_ago: int,
    break_tolerance: float = 0.005,
) -> Tuple[Optional[BuyPoint], str]:
    """从全部已识别买点中挑出「最新且仍然有效」的那一个。

    比只看整体最新买点更稳：例如最新一笔刚构成"一买"，而它前面两根
    K 线上刚确认的"三买"仍然有效，这里就会正确地把三买选出来。
    """
    if not res.ok:
        return None, res.reason or "分析失败"
    if not res.all_buys:
        return None, res.reason or "无买点"

    expired = 0
    broken = 0
    for b in reversed(res.all_buys):
        if b.kind not in kinds:
            continue
        if b.bars_ago > max_bars_ago:
            expired += 1
            continue
        if last_close < b.price * (1.0 - break_tolerance):
            broken += 1
            continue
        return b, ""

    if expired:
        return None, f"买点过期({expired}个)"
    if broken:
        return None, f"已跌破买点({broken}个)"
    return None, "无符合类型的买点"
