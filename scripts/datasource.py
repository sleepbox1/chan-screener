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
# 为什么自己翻页而不用 ak.stock_zh_a_spot_em()：
#   1. akshare 的分页实现一页失败就整体重来，对外只有一个"成功/异常"，
#      在云主机 IP 被东财间歇限流时会重试几十分钟然后全部作废；
#   2. 自研翻页能做到：每页独立重试、页间限速、进度可见、断点续传式重试，
#      整体成功率远高于"59 页连跪一页就清零"。
_CLIST_HOSTS = (
    "https://82.push2.eastmoney.com",
    "https://push2.eastmoney.com",
    "https://1.push2.eastmoney.com",
)

_SPOT_FIELDS = "f12,f14,f2,f3,f9,f23,f8,f6,f20,f21"
_SPOT_KEYMAP = {
    "f12": "代码", "f14": "名称", "f2": "最新价", "f3": "涨跌幅",
    "f9": "市盈率-动态", "f23": "市净率", "f8": "换手率",
    "f6": "成交额", "f20": "总市值", "f21": "流通市值",
}


def _log(msg: str) -> None:
    """带时间戳的进度输出（时间戳是排查 Actions 上卡在哪一步的关键）。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _fetch_spot_pages(fs: str, label: str, page_size: int = 100) -> List[Dict]:
    """直接调东财 clist 接口翻页拉全量快照。

    每页独立重试（互不影响），页间限速。任何一页重试用尽则抛 DataFetchError，
    由调用方决定是否回退到 akshare。
    """
    import requests

    rows: List[Dict] = []
    total = None
    pn = 1
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    while True:
        params = {
            "pn": pn, "pz": page_size, "po": 1, "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2, "invt": 2, "fid": "f12",
            "fs": fs, "fields": _SPOT_FIELDS,
        }
        data = None
        last_err: Optional[Exception] = None
        for attempt in range(8):
            host = _CLIST_HOSTS[attempt % len(_CLIST_HOSTS)]
            try:
                r = session.get(f"{host}/api/qt/clist/get", params=params, timeout=15)
                j = r.json()
                data = (j.get("data") or {}).get("diff")
                if data is not None:
                    last_err = None
                    break
            except Exception as e:  # noqa: BLE001
                last_err = e
            time.sleep(0.5 * (2 ** min(attempt, 4)) + random.uniform(0, 0.4))

        if data is None:
            raise DataFetchError(
                f"{label} 第 {pn} 页重试 8 次仍失败: {type(last_err).__name__}: {last_err}"
            )

        if not data:  # 翻到底了
            break

        for d in data:
            row = {}
            for k, cn in _SPOT_KEYMAP.items():
                v = d.get(k)
                row[cn] = None if v in (None, "-", "") else v
            rows.append(row)

        if total is None:
            total = int(d.get("total") or 0) if isinstance(d, dict) else 0
        if pn % 10 == 0 or (total and pn * page_size >= total):
            got = len(rows)
            _log(f"  {label} 翻页 {pn} 页，累计 {got}/{total or '?'} 条")
        if total and len(rows) >= total:
            break
        pn += 1
        time.sleep(0.25)  # 页间限速，降低触发风控概率

    return rows


_STOCK_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
_ETF_FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827"

# --------------------------------------------------------------------------- #
# 数据源熔断
# --------------------------------------------------------------------------- #
# 东财对云主机/高频 IP 会直接拒连（Connection reset）。一旦发现它不可用，
# 必须尽快切换并**停止继续尝试**，否则每个标的都要白等一轮重试，
# 全市场5000只标的会因此多花几小时。
_EM_FAILS = 0
_EM_DISABLED = False
_EM_FAIL_THRESHOLD = 2


def em_available() -> bool:
    return not _EM_DISABLED


def _em_mark(ok: bool) -> None:
    global _EM_FAILS, _EM_DISABLED
    if ok:
        _EM_FAILS = 0
        return
    _EM_FAILS += 1
    if _EM_FAILS >= _EM_FAIL_THRESHOLD and not _EM_DISABLED:
        _EM_DISABLED = True
        _log(f"  东财连续失败 {_EM_FAILS} 次，本次运行内停用东财，改用备用数据源")


def probe_eastmoney() -> bool:
    """启动时探一次东财分钟线，不可用就直接熔断。

    必须在正式扫描前调用：否则每个标的都要先白等一轮东财重试
    （约 10~30 秒/只），全市场跑一遍会多花好几个小时。
    """
    if _EM_DISABLED:
        return False
    import requests

    try:
        r = requests.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "fields1": "f1,f2", "fields2": "f51,f52,f53",
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "klt": "30", "fqt": "1", "secid": "1.600519",
                "beg": "0", "end": "20500000",
            },
            timeout=10,
        )
        ok = bool((r.json().get("data") or {}).get("klines"))
    except Exception:  # noqa: BLE001
        ok = False
    if ok:
        _em_mark(True)
        _log("  东财通道探测：可用（走前复权行情）")
    else:
        _em_mark(False)
        _EM_DISABLED_force()
        _log("  东财通道探测：不可用 -> 使用腾讯/新浪备用通道（不复权行情）")
    return ok


def _EM_DISABLED_force() -> None:
    global _EM_DISABLED
    _EM_DISABLED = True


_SINA_SPOT_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_SINA_STOCK_NODE = "hs_a"
_SINA_ETF_NODE = "etf_hq_fund"


def _fetch_sina_spot(node: str, label: str, page_size: int = 100) -> List[Dict]:
    """新浪全市场列表（含市盈率 per 字段），作为东财快照的备用通道。"""
    import requests

    rows: List[Dict] = []
    pn = 1
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://finance.sina.com.cn",
        }
    )
    while True:
        params = {
            "page": pn, "num": page_size, "sort": "symbol", "asc": 1,
            "node": node, "symbol": "", "_s_r_a": "page",
        }
        data = None
        last_err: Optional[Exception] = None
        for attempt in range(6):
            try:
                r = session.get(_SINA_SPOT_URL, params=params, timeout=15)
                txt = r.text.strip()
                if txt.startswith("["):
                    data = json.loads(txt)
                    break
                last_err = RuntimeError(txt[:80])
            except Exception as e:  # noqa: BLE001
                last_err = e
            time.sleep(0.5 * (2 ** min(attempt, 3)) + random.uniform(0, 0.3))

        if data is None:
            raise DataFetchError(
                f"{label} 新浪第 {pn} 页失败: {type(last_err).__name__}: {last_err}"
            )
        if not data:
            break

        for d in data:
            rows.append(
                {
                    "代码": d.get("code"),
                    "名称": d.get("name"),
                    "最新价": d.get("trade"),
                    "涨跌幅": d.get("changepercent"),
                    "市盈率-动态": d.get("per"),
                    "市净率": d.get("pb"),
                    "换手率": d.get("turnoverratio"),
                    "成交额": d.get("amount"),
                    "总市值": d.get("mktcap"),
                    "流通市值": d.get("nmc"),
                }
            )
        if len(data) < page_size:
            break
        pn += 1
        time.sleep(0.2)
        if pn % 10 == 0:
            _log(f"  {label} 新浪翻页 {pn} 页，累计 {len(rows)} 条")
    return rows


def _spot_df(
    fs: str,
    label: str,
    akshare_fn,
    akshare_name: str,
    sina_node: str,
) -> "pd.DataFrame":
    """快照三级降级：东财 clist -> 新浪列表 -> akshare。

    任一层成功即返回；东财被熔断后直接跳过，避免每只标的都白等重试。
    """
    if em_available():
        try:
            rows = _fetch_spot_pages(fs, label)
            if rows:
                _em_mark(True)
                _log(f"  {label} 东财直连完成，共 {len(rows)} 条")
                return pd.DataFrame(rows)
            _em_mark(False)
        except Exception as e:  # noqa: BLE001
            _em_mark(False)
            _log(f"  {label} 东财通道失败（{type(e).__name__}: {e}）")

    try:
        rows = _fetch_sina_spot(sina_node, label)
        if rows:
            _log(f"  {label} 新浪通道完成，共 {len(rows)} 条")
            return pd.DataFrame(rows)
    except Exception as e:  # noqa: BLE001
        _log(f"  {label} 新浪通道失败（{type(e).__name__}: {e}）")

    _log(f"  {label} 回退 akshare({akshare_name})")
    return retry(akshare_fn, tries=2, quiet=False)


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

    df = _spot_df(
        _STOCK_FS, "A股快照", ak.stock_zh_a_spot_em, "stock_zh_a_spot_em", _SINA_STOCK_NODE
    )
    if df is None or df.empty:
        raise RuntimeError("A股快照三个通道都返回空，可能是接口变更或网络受限")

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

    df = _spot_df(
        _ETF_FS, "ETF快照", ak.fund_etf_spot_em, "fund_etf_spot_em", _SINA_ETF_NODE
    )
    if df is None or df.empty:
        raise RuntimeError("ETF快照三个通道都返回空")

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


_TX_SYMBOL_MEMO: Dict[str, str] = {}


def to_tx_symbol(code: str) -> str:
    """把 6 位代码转成腾讯/新浪的带市场前缀代码（sh600519 / sz000001）。

    5 开头是沪市 ETF、1 开头是深市 ETF，其余按首位数字判断：
    6/9 -> 沪，0/3 -> 深。
    """
    code = str(code).zfill(6)
    if code in _TX_SYMBOL_MEMO:
        return _TX_SYMBOL_MEMO[code]
    if code.startswith(("6", "9")) or (code.startswith("5") and not code.startswith("159")):
        sym = "sh" + code
    else:
        sym = "sz" + code
    _TX_SYMBOL_MEMO[code] = sym
    return sym


def _tx_min_bars(code: str, period: str, start: str, end: str) -> List[RawBar]:
    """腾讯分钟 K 线通道（备用一）。

    返回格式：[时间(yyyymmddHHMM), 开, 收, 高, 低, 成交量(手), ...]
    """
    import requests

    sym = to_tx_symbol(code)
    r = requests.get(
        "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
        params={"param": f"{sym},m{period},,800"},
        timeout=15,
    )
    data = (r.json().get("data") or {}).get(sym) or {}
    arr = data.get(f"m{period}") or []
    bars: List[RawBar] = []
    for row in arr:
        if not row or len(row) < 6:
            continue
        dt = str(row[0])
        if len(dt) != 12:
            continue
        s = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} {dt[8:10]}:{dt[10:12]}:00"
        if s < start or s > end:
            continue
        try:
            bars.append(
                RawBar(
                    idx=len(bars),
                    dt=s,
                    open=float(row[1]),
                    high=float(row[3]),
                    low=float(row[4]),
                    close=float(row[2]),
                    volume=float(row[5] or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return bars


def _sina_min_bars(code: str, period: str, start: str, end: str) -> List[RawBar]:
    """新浪分钟 K 线通道（备用二）。历史比腾讯长（最多约 1023 根）。"""
    import requests

    sym = to_tx_symbol(code)
    r = requests.get(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var_/CN_MarketDataService.getKLineData",
        params={"symbol": sym, "scale": period, "ma": "no", "datalen": 1023},
        timeout=20,
    )
    txt = r.text
    lo, hi = txt.find("(["), txt.rfind("])")
    if lo < 0 or hi < 0:
        return []
    try:
        arr = json.loads(txt[lo + 1 : hi + 1])
    except Exception:  # noqa: BLE001
        return []

    bars: List[RawBar] = []
    for d in arr:
        s = str(d.get("day") or "")
        if not s:
            continue
        if len(s) == 16:                     # '2026-10-08 14:30'
            s = s + ":00"
        if s < start or s > end:
            continue
        try:
            bars.append(
                RawBar(
                    idx=len(bars),
                    dt=s,
                    open=float(d["open"]),
                    high=float(d["high"]),
                    low=float(d["low"]),
                    close=float(d["close"]),
                    volume=float(d.get("volume") or 0),
                )
            )
        except (TypeError, ValueError, KeyError):
            continue
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
    """拉取分钟 K 线，三级降级：东财 -> 腾讯 -> 新浪。

    period: '5' / '15' / '30' / '60'
    start/end 形如 '2026-07-01 09:30:00'

    注意：东财走前复权（qfq），腾讯/新浪的分钟线是**不复权**行情。
    备用通道只在东财不可用时启用，除权日附近的结构会有轻微差异。
    """
    if cache is not None:
        cached = cache.get(cache_day, f"{'etf' if is_etf else 'stk'}_{code}", period)
        if cached:
            return cached

    import akshare as ak

    bars: List[RawBar] = []

    # 通道一：东财（akshare）
    if em_available():
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

        df = retry(_fetch, tries=1)   # 重试交给上面的熔断器，这里只试一次
        if df is not None:
            _em_mark(True)
            bars = _df_to_bars(df) if not df.empty else []
        else:
            _em_mark(False)

    # 通道二：腾讯
    if not bars:
        bars = retry(lambda: _tx_min_bars(code, period, start, end), tries=3) or []

    # 通道三：新浪
    if not bars:
        bars = retry(lambda: _sina_min_bars(code, period, start, end), tries=2) or []

    if not bars:
        # 与"该标的没有数据"区分开：这样上层能看出到底是接口挂了还是标的本身无数据
        raise DataFetchError(f"{code} {period}分钟 K线三个通道均无数据")

    # 重排 idx，保证连续
    for i, b in enumerate(bars):
        b.idx = i

    if cache is not None and bars:
        cache.put(cache_day, f"{'etf' if is_etf else 'stk'}_{code}", period, bars)
    return bars
