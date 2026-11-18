const test = require('node:test');
const assert = require('node:assert');
const { BASE_URL } = require('../../miniprogram/config.js');

async function loadApi() {
  // 每次重新加载模块拿干净的 transport 状态
  delete require.cache[require.resolve('../../miniprogram/utils/api.js')];
  return require('../../miniprogram/utils/api.js');
}

test('传输抛错 → reject 文案"后端不可达"', async () => {
  const api = await loadApi();
  api.setTransport(() => Promise.reject(new Error('fail')));
  await assert.rejects(
    () => api.request({ path: '/api/meta' }),
    /后端不可达（本机服务开着吗？）/
  );
});

test('retry:true 第一次失败第二次成功 → resolve 第二次结果', async () => {
  const api = await loadApi();
  let calls = 0;
  const results = [
    Promise.reject(new Error('net down')),
    Promise.resolve({ statusCode: 200, data: { data: [], update_time: 'x', disclaimer: 'd' } })
  ];
  api.setTransport((opts) => { calls++; return results[calls - 1]; });
  const res = await api.request({ path: '/api/quality', retry: true });
  assert.equal(calls, 2);
  assert.deepEqual(res.data, []);
  assert.equal(res.update_time, 'x');
});

test('retry:true 两次都失败 → reject 同文案，重试恰一次(计数=2)', async () => {
  const api = await loadApi();
  let calls = 0;
  api.setTransport(() => { calls++; return Promise.reject(new Error('net down')); });
  await assert.rejects(
    () => api.request({ path: '/api/meta', retry: true }),
    /后端不可达（本机服务开着吗？）/
  );
  assert.equal(calls, 2);
});

test('传给 transport 的参数含 url=BASE_URL+path 与 timeout', async () => {
  const api = await loadApi();
  let seen = null;
  api.setTransport((opts) => { seen = opts; return Promise.resolve({ statusCode: 200, data: {} }); });
  await api.request({ path: '/api/market', timeout: 12345 });
  assert.equal(seen.url, BASE_URL + '/api/market');
  assert.equal(seen.timeout, 12345);
});

test('statusCode>=400 也按失败处理(可重试)', async () => {
  const api = await loadApi();
  let calls = 0;
  const results = [
    Promise.resolve({ statusCode: 502, data: 'bad gateway' }),
    Promise.resolve({ statusCode: 200, data: { ok: 1 } })
  ];
  api.setTransport(() => { calls++; return results[calls - 1]; });
  const res = await api.request({ path: '/api/meta', retry: true });
  assert.equal(calls, 2);
  // request resolve 的是响应体(_wrap 后 JSON)，transport 的 data 字段即 body
  assert.deepEqual(res, { ok: 1 });
});

test('默认不重试：单次失败直接 reject', async () => {
  const api = await loadApi();
  let calls = 0;
  api.setTransport(() => { calls++; return Promise.reject(new Error('x')); });
  await assert.rejects(() => api.request({ path: '/api/meta' }), /后端不可达/);
  assert.equal(calls, 1);
});
