/* ============================================================
   缠论多级别买点选股 · 前端逻辑
   ------------------------------------------------------------
   数据来源：data/latest.json（最新一期）与 data/history/<日期>.json
   页面为纯静态，所有筛选/排序都在浏览器本地完成。
   ============================================================ */
'use strict';

const $ = (s) => document.querySelector(s);

const state = {
  dates: [],
  payload: null,
  mode: 'and',          // and = 30分钟 且 5分钟；or = 任一
  type: 'all',          // all | stock | etf
  kinds: new Set(['2', '3']),
  keyword: '',
  current: null,
};

/* ------------------------------------------------------------------ */
/* 工具函数                                                            */
/* ------------------------------------------------------------------ */
const fmt = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(d);

function fmtPct(v) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
}

function fmtAmount(v) {
  if (!v && v !== 0) return '—';
  const n = Number(v);
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿';
  if (n >= 1e4) return (n / 1e4).toFixed(0) + '万';
  return n.toFixed(0);
}

function fmtMV(v) {
  if (!v) return '—';
  const n = Number(v);
  if (n >= 1e12) return (n / 1e12).toFixed(2) + '万亿';
  if (n >= 1e8) return (n / 1e8).toFixed(1) + '亿';
  return (n / 1e4).toFixed(0) + '万';
}

function esc(s) {
  return String(s === null || s === undefined ? '' : s).replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );
}

function pctClass(v) {
  if (v === null || v === undefined) return 'flat';
  const n = Number(v);
  if (n > 0.001) return 'up';
  if (n < -0.001) return 'down';
  return 'flat';
}

function scoreColor(s) {
  if (s >= 85) return '#e0342a';
  if (s >= 75) return '#e07a1f';
  if (s >= 65) return '#c2760a';
  return '#5b7cc4';
}

/* ------------------------------------------------------------------ */
/* 过滤逻辑                                                            */
/* ------------------------------------------------------------------ */
function inKinds(sig) {
  const ks = [];
  if (sig.m30) ks.push(sig.m30.kind);
  if (sig.m5) ks.push(sig.m5.kind);
  if (!ks.length) return false;
  // 命中集合中任意一个买点类型即可
  return ks.some((k) => state.kinds.has(k));
}

function passesMode(sig) {
  if (state.mode === 'and') return !!(sig.m30 && sig.m5);
  return !!(sig.m30 || sig.m5);
}

function filtered() {
  if (!state.payload) return [];
  const kw = state.keyword.trim().toUpperCase();
  return (state.payload.signals || []).filter((s) => {
    if (!passesMode(s)) return false;
    if (!inKinds(s)) return false;
    if (state.type !== 'all' && s.type !== state.type) return false;
    if (kw && !(s.code.includes(kw) || (s.name || '').toUpperCase().includes(kw))) return false;
    return true;
  });
}

/* ------------------------------------------------------------------ */
/* 渲染                                                                */
/* ------------------------------------------------------------------ */
function renderStats(rows) {
  const p = state.payload || {};
  const st = p.stats || {};
  const resonance = rows.filter((r) => r.m30 && r.m5).length;
  const universe = (st.stocks || 0) + (st.etfs || 0);
  const cards = [
    { k: '本次扫描', v: st.scanned ?? '—', s: '只' },
    { k: '当前命中', v: rows.length, s: '只', cls: 'accent' },
    { k: '其中股票', v: rows.filter((r) => r.type === 'stock').length, s: '只' },
    { k: '其中 ETF', v: rows.filter((r) => r.type === 'etf').length, s: '只', cls: 'violet' },
    { k: '双级别共振', v: resonance, s: '只' },
    {
      k: '基础池规模',
      v: universe || (st.matched ?? '—'),
      s: universe ? '只' : '命中',
    },
  ];
  $('#stats').innerHTML = cards
    .map(
      (c) => `<div class="stat ${c.cls || ''}">
        <div class="stat-k">${c.k}</div>
        <div class="stat-v">${esc(c.v)}<small>${c.s}</small></div>
      </div>`
    )
    .join('');
}

function bpBadge(lv, label) {
  if (!lv) return `<span class="bp none">${label} 无</span>`;
  const cls = lv.kind === '3' ? 'b3' : 'b2';
  return `<span class="bp ${cls}">${lv.label} · ${fmt(lv.price, 2)}</span>`;
}

