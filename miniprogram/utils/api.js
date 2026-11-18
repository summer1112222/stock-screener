// wx.request 封装 —— 超时/单次重试/后端不可达文案。
// 合约：resolve 整个 _wrap 后的 JSON（含 data/update_time/disclaimer）；
// 网络层失败或 statusCode>=400 统一 reject Error('后端不可达（本机服务开着吗？）')。
// retry=true 时失败自动重发一次（对齐网页版 _qsFetch 行为，quality 冷缓存场景用）。
// CommonJS；setTransport 注入传输函数供 node --test，生产默认全局 wx.request。

const { BASE_URL } = require('../config.js');

const ERR_MSG = '后端不可达（本机服务开着吗？）';

function defaultTransport(options) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: options.url,
      timeout: options.timeout,
      method: options.method || 'GET',
      success: resolve,
      fail: reject
    });
  });
}

let _transport = defaultTransport;

function setTransport(fn) {
  _transport = fn;
}

function _attempt(opts) {
  return _transport(opts).then((res) => {
    if (!res || typeof res.statusCode !== 'number' || res.statusCode >= 400) {
      throw new Error(ERR_MSG);
    }
    return res;
  });
}

function request(opts) {
  const options = {
    url: BASE_URL + opts.path,
    timeout: opts.timeout === undefined ? 90000 : opts.timeout,
    method: opts.method || 'GET'
  };
  const attempt = () => _attempt(options).then((res) => res.data);
  return attempt().catch((err) => {
    if (opts.retry) {
      return attempt().catch(() => { throw new Error(ERR_MSG); });
    }
    throw err && err.message === ERR_MSG ? err : new Error(ERR_MSG);
  });
}

module.exports = { request, setTransport };
