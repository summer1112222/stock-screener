// 优质筛选页视图纯函数 —— 参数防线(默认参数与 server._warm_quality_cache/
// tracker.DEFAULT_PARAMS["quality"]/网页版下拉三处对齐,命中预热缓存秒回且
// 落 list_track 默认组)+口径分位/主力阶段徽章缺失诚实路径。
// CommonJS 纯函数；页面 JS 不出现写死的 50/0.6(回归防线在本模块测试)。

// 真实默认参数串(写死防回归——改这里必须同时改三处对齐口径,见 spec)
const QUALITY_DEFAULT_PATH = '/api/quality?universe=stock&days=20&min_dims=2&limit=10&combo_method=greedy&resonance_mode=greedy&dim_thresh=0.7&max_per_board=3&max_corr=0.85';

// loose = 默认串替换 dim_thresh=0.6 + 追加 strict_quality=false(其余不动)。
// 代价:偏离缓存键,每次必自己冷算(~1 分钟级),切档前 UI 需预告。
function buildQualityPath(preset) {
  if (preset === 'loose') {
    return '/api/quality?universe=stock&days=20&min_dims=2&limit=10&combo_method=greedy&resonance_mode=greedy&dim_thresh=0.6&max_per_board=3&max_corr=0.85&strict_quality=false';
  }
  return QUALITY_DEFAULT_PATH;
}

// 排除原因中文映射(照抄 web/index.html qsRender 的 reasonZh)
const REASON_ZH = {
  hard_gate: '硬门槛',
  confidence: '低可信度',
  risk_flags: '风险标记',
  min_dims: '口径不足',
  no_resonance: '无共振分'
};

// 五口径分位小色块视图:≥0.7 hi/≥0.5 mid/else lo/缺失 missing(不伪造 0)
function dimChips(dimScores) {
  const ds = dimScores || {};
  return [1, 2, 3, 4, 5].map(k => {
    const v = ds[k];
    if (v === null || v === undefined) return { k, pct: null, cls: 'missing' };
    const pct = Math.round(+v * 100);
    return { k, pct, cls: +v >= 0.7 ? 'hi' : (+v >= 0.5 ? 'mid' : 'lo') };
  });
}

// 主力阶段徽章:出货红/吸筹绿/洗盘黄/其余灰/缺失 '—'
function phaseBadge(phase, confidence) {
  if (!phase) return { text: '—', cls: 'muted' };
  let cls = 'muted';
  if (phase === '出货') cls = 'neg';
  else if (phase === '吸筹') cls = 'pos';
  else if (phase === '洗盘') cls = 'warn';
  const conf = confidence != null ? ' ' + Math.round(+confidence * 100) + '%' : '';
  return { text: phase + conf, cls };
}

// 口径名(照 web dimNames)
const DIM_NAMES = { 1: '风险调整', 2: '价值质量', 3: '资金流向', 4: '多信号', 5: '景气' };

module.exports = {
  QUALITY_DEFAULT_PATH, buildQualityPath, REASON_ZH,
  dimChips, phaseBadge, DIM_NAMES
};
