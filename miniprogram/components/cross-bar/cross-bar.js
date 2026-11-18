// 跨清单摘要条组件 —— 头部前20只读穿透字段,不触网
const { crossChips } = require('../../utils/cross.js');

Component({
  properties: {
    // 传入接口返回的清单原始行(items),组件内部做 chips 组装
    items: {
      type: Array,
      value: [],
      observer() { this._refresh(); }
    }
  },
  data: {
    chips: []
  },
  lifetimes: {
    attached() { this._refresh(); }
  },
  methods: {
    _refresh() {
      this.setData({ chips: crossChips(this.data.items || []) });
    },
    onChipTap(e) {
      const code = e.currentTarget.dataset.code;
      if (code) this.triggerEvent('chiptap', { code });
    }
  }
});
