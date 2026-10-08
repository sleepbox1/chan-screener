# -*- coding: utf-8 -*-
"""
缠论引擎自检
============

用人工构造的 K 线序列验证引擎能否正确完成
包含处理 -> 分型 -> 笔 -> 中枢 -> 买点 的识别。

构造的形态：
    [上涨] -> [中枢震荡] -> [向上突破] -> [回调不破中枢上沿]   => 应识别出 3买
    [下跌] -> [中枢震荡] -> [向下破位+背驰] -> [反弹] -> [回调不破前低] => 应识别出 2买
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chan  # noqa: E402


def bars_from_path(path, vol=0.06, start_dt="2026-01-01 10:00"):
    """把一条价格路径转成 K 线，每根 K 线带固定的上下影幅度。"""
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


def case_third_buy():
    """中枢 -> 向上突破 -> 回调不回中枢 => 3买"""
    path = []
    path += [10.0 + 2.0 * k / 15 for k in range(1, 16)]        # 上涨 10 -> 12
    path += zigzag(11.60, 12.40, 4, 4, 3)                       # 中枢震荡 [11.6, 12.4]
    path += [12.40 + 1.4 * k / 10 for k in range(1, 11)]        # 突破到 13.8
    path += [13.80 - 0.9 * k / 8 for k in range(1, 9)]          # 回调到 12.9 (> ZG)
    path += [12.90 + 0.6 * k / 6 for k in range(1, 7)]          # 再度上行
    return bars_from_path(path)


def case_second_buy():
    """下跌 -> 中枢 -> 破位背驰 -> 反弹 -> 回调不破前低 => 2买"""
    path = []
    path += [20.0 - 3.0 * k / 15 for k in range(1, 16)]         # 下跌 20 -> 17
    path += zigzag(16.60, 17.40, 4, 4, 3)                       # 中枢 [16.6, 17.4]
    path += [17.40 - 2.2 * k / 12 for k in range(1, 13)]        # 向下破位到 15.2
    path += [15.20 + 1.0 * k / 8 for k in range(1, 9)]          # 反弹到 16.2
    path += [16.20 - 0.5 * k / 6 for k in range(1, 7)]          # 回调到 15.7 (> 15.2)
    path += [15.70 + 0.8 * k / 6 for k in range(1, 7)]
    return bars_from_path(path)


def show(title, bars):
    print("=" * 70)
    print(title)
    print("=" * 70)
    res = chan.analyze(bars, "30")
    merged = chan.merge_bars(bars)
    fracs = chan.find_fractals(merged)
    bis, _ = chan.build_bis(merged, fracs)
    zs = chan.find_zhongshus(bis)

    print(f"原始K线 {len(bars)}  合并后 {len(merged)}  分型 {len(fracs)}  "
          f"笔 {len(bis)}  中枢 {len(zs)}")
    for z in zs:
        print(f"  中枢  ZG={z.zg:.3f}  ZD={z.zd:.3f}  笔数={z.bi_count}")
    print(f"笔序列: {[(('↑' if b.direction == 1 else '↓'), round(b.start_price, 2), round(b.end_price, 2)) for b in bis]}")
    print(f"买点: {[(b.label, round(b.price, 3), f'{b.bars_ago}根前') for b in res.all_buys]}")
    print(f"最新有效买点: {res.buy.label if res.buy else '无'} "
          f"{round(res.buy.price,3) if res.buy else ''}")
    print(f"ok={res.ok} reason={res.reason or '-'}")
    return res


def main():
    ok = True

    r3 = show("用例 1：中枢 -> 向上突破 -> 回调不回中枢   （期望识别 3买）", case_third_buy())
    if not r3.buy or r3.buy.kind != "3":
        print("!! 用例1 未识别出 3买")
        ok = False

    r2 = show("用例 2：破位背驰 -> 反弹 -> 回调不破前低    （期望识别 2买）", case_second_buy())
    if not r2.buy or r2.buy.kind not in ("2", "1"):
        print("!! 用例2 未识别出 2买")
        ok = False

    print("=" * 70)
    print("自检结果：", "全部通过" if ok else "存在失败用例")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
