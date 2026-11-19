const test = require('node:test');
const assert = require('node:assert');
const {
  SHARES_CHANNELS, aggByCode, codeMetrics, sortCodeRows,
  aggByActor, filterRows, capRows, channelCounts
} = require('../../miniprogram/utils/smart_agg.js');

test('SHARES_CHANNELS 定义', () => {
  assert.deepEqual(SHARES_CHANNELS, ['十大股东', '高管增减持', '限售解禁']);
});

const MIXED = [
  { code: '600519', name: '贵州茅台', market: 'SH', channel: '龙虎榜', actor: 'A营业部', amount: -60, net_intensity: 0.12 },
  { code: '600519', name: '贵州茅台', market: 'SH', channel: '龙虎榜', actor: 'B营业部', amount: -40 },
  { code: '600519', name: '贵州茅台', market: 'SH', channel: '十大股东', actor: '某基金', amount: 5000 },
  { code: '000001', name: '平安银行', market: 'SZ', channel: '资金流', actor: '', amount: 200000, net_intensity: 0.05 }
];

test('aggByCode 聚合+股数金额单位隔离', () => {
  const arr = aggByCode(MIXED);
  assert.equal(arr.length, 2);
  const maotai = arr.find(x => x.code === '600519');
  assert.ok(maotai);
  assert.equal(maotai.byCh['龙虎榜'].amt, -100);
  assert.equal(maotai.byCh['龙虎榜'].n, 2);
  // byCh 全通道累计(供分列显示,SHARES 标"股");隔离发生在 metrics 层
  assert.equal(maotai.byCh['十大股东'].amt, 5000);
  assert.equal(maotai.byCh['十大股东'].hasAmt, true);
  const m = codeMetrics(maotai);
  // metrics 只聚合金额通道:龙虎榜-100,十大股东5000股不入
  assert.equal(m.abs, 100);
  assert.equal(m.intensity, 0.12); // 金额通道 net_intensity 最大值
  assert.equal(m.nch, 2);
  assert.equal(m.avail, 1);   // 只有龙虎榜是金额通道且 hasAmt
  assert.equal(m.pos, 0);     // -100<0 不正向
  assert.equal(m.ratio, 0);
  // SHARES 股数 5000 不出现在任何金额 metrics 字段
  assert.ok(!Object.values(m).includes(5000));
});

test('codeMetrics 仅有 SHARES 通道 → intensity null 非 0', () => {
  const arr = aggByCode([
    { code: '300750', name: 'X', market: 'SZ', channel: '高管增减持', actor: '高管甲', amount: 300 }
  ]);
  const m = codeMetrics(arr[0]);
  assert.equal(m.intensity, null);
  assert.equal(m.abs, 0);
  assert.equal(m.avail, 0);
  assert.equal(m.ratio, null);
  assert.equal(m.nch, 1);
});

test('sortCodeRows null 强度排有强度行之后(desc)', () => {
  const rows = [
    { code: 'A', metrics: { intensity: null, abs: 999, nch: 1, avail: 0, pos: 0, ratio: null } },
    { code: 'B', metrics: { intensity: -0.5, abs: 10, nch: 1, avail: 1, pos: 0, ratio: 0 } },
    { code: 'C', metrics: { intensity: 0.3, abs: 5, nch: 1, avail: 1, pos: 1, ratio: 1 } }
  ];
  const sorted = sortCodeRows(rows, 'intensity');
  assert.deepEqual(sorted.map(x => x.code), ['C', 'B', 'A']);
  const asc = sortCodeRows(rows, 'abs', false);
  assert.deepEqual(asc.map(x => x.code), ['C', 'B', 'A']);
});

test('aggByActor 累计净额与次数', () => {
  const rows = [
    { actor: 'A营业部', amount: 100 },
    { actor: 'A营业部', amount: 50 },
    { actor: 'B营业部', amount: -30 },
    { actor: '', amount: 10 }
  ];
  const arr = aggByActor(rows);
  assert.equal(arr.length, 3);
  const a = arr.find(x => x.actor === 'A营业部');
  assert.equal(a.amount, 150);
  assert.equal(a.n, 2);
  const none = arr.find(x => x.actor === '—');
  assert.equal(none.amount, 10);
  assert.equal(none.n, 1);
  // 默认按 |amount| 降序
  assert.equal(arr[0].actor, 'A营业部');
});

test('aggByActor 单位隔离:股数通道不入金额累计,单独记 shares(评审 I3)', () => {
  const rows = [
    { actor: '某基金', channel: '龙虎榜', amount: -100 },     // 金额通道
    { actor: '某基金', channel: '十大股东', amount: 5000 },   // 股数通道(股)
    { actor: '纯股东', channel: '限售解禁', amount: 3000 }    // 仅股数
  ];
  const arr = aggByActor(rows);
  const a = arr.find(x => x.actor === '某基金');
  assert.equal(a.amount, -100, '金额累计只含金额通道,股数 5000 不得混入');
  assert.equal(a.hasAmt, true);
  assert.equal(a.shares, 5000, '股数单独累计');
  assert.equal(a.hasShares, true);
  assert.equal(a.n, 2);
  const b = arr.find(x => x.actor === '纯股东');
  assert.equal(b.amount, 0);
  assert.equal(b.hasAmt, false, '无金额通道行 hasAmt=false(页面显—不伪造 0 元)');
  assert.equal(b.shares, 3000);
  assert.equal(b.hasShares, true);
});

test('filterRows chip 通道过滤 + q code/name/actor 命中', () => {
  const chipOnly = filterRows(MIXED, { chip: '龙虎榜' });
  assert.equal(chipOnly.length, 2);
  assert.ok(chipOnly.every(x => x.channel === '龙虎榜'));
  const all = filterRows(MIXED, { chip: 'all' });
  assert.equal(all.length, 4);
  const byCode = filterRows(MIXED, { chip: 'all', q: '600519' });
  assert.equal(byCode.length, 3);
  const byName = filterRows(MIXED, { chip: 'all', q: '平安' });
  assert.equal(byName.length, 1);
  const byActor = filterRows(MIXED, { chip: 'all', q: 'a营业部' });
  assert.equal(byActor.length, 1);
});

test('capRows 截断默认50', () => {
  const rows = Array.from({ length: 80 }, (_, i) => ({ i }));
  assert.equal(capRows(rows).length, 50);
  assert.equal(capRows(rows, 10).length, 10);
  assert.equal(capRows(rows.slice(0, 3)).length, 3);
});

test('channelCounts 各通道计数', () => {
  const counts = channelCounts(MIXED);
  const map = Object.fromEntries(counts.map(c => [c.channel, c.n]));
  assert.equal(map['龙虎榜'], 2);
  assert.equal(map['十大股东'], 1);
  assert.equal(map['资金流'], 1);
  assert.equal(counts.length, 3);
});

test('chList 供 wxml 渲染:含 isShares 标注与顺序', () => {
  const maotai = aggByCode(MIXED).find(x => x.code === '600519');
  assert.ok(Array.isArray(maotai.chList));
  const shares = maotai.chList.find(c => c.ch === '十大股东');
  assert.equal(shares.isShares, true);
  const money = maotai.chList.find(c => c.ch === '龙虎榜');
  assert.equal(money.isShares, false);
  assert.equal(money.amt, -100);
  assert.equal(money.n, 2);
});
