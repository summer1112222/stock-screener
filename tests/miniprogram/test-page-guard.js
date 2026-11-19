// 页面级测试(Page/wx mock harness)—— 评审递延 minor 修复的锚定:
// M1 通道净额恰 0 如实显示(非 truthy 判缺失)、M2 在途请求守卫(旧响应后到
// 不得覆盖新档)、M3 五步徽章 wx:key 需唯一 k 字段、M4 明细流展开全量上限 300。
const test = require('node:test');
const assert = require('node:assert');
const api = require('../../miniprogram/utils/api.js');

// ---- harness:捕获 Page 配置 + 假 setData 合并 ----
function loadPage(rel) {
  let cfg;
  global.Page = (c) => { cfg = c; };
  delete require.cache[require.resolve(rel)];   // 同一页面文件多次加载须清缓存(utils 依赖共享 api 实例不清)
  require(rel);
  cfg.setData = function (d) { Object.assign(cfg.data, d); };
  return cfg;
}
global.wx = { showModal() {}, stopPullDownRefresh() {} };

// 可控传输:每次 api.request 入队一个 deferred,测试按序 resolve 模拟乱序到达
const queue = [];
api.setTransport(() => new Promise((resolve, reject) => {
  queue.push({ resolve, reject });
}));
const ok = (body) => ({ statusCode: 200, data: body });

test('M2:主力动向页在途守卫——旧响应后到不覆盖新数据', async () => {
  const cfg = loadPage('../../miniprogram/pages/smart/smart.js');
  cfg.onLoad();
  const p1 = cfg.load();   // 入队: channels(0) today(1)
  const p2 = cfg.load();   // 入队: channels(2) today(3)
  // 新请求先完成
  queue[3].resolve(ok({ data: [{ code: '600001', name: '新', channel: '龙虎榜', amount: 5 }], cand_disclaimer: 'D', update_time: 't' }));
  queue[2].resolve(ok({ data: { 龙虎榜: { ok: true, rows: 1 } } }));
  await p2;
  // 旧请求后完成(乱序到达)
  queue[1].resolve(ok({ data: [{ code: '600000', name: '旧', channel: '龙虎榜', amount: 9 }], cand_disclaimer: 'D', update_time: 't' }));
  queue[0].resolve(ok({ data: {} }));
  await p1;
  assert.equal(cfg.data.rows.length, 1);
  assert.equal(cfg.data.rows[0].code, '600001', '旧响应后到必须被守卫丢弃');
});

test('M1:通道净额对冲恰 0 如实显示 0.00,全缺失才显 —', () => {
  const cfg = loadPage('../../miniprogram/pages/smart/smart.js');
  cfg.onLoad();
  cfg._raw = [
    { code: 'A', channel: '龙虎榜', amount: null },
    { code: 'B', channel: '龙虎榜', amount: 100 },
    { code: 'B', channel: '龙虎榜', amount: -100 },
    { code: 'C', channel: '十大股东', amount: 5000 }
  ];
  cfg._apply();
  const a = cfg.data.rows.find(r => r.code === 'A');
  const b = cfg.data.rows.find(r => r.code === 'B');
  const c = cfg.data.rows.find(r => r.code === 'C');
  assert.equal(a.chList[0].amt, '—', '全缺失行显 —');
  assert.equal(b.chList[0].amt, '0.00', '真实对冲 0 显 0.00 不显 —(M1)');
  assert.equal(c.chList[0].amt, '5000.00股', '股数通道带股单位');
});

test('M4:明细流展开全量上限 300(网页 CAP 同款),total 仍如实', () => {
  const cfg = loadPage('../../miniprogram/pages/smart/smart.js');
  cfg.onLoad();
  cfg.setData({ viewMode: 'flow', full: true, chip: 'all', q: '' });
  cfg._raw = Array.from({ length: 400 }, (_, i) =>
    ({ code: '6000' + (i % 10), name: 'N' + i, channel: '龙虎榜', amount: 1, date: 'd' + i }));
  cfg._apply();
  assert.equal(cfg.data.rows.length, 300, '全量展开上限 300 防低端真机卡死');
  assert.equal(cfg.data.total, 400, 'total 如实报全量数');
});

test('M2:优质筛选页在途守卫——先回的旧预设不覆盖新预设', async () => {
  const cfg = loadPage('../../miniprogram/pages/quality/quality.js');
  cfg.onLoad();
  const p1 = cfg.load('strict');
  const p2 = cfg.load('loose');
  // p1/p2 各入队 1 个 transport 调用,取最后两个(新在后)
  const t2 = queue[queue.length - 1];
  const t1 = queue[queue.length - 2];
  t2.resolve(ok({ data: { main: [{ code: 'NEW' }] }, cand_disclaimer: 'D2' }));
  await p2;
  t1.resolve(ok({ data: { main: [{ code: 'OLD' }] }, cand_disclaimer: 'D1' }));
  await p1;
  assert.equal(cfg.data.main.length, 1);
  assert.equal(cfg.data.main[0].code, 'NEW', '旧预设响应后到必须丢弃');
  assert.equal(cfg.data.preset, 'loose');
});

test('M2:次日强势页在途守卫 + M3:steps 徽章带唯一 k 字段', async () => {
  const cfg = loadPage('../../miniprogram/pages/nextday/nextday.js');
  cfg.onLoad();
  const p1 = cfg.load();
  const p2 = cfg.load();
  const t2 = queue[queue.length - 1];
  const t1 = queue[queue.length - 2];
  t2.resolve(ok({ data: { items: [{ code: 'N1', name: '新', score: 0.5, score_coverage: 1, step_status: { step1: 'pass' } }], count: 1 }, cand_disclaimer: 'D' }));
  await p2;
  t1.resolve(ok({ data: { items: [{ code: 'O1', name: '旧', score: 0.1, score_coverage: 1 }], count: 1 }, cand_disclaimer: 'D' }));
  await p1;
  assert.equal(cfg.data.items[0].code, 'N1', '旧响应后到必须丢弃');
  // M3:steps 数组元素带唯一 k(1-5)供 wx:key
  const steps = cfg.data.items[0].steps;
  assert.equal(steps.length, 5);
  assert.equal(steps[0].k, 1, 'steps 元素须有唯一 k 字段(wx:key="k")');
  assert.equal(steps[4].k, 5);
});
