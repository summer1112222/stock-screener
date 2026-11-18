// 跨清单摘要条 chips 组装 —— 语义移植自 web/index.html _crossBarHtml。
// 只读响应里已有的穿透字段(quality_pct/mf_phase/streak_inflow|outflow/
// sector_heat/reasons),缺失跳过不伪造,不触网。
// CommonJS 纯函数。

function crossChips(items, n) {
  const N = n === undefined ? 20 : n;
  if (!items || !items.length) return [];
  return items.slice(0, N).map(x => {
    const text = [x.code || '', x.name || ''].filter(Boolean).join(' ');
    const extra = [];
    if (x.quality_pct != null) extra.push('quality ' + (+x.quality_pct).toFixed(2));
    if (x.mf_phase) extra.push(x.mf_phase);
    if (x.streak_inflow > 0) extra.push('流入' + x.streak_inflow + '日');
    else if (x.streak_outflow > 0) extra.push('流出' + x.streak_outflow + '日');
    if (x.sector_heat != null) extra.push('景气' + (+x.sector_heat).toFixed(2));
    return {
      code: x.code || '',
      name: x.name || '',
      text,
      extra,
      extraText: extra.join(' · '),   // wxml 数组插值默认逗号连接,显式串对齐网页' · '分隔
      reasons: (x.reasons || []).join('；')
    };
  });
}

module.exports = { crossChips };
