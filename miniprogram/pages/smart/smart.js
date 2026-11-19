// 主力动向页 —— 三视图(按个股聚合/按席位/明细流)+通道灯+跨清单摘要条。
// 数据：GET /api/smart-money/today?days=7(默认参数→落 tracker 默认组) +
//       GET /api/smart-money/channels(通道灯)。合规定位:观察清单非荐股。
// setData 只喂 capRows 结果(首屏50,Review Focus #5),展开按钮置 full 喂全量。
const api = require('../../utils/api.js');
const { fmtNum } = require('../../utils/fmt.js');
const {
  aggByCode, codeMetrics, sortCodeRows, aggByActor,
  filterRows, capRows, channelCounts
} = require('../../utils/smart_agg.js');

const FIVE_MIN = 5 * 60 * 1000;
const PAGE_SIZE = 50;
const FALLBACK_DISCLAIMER = '主力动向观察清单，机械归类，非荐股非买卖信号，盈亏自负';

let _lastLoad = 0;

Page({
  data: {
    loading: false,
    error: '',
    updateText: '',
    channels: [],        // 通道灯 [{name,icon,cls,rows,short,full}]
    crossItems: [],
    viewMode: 'code',    // code|actor|flow
    chip: 'all',
    q: '',
    chips: [],           // [{key,label,n,active}]
    rows: [],            // 当前视图渲染行(viewmodel)
    total: 0,
    full: false,
    disclaimer: FALLBACK_DISCLAIMER
  },

  onLoad() {
    this._raw = [];
    this._qpMap = {};
    this._loaded = false;
  },

  onShow() {
    if (!this._loaded || Date.now() - _lastLoad > FIVE_MIN) this.load();
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh());
  },

  async load() {
    this.setData({ loading: true, error: '' });
    _lastLoad = Date.now();
    this._loaded = true;
    try {
      const [chRes, tdRes] = await Promise.allSettled([
        api.request({ path: '/api/smart-money/channels', timeout: 30000 }),
        api.request({ path: '/api/smart-money/today?days=7' })
      ]);
      // 通道灯(失败不阻塞清单,只不显示灯)
      let channels = [];
      if (chRes.status === 'fulfilled') {
        const d = chRes.value && chRes.value.data;
        if (d && typeof d === 'object') {
          channels = Object.entries(d).map(([k, v]) => {
            const stale = !!(v && v.stale), ok = !!(v && v.ok);
            return {
              name: k,
              icon: stale ? '◐' : (ok ? '●' : '○'),
              cls: stale ? 'warn' : (ok ? 'ok' : 'fail'),
              rows: v && v.rows != null ? ':' + v.rows + '行' : '',
              short: (!ok && !stale && v && v.err) ? String(v.err).slice(0, 10) : '',
              full: `${k}：${stale ? ('回退至 ' + (v.stale_date || '') + ' ' + (v.err || '')) : (ok ? (v.source || 'ok') : (v.err || '不可用'))} ${v && v.rows != null ? v.rows + '行' : ''} ${v && v.date ? '(' + v.date + ')' : ''}`
            };
          });
        }
      }
      // today 清单(自用最高频故障=后端没开,给明确文案+空态)
      if (tdRes.status === 'rejected') {
        this._raw = [];
        this._qpMap = {};
        this.setData({
          loading: false,
          error: tdRes.reason && tdRes.reason.message ? tdRes.reason.message : String(tdRes.reason),
          channels, crossItems: [], rows: [], total: 0, full: false,
          updateText: '', disclaimer: FALLBACK_DISCLAIMER
        });
        return;
      }
      const td = tdRes.value || {};
      this._raw = td.data || [];
      this._qpMap = {};
      this._raw.forEach(r => {
        if (r && r.quality_pct != null && !(r.code in this._qpMap)) this._qpMap[r.code] = r.quality_pct;
      });
      const upd = (td.date ? '数据日期 ' + td.date + ' · ' : '') + '更新于 ' + (td.update_time || '—');
      this.setData({
        loading: false,
        error: '',
        channels,
        crossItems: this._raw.slice(0, 20),
        updateText: upd,
        disclaimer: td.cand_disclaimer || td.disclaimer || FALLBACK_DISCLAIMER,
        chip: 'all', q: '', full: false
      });
      this._apply();
    } catch (e) {
      // 兜底:任何未预期异常也不能白屏
      this.setData({ loading: false, error: e && e.message ? e.message : String(e), rows: [], total: 0 });
    }
  },

  // 纯视图重组:raw × {chip,q,viewMode,full} → rows(只喂截断后数组)
  _apply() {
    const { viewMode, chip, q, full } = this.data;
    const filtered = filterRows(this._raw, { chip, q });
    let rows = [];
    if (viewMode === 'code') {
      let entries = aggByCode(filtered);
      entries.forEach(e => { e.metrics = codeMetrics(e); });
      entries = sortCodeRows(entries, 'intensity');
      rows = entries.map(e => {
        const M = e.metrics;
        return {
          code: e.code, name: e.name, market: e.market,
          intensity: M.intensity != null ? (M.intensity * 100).toFixed(2) + '%' : '—',
          intensityCls: M.intensity == null ? 'muted' : (M.intensity >= 0 ? 'pos' : 'neg'),
          nch: M.nch,
          ratio: M.ratio != null ? (M.pos + '/' + M.avail + '·' + Math.round(M.ratio * 100) + '%') : '—',
          qualityPct: this._qpMap[e.code] != null ? 'Q' + (this._qpMap[e.code] * 100).toFixed(0) : '',
          chList: e.chList.map(c => ({
            ch: c.ch,
            amt: c.amt ? (fmtNum(c.amt) + (c.isShares ? '股' : '')) : '—',
            isShares: c.isShares,
            cls: c.amt > 0 ? 'pos' : (c.amt < 0 ? 'neg' : 'muted')
          }))
        };
      });
    } else if (viewMode === 'actor') {
      rows = aggByActor(filtered).map(x => ({
        actor: x.actor,
        amount: fmtNum(x.amount),
        amountCls: x.amount >= 0 ? 'pos' : 'neg',
        n: x.n
      }));
    } else {
      rows = filtered.map((x, i) => {
        const isShares = ['十大股东', '高管增减持', '限售解禁'].includes(x.channel);
        const ni = x.net_intensity;
        return {
          key: (x.date || '') + (x.code || '') + i,
          date: x.date || '', code: x.code || '', name: x.name || '',
          market: x.market || '', channel: x.channel || '', actor: x.actor || '—',
          action: x.action || '',
          amount: x.amount != null ? (fmtNum(x.amount) + (isShares ? '股' : '')) : '—',
          amountCls: x.amount != null ? (x.amount >= 0 ? 'pos' : 'neg') : 'muted',
          intensity: ni != null ? (ni * 100).toFixed(2) + '%' : '—',
          intensityCls: ni == null ? 'muted' : (ni >= 0 ? 'pos' : 'neg')
        };
      });
    }
    const total = rows.length;
    this.setData({
      rows: full ? rows : capRows(rows, PAGE_SIZE),
      total,
      chips: this._buildChips()
    });
  },

  _buildChips() {
    const { chip } = this.data;
    const counts = channelCounts(this._raw);
    const out = [{ key: 'all', label: '全部', n: this._raw.length, active: chip === 'all' }];
    counts.forEach(c => out.push({
      key: c.channel, label: c.channel, n: c.n, active: chip === c.channel
    }));
    return out;
  },

  onViewMode(e) {
    this.setData({ viewMode: e.currentTarget.dataset.mode, full: false });
    this._apply();
  },

  onChipTap(e) {
    this.setData({ chip: e.currentTarget.dataset.chip });
    this._apply();
  },

  onSearch(e) {
    this.setData({ q: e.detail.value });
    this._apply();
  },

  onToggleFull() {
    this.setData({ full: !this.data.full });
    this._apply();
  },

  onChannelTap(e) {
    const i = e.currentTarget.dataset.idx;
    const c = this.data.channels[i];
    if (c) wx.showModal({ title: c.name, content: c.full || '无详情', showCancel: false });
  },

  onCrossChipTap() {
    // 自用首期:chip 点击暂无跳转目标(个股分析页不在首期范围)
  }
});
