// 蓬蒿者 —— stock-screener 随身客户端（个人主体自用，不上架）
// 合规：本小程序只是既有 FastAPI 后端的只读展示层，disclaimer 由后端 _wrap 管道
// 照常返回，页脚原样显示；不荐股、不输出买卖点、不下单。
App({
  onLaunch() {
    // 无全局状态需求：BASE_URL 在 config.js 单点配置
  }
});