function renderTable(rows) {
  $('#tbody').innerHTML = rows
    .map((s, i) => {
      const tag = s.type === 'etf' ? '<span class="tag etf">ETF</span>' : `<span class="tag">${esc(s.market)}</span>`;
      const sc = scoreColor(s.score);
      return `<tr data-code="${esc(s.code)}">
        <td class="cell-code">${i + 1}</td>
        <td>
          <div class="cell-name">${esc(s.name)}${tag}</div>
          <div class="cell-code">${esc(s.code)}</div>
        </td>
        <td class="num">${fmt(s.price, 2)}</td>
        <td class="num ${pctClass(s.change_pct)}">${fmtPct(s.change_pct)}</td>
        <td class="num">${s.pe_dynamic ? fmt(s.pe_dynamic, 1) : '—'}</td>
        <td>${bpBadge(s.m30, '30分')}</td>
        <td>${bpBadge(s.m5, '5分')}</td>
        <td>
          <div class="score-cell">
            <span class="score-num" style="color:${sc}">${s.score}</span>
            <span class="score-bar"><i style="width:${Math.min(100, s.score)}%;background:${sc}"></i></span>
          </div>
        </td>
      </tr>`;
    })
    .join('');

  $('#tbody').querySelectorAll('tr').forEach((tr) =>
    tr.addEventListener('click', () => openDrawer(tr.dataset.code))
  );
}

function renderCards(rows) {
  $('#cards').innerHTML = rows
    .map((s) => {
      const sc = scoreColor(s.score);
      const tag = s.type === 'etf' ? '<span class="tag etf">ETF</span>' : '';
      return `<div class="sig-card" data-code="${esc(s.code)}">
        <div class="row1">
          <span class="nm">${esc(s.name)}</span>
          <span class="cd">${esc(s.code)}${s.change_pct !== null && s.change_pct !== undefined
            ? ` · <span class="${pctClass(s.change_pct)}" style="color:${s.change_pct > 0 ? 'var(--up)' : s.change_pct < 0 ? 'var(--down)' : 'var(--text-3)'}">${fmtPct(s.change_pct)}</span>`
            : ''}</span>
          ${tag}
          <span class="px">${fmt(s.price, 2)}</span>
        </div>
        <div class="row2">${bpBadge(s.m30, '30分')}${bpBadge(s.m5, '5分')}</div>
        <div class="row3">
          <span>动态PE <b>${s.pe_dynamic ? fmt(s.pe_dynamic, 1) : '—'}</b></span>
          <span>评分 <b style="color:${sc}">${s.score}</b></span>
          <span style="margin-left:auto">${s.tags.join(' · ')}</span>
        </div>
      </div>`;
    })
    .join('');

  $('#cards').querySelectorAll('.sig-card').forEach((el) =>
    el.addEventListener('click', () => openDrawer(el.dataset.code))
  );
}

function emptyState(rows) {
  const el = $('#empty');
  if (rows.length) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  const total = (state.payload && state.payload.signals || []).length;
  let html;
  if (!state.payload) {
    html = `<div class="big">还没有筛选数据</div>
      <p>在项目目录执行一次 <code>python scripts/screener.py</code>，<br>
      或推送到 GitHub 后等待 Actions 盘后自动运行。</p>`;
  } else if (total === 0) {
    html = `<div class="big">该交易日没有符合条件的标的</div>
      <p>全市场扫描完成，暂无同时满足缠论买点条件的品种。<br>换个交易日看看，或放宽级别逻辑为「任一满足」。</p>`;
  } else {
    html = `<div class="big">当前筛选条件下没有结果</div>
      <p>市场共命中 ${total} 只，但没有符合当前级别逻辑 / 买点类型 / 关键词的组合。<br>试试切换为「任一满足」。</p>`;
  }
  el.innerHTML = html;
}

function render() {
  const rows = filtered();
  renderStats(rows);
  renderTable(rows);
  renderCards(rows);
  emptyState(rows);
  const p = state.payload || {};
  $('#resultTitle').textContent = '筛选结果';
  $('#resultSub').textContent = p.trade_date ? `交易日 ${p.trade_date}` : '';
  $('#updatedAt').textContent = p.generated_at ? `更新于 ${p.generated_at}` : '加载中…';

  const banner = $('#demoBanner');
  if (p.demo) {
    banner.hidden = false;
    banner.innerHTML =
      '<span class="ic">⚠</span><div><b>当前展示的是示例数据</b>，仅用于预览页面效果。' +
      '在项目目录执行 <code>python scripts/screener.py</code>，或推送到 GitHub 等待 Actions 盘后自动运行，' +
      '结果会覆盖 <code>data/latest.json</code>。</div>';
  } else {
    banner.hidden = true;
  }
}

