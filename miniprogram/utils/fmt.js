// 数字格式化 —— 语义移植自 web/index.html 的 fmtNum/fmtFixed。
// CommonJS（node --test 可直接 require，小程序原生也支持 require）。
// 缺失诚实返回 '—'，不伪造 0/NaN。

function _num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : parseFloat(v);
  return isFinite(n) ? n : null;
}

function fmtNum(v) {
  const n = _num(v);
  if (n === null) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e8) return (n / 1e8).toFixed(2) + '亿';
  if (abs >= 1e4) return (n / 1e4).toFixed(2) + '万';
  return n.toFixed(2);
}

function fmtFixed(v, n) {
  const d = n === undefined ? 2 : n;
  const x = _num(v);
  if (x === null) return '—';
  return x.toFixed(d);
}

module.exports = { fmtNum, fmtFixed };
