# 缠论多级别买点选股 · A股 / ETF 盘后自动筛选

每天收盘后自动跑一遍全市场，挑出**同时满足基本面过滤 + 缠论多级别买点**的股票和 ETF，  
结果输出成静态 JSON，由 GitHub Pages 托管的网页展示。全程云端运行，不需要本地电脑开机。

---

## 一、筛选条件

| 维度      | 规则                                                     |
| ------- | ------------------------------------------------------ |
| 基本面（股票） | 动态市盈率 **> 0**；**非 ST**（含 *ST、退市整理）                     |
| 板块（股票）  | **排除北交所**（43x / 83x / 87x / 88x / 920），仅保留沪深主板、创业板、科创板 |
| 技术面     | **30 分钟**出现缠论**二买或三买**，**5 分钟**出现缠论**二买或三买**           |
| 两级别关系   | 网页可切换：**共振（30分 且 5分）** ← 默认 ／ **任一满足**                 |
| ETF     | 无市盈率概念，跳过 PE 条件，仅做缠论结构判断；同类型仅保留得分最高的一只                 |

### 买点定义（本项目的实现口径）

- **三买**：中枢形成后，一笔**向上离开**中枢（终点 > 中枢上沿 ZG），  
  随后的回调笔**不跌回中枢**（低点 > ZG）。该回调笔终点即三买。
- **二买**：向下**离开中枢创出阶段低点**（或同时出现 MACD 背驰的一买）后，  
  反弹一笔、再回调一笔，**回调不破前低**。该回调笔终点即二买。
- **有效期**：买点需在时效内（30 分钟 ≤ 16 根、5 分钟 ≤ 48 根），  
  且最新价未跌破买点价位 0.5%，否则视为信号失效。

> 买点由分型确认，而分型需要右侧一根 K 线才能成立，因此信号**天然滞后 1 根 K 线**。  
> 这是盘后选股的合理口径 —— 我们要的是"今天收盘刚确认"的买点，不是预测。

---

## 二、目录结构

```
chan-screener/
├── index.html                    网页（单页，纯静态）
├── serve.py                      本地预览服务器
├── assets/
│   ├── style.css                 样式（响应式，移动端为卡片视图）
│   └── app.js                    前端逻辑：过滤 / 排序 / 详情抽屉
├── data/
│   ├── latest.json               最新一期筛选结果 ← 网页读这个
│   ├── index.json                可用交易日列表
│   └── history/<日期>.json       历史每日结果
├── scripts/
│   ├── chan.py                   ★ 缠论引擎：包含处理→分型→笔→中枢→买点
│   ├── datasource.py             AkShare 数据源封装（重试 / 并发 / 缓存）
│   ├── screener.py               ★ 主筛选脚本
│   ├── selftest.py               引擎自检（构造形态验证 2买 / 3买）
│   └── make_demo_data.py         示例数据生成器（仅首次预览用）
├── config.yaml                   ★ 参数配置
├── requirements.txt
└── .github/workflows/daily-screen.yml   ★ 云端自动化
```

---

## 三、本地跑一遍

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 引擎自检（几秒钟，确认缠论逻辑正常）
python scripts/selftest.py

# 3. 全市场筛选（首次约 8~15 分钟，取决于网络）
python scripts/screener.py