/* ------------------------------------------------------------------ */
/* 详情抽屉                                                            */
/* ------------------------------------------------------------------ */
function zsBar(level) {
  const zd = level.zs_zd, zg = level.zs_zg, buy = level.price, now = state.current?.price;
  if (zd === null || zd === undefined || zg === null || zg === undefined) return '';
  const vals = [zd, zg, buy, now].filter((v) => typeof v === 'number' && !Number.isNaN(v));
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.22 || hi * 0.01;
  lo -= pad; hi += pad;
  const pos = (v) => (((v - lo) / (hi - lo)) * 100).toFixed(2);
  const zl = pos(zd), zr = pos(zg);
  return `
    <div class="zs-bar">
      <div class="zs-zone" style="left:${zl}%;width:${(zr - zl).toFixed(2)}%"></div>
      <div class="zs-price" style="left:${pos(buy)}%" title="买点 ${fmt(buy, 2)}"></div>
      ${typeof now === 'number' ? `<div class="zs-price now" style="left:${pos(now)}%" title="现价 ${fmt(now, 2)}"></div>` : ''}
    </div>
    <div class="zs-lbl">
      <span>下沿 ${fmt(zd, 2)}</span>
      <span>中枢区间</span>
      <span>上沿 ${fmt(zg, 2)}</span>
    </div>`;
}

function levelBox(name, lv, lvKey) {
  if (!lv) {
    return `<div class="lv-box miss">
      <div class="lv-h"><span class="t">${name}</span>
        <span class="r">未出现符合的二买 / 三买</span></div>
    </div>`;
  }
  const fresh = lv.bars_ago <= 2 ? '刚刚确认' :
                lv.bars_ago <= 8 ? '信号新鲜' : '略有滞后';
  return `<div class="lv-box hit">
    <div class="lv-h">
      <span class="t">${name}</span>
      <span class="bp ${lv.kind === '3' ? 'b3' : 'b2'}">${lv.label}</span>
      <span class="r">${fresh} · ${lv.bars_ago} 根K线前</span>
    </div>
    <div class="kv" style="margin:0 0 4px">
      <div class="kv-item"><div class="k">买点价位</div><div class="v">${fmt(lv.price, 2)}</div></div>
      <div class="kv-item"><div class="k">确认时间</div><div class="v" style="font-size:13px">${esc(lv.dt)}</div></div>
    </div>
    ${zsBar(lv)}
    <p class="note" style="margin-top:10px">${esc(lv.detail || '')}</p>
  </div>`;
}

function openDrawer(code) {
  const s = (state.payload.signals || []).find((x) => x.code === code);
  if (!s) return;
  state.current = s;

  $('#drName').textContent = s.name;
  $('#drCode').textContent = `${s.code} · ${s.type === 'etf' ? 'ETF' : s.market}`;

  const sc = scoreColor(s.score);
  $('#drBody').innerHTML = `
    <div class="kv">
      <div class="kv-item"><div class="k">最新价</div>
        <div class="v" style="color:var(--${pctClass(s.change_pct) === 'up' ? 'up' : pctClass(s.change_pct) === 'down' ? 'down' : 'text'})">
          ${fmt(s.price, 2)}</div></div>
      <div class="kv-item"><div class="k">涨跌幅</div>
        <div class="v" style="color:var(--${pctClass(s.change_pct) === 'up' ? 'up' : pctClass(s.change_pct) === 'down' ? 'down' : 'text'})">
          ${fmtPct(s.change_pct)}</div></div>
      <div class="kv-item"><div class="k">动态市盈率</div><div class="v">${s.pe_dynamic ? fmt(s.pe_dynamic, 2) : '—'}</div></div>
      <div class="kv-item"><div class="k">换手率</div><div class="v">${s.turnover ? fmt(s.turnover, 2) + '%' : '—'}</div></div>
      <div class="kv-item"><div class="k">成交额</div><div class="v">${fmtAmount(s.amount)}</div></div>
      <div class="kv-item"><div class="k">总市值</div><div class="v">${fmtMV(s.total_mv)}</div></div>
    </div>

    <div class="sect-title">买点结构</div>
    ${levelBox('30 分钟级别', s.m30, 'm30')}
    ${levelBox('5 分钟级别', s.m5, 'm5')}

    <div class="sect-title">综合评分</div>
    <div style="display:flex;align-items:center;gap:12px">
      <span style="font-size:30px;font-weight:700;letter-spacing:-1px;color:${sc}">${s.score}</span>
      <div class="score-bar" style="flex:1;height:7px">
        <i style="width:${Math.min(100, s.score)}%;background:${sc}"></i>
      </div>
    </div>
    <p class="note" style="margin-top:12px">
      评分 = 买点类型（三买 &gt; 二买）+ 级别权重（30分 &gt; 5分）+ 双级别共振加分 + 信号新鲜度 + 市盈率合理性。
      分数越高代表「结构更完整、信号更近」，<b>不代表上涨概率</b>。
    </p>

    <div class="sect-title">提示</div>
    <p class="note warn">
      买点确认天然滞后 1 根 K 线（分型需要右侧 K 线成立）。本信号用于盘后复盘与次日观察，
      实盘请结合仓位管理与止损纪律，不构成投资建议。
    </p>
  `;

  $('#drawer').classList.add('on');
  $('#drawer').setAttribute('aria-hidden', 'false');
  $('#scrim').classList.add('on');
}

