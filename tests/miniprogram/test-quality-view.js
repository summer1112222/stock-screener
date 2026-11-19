// 优质筛选页视图纯函数测试 —— 参数防线(Review Focus #3)+缺失诚实(Review Focus #4)。
const test = require('node:test');
const assert = require('node:assert');
const {
  QUALITY_DEFAULT_PATH, buildQualityPath, REASON_ZH, dimChips, phaseBadge
} = require('../../miniprogram/utils/quality_view.js');

// 把 query 串解析成参数 map(测试工具)
function parseParams(url) {
  const q = url.split('?')[1] || '';
  const m = {};
  q.split('&').filter(Boolean).forEach(kv => {
    const [k, v] = kv.split('=');
    m[k] = v;
  });
  return m;
}

test('默认路径=真实默认参数 limit=10/max_corr=0.85(防回归 50/0.6)', () => {
  assert.ok(QUALITY_DEFAULT_PATH.startsWith('/api/quality?'));
  const m = parseParams(QUALITY_DEFAULT_PATH);
  assert.equal(m.universe, 'stock');
  assert.equal(m.days, '20');
  assert.equal(m.min_dims, '2');
  assert.equal(m.limit, '10');          // 写死:真实默认 10 只(秒回+追踪对齐)
  assert.equal(m.combo_method, 'greedy');
  assert.equal(m.resonance_mode, 'greedy');
  assert.equal(m.dim_thresh, '0.7');
  assert.equal(m.max_per_board, '3');
  assert.equal(m.max_corr, '0.85');     // 写死:真实默认(防回归 0.6)
});

test("buildQualityPath('strict') === 默认串", () => {
  assert.equal(buildQualityPath('strict'), QUALITY_DEFAULT_PATH);
});

test("buildQualityPath('loose') = strict_quality=false + dim_thresh 替换为 0.6,其余不变", () => {
  const p = buildQualityPath('loose');
  assert.ok(p.includes('strict_quality=false'), '须含 strict_quality=false');
  assert.ok(p.includes('dim_thresh=0.6'), '须含 dim_thresh=0.6');
  assert.ok(!p.includes('dim_thresh=0.7'), '不得残留 dim_thresh=0.7');
  const lo = parseParams(p), df = parseParams(QUALITY_DEFAULT_PATH);
  Object.keys(df).forEach(k => {
    if (k === 'dim_thresh') return;
    assert.equal(lo[k], df[k], `宽松档参数 ${k} 须与默认一致`);
  });
  assert.equal(lo.strict_quality, 'false');
});

test('REASON_ZH 排除原因中文映射', () => {
  assert.equal(REASON_ZH.hard_gate, '硬门槛');
  assert.equal(REASON_ZH.confidence, '低可信度');
  assert.equal(REASON_ZH.risk_flags, '风险标记');
  assert.equal(REASON_ZH.min_dims, '口径不足');
  assert.equal(REASON_ZH.no_resonance, '无共振分');
});

test('dimChips 五口径缺失→missing 不伪造', () => {
  const chips = dimChips({ 1: 0.85, 3: 0.55 });
  assert.equal(chips.length, 5);
  assert.deepEqual(chips[0], { k: 1, pct: 85, cls: 'hi' });   // ≥0.7
  assert.deepEqual(chips[1], { k: 2, pct: null, cls: 'missing' });
  assert.deepEqual(chips[2], { k: 3, pct: 55, cls: 'mid' });  // ≥0.5
  const all = dimChips(null);
  assert.equal(all.filter(c => c.cls === 'missing').length, 5);
});

test('phaseBadge 出货红/吸筹绿/缺失灰', () => {
  assert.equal(phaseBadge('出货').cls, 'neg');
  assert.equal(phaseBadge('吸筹').cls, 'pos');
  assert.equal(phaseBadge('洗盘').cls, 'warn');
  assert.equal(phaseBadge(null).cls, 'muted');
  assert.equal(phaseBadge(null).text, '—');
});
