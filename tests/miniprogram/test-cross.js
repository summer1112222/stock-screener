const test = require('node:test');
const assert = require('node:assert');
const { crossChips } = require('../../miniprogram/utils/cross.js');

test('完整字段行 → text 含代码名称,extra 含 quality/流入/景气', () => {
  const items = [{
    code: '600519', name: '贵州茅台',
    quality_pct: 0.72, mf_phase: '吸筹',
    streak_inflow: 3, sector_heat: 0.65,
    reasons: ['a', 'b']
  }];
  const chips = crossChips(items);
  assert.equal(chips.length, 1);
  const c = chips[0];
  assert.equal(c.code, '600519');
  assert.equal(c.text, '600519 贵州茅台');
  assert.ok(c.extra.includes('quality 0.72'));
  assert.ok(c.extra.includes('吸筹'));
  assert.ok(c.extra.includes('流入3日'));
  assert.ok(c.extra.includes('景气0.65'));
  assert.equal(c.reasons, 'a；b');
});

test('全缺失行 → extra 空数组', () => {
  const chips = crossChips([{ code: '000001', name: '平安银行' }]);
  assert.deepEqual(chips[0].extra, []);
});

test('null quality_pct 不出现 quality 字样', () => {
  const chips = crossChips([{ code: 'X', name: 'Y', quality_pct: null, mf_phase: '' }]);
  assert.deepEqual(chips[0].extra, []);
  assert.ok(!chips[0].extra.some(e => e.indexOf('quality') >= 0));
});

test('流出优先级:streak_outflow 在无 inflow 时显示', () => {
  const chips = crossChips([{ code: 'X', name: 'Y', streak_inflow: 0, streak_outflow: 4 }]);
  assert.ok(chips[0].extra.includes('流出4日'));
});

test('默认头部前20截断', () => {
  const items = Array.from({ length: 30 }, (_, i) => ({ code: String(i), name: 'n' + i }));
  assert.equal(crossChips(items).length, 20);
  assert.equal(crossChips(items, 5).length, 5);
});

test('空/缺失输入 → 空数组', () => {
  assert.deepEqual(crossChips([]), []);
  assert.deepEqual(crossChips(null), []);
});

test('cross-bar 组件 json 声明 component', () => {
  const fs = require('node:fs');
  const j = JSON.parse(fs.readFileSync('miniprogram/components/cross-bar/cross-bar.json', 'utf8'));
  assert.equal(j.component, true);
});
