// app.json 深浅主题结构测试 —— 评审 M5:darkmode 声明 + theme.json 双模式变量齐全。
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');

test('M5:darkmode true + themeLocation + @引用均在 light/dark 有定义', () => {
  const app = JSON.parse(fs.readFileSync('miniprogram/app.json', 'utf8'));
  assert.equal(app.darkmode, true, '须声明 darkmode:true 否则 wxss 浅色媒体查询不生效');
  assert.equal(app.themeLocation, 'theme.json');
  const theme = JSON.parse(fs.readFileSync('miniprogram/theme.json', 'utf8'));
  assert.ok(theme.light && theme.dark, 'theme.json 须含 light 与 dark 两套');
  // 收集 app.json window/tabBar 里所有 @引用,逐个断言两套主题都有定义
  const refs = [];
  ['window', 'tabBar'].forEach(sec => {
    const o = app[sec] || {};
    Object.values(o).forEach(v => {
      if (typeof v === 'string' && v.startsWith('@')) refs.push(v.slice(1));
    });
  });
  assert.ok(refs.length >= 5, '导航栏/tabBar 主要颜色应走主题变量,当前 ' + refs.length + ' 个');
  refs.forEach(k => {
    assert.ok(k in theme.light, 'light 缺变量 ' + k);
    assert.ok(k in theme.dark, 'dark 缺变量 ' + k);
  });
});
