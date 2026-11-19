// 主力动向聚合纯函数 —— 语义移植自 web/index.html smRender 的 code/actor 视图。
// 单位隔离(本模块是 Review Focus #2 的唯一防线):
//   byCh 全通道累计 amt/n(股数通道供分列显示,页面上标"股");
//   codeMetrics(intensity/abs/avail/pos/ratio) 只聚合金额通道(龙虎榜/资金流/北向),
//   SHARES_CHANNELS(十大股东/高管增减持/限售解禁,单位=股)绝不混入金额比较。
// 缺强度为 null 不是 0(null-last 排序,显 — 不伪造)。
// CommonJS 纯函数,不触网不触 wx。

const SHARES_CHANNELS = ['十大股东', '高管增减持', '限售解禁'];

// 六通道展示顺序(照网页 CH_COLS;未知通道追加在后)
const CH_ORDER = ['龙虎榜', '资金流', '北向', '十大股东', '高管增减持', '限售解禁'];

function _isShares(ch) {
  return SHARES_CHANNELS.includes(ch);
}

function _num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : parseFloat(v);
  return isFinite(n) ? n : null;
}

function aggByCode(rows) {
  const m = {};
  (rows || []).forEach(x => {
    const c = x.code || '';
    if (!c) return;
    if (!m[c]) m[c] = { code: c, name: x.name || '', market: x.market || '', byCh: {} };
    const ch = x.channel || '';
    if (!m[c].byCh[ch]) m[c].byCh[ch] = { amt: 0, n: 0, hasAmt: false, ints: [] };
    const d0 = m[c].byCh[ch];
    const amt = _num(x.amount);
    if (amt !== null) { d0.amt += amt; d0.hasAmt = true; }
    d0.n += 1;
    const ni = _num(x.net_intensity);
    // 强度只收金额通道(股数通道无此语义且量纲不同)
    if (ni !== null && !_isShares(ch)) d0.ints.push(ni);
  });
  return Object.values(m).map(o => {
    // chList: wxml 直接 wx:for 的通道视图模型(定序+isShares 标注)
    const seen = Object.keys(o.byCh);
    const ordered = CH_ORDER.filter(ch => seen.includes(ch)).concat(
      seen.filter(ch => !CH_ORDER.includes(ch))
    );
    o.chList = ordered.map(ch => ({
      ch,
      amt: o.byCh[ch].amt,
      hasAmt: o.byCh[ch].hasAmt,
      n: o.byCh[ch].n,
      isShares: _isShares(ch)
    }));
    return o;
  });
}

function codeMetrics(entry) {
  const o = { intensity: null, abs: 0, nch: 0, avail: 0, pos: 0, ratio: null };
  const byCh = entry.byCh || {};
  o.nch = Object.keys(byCh).length;
  let ints = [], absSum = 0, avail = 0, pos = 0;
  Object.entries(byCh).forEach(([ch, v]) => {
    if (_isShares(ch)) return;           // 单位隔离:股数通道不入金额聚合
    ints.push(...(v.ints || []));
    if (v.hasAmt) { avail++; absSum += v.amt; if (v.amt > 0) pos++; }
  });
  o.intensity = ints.length ? Math.max(...ints) : null;
  o.abs = Math.abs(absSum);
  o.avail = avail;
  o.pos = pos;
  o.ratio = avail ? pos / avail : null;
  return o;
}

// null 值排最后(缺强度不当 0 参与比较)
function _nullLast(va, vb, desc) {
  const na = va === null || va === undefined, nb = vb === null || vb === undefined;
  if (na && nb) return 0;
  if (na) return 1;
  if (nb) return -1;
  return desc ? vb - va : va - vb;
}

function sortCodeRows(arr, metric, desc) {
  const d = desc === undefined ? true : desc;
  return [...(arr || [])].sort((a, b) =>
    _nullLast(a.metrics[metric], b.metrics[metric], d)
  );
}

// 按席位聚合:金额通道累计 amount(元),股数通道单独累计 shares(股)绝不混比
// (单位隔离同 codeMetrics;网页版此处混比是已知缺陷,本端不照抄——见评审 I3)
function aggByActor(rows) {
  const m = {};
  (rows || []).forEach(x => {
    const a = x.actor || '—';
    if (!m[a]) m[a] = { actor: a, amount: 0, hasAmt: false, shares: 0, hasShares: false, n: 0 };
    const amt = _num(x.amount);
    if (amt !== null) {
      if (_isShares(x.channel || '')) { m[a].shares += amt; m[a].hasShares = true; }
      else { m[a].amount += amt; m[a].hasAmt = true; }
    }
    m[a].n += 1;
  });
  // 默认按 |累计净额| 降序(照网页默认 SK=amount/SD=desc 的 _cmp 语义)
  return Object.values(m).sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
}

function filterRows(rows, opts) {
  const o = opts || {};
  let out = rows || [];
  if (o.chip && o.chip !== 'all') out = out.filter(x => x.channel === o.chip);
  if (o.q) {
    const q = String(o.q).toLowerCase();
    out = out.filter(x => ((x.code || '') + (x.actor || '') + (x.name || '')).toLowerCase().indexOf(q) >= 0);
  }
  return out;
}

function capRows(rows, cap) {
  const c = cap === undefined ? 50 : cap;
  return (rows || []).slice(0, c);
}

function channelCounts(rows) {
  const m = {};
  (rows || []).forEach(x => {
    const ch = x.channel || '';
    if (!ch) return;
    m[ch] = (m[ch] || 0) + 1;
  });
  return Object.entries(m).map(([channel, n]) => ({ channel, n }));
}

module.exports = {
  SHARES_CHANNELS, aggByCode, codeMetrics, sortCodeRows,
  aggByActor, filterRows, capRows, channelCounts
};
