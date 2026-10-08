# -*- coding: utf-8 -*-
"""
数据源封装层
============

统一封装 AkShare 的东方财富接口，向上提供三件事：

    get_stock_universe()  -> 全市场 A 股快照 + 基础过滤
    get_etf_universe()    -> 全市场 ETF 快照
    get_min_bars()        -> 单标的分钟级 K 线

设计要点：
  * 所有网络调用都带指数退避重试，东财接口偶发超时是常态；
  * 分钟 K 线带本地磁盘缓存，同一天重复运行不会重复拉取；
  * 过滤规则集中在本模块，screener 只关心业务。
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import asdict
from typing import Callable, Dict, List, Optional, Sequence

import pandas as pd

from chan import RawBar

# --------------------------------------------------------------------------- #
# 过滤规则
# --------------------------------------------------------------------------- #

# 沪深 A 股：沪市主板 60x / 科创板 68x / 深市主板 00x / 创业板 30x
# 北交所：43x / 83x / 87x / 88x / 920 —— 一律排除
_MAIN_BOARD_PREFIX = ("60", "68", "00", "30")

# 名称里出现这些字样的一律排除（ST、*ST、退市整理）
_EXCLUDE_NAME_KEYWORDS = ("ST", "st", "退", "*")


def is_st(name: str) -> bool:
    if not isinstance(name, str):
        return True
    return any(k in name.upper() for k in ("ST", "退"))


def is_bse(code: str) -> bool:
    """是否为北交所标的。"""
    code = str(code).zfill(6)
    return not code.startswith(_MAIN_BOARD_PREFIX)


def market_of(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("688") or code.startswith("689"):
        return "科创板"
    if code.startswith("60"):
        return "沪市主板"
    if code.startswith("300") or code.startswith("301"):
        return "创业板"
    if code.startswith("00"):
        return "深市主板"
    if code.startswith(("5", "1")):
        return "ETF"
    return "其他"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _pick(df: pd.DataFrame, *names: str) -> Optional[str]:
    """在 DataFrame 中按候选列名找第一个存在的列（兼容 AkShare 版本差异）。"""
    for n in names:
        if n in df.columns:
            return n
    return None


def _num(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


class DataFetchError(RuntimeError):
    """分钟 K 线多次重试后仍取不到数据。"""


# --------------------------------------------------------------------------- #
# 全局 HTTP 重试垫片
# --------------------------------------------------------------------------- #
# 为什么需要它：
#   AkShare 的 `stock_zh_a_spot_em` 之类的接口内部会**自己翻几十页**
#   （A 股快照 5572 只 ÷ 100 = 59 页），而它内部是裸的 `requests.get`。
#   只要其中任意一页偶发断连，整个函数就抛异常，前面 58 页白拿。
#   这里在进程启动时装一层重试，等于给 AkShare 所有接口都加上了容错，
#   比在每个调用点外面包 retry 有效得多。
_HTTP_TRIES = int(os.environ.get("CHAN_HTTP_TRIES", "6"))
_http_patched = False


def install_http_retry(tries: int = _HTTP_TRIES) -> None:
    """给 requests.get 与 Session.get/post 装一层指数退避重试。幂等。

    必须同时打这两个入口，缺一不可：
      * `stock_zh_a_hist_min_em` 这类接口用的是模块级 `requests.get`；
      * 而 `fetch_paginated_data`（快照接口的分页实现）用的是
        `requests.Session().get` —— 只补 `requests.get` 对它完全无效。
    只补其中一个，都会出现"分钟线正常但快照翻页必挂"的现象。
    """
    global _http_patched
    if _http_patched:
        return
    import requests

    def wrap(orig):
        def inner(*a, **kw):
            last = None
            for i in range(tries):
                try:
                    return orig(*a, **kw)
                except Exception as e:  # noqa: BLE001
                    last = e
                    if i < tries - 1:
                        time.sleep(0.4 * (2**i) + random.uniform(0, 0.3))
            raise last

        return inner

    requests.get = wrap(requests.get)
    requests.post = wrap(requests.post)
    requests.sessions.Session.get = wrap(requests.sessions.Session.get)
    requests.sessions.Session.post = wrap(requests.sessions.Session.post)
    _http_patched = True


def retry(fn: Callable, tries: int = 4, base: float = 0.6, quiet: bool = True):
    """指数退避重试。

    东财接口在高并发或弱网下会间歇性断连，退避 + 抖动可以显著提升成功率。
    quiet=True 时全部失败返回 None；quiet=False 时把最后一次异常抛出去。
    """
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - 数据源异常种类繁多
            last = e
            if i < tries - 1:
                time.sleep(base * (2**i) + random.uniform(0, 0.4))
    if not quiet:
        raise last
    return None




# --------------------------------------------------------------------------- #
# 快照：股票 / ETF
# --------------------------------------------------------------------------- #
def get_stock_universe(
    min_amount: float = 0.0,
    min_total_mv: float = 0.0,
    exclude_st: bool = True,
    exclude_bse: bool = True,
    require_positive_pe: bool = True,
) -> List[Dict]:
    """拉取全市场 A 股快照并按条件过滤。

    返回 [{code, name, price, change_pct, pe_dynamic, pb, turnover,
           amount, total_mv, float_mv, market}, ...]
    """
    import akshare as ak

    df = retry(lambda: ak.stock_zh_a_spot_em(), tries=4, quiet=False)
    if df is None or df.empty:
        raise RuntimeError("stock_zh_a_spot_em 返回空，可能是接口变更或网络受限")

    c_code = _pick(df, "代码")
    c_name = _pick(df, "名称")
    c_price = _pick(df, "最新价")
    c_chg = _pick(df, "涨跌幅")
    c_pe = _pick(df, "市盈率-动态", "动态市盈率", "市盈率")
    c_pb = _pick(df, "市净率")
    c_turn = _pick(df, "换手率")
    c_amt = _pick(df, "成交额")
    c_mv = _pick(df, "总市值")
    c_fmv = _pick(df, "流通市值")

    out: List[Dict] = []
    skipped = {"st": 0, "bse": 0, "pe": 0, "halt": 0, "amount": 0, "mv": 0}

    for _, r in df.iterrows():
        code = str(r[c_code]).zfill(6) if c_code else ""
        name = str(r[c_name]) if c_name else ""
        if not code or len(code) != 6 or not code.isdigit():
            continue

        if exclude_bse and is_bse(code):
            skipped["bse"] += 1
            continue
        if exclude_st and is_st(name):
            skipped["st"] += 1
            continue

        price = _num(r[c_price]) if c_price else None
        if price is None or price <= 0:          # 停牌 / 无报价
            skipped["halt"] += 1
            continue

        pe = _num(r[c_pe]) if c_pe else None
        if require_positive_pe and (pe is None or pe <= 0):
            skipped["pe"] += 1
            continue

        amount = _num(r[c_amt]) if c_amt else None
        if min_amount and (amount is None or amount < min_amount):
            skipped["amount"] += 1
            continue

        mv = _num(r[c_mv]) if c_mv else None
        if min_total_mv and (mv is None or mv < min_total_mv):
            skipped["mv"] += 1
            continue

        out.append(
            {
                "code": code,
                "name": name,
                "price": price,
                "change_pct": _num(r[c_chg]) if c_chg else None,
                "pe_dynamic": pe,
                "pb": _num(r[c_pb]) if c_pb else None,
                "turnover": _num(r[c_turn]) if c_turn else None,
                "amount": amount,
                "total_mv": mv,
                "float_mv": _num(r[c_fmv]) if c_fmv else None,
                "market": market_of(code),
            }
        )

    out.sort(key=lambda x: x["code"])
    return out


def get_code_name_map() -> Dict[str, str]:
    """全市场代码 -> 名称。轻量接口（约 18 页），作为快照失败时的兜底来源。"""
    import akshare as ak

    df = retry(lambda: ak.stock_info_a_code_name(), tries=3)
    if df is None or df.empty:
        return {}
    c_code = _pick(df, "code", "代码")
    c_name = _pick(df, "name", "名称")
    if not c_code or not c_name:
        return {}
    out: Dict[str, str] = {}
    for _, r in df.iterrows():
        code = str(r[c_code]).zfill(6)
        if len(code) == 6 and code.isdigit():
            out[code] = str(r[c_name]).strip()
    return out


def get_etf_universe(exclude_st: bool = True, min_amount: float = 0.0) -> List[Dict]:
    """拉取全市场 ETF 快照。ETF 没有市盈率概念，这里不做 PE 过滤。"""
    import akshare as ak

    df = retry(lambda: ak.fund_etf_spot_em(), tries=4, quiet=False)
    if df is None or df.empty:
        raise RuntimeError("fund_etf_spot_em 返回空")

    c_code = _pick(df, "代码")
    c_name = _pick(df, "名称")
    c_price = _pick(df, "最新价")
    c_chg = _pick(df, "涨跌幅")
    c_amt = _pick(df, "成交额")
    c_mv = _pick(df, "总市值", "流通市值")

    out: List[Dict] = []
    for _, r in df.iterrows():
        code = str(r[c_code]).zfill(6) if c_code else ""
        name = str(r[c_name]) if c_name else ""
        if not code or len(code) != 6 or not code.isdigit():
            continue
        price = _num(r[c_price]) if c_price else None
        if price is None or price <= 0:
            continue
        amount = _num(r[c_amt]) if c_amt else None
        if min_amount and (amount is None or amount < min_amount):
            continue

        out.append(
            {
                "code": code,
                "name": name,
                "price": price,
                "change_pct": _num(r[c_chg]) if c_chg else None,
                "pe_dynamic": None,      # ETF 无动态市盈率
                "pb": None,
                "turnover": None,
                "amount": amount,
                "total_mv": _num(r[c_mv]) if c_mv else None,
                "float_mv": None,
                "market": "ETF",
            }
        )

    out.sort(key=lambda x: x["code"])
    return out


# --------------------------------------------------------------------------- #
# 分钟 K 线
# --------------------------------------------------------------------------- #
class BarCache:
    """按 (交易日, 代码, 周期) 缓存分钟 K 线，避免重复请求。"""

    def __init__(self, root: Optional[str] = None):
        self.root = root
        if root:
            os.makedirs(root, exist_ok=True)

    def _path(self, day: str, code: str, period: str) -> str:
        return os.path.join(self.root, day, f"{code}_{period}.json")

    def get(self, day: str, code: str, period: str) -> Optional[List[RawBar]]:
        if not self.root:
            return None
        p = self._path(day, code, period)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [RawBar(**d) for d in data]
        except Exception:  # noqa: BLE001
            return None

    def put(self, day: str, code: str, period: str, bars: Sequence[RawBar]) -> None:
        if not self.root:
            return
        p = self._path(day, code, period)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump([asdict(b) for b in bars], f, ensure_ascii=False)
        os.replace(tmp, p)


def _df_to_bars(df: pd.DataFrame) -> List[RawBar]:
    c_dt = _pick(df, "时间", "日期")
    c_o = _pick(df, "开盘")
    c_h = _pick(df, "最高")
    c_l = _pick(df, "最低")
    c_c = _pick(df, "收盘")
    c_v = _pick(df, "成交量")
    if not all([c_dt, c_o, c_h, c_l, c_c]):
        return []

    bars: List[RawBar] = []
    for i, (_, r) in enumerate(df.iterrows()):
        o, h, l, c = _num(r[c_o]), _num(r[c_h]), _num(r[c_l]), _num(r[c_c])
        if None in (o, h, l, c):
            continue
        bars.append(
            RawBar(
                idx=len(bars),
                dt=str(r[c_dt]),
                open=o,
                high=h,
                low=l,
                close=c,
                volume=_num(r[c_v]) or 0.0 if c_v else 0.0,
            )
        )
    return bars


def get_min_bars(
    code: str,
    period: str,
    start: str,
    end: str,
    is_etf: bool = False,
    adjust: str = "qfq",
    cache: Optional[BarCache] = None,
    cache_day: str = "",
) -> List[RawBar]:
    """拉取分钟 K 线。

    period: '5' / '15' / '30' / '60'
    start/end 形如 '2026-07-01 09:30:00'
    """
    if cache is not None:
        cached = cache.get(cache_day, f"{'etf' if is_etf else 'stk'}_{code}", period)
        if cached:
            return cached

    import akshare as ak

    def _fetch():
        # 轻微抖动，避免多线程在同一毫秒齐发被对端限流
        time.sleep(random.uniform(0, 0.12))
        fn = ak.fund_etf_hist_min_em if is_etf else ak.stock_zh_a_hist_min_em
        return fn(
            symbol=code,
            period=period,
            adjust=adjust,
            start_date=start,
            end_date=end,
        )

    df = retry(_fetch, tries=5)
    if df is None:
        # 与"该标的没有数据"区分开：这样上层能看出到底是接口挂了还是标的本身无数据
        raise DataFetchError(f"{code} {period}分钟 K线获取失败（重试已用尽）")

    bars = _df_to_bars(df) if not df.empty else []

    # 重排 idx，保证连续
    for i, b in enumerate(bars):
        b.idx = i

    if cache is not None and bars:
        cache.put(cache_day, f"{'etf' if is_etf else 'stk'}_{code}", period, bars)
    return bars
