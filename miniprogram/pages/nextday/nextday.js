// 次日强势页 —— mode strict/score + filter 三档(严格/宽敞/全部)+五步徽章。
// 数据:GET /api/nextday-strong(默认参数;30s 进程缓存+首次约 45s → 90s 超时,
// 失败重试一次对齐网页 _qsFetch)。合规定位:5 因子机械排序观察清单。
// 五步三态/首失败步骤/覆盖率过滤语义照网页 ndRender,纯函数在 nextday_view.js。
const api = require('../../utils/api.js');
const { fmtFixed } = require('../../utils/fmt.js');
const {
  STEP_LABELS, stepState, firstFail, ndSummary,
  factorCell, buildNextdayPath
} = require('../../utils/nextday_view.js');

const FIVE_MIN = 5 * 60 * 1000;
const FALLBACK_DISCLAIMER = '次日强势清单——5因子机械排序观察清单，非荐股非买卖信号，盈亏自负';
// 五因子短标签(顺序=后端 factor_scores 键序,长名见网页版 th title)
const FACTOR_KEYS = [
  ['mom_5_1', '动量'], ['rel_strength', '强度'], ['sr_10', '波动'],
  ['close_vol_corr', '量价'], ['liq_turnover', '流动']
];

let _lastLoad = 0;

Page({
  data: {
    loading: false,
    loadingText: '加载中(5步漏斗+5因子评分,首次较慢;30s缓存)…',
    error: '',
    updateText: '',
    crossItems: [],
    mode: 'strict',       // strict|score(URL selection_mode)
    flt: 'strict',        // strict|wide|all(漏斗三档,参数映射在 nextday_view)
    coverMin: 50,         // 覆盖率过滤档:50/30/0(全部)
    summaryText: '',
    items: [],            // 过滤后渲染行 viewmodel
    total: 0,
    disclaimer: FALLBACK_DISCLAIMER
  },

  onLoad() {
    this._raw = [];
    this._loaded = false;
    this._seq = 0;
  },

  onShow() {
    if (!this._loaded || Date.now() - _lastLoad > FIVE_MIN) this.load();
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh());
  },

  onModeTap(e) {
    const mode = e.currentTarget.dataset.mode;
    if (mode === this.data.mode) return;
    this.setData({ mode });
    this.load();
  },

  onFltTap(e) {
    const flt = e.currentTarget.dataset.flt;
    if (flt === this.data.flt) return;
    this.setData({ flt });
    this.load();
  },

  onCoverTap(e) {
    const v = +e.currentTarget.dataset.v;
    if (v === this.data.coverMin) return;
    this.setData({ coverMin: v });
    this._render();   // 本地过滤,不重拉
  },

  async load() {
    this.setData({ loading: true, error: '' });
    _lastLoad = Date.now();
    this._loaded = true;
    const t0 = Date.now();
    const { mode, flt } = this.data;
    const seq = ++this._seq;   // 在途守卫:旧档响应后到直接丢弃(评审 M2)
    try {
      const r = await api.request({
        path: buildNextdayPath(mode, flt),
        timeout: 90000,     // 30s 缓存+首次约 45s
        retry: true         // 对齐网页 _qsFetch 重试一次
      });
      if (seq !== this._seq) return;
      const d = r.data || {};
      const dt = ((Date.now() - t0) / 1000).toFixed(1);
      this._raw = d.items || [];
      const modeTag = d.selection_mode === 'fallback' ? '降级观察清单；'
        : (d.selection_mode === 'strict' ? '严格通过清单；' : '');
      const med = d.market_median_chg != null ? fmtFixed(d.market_median_chg, 2) + '%' : '-';
      this.setData({
        loading: false,
        error: '',
        crossItems: (d.passed_items || d.items || []).slice(0, 20),
        updateText: '更新于 ' + (r.update_time || '—'),
        summaryText: modeTag + (d.note ? d.note + '；' : '') + '共' + (d.count || 0) +
          '只；大盘中位' + med + '；耗时' + dt + 's' + (dt < 2 ? '（缓存命中）' : ''),
        disclaimer: d.cand_disclaimer || r.cand_disclaimer || FALLBACK_DISCLAIMER
      });
      this._render();
    } catch (e) {
      this._raw = [];
      const msg = e && e.message ? e.message : String(e);
      this.setData({
        loading: false,
        error: '加载失败: ' + msg,
        items: [], total: 0, crossItems: [], summaryText: '', updateText: ''
      });
    }
  },

  // 纯视图重组:raw × coverMin → items(缺失字段 '—' 不伪造,Review Focus #4)
  _render() {
    const th = this.data.coverMin / 100;
    const s = ndSummary(this._raw);
    const items = this._raw.filter(x => Number(x.score_coverage || 0) >= th).map(x => {
      const ff = firstFail(x);
      const states = [0, 1, 2, 3, 4].map(i => stepState(x, i));
      // k=步序号(1-5)供 wx:key="k" 唯一键(评审 M3)
      const steps = states.map((st, i) => ({
        k: i + 1,
        icon: st === 'pass' ? '✓' : (st === 'missing' ? '?' : '×'),
        cls: st
      }));
      const rBoost = x.radar_boost != null ? Number(x.radar_boost) : null;
      const dq = x.data_quality || {};
      return {
        key: (x.code || '') + (x.rank != null ? x.rank : ''),
        rank: x.rank != null ? x.rank : '',
        code: x.code || '',
        name: x.name || '',
        score: x.score != null ? fmtFixed(x.score, 2) : '—',
        baseScore: x.base_score != null ? fmtFixed(x.base_score, 2) : '',
        radarIn: x.radar_resonance_in != null ? String(x.radar_resonance_in) : '—',
        radarInMiss: x.radar_resonance_in == null,
        boost: (rBoost != null && rBoost > 1.0) ? ('×' + fmtFixed(rBoost, 2)) : '—',
        boostOn: rBoost != null && rBoost > 1.0,
        riskFlag: x.risk_flag || '',
        steps,
        failHint: ff ? ('卡在' + ff.label + '：' + ff.reason.slice(0, 24)) : '',
        hard: (x.hard_pass != null ? x.hard_pass : 0) + '/4',
        factors: FACTOR_KEYS.map(([k, label]) => {
          const v = factorCell(x, k);
          return { k: label, v, miss: v === '—' };
        }),
        cover: Math.round(Number(x.score_coverage || 0) * 100) + '%',
        source: dq.source || '—',
        board: x.board || '—',
        reasonLines: STEP_LABELS.map((n, i) => n + '：' +
          ((x.step_reasons || {})['step' + (i + 1)] || '—')),
        missingText: (dq.missing || []).join('、') || '无',
        reasonsOpen: false
      };
    });
    this.setData({
      items,
      total: s.total,
      passCount: s.pass,
      missingCount: s.missing,
      coverPct: s.coverPct
    });
  },

  onToggleReasons(e) {
    const i = e.currentTarget.dataset.idx;
    const key = 'items[' + i + '].reasonsOpen';
    this.setData({ [key]: !this.data.items[i].reasonsOpen });
  },

  onReload() {
    this.load();
  },

  onCrossChipTap() {
    // 自用首期:chip 点击暂无跳转目标(个股分析页不在首期范围)
  }
});
