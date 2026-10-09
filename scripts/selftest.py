# -*- coding: utf-8 -*-
"""
缠论引擎自检
============

用人工构造的 K 线序列验证引擎能否正确完成
包含处理 -> 分型 -> 笔 -> 中枢 -> 买点 的识别，并覆盖正例与反例。

用例清单（严格口径为默认）：
    1. 中枢 -> 向上突破并创中枢新高 -> 回调不回中枢          => 3买
    2. 破位 -> 反弹 -> 回调不破前低（宽松模式）              => 2买
    3. 中枢 -> 向下破位但动能衰竭（真背驰）-> 反弹 -> 回调    => 2买（严格模式）
    4. 中枢 -> 向上笔只小破上沿但未创中枢新高 -> 回调不回中枢  => 不应识别 3买（反例）
    5. 三买成立后价格跌回中枢                                => 该买点应判定失效（反例）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chan  # noqa: E402

VOL = 0.06          # 每根 K 线的上下影幅度
STRICT = dict(require_beichi=True, min_zs_span_pct=0.006, zs_break_tol=0.003)


def bars_from_points(points, bars_per_leg=6, vol=VOL):
    """按"转折点序列"生成 K 线，每一段用 bars_per_leg 根 K 线走完。"""
    prices = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        for k in range(bars_per_leg):
            prices.append(a + (b - a) * (k + 1) / bars_per_leg)
    out = []
    for i, p in enumerate(prices):
        out.append(
            chan.RawBar(
                idx=i,
                dt=f"2026-01-{(i // 8) + 1:02d} {10 + i % 8:02d}:00",
                open=p,
                high=p + vol,
                low=p - vol,
                close=p,
            )
        )
    return out


def bars_from_path(path, vol=VOL):
    """把一条价格路径转成 K 线（每根一个点）。"""
    out = []
    for i, p in enumerate(path):
        out.append(
            chan.RawBar(
                idx=i,
                dt=f"2026-01-{(i // 8) + 1:02d} {10 + i % 8:02d}:00",
                open=p,
                high=p + vol,
                low=p - vol,
                close=p,
            )
        )
    return out


def zigzag(lo, hi, up_legs, dn_legs, rounds):
    """生成一段 zigzag 震荡路径。"""
    p = []
    for _ in range(rounds):
        p += [lo + (hi - lo) * k / up_legs for k in range(1, up_legs + 1)]
        p += [hi - (hi - lo) * k / dn_legs for k in range(1, dn_legs + 1)]
    return p


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def case_third_buy():
    """1. 中枢 -> 向上突破创新高 -> 回调不回中枢 => 3买"""
    path = []
    path += [10.0 + 2.0 * k / 15 for k in range(1, 16)]        # 上涨 10 -> 12
    path += zigzag(11.60, 12.40, 4, 4, 3)                      # 中枢震荡 [11.6, 12.4]
    path += [12.40 + 1.4 * k / 10 for k in range(1, 11)]       # 突破到 13.8（创中枢新高）
    path += [13.80 - 0.9 * k / 8 for k in range(1, 9)]         # 回调到 12.9 (> ZG)
    path += [12.90 + 0.6 * k / 6 for k in range(1, 7)]         # 再度上行
    return bars_from_path(path)


def case_second_buy_loose():
    """2. 破位 -> 反弹 -> 回调不破前低（宽松模式，不要求背驰）"""
    path = []
    path += [20.0 - 3.0 * k / 15 for k in range(1, 16)]
    path += zigzag(16.60, 17.40, 4, 4, 3)
    path += [17.40 - 2.2 * k / 12 for k in range(1, 13)]
    path += [15.20 + 1.0 * k / 8 for k in range(1, 9)]
    path += [16.20 - 0.5 * k / 6 for k in range(1, 7)]
    path += [15.70 + 0.8 * k / 6 for k in range(1, 7)]
    return bars_from_path(path)


def case_second_buy_strict():
    """3. 单元级验证背驰判据（不走 K 线构造，直接喂 bis/中枢/hist）。

    构造：中枢 [16.6, 17.4] -> 向下离开（低点 15.6）
      * 中枢内那根向下笔的 MACD 面积大（下跌快）
      * 破位笔的 MACD 面积小（下跌缓）
    期望：严格模式下识别出 一买 + 二买；把两段面积对调后则不应识别。
    """
    return None


def _unit_bis_and_zs():
    """构造用于背驰单元测试的笔序列与中枢。"""
    def bi(seq, sp, ep, sraw, eraw, direction):
        return chan.Bi(
            seq=seq, start_idx=seq * 3, end_idx=seq * 3 + 2,
            start_raw=sraw, end_raw=eraw,
            start_dt=f"2026-01-{seq+1:02d} 10:00", end_dt=f"2026-01-{seq+1:02d} 14:00",
            start_price=sp, end_price=ep, direction=direction,
        )

    # 笔0 上涨进入中枢；笔1 中枢内下跌；笔2 中枢内上涨；笔3 向下离开；笔4 反弹；笔5 回调
    bis = [
        bi(0, 15.9, 17.4, 0, 10, 1),
        bi(1, 17.4, 16.6, 10, 20, -1),
        bi(2, 16.6, 17.3, 20, 30, 1),
        bi(3, 17.3, 15.6, 30, 40, -1),   # 向下离开中枢（创新低）
        bi(4, 15.6, 16.5, 40, 50, 1),    # 反弹
        bi(5, 16.5, 15.9, 50, 60, -1),   # 回调不破前低 15.6
    ]
    zs = chan.Zhongshu(
        zg=17.4, zd=16.6, start_bi=0, end_bi=2,
        start_dt="2026-01-01 10:00", end_dt="2026-01-03 14:00", bi_count=3,
    )
    return bis, [zs]


def check_beichi_rule():
    """返回 (是否通过, 说明文本列表)。"""
    lines = []
    ok = True
    bis, zs = _unit_bis_and_zs()

    def hist_with(area_prev, area_leave):
        """前 30 根给 area_prev 的总量（中枢内下跌笔区间 10~20），
        30~40 根给 area_leave 的总量（离开笔区间）。"""
        h = [0.0] * 61
        for i in range(10, 21):
            h[i] = area_prev / 11.0
        for i in range(30, 41):
            h[i] = area_leave / 11.0
        return h

    # 有背驰：离开笔动能只有前一段的 40%
    buys_strict = chan.find_buys(bis, zs, hist_with(1.0, 0.4), 61,
                                 beichi_ratio=0.85, require_beichi=True)
    kinds = [b.kind for b in buys_strict]
    lines.append(f"  有背驰 + 严格模式 -> 买点 {kinds}")
    if "2" not in kinds:
        lines.append("    !! 背驰成立却没识别出二买")
        ok = False

    # 无背驰：离开笔动能是前一段的 1.5 倍
    buys_no_bc = chan.find_buys(bis, zs, hist_with(1.0, 1.5), 61,
                                beichi_ratio=0.85, require_beichi=True)
    kinds_no = [b.kind for b in buys_no_bc]
    lines.append(f"  无背驰 + 严格模式 -> 买点 {kinds_no}")
    if "2" in kinds_no:
        lines.append("    !! 无背驰却识别出二买（严格模式失效）")
        ok = False

    # 同样的无背驰数据，宽松模式应当识别出二买
    buys_loose = chan.find_buys(bis, zs, hist_with(1.0, 1.5), 61,
                                beichi_ratio=0.85, require_beichi=False)
    kinds_loose = [b.kind for b in buys_loose]
    lines.append(f"  无背驰 + 宽松模式 -> 买点 {kinds_loose}")
    if "2" not in kinds_loose:
        lines.append("    !! 宽松模式兜底失效")
        ok = False

    return ok, lines


def case_fake_third_buy():
    """4. 反例：中枢内曾有一笔冲到 12.9，之后的向上笔只到 12.6（未创中枢新高）
    虽然站上了中枢上沿 12.45，也不该算三买。"""
    pts = [10.0, 12.9, 11.7, 12.45, 11.75, 12.6, 12.5, 12.85]
    return bars_from_points(pts, bars_per_leg=6)


def case_broken_third_buy():
    """5. 反例：三买成立后价格跌回中枢上沿之下 => 该买点应判定失效"""
    path = []
    path += [10.0 + 2.0 * k / 15 for k in range(1, 16)]
    path += zigzag(11.60, 12.40, 4, 4, 3)
    path += [12.40 + 1.4 * k / 10 for k in range(1, 11)]       # 突破到 13.8
    path += [13.80 - 0.9 * k / 8 for k in range(1, 9)]         # 回调到 12.9（三买成立）
    path += [12.90 + 0.4 * k / 4 for k in range(1, 5)]         # 小反弹
    path += [13.30 - 2.2 * k / 16 for k in range(1, 17)]       # 跌回中枢（11.1 < 12.4）
    return bars_from_path(path)


# --------------------------------------------------------------------------- #
def show(title, bars, **kw):
    print("=" * 74)
    print(title)
    print("=" * 74)
    res = chan.analyze(bars, "30", **kw)
    merged = chan.merge_bars(bars)
    fracs = chan.find_fractals(merged)
    bis, _ = chan.build_bis(merged, fracs)
    zs = chan.find_zhongshus(bis, min_span_pct=kw.get("min_zs_span_pct", 0.0))

    print(f"原始K线 {len(bars)}  合并后 {len(merged)}  分型 {len(fracs)}  "
          f"笔 {len(bis)}  中枢 {len(zs)}")
    for z in zs:
        print(f"  中枢  ZG={z.zg:.3f}  ZD={z.zd:.3f}  笔数={z.bi_count}")
    print(f"识别到的买点: {[(b.label, round(b.price, 3), f'{b.bars_ago}根前') for b in res.all_buys]}")
    print(f"最新买点: {res.buy.label if res.buy else '无'}")
    return res


def main():
    ok = True

    # 用例 1
    r1 = show("用例1：中枢 -> 突破创中枢新高 -> 回调不回中枢（期望 3买）", case_third_buy(), **STRICT)
    if not r1.buy or r1.buy.kind != "3":
        print("!! 用例1 未识别出 3买"); ok = False

    # 用例 2：宽松模式
    r2 = show("用例2：破位 -> 反弹 -> 回调不破前低（宽松模式，期望 2买）",
              case_second_buy_loose(), require_beichi=False, min_zs_span_pct=0.006, zs_break_tol=0.003)
    if not (r2.buy and r2.buy.kind in ("2", "1")):
        print("!! 用例2 未识别出 2买"); ok = False

    # 用例 3：单元级背驰判据（严格 / 宽松 / 无背驰三种组合）
    print("=" * 74)
    print("用例3：背驰判据单元验证（直接构造 笔/中枢/MACD面积）")
    print("=" * 74)
    bc_ok, bc_lines = check_beichi_rule()
    for ln in bc_lines:
        print(ln)
    if not bc_ok:
        ok = False

    # 用例 4：反例——未创中枢新高的"伪三买"
    r4 = show("用例4（反例）：向上笔未创中枢新高，不该算 3买", case_fake_third_buy(), **STRICT)
    if any(b.kind == "3" for b in r4.all_buys):
        print("!! 用例4 误报：未创新高的假突破被识别成 3买"); ok = False

    # 用例 5：反例——三买后跌回中枢应失效
    r5 = show("用例5（反例）：三买后价格跌回中枢，应判定失效", case_broken_third_buy(), **STRICT)
    buy5, why5 = chan.pick_valid_buy(r5, ["2", "3"], r5.last_close, 60, 0.005, 0.15)
    print(f"失效校验: {'仍有效 -> ' + str(buy5.label) if buy5 else '已失效（' + why5 + '）'}")
    if buy5 is not None and buy5.kind == "3":
        print("!! 用例5 误报：跌回中枢的三买仍被判为有效"); ok = False

    print("=" * 74)
    print("自检结果：", "全部通过" if ok else "存在失败用例")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