function closeDrawer() {
  $('#drawer').classList.remove('on');
  $('#drawer').setAttribute('aria-hidden', 'true');
  $('#scrim').classList.remove('on');
  state.current = null;
}

/* ------------------------------------------------------------------ */
/* 数据加载                                                            */
/* ------------------------------------------------------------------ */
async function loadJSON(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

async function loadPayload(date) {
  try {
    if (date && date !== 'latest') {
      state.payload = await loadJSON(`data/history/${date}.json`);
    } else {
      state.payload = await loadJSON('data/latest.json');
    }
  } catch (e) {
    state.payload = null;
    console.warn('数据加载失败', e);
  }
  render();
}

function fillDates(dates, active) {
  const sel = $('#dateSel');
  if (!dates.length) {
    sel.innerHTML = '<option>—</option>';
    return;
  }
  sel.innerHTML = dates
    .map((d) => `<option value="${d}"${d === active ? ' selected' : ''}>${d}</option>`)
    .join('');
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                            */
/* ------------------------------------------------------------------ */
function bind() {
  $('#modeSeg').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    state.mode = b.dataset.mode;
    [...$('#modeSeg').children].forEach((x) =>
      x.setAttribute('aria-pressed', String(x === b))
    );
    render();
  });

  $('#typeSeg').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    state.type = b.dataset.type;
    [...$('#typeSeg').children].forEach((x) =>
      x.setAttribute('aria-pressed', String(x === b))
    );
    render();
  });

  $('#kindChips').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    const k = b.dataset.kind;
    if (state.kinds.has(k)) {
      if (state.kinds.size === 1) return;   // 至少保留一项
      state.kinds.delete(k);
      b.setAttribute('aria-pressed', 'false');
    } else {
      state.kinds.add(k);
      b.setAttribute('aria-pressed', 'true');
    }
    render();
  });

  let timer = null;
  $('#search').addEventListener('input', (e) => {
    clearTimeout(timer);
    const v = e.target.value;
    timer = setTimeout(() => {
      state.keyword = v;
      render();
    }, 130);
  });

  $('#dateSel').addEventListener('change', (e) => loadPayload(e.target.value));
  $('#drClose').addEventListener('click', closeDrawer);
  $('#scrim').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeDrawer();
  });
}

/* ------------------------------------------------------------------ */
async function boot() {
  bind();

  if (location.protocol === 'file:') {
    $('#empty').hidden = false;
    $('#empty').innerHTML = `<div class="big">请用本地服务器打开</div>
      <p>浏览器安全策略禁止 <code>file://</code> 页面读取本地数据文件。<br>
      在项目目录执行 <code>python serve.py</code>，然后访问 <code>http://localhost:8000</code>。</p>`;
    $('#resultTitle').textContent = '本地预览';
    $('#updatedAt').textContent = '—';
    return;
  }

  let idx = { dates: [] };
  try {
    idx = await loadJSON('data/index.json');
  } catch (e) {
    console.warn('index.json 不存在，退化为只读 latest.json');
  }
  state.dates = idx.dates || [];
  fillDates(state.dates, state.dates[0]);
  await loadPayload(idx.dates && idx.dates[0] ? idx.dates[0] : 'latest');
}

boot();
