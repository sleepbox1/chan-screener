# -*- coding: utf-8 -*-
"""
示例数据生成器（仅用于首次预览）
================================

在真实筛选还没跑过之前，先生成一份结构与真实输出完全一致的示例数据，
让网站立刻能看到完整效果。数据带 `"demo": true` 标记，页面顶部会显示醒目提示。

真实运行一次 `python scripts/screener.py` 后，latest.json 会被自动覆盖。

    python scripts/make_demo_data.py
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data")

STOCKS = [
    ("600519", "贵州茅台", 1682.00, 22.4, 1.35, 5_820_000_000, 2_113_000_000_000),
    ("300750", "宁德时代", 268.50, 24.8, 2.61, 8_140_000_000, 1_182_000_000_000),
    ("002594", "比亚迪", 312.40, 26.9, -0.84, 4_260_000_000, 909_000_000_000),
    ("601899", "紫金矿业", 19.86, 15.2, 3.12, 6_180_000_000, 528_000_000_000),
    ("300308", "中际旭创", 158.70, 38.6, 5.42, 7_920_000_000, 178_000_000_000),
    ("002415", "海康威视", 32.18, 21.3, 1.06, 2_150_000_000, 297_000_000_000),
    ("601138", "工业富联", 24.63, 19.7, 2.88, 5_430_000_000, 489_000_000_000),
    ("688981", "中芯国际", 88.42, 96.4, 1.92, 9_260_000_000, 704_000_000_000),
    ("002371", "北方华创", 412.60, 44.1, -1.28, 3_180_000_000, 219_000_000_000),
    ("600276", "恒瑞医药", 52.34, 58.2, 0.72, 2_640_000_000, 334_000_000_000),
    ("300059", "东方财富", 22.85, 31.5, 3.94, 12_460_000_000, 360_000_000_000),
    ("601088", "中国神华", 41.26, 12.6, 0.48, 1_980_000_000, 819_000_000_000),
    ("603501", "韦尔股份", 128.90, 46.8, 2.15, 2_860_000_000, 156_000_000_000),
    ("300124", "汇川技术", 68.72, 33.9, -0.62, 1_740_000_000, 184_000_000_000),
    ("000063", "中兴通讯", 36.15, 18.4, 1.77, 4_920_000_000, 172_000_000_000),
    ("688111", "金山办公", 288.40, 82.6, 4.31, 2_310_000_000, 133_000_000_000),
    ("601668", "中国建筑", 5.86, 4.8, 0.34, 1_260_000_000, 245_000_000_000),
    ("002230", "科大讯飞", 48.92, 71.3, 2.66, 3_740_000_000, 113_000_000_000),
    ("600030", "中信证券", 27.14, 16.8, 1.42, 6_820_000_000, 402_000_000_000),
    ("300750", "宁德时代", 268.50, 24.8, 2.61, 8_140_000_000, 1_182_000_000_000),
    ("000858", "五粮液", 148.60, 17.2, -0.41, 3_260_000_000, 576_000_000_000),
    ("603986", "兆易创新", 128.30, 52.7, 3.28, 2_940_000_000, 85_400_000_000),
    ("601012", "隆基绿能", 18.42, 28.9, 1.65, 2_180_000_000, 139_000_000_000),
    ("688041", "海光信息", 142.80, 108.5, 3.87, 4_360_000_000, 331_000_000_000),
    ("002475", "立讯精密", 44.26, 23.6, 1.14, 3_520_000_000, 320_000_000_000),
    ("600887", "伊利股份", 28.94, 15.9, 0.62, 1_480_000_000, 184_000_000_000),
    ("600438", "通威股份", 22.68, 34.2, 2.44, 1_960_000_000, 102_000_000_000),
    ("000725", "京东方A", 4.62, 41.8, 1.99, 6_140_000_000, 174_000_000_000),
    ("601318", "中国平安", 58.32, 9.4, 1.08, 5_280_000_000, 1_062_000_000_000),
    ("603259", "药明康德", 62.85, 27.4, -1.55, 2_720_000_000, 183_000_000_000),
    ("000651", "格力电器", 44.72, 8.6, 0.86, 1_860_000_000, 252_000_000_000),
    ("002027", "分众传媒", 7.24, 22.8, 2.55, 1_320_000_000, 104_000_000_000),
    ("600900", "长江电力", 28.16, 19.8, 0.36, 2_040_000_000, 689_000_000_000),
    ("002460", "赣锋锂业", 36.48, 46.2, 3.71, 1_680_000_000, 73_600_000_000),
    ("600009", "上海机场", 38.92, 31.4, 1.28, 890_000_000, 96_800_000_000),
]

ETFS = [
    ("510300", "沪深300ETF", 4.126, 1.24, 2_860_000_000, 132_000_000_000),
    ("512480", "半导体ETF", 1.284, 3.86, 1_920_000_000, 24_600_000_000),
    ("588000", "科创50ETF", 1.146, 2.42, 3_240_000_000, 68_200_000_000),
    ("512880", "证券ETF", 1.062, 1.68, 2_180_000_000, 38_400_000_000),
    ("515030", "新能源车ETF", 0.982, 2.94, 1_460_000_000, 18_900_000_000),
    ("512660", "军工ETF", 1.328, 2.16, 980_000_000, 12_400_000_000),
    ("159915", "创业板ETF", 2.146, 2.68, 2_640_000_000, 52_800_000_000),
    ("512170", "医疗ETF", 0.486, 1.42, 860_000_000, 9_620_000_000),
    ("515790", "光伏ETF", 0.812, 2.33, 760_000_000, 8_240_000_000),
    ("159995", "芯片ETF", 1.186, 3.55, 1_280_000_000, 15_600_000_000),
    ("512800", "银行ETF", 1.462, 0.86, 640_000_000, 11_800_000_000),
    ("513100", "纳指ETF", 1.682, 1.94, 1_840_000_000, 21_400_000_000),
]


def level_payload(rng, code, price, lv_name, zs_span=0.045, fresh_max=12):
    """构造一个看起来合理的级别买点结构。"""
    kind = rng.choice(["2", "3"])
    zd = round(price * (1 - zs_span * rng.uniform(0.7, 1.2)), 3)
    zg = round(price * (1 - zs_span * rng.uniform(0.05, 0.35)), 3)
    if kind == "3":
        buy = round(zg * rng.uniform(1.001, 1.02), 3)
    else:
        buy = round(zd * rng.uniform(0.985, 1.005), 3)
    bars_ago = rng.randint(0, fresh_max)

    step = 5 if lv_name == "5" else 30
    total_min = bars_ago * step
    base = datetime.now() - timedelta(minutes=total_min)
    # 只取交易时段
    hh = 9 + (base.hour + (base.minute // 60)) % 6
    dt = base.strftime("%Y-%m-%d") + f" {max(9, min(14, hh)):02d}:{base.minute // step * step:02d}:00"

    if kind == "3":
        detail = f"向上离开中枢上沿{zg:.2f}，回调{buy:.2f}未回中枢"
    else:
        detail = f"背驰一买低点{zd:.2f}后回调至{buy:.2f}未破前低，动能比{rng.uniform(0.42, 0.82):.2f}"

    return {
        "kind": kind,
        "label": f"{kind}买",
        "price": buy,
        "dt": dt,
        "bars_ago": bars_ago,
        "detail": detail,
        "zs_zg": zg,
        "zs_zd": zd,
        "zs_start": (datetime.now() - timedelta(days=rng.randint(6, 20))).strftime("%Y-%m-%d") + " 10:00:00",
        "zs_end": (datetime.now() - timedelta(days=rng.randint(1, 5))).strftime("%Y-%m-%d") + " 14:00:00",
        "bi_count": rng.randint(9, 23),
    }


def score(kind30, kind5, fresh):
    s = 0.0
    if kind30:
        s += (40 if kind30 == "3" else 34)
    if kind5:
        s += (30 if kind5 == "3" else 26) * 0.85
    if kind30 and kind5:
        s += 15
    s += 10 if fresh <= 2 else 6 if fresh <= 6 else 3 if fresh <= 12 else 0
    return int(max(0, min(100, round(s))))


def build_levels(rng, code, price, mode):
    if mode == "both":
        return (level_payload(rng, code, price, "30", fresh_max=12),
                level_payload(rng, code, price, "5", fresh_max=36))
    if mode == "m30":
        return level_payload(rng, code, price, "30", fresh_max=12), None
    return None, level_payload(rng, code, price, "5", fresh_max=36)


def main():
    rng = random.Random(20261008)
    today = datetime.now().strftime("%Y-%m-%d")
    signals = []

    m30_rng = level_payload  # noqa: F841

    # ---- 股票：约 60% 双级别共振，40% 单级别 ----
    seen = set()
    for code, name, price, pe, chg, amt, mv in STOCKS:
        if code in seen:
            continue
        seen.add(code)
        mode = "both" if rng.random() < 0.62 else rng.choice(["m30", "m5"])
        m30, m5 = build_levels(rng, code, price, mode)
        tags = ([f"30分{m30['label']}"] if m30 else []) + ([f"5分{m5['label']}"] if m5 else [])
        fresh = min([x["bars_ago"] for x in (m30, m5) if x])
        signals.append({
            "code": code, "name": name, "type": "stock",
            "market": "科创板" if code.startswith("68") else
                      "创业板" if code.startswith("30") else
                      "沪市主板" if code.startswith("6") else "深市主板",
            "price": price, "change_pct": chg, "pe_dynamic": pe,
            "turnover": round(rng.uniform(0.6, 6.8), 2),
            "amount": amt, "total_mv": mv,
            "m30": m30, "m5": m5,
            "resonance": bool(m30 and m5),
            "tags": tags,
            "score": score(m30["kind"] if m30 else None, m5["kind"] if m5 else None, fresh),
        })

    # ---- ETF：同类型只留一只，所以这里每个桶放一个 ----
    for code, name, price, chg, amt, mv in ETFS:
        mode = "both" if rng.random() < 0.5 else rng.choice(["m30", "m5"])
        m30, m5 = build_levels(rng, code, price, mode)
        tags = ([f"30分{m30['label']}"] if m30 else []) + ([f"5分{m5['label']}"] if m5 else [])
        fresh = min([x["bars_ago"] for x in (m30, m5) if x])
        signals.append({
            "code": code, "name": name, "type": "etf", "market": "ETF",
            "price": price, "change_pct": chg, "pe_dynamic": None,
            "turnover": None, "amount": amt, "total_mv": mv,
            "m30": m30, "m5": m5,
            "resonance": bool(m30 and m5),
            "tags": tags,
            "score": score(m30["kind"] if m30 else None, m5["kind"] if m5 else None, fresh),
        })

    signals.sort(key=lambda x: -x["score"])

    payload = {
        "demo": True,
        "demo_note": "这是示例数据，用于预览页面效果。跑一次 scripts/screener.py 后会被真实结果覆盖。",
        "trade_date": today,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": 0,
        "params": {
            "buy_kinds": ["2", "3"],
            "universe": {"exclude_st": True, "exclude_bse": True, "require_positive_pe": True,
                         "min_amount": 0, "min_total_mv": 0},
            "levels": {
                "m30": {"period": "30", "lookback_days": 150, "max_bars_ago": 16,
                        "min_gap": 4, "beichi_ratio": 0.85},
                "m5": {"period": "5", "lookback_days": 40, "max_bars_ago": 48,
                       "min_gap": 4, "beichi_ratio": 0.85},
            },
            "break_tolerance": 0.005,
            "etf_same_type_limit": 1,
        },
        "stats": {
            "scanned": 4382,
            "matched": len(signals),
            "stock": len([s for s in signals if s["type"] == "stock"]),
            "etf": len([s for s in signals if s["type"] == "etf"]),
            "resonance": len([s for s in signals if s["resonance"]]),
            "stocks": 3126,
            "etfs": 1256,
            "reason_dist": {"无买点": 4012, "30分钟数据不足": 71, "买点过期": 108, "已跌破买点": 62},
        },
        "signals": signals,
    }

    os.makedirs(os.path.join(OUT, "history"), exist_ok=True)
    for path in [os.path.join(OUT, "latest.json"),
                 os.path.join(OUT, "history", f"{today}.json")]:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)

    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump({
            "dates": [today],
            "updated_at": payload["generated_at"],
            "latest": {"trade_date": today, "matched": len(signals), "scanned": 4382},
        }, f, ensure_ascii=False, indent=1)

    print(f"[ok] 示例数据已生成：{len(signals)} 条信号 -> data/latest.json")


if __name__ == "__main__":
    main()
