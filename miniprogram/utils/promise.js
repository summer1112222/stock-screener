// Promise.allSettled shim —— 评审 M6:较旧小程序基础库无原生实现时降级。
// 主力页 load 用 allSettled 并行拉 channels+today,无 shim 时旧库直接
// TypeError 走兜底错误条,通道灯+清单全不可用。
// CommonJS 纯函数。

function allSettledFallback(promises) {
  return Promise.all((promises || []).map(p => Promise.resolve(p).then(
    (v) => ({ status: 'fulfilled', value: v }),
    (e) => ({ status: 'rejected', reason: e })
  )));
}

function allSettled(promises) {
  return (typeof Promise.allSettled === 'function')
    ? Promise.allSettled(promises)
    : allSettledFallback(promises);
}

module.exports = { allSettled, allSettledFallback };
