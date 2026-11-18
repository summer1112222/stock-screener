const test = require('node:test');
const assert = require('node:assert');
const { fmtNum, fmtFixed } = require('../../miniprogram/utils/fmt.js');

test('fmtNum 万亿缩写', () => {
  assert.equal(fmtNum(163000000), '1.63亿');
  assert.equal(fmtNum(8227400), '822.74万');
  assert.equal(fmtNum(-8227400), '-822.74万');
  assert.equal(fmtNum(123.456), '123.46');
});

test('fmtNum 缺失诚实 —', () => {
  assert.equal(fmtNum(null), '—');
  assert.equal(fmtNum(undefined), '—');
  assert.equal(fmtNum(NaN), '—');
});

test('fmtFixed', () => {
  assert.equal(fmtFixed(0.7123), '0.71');
  assert.equal(fmtFixed(null), '—');
});

test('app.json 语法与关键配置', () => {
  const fs = require('node:fs');
  const app = JSON.parse(fs.readFileSync('miniprogram/app.json', 'utf8'));
  assert.ok(app.pages.includes('pages/smart/smart'));
  assert.ok(app.pages.includes('pages/quality/quality'));
  assert.ok(app.pages.includes('pages/nextday/nextday'));
  assert.ok(app.tabBar && app.tabBar.list && app.tabBar.list.length === 3);
  assert.equal(app.networkTimeout && app.networkTimeout.request, 180000);
  assert.equal(app.window && app.window.navigationBarTitleText, '蓬蒿者');
});

test('config.js 导出 BASE_URL', () => {
  const cfg = require('../../miniprogram/config.js');
  assert.equal(typeof cfg.BASE_URL, 'string');
  assert.ok(cfg.BASE_URL.startsWith('http://'));
});
