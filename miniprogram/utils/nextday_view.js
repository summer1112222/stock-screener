// 次日强势页视图纯函数 —— 五步三态推导/首失败步骤/摘要统计/URL 参数三档映射。
// 语义照抄 web/index.html ndLoad/ndRender:step_status 优先,缺但 failed_steps
// 含之→fail,都无→missing;factor_scores 缺键诚实 '—' 不伪造。
// CommonJS 纯函数。

const STEP_LABELS = ['步1量价', '步2规模', '步3趋势', '步4软分', '步5板块'];

// i=0..4;step_status 有值用之(falsy 空串按缺省走兜底链)
function stepState(x, i) {
  const key = 'step' + (i + 1);
  const v = (x && x.step_status && x.step_status[key]);
  if (v != null && v !== '') return v;
  if (x && Array.isArray(x.failed_steps) && x.failed_steps.includes(key)) return 'fail';
  return 'missing';
}

// 第一个非 pass 步骤 {idx, label, reason};全过→null。reason 缺省'未达门槛'。
function firstFail(x) {
  for (let i = 0; i < 5; i++) {
    if (stepState(x, i) !== 'pass') {
      const key = 'step' + (i + 1);
      const r = (x && x.step_reasons && x.step_reasons[key]) || '未达门槛';
      return { idx: i, label: STEP_LABELS[i], reason: String(r) };
    }
  }
  return null;
}

// 摘要:pass=五步全过数;missing=含 missing 步的行数;coverPct=平均覆盖率取整(空不除零)
function ndSummary(items) {
  const arr = items || [];
  let pass = 0, missing = 0, cover = 0;
  arr.forEach(x => {
    const states = [0, 1, 2, 3, 4].map(i => stepState(x, i));
    if (states.every(s => s === 'pass')) pass += 1;
    if (states.includes('missing')) missing += 1;
    cover += Number(x && x.score_coverage || 0);
  });
  return {
    total: arr.length,
    pass,
    missing,
    coverPct: arr.length ? Math.round(cover / arr.length * 100) : 0
  };
}

// 五因子分单元格:缺键(含 null)→'—';有值按 0-1 分位×100 取整显示
// (网页版 toFixed(0) 会把 0.731 显示成 '1',信息全损,不照抄——见 ledger 裁决)
function factorCell(x, key) {
  const fs = x && x.factor_scores;
  const v = fs ? fs[key] : null;
  if (v === null || v === undefined) return '—';
  return String(Math.round(Number(v) * 100));
}

// filter 三档参数映射(照抄网页 ndLoad:strict=5步漏斗默认/wide=放宽/all=不过滤)
const _FILTER_PARAMS = {
  strict: { mcp: 5, mt: 3, mp: 50, mvLo: 10, mvHi: 200, pe: 150, est: 1 },
  wide: { mcp: 1, mt: 1.5, mp: 80, mvLo: 5, mvHi: 300, pe: 200, est: 0 },
  all: { mcp: 0, mt: 0, mp: 9999, mvLo: 0, mvHi: 999999, pe: 9999, est: 0 }
};

// mode: strict|score(URL selection_mode);flt: strict|wide|all
function buildNextdayPath(mode, flt, days, limit) {
  const p = _FILTER_PARAMS[flt] || _FILTER_PARAMS.strict;
  const m = mode === 'score' ? 'score' : 'strict';
  const d = days === undefined ? 90 : days;
  const l = limit === undefined ? 100 : limit;
  return '/api/nextday-strong?universe=stock&days=' + d + '&limit=' + l +
    '&selection_mode=' + m +
    '&min_change_pct=' + p.mcp + '&min_turnover=' + p.mt + '&max_price=' + p.mp +
    '&min_mv=' + p.mvLo + '&max_mv=' + p.mvHi + '&max_pe=' + p.pe +
    '&exclude_st=' + p.est;
}

module.exports = { STEP_LABELS, stepState, firstFail, ndSummary, factorCell, buildNextdayPath };