# 4. 起本地服务器看效果
python serve.py            # 自动打开 http://localhost:8000
```

调参与调试常用参数：

```bash
python scripts/screener.py --limit 200              # 只扫前 200 只，快速验证
python scripts/screener.py --sample 900             # 全市场均匀抽样 900 只（推荐调试用）
python scripts/screener.py --codes 600519,000001,510300   # 只跑指定标的
python scripts/screener.py --no-etf                 # 跳过 ETF
python scripts/screener.py --concurrency 16         # 加大并发
python scripts/screener.py --no-cache               # 忽略分钟线缓存
```

> `--limit` 是截取排序后的前 N 只，代码会清一色集中在 `00` 开头（深市主板），  
> 排查问题时更推荐 `--sample N` —— 它按等间隔抽样，沪深主板 / 创业板 / 科创板都能覆盖到。

---

## 四、部署到 GitHub（云端自动化）

### 1. 建仓库并推送

把 `chan-screener` 目录**作为仓库根目录**推送（Pages 要直接托管根目录）：

```bash
cd chan-screener
git init
git add .
git commit -m "feat: 缠论多级别买点盘后自动筛选"
git branch -M main
git remote add origin https://github.com/<你的用户名>/chan-screener.git
git push -u origin main
```

### 2. 开启 GitHub Pages

`Settings` → `Pages` → Source 选 **Deploy from a branch** →  
Branch 选 **main**、目录选 **/ (root)** → Save。

一两分钟后访问 `https://<你的用户名>.github.io/chan-screener/`。

### 3. 自动化已就绪

`.github/workflows/daily-screen.yml` 已配置：

- **触发时间**：每周一至周五 **北京时间 15:40**（UTC 07:40），A 股收盘后 40 分钟
- **动作**：跑筛选 → 把结果写回 `data/` → 提交 → Pages 自动更新
- **手动触发**：`Actions` → `盘后缠论选股` → `Run workflow`，  
  可填 `limit` / `codes` 做单次调试

> ⚠️ **重要**：GitHub 的定时任务在仓库**连续 60 天没有任何提交**时会被自动暂停。  
> 本工作流每个交易日都会提交数据，正常情况下不会触发。  
> 如果长时间休市（如春节长假），回来后在 Actions 页面点一次 `Enable workflow` 即可。

### 4. 时区与交易日说明

- cron 用 UTC，脚本内部用北京时间取数；
- 遇节假日非交易日时，接口返回的最新 K 线仍是上一交易日，  
  脚本会自动把结果的 `trade_date` 校准为**真实最后一个交易日**，不会产生假数据。

---

## 五、参数怎么调

全部集中在 `config.yaml`，改完提交即可生效：

| 参数                                 | 作用                  | 建议                                |
| ---------------------------------- | ------------------- | --------------------------------- |
| `universe.require_positive_pe`     | 动态市盈率必须为正           | 需求要求，保持 `true`                    |
| `universe.min_amount`              | 成交额下限（元）            | 想剔除僵尸股可设 `30000000`（3000万）        |
| `universe.min_total_mv`            | 总市值下限（元）            | 想只看大中盘可设 `2000000000`（20亿）        |
| `levels.m30.max_bars_ago`          | 30 分钟买点有效期（K线根数）    | 调大 → 信号更多但更旧；默认 16 ≈ 2 个交易日       |
| `levels.m5.max_bars_ago`           | 5 分钟买点有效期           | 默认 48 ≈ 1 个交易日                    |
| `levels.*.min_gap`                 | 笔的最小分型间隔            | `4` = 新笔标准（默认）；`5` = 老笔标准，更严格     |
| `levels.*.beichi_ratio`            | 背驰阈值（本笔动能 / 前同向笔动能） | 调小更严格，默认 `0.85`                   |
| `levels.*.require_beichi_for_buy2` | 二买是否必须由 MACD 背驰确认   | 默认 `false`（宽松）；改 `true` 信号大幅减少但更精 |
| `break_tolerance`                  | 允许跌破买点价位的比例         | 默认 `0.005`（0.5%）                  |
| `concurrency`                      | 并发线程数               | 本地 8~16；Actions 上 8 较稳            |

### 信号太少的调参顺序

1. 先把 `max_bars_ago` 调大（30 分钟 `16 → 32`，5 分钟 `48 → 96`）—— 放宽有效期，见效最快；
2. 再看 `require_beichi_for_buy2` 是否被设成了 `true`，改回 `false`；
3. 还嫌少就把 `min_gap` 从 `4` 降到 `3`（放宽笔的定义，5 分钟级别效果明显）；
4. **不建议**动 `beichi_ratio` 来放量，它对信号数量的影响远小于前几项，却最容易引入噪声。

