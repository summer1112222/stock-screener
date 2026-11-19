// 次日强势页视图纯函数测试 —— 五步三态推导/首失败步骤/摘要统计/缺失诚实。
const test = require('node:test');
const assert = require('node:assert');
const {
  STEP_LABELS, stepState, firstFail, ndSummary,
  factorCell, buildNextdayPath
} = require('../../miniprogram/utils/nextday_view.js');

test('stepState 三态:step_status 有值用之/缺但 failed_steps 含→fail/都无→missing', () => {
  const x = {
    step_status: { step1: 'pass', step3: 'missing' },
    failed_steps: ['step2']
  };
  assert.equal(stepState(x, 0), 'pass');
  assert.equal(stepState(x, 1), 'fail');     // step_status 无 step2,failed_steps 含
  assert.equal(stepState(x, 2), 'missing');  // step_status 显式 missing
  assert.equal(stepState(x, 3), 'missing');  // 都无
  assert.equal(stepState(x, 4), 'missing');
});

test('firstFail:第一个非 pass 步骤,reason 缺省未达门槛,全过→null', () => {
  const a = { step_status: { step1: 'pass', step2: 'fail' }, step_reasons: { step2: '换手不足' } };
  assert.deepEqual(firstFail(a), { idx: 1, label: '步2规模', reason: '换手不足' });
  const b = { step_status: { step1: 'fail' } };  // reason 缺省
  assert.deepEqual(firstFail(b), { idx: 0, label: '步1量价', reason: '未达门槛' });
  const c = { step_status: { step1: 'pass', step2: 'pass', step3: 'pass', step4: 'pass', step5: 'pass' } };
  assert.equal(firstFail(c), null);
});

test('ndSummary:候选/五步全过/含缺数据/平均覆盖率,空数组不除零', () => {
  const items = [
    { step_status: { step1: 'pass', step2: 'pass', step3: 'pass', step4: 'pass', step5: 'pass' }, score_coverage: 0.8 },
    { step_status: { step1: 'pass', step2: 'missing', step3: 'fail' }, score_coverage: 0.4 },
    { step_status: { step1: 'pass', step2: 'pass', step3: 'missing', step4: 'pass', step5: 'fail' }, score_coverage: 0.6 }
  ];
  const s = ndSummary(items);
  assert.equal(s.total, 3);
  assert.equal(s.pass, 1);      // 仅第一只五步全过
  assert.equal(s.missing, 2);   // 2、3 含 missing
  assert.equal(s.coverPct, 60); // (0.8+0.4+0.6)/3=0.6→60
  const empty = ndSummary([]);
  assert.deepEqual(empty, { total: 0, pass: 0, missing: 0, coverPct: 0 });
});

test('factorCell:factor_scores 缺键→—,有值取整', () => {
  assert.equal(factorCell({ factor_scores: { mom_5_1: 0.731 } }, 'mom_5_1'), '73');
  assert.equal(factorCell({ factor_scores: {} }, 'mom_5_1'), '—');
  assert.equal(factorCell({}, 'mom_5_1'), '—');
  assert.equal(factorCell({ factor_scores: { sr_10: null } }, 'sr_10'), '—');
});

test('STEP_LABELS 五步', () => {
  assert.deepEqual(STEP_LABELS, ['步1量价', '步2规模', '步3趋势', '步4软分', '步5板块']);
});

test('buildNextdayPath:mode/filter 三档参数照抄网页 ndLoad 映射', () => {
  const strict = buildNextdayPath('strict', 'strict');
  assert.ok(strict.startsWith('/api/nextday-strong?'));
  assert.ok(strict.includes('selection_mode=strict'));
  assert.ok(strict.includes('min_change_pct=5&min_turnover=3&max_price=50'));
  assert.ok(strict.includes('min_mv=10&max_mv=200&max_pe=150&exclude_st=1'));
  // 宽敞档:放宽幅度
  const wide = buildNextdayPath('strict', 'wide');
  assert.ok(wide.includes('min_change_pct=1&min_turnover=1.5&max_price=80'));
  assert.ok(wide.includes('min_mv=5&max_mv=300&max_pe=200&exclude_st=0'));
  // 全部=不过滤
  const all = buildNextdayPath('strict', 'all');
  assert.ok(all.includes('min_change_pct=0&min_turnover=0&max_price=9999'));
  assert.ok(all.includes('min_mv=0&max_mv=999999&max_pe=9999&exclude_st=0'));
  // mode=score
  assert.ok(buildNextdayPath('score', 'strict').includes('selection_mode=score'));
});
