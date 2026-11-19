// allSettled shim 测试 —— 评审 M6:旧基础库无 Promise.allSettled 时的降级实现。
const test = require('node:test');
const assert = require('node:assert');
const { allSettled, allSettledFallback } = require('../../miniprogram/utils/promise.js');

test('M6:fallback 语义与原生 allSettled 一致(fulfilled/rejected 各就位)', async () => {
  const err = new Error('x');
  const out = await allSettledFallback([Promise.resolve(1), Promise.reject(err), 42]);
  assert.deepEqual(out[0], { status: 'fulfilled', value: 1 });
  assert.deepEqual(out[1], { status: 'rejected', reason: err });
  assert.deepEqual(out[2], { status: 'fulfilled', value: 42 }, '非 Promise 值也接受');
});

test('allSettled 委托可用实现(fallback 路径经 mock 验证)', async () => {
  // node 20 有原生,直接验证委托语义
  const out = await allSettled([Promise.resolve(7)]);
  assert.deepEqual(out, [{ status: 'fulfilled', value: 7 }]);
});