---

## 六、数据源

全部来自 **AkShare** 的东方财富接口：

| 用途         | 接口                       |
| ---------- | ------------------------ |
| A 股全市场快照   | `stock_zh_a_spot_em`     |
| ETF 全市场快照  | `fund_etf_spot_em`       |
| 股票分钟 K 线   | `stock_zh_a_hist_min_em` |
| ETF 分钟 K 线 | `fund_etf_hist_min_em`   |

K 线统一使用**前复权**（`adjust="qfq"`）。所有请求带指数退避重试，  
分钟线按"交易日 + 标的 + 周期"落盘缓存到 `.cache/`（不入库），当天重复运行不必重拉。

### 一个容易被忽略的坑：AkShare 的翻页接口

`stock_zh_a_spot_em`（A 股快照）内部会**自己翻 59 页**（5572 只 ÷ 100），  
而它翻页用的是 `requests.Session().get`，**不是**模块级的 `requests.get`。

这意味着：只在业务代码外面包一层 `retry()` 是没用的 ——  
只要 59 页里任意一页偶发断连，整个函数直接抛异常，前面 58 页的数据全部作废。

因此 `datasource.install_http_retry()` 会在进程启动时同时给  
`requests.get` / `requests.post` / `Session.get` / `Session.post` 装上退避重试，  
一次性覆盖 AkShare 全部接口。这是本项目和"随手调 akshare"最大的工程区别。

（可通过环境变量 `CHAN_HTTP_TRIES` 调整重试次数，默认 6。）

---

## 七、自定义功能说明

**为什么不做线段划分？** 缠论完整的"笔 → 线段 → 中枢"递归里，线段划分极度依赖  
特征序列的处理细节，不同实现差异很大，且对二买/三买的判定影响极小  
（2/3 买只依赖"笔的离开 + 笔的回抽"）。因此本引擎在笔这一层直接衔接中枢，  
换来的是**结果可复现、逻辑可审计**。

**评分怎么算的？** 买点类型（三买 > 二买）+ 级别权重（30分 > 5分）

- 双级别共振加分 + 信号新鲜度 + 市盈率合理性，满分 100。  
  分数衡量的是"结构完整度 + 信号新鲜度"，**不代表上涨概率**。

---

## 八、免责声明

本项目为技术与量化研究工具，所有输出由程序自动生成，  
**仅供研究参考，不构成任何投资建议**。据此操作，风险自负。

---

## 附：常见问题

**Q：本地跑的时候大量报 `ProxyError` / 请求失败怎么办？**  
说明本机走了代理，东财域名被代理间歇性拦截。这不影响 GitHub Actions 上的运行  
（云端 runner 直连，无代理）。本地想跑也可以通过设置环境变量绕过：  
`export NO_PROXY=*.eastmoney.com`（Linux/macOS）或  
`set NO_PROXY=*.eastmoney.com`（Windows）。

**Q：全市场跑一次要多久？**  
本地（8 并发）约 25~~40 分钟：5572 只股票快照翻页本身就要 1~~2 分钟，  
之后每只标的要拉 2 次分钟线。GitHub Actions 上更快。  
先用 `--limit 200` 验证链路，确认没问题再跑全量。

**Q：为什么结果里有些票的"现价"是 0？**  
只在使用 `--codes` 且全市场快照拉取失败时才会出现（拿不到报价）。  
正式全市场运行时不会。

**Q：想加"日线级别买点"怎么改？**  
在 `config.yaml` 的 `levels` 下加一个 `d1`（`period` 用 `daily`），  
在 `screener.analyze_one` 里照 m30 的写法复制一段即可；  
前端会自动多出一栏，只需在 `assets/app.js` 的 `levelBox` 调用处补一行。
