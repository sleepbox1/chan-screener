# -*- coding: utf-8 -*-
"""
A 股 / ETF 盘后自动筛选器
=========================

筛选条件（用户需求）：

    股票：动态市盈率 > 0  且  非 ST  且  非北交所
    ETF ：跳过市盈率，只查缠论结构
    技术：30 分钟级别出现 2 买或 3 买  且/或  5 分钟级别出现 2 买或 3 买

用法：

    python screener.py                        # 全市场正式跑
    python screener.py --limit 200            # 只跑前 200 只，用于验证
    python screener.py --codes 600519,000001  # 指定标的
    python screener.py --no-etf               # 跳过 ETF
    python screener.py --out ../data          # 指定输出目录
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chan  # noqa: E402
import datasource as ds  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG = {
    "universe": {
        "exclude_st": True,
        "exclude_bse": True,
        "require_positive_pe": True,
        "min_amount": 0,
        "min_total_mv": 0,
    },
    "levels": {
        "m30": {
            "period": "30",
            "lookback_days": 150,
            "max_bars_ago": 16,
            "min_gap": 4,
            "beichi_ratio": 0.85,
        },
        "m5": {
            "period": "5",
            "lookback_days": 40,
            "max_bars_ago": 48,
            "min_gap": 4,
            "beichi_ratio": 0.85,
        },
    },
    "buy_kinds": ["2", "3"],
    "break_tolerance": 0.005,
    "concurrency": 8,
    "etf": {"enabled": True, "same_type_limit": 1},
    # 买点严格度（缠论原始口径，宁可少而准）
    "strict": {
        "require_beichi_for_buy2": True,   # 二买必须由 MACD 背驰的一买引出
        "min_zs_span_pct": 0.006,          # 中枢最小高度（占上沿比例），过滤窄幅噪音中枢
        "zs_break_tol": 0.003,             # 三买回调需高于中枢上沿的缓冲比例
        "max_gain_from_buy_pct": 0.15,     # 现价距买点涨幅上限（追高过滤）
    },
}


def load_config(path: Optional[str]) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if not path:
        default_path = os.path.join(ROOT, "config.yaml")
        path = default_path if os.path.exists(default_path) else None
    if path and os.path.exists(path):
        try:
            import yaml  # type: ignore
        except ImportError:
            print("[warn] 未安装 PyYAML，改用内置默认配置")
            return cfg
        with open(path, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


# --------------------------------------------------------------------------- #
# 打分
# --------------------------------------------------------------------------- #
_KIND_SCORE = {"3": 40, "2": 34}


def score_signal(sig: Dict, m30: Optional[Dict], m5: Optional[Dict]) -> int:
    s = 0.0
    best_fresh = 99
    for lv, base in ((m30, 1.0), (m5, 0.85)):
        if not lv:
            continue
        s += _KIND_SCORE.get(lv["kind"], 25) * base
        best_fresh = min(best_fresh, lv["bars_ago"])

    if m30 and m5:
        s += 15  # 多级别共振

    if best_fresh <= 2:
        s += 10
    elif best_fresh <= 6:
        s += 6
    elif best_fresh <= 12:
        s += 3

    pe = sig.get("pe_dynamic")
    if pe:
        if pe < 50:
            s += 5
        elif pe < 100:
            s += 2

    return int(max(0, min(100, round(s))))


# --------------------------------------------------------------------------- #
# 单标的处理
# --------------------------------------------------------------------------- #
def analyze_one(
    item: Dict,
    is_etf: bool,
    cfg: dict,
    start30: str,
    start5: str,
    end: str,
    cache: Optional[ds.BarCache],
    cache_day: str,
) -> Tuple[Optional[Dict], str]:
    """返回 (信号字典 或 None, 跳过原因)。"""
    code = item["code"]
    try:
        lv_cfg = cfg["levels"]
        kinds = cfg["buy_kinds"]
        tol = cfg["break_tolerance"]
        st = cfg.get("strict", {})
        span = float(st.get("min_zs_span_pct", 0.0))
        zs_tol = float(st.get("zs_break_tol", 0.003))
        max_gain = float(st.get("max_gain_from_buy_pct", 0.15))

        bars30 = ds.get_min_bars(
            code, lv_cfg["m30"]["period"], start30, end, is_etf, "qfq", cache, cache_day
        )
        if len(bars30) < 60:
            return None, "30分钟数据不足"

        r30 = chan.analyze(
            bars30,
            "30",
            min_gap=lv_cfg["m30"]["min_gap"],
            beichi_ratio=lv_cfg["m30"]["beichi_ratio"],
            require_beichi=bool(
                lv_cfg["m30"].get(
                    "require_beichi_for_buy2",
                    st.get("require_beichi_for_buy2", True),
                )
            ),
            min_zs_span_pct=span,
            zs_break_tol=zs_tol,
        )
        buy30, _ = chan.pick_valid_buy(
            r30, kinds, r30.last_close, lv_cfg["m30"]["max_bars_ago"], tol, max_gain
        )

        bars5 = ds.get_min_bars(
            code, lv_cfg["m5"]["period"], start5, end, is_etf, "qfq", cache, cache_day
        )
        buy5, r5 = None, None
        if len(bars5) >= 60:
            r5 = chan.analyze(
                bars5,
                "5",
                min_gap=lv_cfg["m5"]["min_gap"],
                beichi_ratio=lv_cfg["m5"]["beichi_ratio"],
                require_beichi=bool(
                    lv_cfg["m5"].get(
                        "require_beichi_for_buy2",
                        st.get("require_beichi_for_buy2", True),
                    )
                ),
                min_zs_span_pct=span,
                zs_break_tol=zs_tol,
            )
            buy5, _ = chan.pick_valid_buy(
                r5, kinds, r5.last_close, lv_cfg["m5"]["max_bars_ago"], tol, max_gain
            )

        if buy30 is None and buy5 is None:
            return None, "无买点"

        d30 = _level_payload(buy30) if buy30 else None
        d5 = _level_payload(buy5) if buy5 else None

        tags = []
        if buy30:
            tags.append(f"30分{buy30.label}")
        if buy5:
            tags.append(f"5分{buy5.label}")

        sig = {
            "code": code,
            "name": item["name"],
            "type": "etf" if is_etf else "stock",
            "market": item["market"],
            "price": round(item["price"], 3),
            "change_pct": item.get("change_pct"),
            "pe_dynamic": item.get("pe_dynamic"),
            "turnover": item.get("turnover"),
            "amount": item.get("amount"),
            "total_mv": item.get("total_mv"),
            "m30": d30,
            "m5": d5,
            "resonance": bool(d30 and d5),
            "tags": tags,
        }
        sig["score"] = score_signal(sig, d30, d5)
        return sig, ""
    except ds.DataFetchError:
        return None, "数据获取失败"
    except Exception as e:  # noqa: BLE001
        return None, f"异常:{type(e).__name__}:{e}"


def _level_payload(b: chan.BuyPoint) -> Dict:
    """把买点整理成前端消费的结构。

    中枢取买点自身关联的那个，而不是全序列里最后一个 —— 买点归属的中枢
    往往不是最新中枢，用错了会让页面上的中枢区间对不上买点位置。
    """
    zs = b.zhongshu
    return {
        "kind": b.kind,
        "label": b.label,
        "price": round(b.price, 3),
        "dt": b.dt,
        "bars_ago": b.bars_ago,
        "detail": b.detail,
        "zs_zg": round(zs.zg, 3) if zs else None,
        "zs_zd": round(zs.zd, 3) if zs else None,
        "zs_start": zs.start_dt if zs else None,
        "zs_end": zs.end_dt if zs else None,
        "zs_bi_count": zs.bi_count if zs else None,
    }


# --------------------------------------------------------------------------- #
# ETF 去重：同类型只保留一只
# --------------------------------------------------------------------------- #
def _etf_bucket(name: str) -> str:
    """把 ETF 名称归到一个粗粒度类型桶，用于"同类型只留一只"。"""
    keys = [
        "沪深300", "中证500", "中证1000", "中证2000", "上证50", "科创50", "科创100",
        "创业板", "北证50", "中证A500", "中证A50", "红利", "银行", "证券", "保险",
        "白酒", "食品饮料", "医药", "医疗", "生物", "创新药", "芯片", "半导体",
        "人工智能", "机器人", "新能源", "光伏", "电池", "军工", "国防", "有色",
        "煤炭", "钢铁", "地产", "房地产", "农业", "养殖", "传媒", "游戏", "计算机",
        "软件", "通信", "5G", "消费电子", "汽车", "黄金", "原油", "纳斯达克",
        "标普", "恒生", "港股", "中概", "央企", "国企", "电力", "化工", "建材",
        "环保", "家电", "旅游", "物流", "机械", "稀土", "碳中和", "云计算", "大数据",
    ]
    for k in keys:
        if k in name:
            return k
    return name[:4] if len(name) >= 4 else name


def dedup_etf(signals: List[Dict], limit: int = 1) -> List[Dict]:
    if limit <= 0:
        return signals
    buckets: Dict[str, List[Dict]] = {}
    others: List[Dict] = []
    for s in signals:
        b = _etf_bucket(s["name"])
        if b:
            buckets.setdefault(b, []).append(s)
        else:
            others.append(s)
    kept: List[Dict] = list(others)
    for _b, arr in buckets.items():
        arr.sort(key=lambda x: -x["score"])
        kept.extend(arr[:limit])
    kept.sort(key=lambda x: -x["score"])
    return kept


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run(args) -> int:
    t0 = time.time()

    # 越早装越好：让 AkShare 内部那几十页翻页请求也具备重试能力
    ds.install_http_retry()
    # 探测东财可用性：被限流时立刻熔断，别让每个标的都白等一轮重试
    ds.probe_eastmoney()

    cfg = load_config(args.config)
    if args.no_etf:
        cfg["etf"]["enabled"] = False
    if args.concurrency:
        cfg["concurrency"] = args.concurrency

    out_dir = os.path.abspath(args.out or os.path.join(ROOT, "data"))
    hist_dir = os.path.join(out_dir, "history")
    os.makedirs(hist_dir, exist_ok=True)

    cache = None if args.no_cache else ds.BarCache(os.path.join(ROOT, ".cache", "bars"))
    # --cache-day 允许复用历史交易日的缓存做离线复算（数据源被限流时尤其有用）
    cache_day = args.cache_day or datetime.now().strftime("%Y-%m-%d")

    today = datetime.now()
    end = today.strftime("%Y-%m-%d 23:59:59")
    start30 = (today - timedelta(days=int(cfg["levels"]["m30"]["lookback_days"]))).strftime(
        "%Y-%m-%d 09:00:00"
    )
    start5 = (today - timedelta(days=int(cfg["levels"]["m5"]["lookback_days"]))).strftime(
        "%Y-%m-%d 09:00:00"
    )

    print("=" * 68)
    print(" A股/ETF 缠论多级别买点筛选")
    print("=" * 68)

    # ---------- 1. 股票池 ----------
    targets: List[Tuple[Dict, bool]] = []
    universe_stat = {}

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        meta = _lookup_meta(codes, cfg)
        for c in codes:
            is_etf = c.startswith(("5", "1"))
            m = meta.get(c, {})
            targets.append(
                (
                    {
                        "code": c,
                        "name": m.get("name", c),
                        "price": m.get("price", 0.0),
                        "change_pct": m.get("change_pct"),
                        "pe_dynamic": m.get("pe_dynamic"),
                        "amount": m.get("amount"),
                        "total_mv": m.get("total_mv"),
                        "turnover": m.get("turnover"),
                        "market": ds.market_of(c),
                    },
                    is_etf,
                )
            )
        print(f"[池] 指定标的 {len(targets)} 只（已尝试补全快照信息）")
    else:
        u = cfg["universe"]
        # 快照是全流程第一步，也是唯一没有逐标的兜底的环节，给它套一层整体重试
        stocks = None
        for attempt in (1, 2):
            try:
                ds._log("[池] 拉取全市场 A 股快照 ...")
                stocks = ds.get_stock_universe(
                    min_amount=u["min_amount"],
                    min_total_mv=u["min_total_mv"],
                    exclude_st=u["exclude_st"],
                    exclude_bse=u["exclude_bse"],
                    require_positive_pe=u["require_positive_pe"],
                )
                break
            except Exception as e:  # noqa: BLE001
                ds._log(f"[warn] A股快照第 {attempt} 次失败：{type(e).__name__}: {e}")
                if attempt == 1:
                    time.sleep(10)
        if stocks is None:
            print("[err] A股快照两次尝试均失败，退出（详见上方 warn 日志）", flush=True)
            return 3
        print(f"[池] 股票通过基础过滤：{len(stocks)} 只 "
              f"(已剔除 ST / 北交所 / 动态市盈率≤0 / 停牌)", flush=True)
        universe_stat["stocks"] = len(stocks)

        etfs: List[Dict] = []
        if cfg["etf"]["enabled"]:
            try:
                ds._log("[池] 拉取全市场 ETF 快照 ...")
                etfs = ds.get_etf_universe(min_amount=u["min_amount"])
                print(f"[池] ETF：{len(etfs)} 只（跳过市盈率条件）", flush=True)
                universe_stat["etfs"] = len(etfs)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] ETF 快照失败，跳过 ETF：{e}", flush=True)

        targets = [(s, False) for s in stocks] + [(e, True) for e in etfs]
        if args.sample:
            # 均匀抽样：股票池按代码排序后等间隔取，能同时覆盖沪深主板 / 创业板 / 科创板，
            # 比 --limit 只截取前 N 只（清一色 00 开头）有代表性得多
            n = int(args.sample)
            if n < len(stocks):
                step = len(stocks) / float(n)
                stocks_s = [stocks[min(int(i * step), len(stocks) - 1)] for i in range(n)]
            else:
                stocks_s = stocks
            etfs_s = etfs[: max(0, n // 4)] if etfs else []
            targets = [(s, False) for s in stocks_s] + [(e, True) for e in etfs_s]
            print(f"[池] --sample {n} 生效：股票 {len(stocks_s)} + ETF {len(etfs_s)}")
        if args.limit:
            targets = targets[: args.limit]
            print(f"[池] --limit 生效，仅扫描前 {len(targets)} 只")

    if not targets:
        print("[err] 股票池为空，退出")
        return 2

    # ---------- 2. 并发分析 ----------
    ds._log(f"[扫] 开始分析 {len(targets)} 只标的（并发 {cfg['concurrency']}）...")
    signals: List[Dict] = []
    reasons: Dict[str, int] = {}
    done = 0
    t1 = time.time()

    with ThreadPoolExecutor(max_workers=int(cfg["concurrency"])) as pool:
        futs = {
            pool.submit(
                analyze_one, item, is_etf, cfg, start30, start5, end, cache, cache_day
            ): item
            for item, is_etf in targets
        }
        for fut in as_completed(futs):
            done += 1
            try:
                sig, why = fut.result()
            except Exception as e:  # noqa: BLE001
                sig, why = None, f"异常:{e}"
            if sig:
                signals.append(sig)
            elif why:
                key = why.split(":")[0]
                reasons[key] = reasons.get(key, 0) + 1
            if done % 200 == 0 or done == len(targets):
                el = time.time() - t1
                rate = done / el if el > 0 else 0
                eta = (len(targets) - done) / rate if rate > 0 else 0
                ds._log(
                    f"    进度 {done}/{len(targets)}  命中 {len(signals)}  "
                    f"{rate:.1f}只/秒  ETA {eta/60:.1f}分"
                )

    # ---------- 3. 排序 / 去重 ----------
    stocks_sig = sorted([s for s in signals if s["type"] == "stock"], key=lambda x: -x["score"])
    etf_sig = sorted([s for s in signals if s["type"] == "etf"], key=lambda x: -x["score"])
    etf_sig = dedup_etf(etf_sig, int(cfg["etf"].get("same_type_limit", 1)))
    all_sig = sorted(stocks_sig + etf_sig, key=lambda x: -x["score"])

    # ---------- 4. 交易日期 ----------
    last_dts = [s["m30"]["dt"] for s in all_sig if s.get("m30")] + [
        s["m5"]["dt"] for s in all_sig if s.get("m5")
    ]
    trade_date = datetime.now().strftime("%Y-%m-%d")
    if last_dts:
        trade_date = max(last_dts)[:10]

    payload = {
        "trade_date": trade_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(time.time() - t0, 1),
        "params": {
            "buy_kinds": cfg["buy_kinds"],
            "universe": cfg["universe"],
            "levels": cfg["levels"],
            "break_tolerance": cfg["break_tolerance"],
            "etf_same_type_limit": cfg["etf"].get("same_type_limit", 1),
        },
        "stats": {
            "scanned": len(targets),
            "matched": len(all_sig),
            "stock": len(stocks_sig),
            "etf": len(etf_sig),
            "resonance": len([s for s in all_sig if s["resonance"]]),
            "reason_dist": dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:8]),
            **universe_stat,
        },
        "signals": all_sig,
    }

    latest = os.path.join(out_dir, "latest.json")
    _write_json(latest, payload)
    _write_json(os.path.join(hist_dir, f"{trade_date}.json"), payload)
    _update_index(out_dir, trade_date, payload)

    print("-" * 68)
    print(f"[完成] 交易日 {trade_date}  扫描 {len(targets)}  命中 {len(all_sig)}"
          f"（股票 {len(stocks_sig)} / ETF {len(etf_sig)}，共振 {payload['stats']['resonance']}）")
    print(f"[完成] 耗时 {payload['elapsed_sec']}s  输出 {latest}")
    if reasons:
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:5]
        print("[跳过原因] " + "  ".join(f"{k}={v}" for k, v in top))
    return 0


def _lookup_meta(codes: List[str], cfg: dict) -> Dict[str, Dict]:
    """--codes 模式下，尽量从全市场快照里补出名称 / 现价 / 市盈率等信息。

    快照拉取失败（弱网 / 接口抖动）不影响主流程，退化为只显示代码。
    """
    want = {c for c in codes}
    meta: Dict[str, Dict] = {}
    try:
        for row in ds.get_stock_universe(
            exclude_st=False, exclude_bse=False, require_positive_pe=False
        ):
            if row["code"] in want:
                meta[row["code"]] = row
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 股票快照获取失败，--codes 将只显示代码：{e}")
    try:
        for row in ds.get_etf_universe():
            if row["code"] in want:
                meta[row["code"]] = row
    except Exception as e:  # noqa: BLE001
        print(f"[warn] ETF 快照获取失败：{e}")

    # 兜底：至少把名称补上，好过页面上只显示一串数字
    missing = want - set(meta)
    if missing:
        names = ds.get_code_name_map()
        for c in missing:
            if c in names:
                meta.setdefault(c, {})
                meta[c].setdefault("name", names[c])
    return meta


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _update_index(out_dir: str, trade_date: str, payload: dict) -> None:
    idx_path = os.path.join(out_dir, "index.json")
    idx = {"dates": [], "updated_at": ""}
    if os.path.exists(idx_path):
        try:
            with open(idx_path, "r", encoding="utf-8") as f:
                idx = json.load(f)
        except Exception:  # noqa: BLE001
            pass
    dates = set(idx.get("dates") or [])
    dates.add(trade_date)
    idx = {
        "dates": sorted(dates, reverse=True),
        "updated_at": payload["generated_at"],
        "latest": {
            "trade_date": trade_date,
            "matched": payload["stats"]["matched"],
            "scanned": payload["stats"]["scanned"],
        },
    }
    _write_json(idx_path, idx)


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="A股/ETF 缠论多级别买点盘后筛选")
    ap.add_argument("--config", help="配置文件路径 (config.yaml)")
    ap.add_argument("--out", help="输出目录，默认 <项目>/data")
    ap.add_argument("--limit", type=int, default=0, help="只扫描前 N 只（调试用）")
    ap.add_argument(
        "--sample",
        type=int,
        default=0,
        help="从股票池中均匀抽样 N 只（覆盖各板块，适合全市场跑不动时的快速抽样）",
    )
    ap.add_argument("--codes", help="指定标的，逗号分隔，如 600519,000001,510300")
    ap.add_argument("--no-etf", action="store_true", help="跳过 ETF")
    ap.add_argument("--no-cache", action="store_true", help="禁用分钟K线磁盘缓存")
    ap.add_argument(
        "--cache-day",
        default="",
        help="指定缓存目录名（如 2026-10-08），用于复用历史缓存离线复算",
    )
    ap.add_argument("--concurrency", type=int, default=0, help="并发线程数")
    args = ap.parse_args()

    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n[中断]")
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"[致命错误] {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
