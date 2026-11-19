// 优质筛选页 —— 严格/宽松预设+口径灯+降级提示+by_dim 诊断。
// 数据:GET /api/quality(默认参数=真实默认,落 tracker 默认组;宽松档
// strict_quality=false+dim_thresh=0.6,偏离缓存键需冷算,切档前 showModal 预告)。
// quality 冷缓存 ~67s → timeout=170000 + retry=true(网络层封装单次重试)。
// 合规定位:多口径共振机械排序观察清单,非荐股非买卖信号。
const api = require('../../utils/api.js');
const { fmtNum, fmtFixed } = require('../../utils/fmt.js');
const {
  buildQualityPath, REASON_ZH, dimChips, phaseBadge, DIM_NAMES, pickDisclaimer
} = require('../../utils/quality_view.js');

const FIVE_MIN = 5 * 60 * 1000;
const FALLBACK_DISCLAIMER = '多口径共振机械排序观察清单，非荐股非买卖信号，盈亏自负';
const LOOSE_HINT = '宽松档偏离缓存键，每次需 1 分钟级冷算（严格档命中缓存可秒回）。确认切换？';

let _lastLoad = 0;

Page({
  data: {
    loading: false,
    loadingText: '加载中…(buffett 财报+历史+信号较慢，首次约 1-2 分钟，超时自动重试一次)',
    error: '',
    updateText: '',
    crossItems: [],
    preset: 'strict',      // strict|loose(页面 JS 不出现写死 50/0.6,参数全在 quality_view)
    statusText: '',
    dimLights: [],
    degradeText: '',
    summaryText: '',
    noteText: '',
    main: [],              // 主清单 viewmodel
    byDimSections: [],
    byDimOpen: false,
    disclaimer: FALLBACK_DISCLAIMER
  },

  onLoad() {
    this._loaded = false;
    this._seq = 0;
  },

  onShow() {
    if (!this._loaded || Date.now() - _lastLoad > FIVE_MIN) this.load(this.data.preset);
  },

  onPullDownRefresh() {
    this.load(this.data.preset).finally(() => wx.stopPullDownRefresh());
  },

  onPresetTap(e) {
    const preset = e.currentTarget.dataset.preset;
    if (preset === this.data.preset) return;
    if (preset === 'loose') {
      wx.showModal({
        title: '切换宽松档',
        content: LOOSE_HINT,
        success: (res) => { if (res.confirm) this.load('loose'); }
      });
    } else {
      this.load('strict');
    }
  },

  async load(preset) {
    this.setData({ loading: true, error: '', preset });
    _lastLoad = Date.now();
    this._loaded = true;
    const t0 = Date.now();
    const seq = ++this._seq;   // 在途守卫:旧预设响应后到直接丢弃(评审 M2)
    try {
      const r = await api.request({
        path: buildQualityPath(preset),
        timeout: 170000,   // 冷缓存 ~67s,留余量;app.json networkTimeout=180000
        retry: true        // 对齐网页 _qsFetch:失败自动重试一次
      });
      if (seq !== this._seq) return;
      const d = r.data || {};
      const dt = ((Date.now() - t0) / 1000).toFixed(1);
      const dimLights = Object.entries(d.dim_status || {}).map(([k, v]) => {
        const s = String(v || '');
        const ok = s.startsWith('ok');
        return { k, cls: ok ? 'ok' : 'warn', short: s.slice(0, 30), full: s };
      });
      const rs = String(d.refine_status || '');
      const degrade = rs && !rs.startsWith('ok')
        ? '本次为纯共振排序（精排状态：' + rs.slice(0, 60) + '），盘口流动性/板块加成未计入'
        : '';
      const ex = d.excluded_summary || {};
      const summary = (d.eligible_count != null || d.requested_limit != null)
        ? '请求 ' + (d.requested_limit != null ? d.requested_limit : '-') + ' 只 · 严格合格 ' +
          (d.eligible_count != null ? d.eligible_count : '-') + ' 只 · 硬门槛排除 ' +
          (ex.hard_gate || 0) + ' · 低可信度排除 ' + (ex.confidence || 0) +
          ' · 口径不足 ' + (ex.min_dims || 0)
        : '';
      const note = (d.selection_status === 'insufficient' && d.selection_note)
        ? '⚠ ' + d.selection_note + '（非错误：严格口径宁缺毋滥）'
        : '';
      this.setData({
        loading: false,
        error: '',
        crossItems: (d.main || []).slice(0, 20),
        updateText: '更新于 ' + (r.update_time || '—'),
        statusText: '主清单 ' + (d.main || []).length + ' 只 · 耗时 ' + dt + 's' +
          (dt < 2 ? '（缓存命中）' : ''),
        dimLights,
        degradeText: degrade,
        summaryText: summary,
        noteText: note,
        disclaimer: pickDisclaimer(r, FALLBACK_DISCLAIMER),
        byDimOpen: false
      });
      this._renderMain(d.main || []);
      this._renderByDim(d.by_dim || {});
    } catch (e) {
      const msg = e && e.message ? e.message : String(e);
      this.setData({
        loading: false,
        error: '加载失败/超时（已重试 1 次仍败）。' + msg +
          '。网络或财报接口阻塞可能导致计算超时，稍后重试，5 分钟内已算结果会缓存秒回。',
        main: [], byDimSections: [], crossItems: [], statusText: '',
        dimLights: [], degradeText: '', summaryText: '', noteText: '', updateText: ''
      });
    }
  },

  // 主清单 viewmodel:缺失字段 '—' 不伪造(Review Focus #4)
  _renderMain(main) {
    this._mainRaw = main;
    this.setData({
      main: main.map(x => {
        const pb = phaseBadge(x.mf_phase, x.mf_confidence);
        const beh = x.streak_inflow > 0 ? '入' + x.streak_inflow + '日'
          : (x.streak_outflow > 0 ? '出' + x.streak_outflow + '日' : '—');
        const conf = x.confidence_level || (x.data_confidence != null ? fmtFixed(x.data_confidence, 2) : '');
        return {
          code: x.code || '',
          name: x.name || '',
          resonance: x.resonance != null ? fmtFixed(x.resonance, 2) : '—',
          hits: x.hits || 0,
          dims: dimChips(x.dim_scores),
          phase: pb,
          beh,
          cumNet: fmtNum(x.cum_net),
          conf,
          riskFlags: (x.risk_flags || []).join('、'),
          reasons: (x.reasons || []).join('；'),
          reasonsOpen: false
        };
      })
    });
  },

  // by_dim 各口径诊断折叠区:排除原因经 REASON_ZH 中文映射
  _renderByDim(byDim) {
    const sections = [];
    Object.keys(byDim).forEach(k => {
      const lst = byDim[k] || [];
      if (!lst.length) return;
      sections.push({
        k,
        name: DIM_NAMES[k] || '',
        rows: lst.map(x => {
          const exr = (x.exclusion_reasons || []).map(r => REASON_ZH[r] || r).join('、');
          const sc = (x.dim_scores || {})[k];
          return {
            code: x.code || '',
            name: x.name || '',
            score: sc != null ? fmtFixed(sc, 2) : '—',
            resonance: x.resonance != null ? fmtFixed(x.resonance, 2) : '—',
            st: x.eligibility ? '合格' : (exr || '未合格'),
            ok: !!x.eligibility
          };
        })
      });
    });
    this.setData({ byDimSections: sections });
  },

  onDimLightTap(e) {
    const i = e.currentTarget.dataset.idx;
    const l = this.data.dimLights[i];
    if (l) wx.showModal({ title: '口径' + l.k, content: l.full || '无详情', showCancel: false });
  },

  onToggleReasons(e) {
    const i = e.currentTarget.dataset.idx;
    const key = 'main[' + i + '].reasonsOpen';
    this.setData({ [key]: !this.data.main[i].reasonsOpen });
  },

  onToggleByDim() {
    this.setData({ byDimOpen: !this.data.byDimOpen });
  },

  onReload() {
    this.load(this.data.preset);
  },

  onCrossChipTap() {
    // 自用首期:chip 点击暂无跳转目标(个股分析页不在首期范围)
  }
});
